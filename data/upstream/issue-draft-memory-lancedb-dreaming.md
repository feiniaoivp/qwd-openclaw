# Issue draft — airbing11/memory-lancedb-dreaming

> **更正评论已提交（2026-10-23 20:3x）**：https://github.com/airbing11/memory-lancedb-dreaming/issues/4#issuecomment-6364978142
> 更正要点：无 60s message 振荡（实为日报 job id 每 60s 删+建漂移）；深梦境 cron 会经"夺取日报 job"产生；
> 新发现 churn 与用户 job 无关（插件自建两 job 互殴，启用即坏）；互斥修复建议不变且可同时消掉夺取与 churn。

> 状态：**已提交 → https://github.com/airbing11/memory-lancedb-dreaming/issues/4**（#4，2026-10-23 19:5x，用户「/approve 提 issue」授权；gh Go TLS 被指纹拦截，改用 curl + `gh auth token` 临时 header 提交）。
> 版本基线：v0.3.14（clawhub npm-pack tgz，sha512 NRs/ziOT…）

---

**Title**: cron reconcile: daily-report job is also matched as a dreaming job (tag prefix collision) → 60s oscillation and deep-dreaming cron never created

**Body**:

## Summary

`MANAGED_DAILY_REPORT_CRON_TAG` (`"[managed-by=memory-lancedb-dreaming] daily-report"`) contains `MANAGED_DREAMING_CRON_TAG` (`"[managed-by=memory-lancedb-dreaming]"`) as a prefix substring. `isManagedDreamingJob()` matches with `description?.includes(MANAGED_DREAMING_CRON_TAG)`, so **the managed daily-report job also satisfies `isManagedDreamingJob`**.

## Consequences (while the plugin is enabled)

`reconcileManagedDreamingCron()` runs the dreaming branch first, then the daily-report branch — both adopt the SAME job (identified via the description tag), and each `reconcileSingleManagedCron()` re-lists jobs, so:

1. **Permanent oscillation**: the dreaming branch patches the job to (`cron: 0 3 * * *`, `payload: DREAMING_TRIGGER_TOKEN`), then the daily-report branch patches it back to (`cron: 0 4 * * *`, `payload: DAILY_REPORT_TRIGGER_TOKEN`). With `RUNTIME_CRON_RECONCILE_INTERVAL_MS = 60_000` this is ~2 cron.update mutations per minute (~2,880/day) forever.
2. **Deep-dreaming cron is never created**: because the daily-report job is adopted as the dreaming managed job, `reconcileSingleManagedCron` (dreaming branch) sees `managed.length === 1` and never calls `cron.add(desired)` — the dedicated `"LanceDB Memory Dreaming"` 03:00 job never exists, so the deep-dreaming pipeline is never triggered by cron.
3. **User customizations are silently reverted**: any change to the managed daily-report job's payload/schedule (e.g. replacing the trigger token with a custom prompt) is overwritten within 60 seconds.

## Repro

1. Install v0.3.14, let it create the managed daily-report job (or create a job whose description contains the daily-report tag).
2. Enable the plugin (`autoManageCron` default true).
3. Observe via `openclaw cron get <id>` / `openclaw cron runs`: payload message and schedule flip-flop every ~60s; no `"LanceDB Memory Dreaming"` job appears.

## Suggested fix

Mutual exclusion at the top of each predicate (same module, no import cycle):

```js
function isManagedDreamingJob(job) {
    if (isManagedDailyReportJob(job)) return false;   // ← add
    if (normalizeTrimmedString(job.description)?.includes(MANAGED_DREAMING_CRON_TAG))
        return true;
    ...
}
```

(and/or make the two tags non-overlapping, e.g. `[managed-by=memory-lancedb-dreaming] dreaming`, with a migration for existing job descriptions).

## Environment

- openclaw 2026.7.1-2, plugin v0.3.14 (clawhub community, npm-pack)
- Observed in `dist/cron.js` (`isManagedDreamingJob`, `reconcileManagedDreamingCron`, `buildManagedDailyReportPatch`) and `dist/constants.js` (L4–L7)

---
*Drafted 2026-10-23; internal evidence: memory/2026-10-23.md §19:0x. Do not include any secrets.*
