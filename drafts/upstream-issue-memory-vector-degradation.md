# Upstream Issue Draft — openclaw/openclaw

> 状态：草稿待 /approve 后发布（curl + token header 兜底，gh 被指纹拦截）
> 仓库：openclaw/openclaw · 版本：2026.7.1-2
> 关联：#165245（memory.promotion 事件可观测性）

---

## Title

memory-core: embedding provider failure is cached permanently and degrades to FTS-only **silently** (no log, no retry, no self-heal until gateway restart)

## Body

### Environment

- OpenClaw 2026.7.1-2 (macOS, gateway via launchd)
- `agents.defaults.memorySearch.provider = "openai-compatible"`, model `BAAI/bge-m3`, `remote.baseUrl = http://localhost:8080/v1` (self-hosted FastAPI/gunicorn embeddings server)
- `memory.backend = "builtin"`, plugin `memory-core` enabled

### Observed behavior

After a restart of the (healthy, verified-by-curl) embeddings server, `memory_search` returned FTS-only results with `vectorScore = 0` on **4 consecutive calls over ~7 minutes** (06:25–06:32 local). No `warn`/`error` was written to gateway logs; the server's access log confirmed requests were either absent or indistinguishable (see "observability gap" below). A gateway restart at 06:37 restored vector recall immediately (`vectorScore` 0.55–0.73 on next call).

So: one transient failure window permanently degraded vector recall inside the running gateway process, with **zero observability**.

### Root cause chain (source-verified, `dist/manager-DB4_iNu4.js` @ 2026.7.1-2)

1. **Soft-null provider result is cached as a success.**
   `applyProviderResult()` sets `this.providerInitialized = true` **unconditionally**, even when `loadProviderResult()`/`createEmbeddingProvider()` resolves a soft failure (`provider: null` + `providerUnavailableReason`). `ensureProviderInitialized()` short-circuits on `if (this.providerInitialized) return` — the failure is never retried for the lifetime of the manager instance.

2. **Silent FTS-only branch on null provider.**
   In `search()`, the `if (!this.provider)` branch calls `assertRequiredProviderAvailable("search")` — a **no-op in non-required mode** — then returns keyword-only results without any log line. (In `required` mode it throws, which is observable; the silent window is any path where provider becomes null under `optional`, or where embedding returns an empty vector instead of failing.)

3. **The sync-side retry escape hatch can be permanently closed.**
   The only other retry path (`resetProviderInitializationForRetry()` inside sync) is gated by `existingMeta.model === "fts-only"` — once index metadata has been written as fts-only, sync returns early and the retry channel is dead too.

4. **Empty/zero query vectors skip the vector leg silently.**
   `const vectorResults = queryVec.some((v) => v !== 0) ? await this.searchVector(...) : [];` — an empty or all-zero query vector produces **no log at all**, and the search silently returns FTS-only. Notably, on the openai-compatible HTTP path, `readEmbeddingVector()` throws on non-finite numbers but **passes an empty array (`embedding: []`) through silently** (`Array.isArray([])` passes, loop body never runs), and the adapter treats `[]` as truthy (`if (!embedding) throw` does not fire).

5. **Fallback provider switch is one-way.**
   `activateFallbackProvider()` refuses to re-run once `fallbackFrom` is set (`if (this.fallbackFrom) return false`), so a one-time fallback activation is never revisited even if the primary provider recovers.

### Why it couldn't be diagnosed after the fact

- gunicorn access log was configured without a timestamp format (`%(h)s - "%(r)s" %(s)s`), so requests cannot be correlated in time.
- The silent branches above emit nothing at `warn`/`error` level.
Together this turned a 7-minute self-healing-in-principle incident into an unattributable one (we initially misattributed it to a non-self-healing HTTP client / connection pool).

### Suggested fixes (any subset helps)

1. Don't set `providerInitialized = true` when the resolved provider is null; or add a TTL-based re-probe for null-provider managers (e.g. reuse `EMBEDDING_PROBE_CACHE_TTL_MS` semantics).
2. Add a `log.warn` (including `providerUnavailableReason`) to the null-provider FTS-only branch in `search()`.
3. Log at `debug`/`warn` when `queryVec.some((v) => v !== 0)` is false so empty-vector degradation is visible.
4. Make `readEmbeddingVector()` throw on empty arrays (consistent with `undefined`), so a malformed server response surfaces instead of silently zeroing recall.
5. Allow `activateFallbackProvider()` to re-evaluate after a cooldown instead of a permanent one-way latch.

### Local mitigation we shipped

A heartbeat task running `memory_search` every 2h and alerting on `vectorScore === 0` (restart still requires explicit operator approval), plus timestamped gunicorn access logs (`--access-logformat '%(t)s %(h)s - "%(r)s" %(s)s %(L)ss'`).

Happy to provide more detail (full timeline, logs with secrets redacted) if useful.
