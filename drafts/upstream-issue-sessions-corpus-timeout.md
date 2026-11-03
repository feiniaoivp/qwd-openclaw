# Upstream issue draft: sessions corpus timeout

> ✅ Published as [#183499](https://github.com/openclaw/openclaw/issues/183499) (2026-10-25 17:4x, HTTP 201).

### What happens

`memory_search` with `corpus: "sessions"` times out on **every** call in this environment, while `corpus: "memory"` in the same session works fine. The tool schema advertises `corpus=sessions` unconditionally, but the sessions source was never indexed on this install (default `sources` is `["memory"]`), and each call appears to enter a pre-embedding phase (search-time catch-up / dirty-check over the whole session store) that cannot finish within the 15s tool budget. Every failure is reported with a generic **"embedding/provider error"**, which is misleading — the embedding endpoint receives zero requests during the hang.

### Environment

- openclaw 2026.7.1-2 (version-pinned), macOS 15.8.1 x64, node v26.3.1
- builtin memory backend; `memorySearch.provider="openai-compatible"`, model `BAAI/bge-m3` via a local HTTP endpoint (healthy: direct probes return 1024-dim vectors in ~0.17s)
- session store: **10,211 transcript files / 2.4 GB**
- index state: `memory_index_meta.sources=["memory"]`; `memory_index_sources` = 579 rows, all `source='memory'`, **0 rows for `'sessions'`**

### Reproduction

1. Default config (no `memorySearch.sources` override), large sessions dir.
2. Call the `memory_search` tool with `corpus: "sessions"`, small `maxResults` (e.g. 3), any query text.
3. Result (4/4 calls on 2026-10-25, distinct queries):

```json
{
  "results": [],
  "disabled": true,
  "unavailable": true,
  "error": "memory_search timed out after 15s",
  "warning": "Memory search is unavailable due to an embedding/provider error."
}
```

4. Control: `corpus: "memory"` returns in 264–547 ms with healthy vector scores → endpoint and index are fine; the failure is corpus-specific.

### Evidence gathered while it hangs

- **Zero embedding traffic during the 15s window**: sampling the embedding endpoint's access log and the index DB WAL at 2s intervals shows 0 new POSTs and 0 WAL growth → the hang sits **before any embedding request**; the "embedding/provider error" message is misattributed.
- **Zero gateway logs** for any of the four failed calls.
- **Doomed catch-up churn**: during the first `corpus=sessions` usage window, a `*.memory-reindex-*` temp DB appeared (WAL grew to 41 MB) and was cleaned up ~15 min later **without merging** — `memory_index_meta.sources` stayed `["memory"]`, no `sessions` rows appeared. The catch-up never persists, so every later call repeats work that can never finish.
- In the 2026.7.1-2 dist, `startAsyncSearchSync()` runs a sync on the search path when dirty state exists, and sessions dirty-tracking enumerates session files (`updateSessionDelta()` — stat/hash per file). With 10k files this phase alone can exceed the 15s tool budget. (Hedge: minified dist; the exact trigger branch for sessions-without-`sources`-config is not fully pinned.)

### Impact

- `corpus=sessions` can **never** succeed here, yet the tool schema still advertises it — each attempt burns 15s and may kick heavy temp-DB churn (hundreds of MB written then deleted).
- The failure is indistinguishable from a provider outage to the caller ("embedding/provider error"), inviting pointless remediation (restarting the embedding service, rebuilding the index…).
- Nothing in the gateway log explains what the search-time sync is doing (observability gap).

### Suggested fixes

1. **Bound or skip the search-time catch-up**: don't catch up the sessions source inline at search time — either skip it (return keyword/empty results with a diagnostic) or bound it (visibility-matched / N most recent); full indexing belongs to an explicit or background path.
2. **Honest diagnostic when the corpus was never indexed**: if `corpus=sessions` is requested but `meta.sources` lacks `"sessions"`, return a clear result like "sessions source is not indexed; add \"sessions\" to memorySearch.sources" instead of a generic provider-error timeout.
3. **Don't misattribute tool timeouts to "embedding/provider error"** when no embedding request was made.
4. **Log search-time sync start/abort** (one line each) so this state is observable.
5. **Docs/tool-schema alignment**: mark `corpus=sessions` in the tool description as requiring `memorySearch.sources: ["memory","sessions"]`.

Related but distinct from #183425 (silent all-zero-vector semantic omission): that issue is about silent degradation of the memory source; this one is about `corpus=sessions` being permanently unusable with misleading diagnostics on large session stores.
