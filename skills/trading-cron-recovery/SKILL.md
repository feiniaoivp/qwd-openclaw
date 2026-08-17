---
name: "trading-cron-recovery"
description: "\"Diagnose and fix failing cron jobs in the A-share monitoring pipeline by checking model configs, script outputs, and delivery status\""
---

# Trading Cron Recovery Skill

Diagnose and fix failing cron jobs in the A-share monitoring pipeline. Use when cron jobs show `error` status, `not-delivered` Telegram messages, or model timeout failures.

## When to Use

- Cron jobs in `cron list` show `lastRunStatus: "error"`
- Telegram delivery shows `lastDeliveryStatus: "not-delivered"`
- Errors mention `model-call-started` timeout or `Process: \`<name>\` failed`
- Multiple consecutive failures (`consecutiveErrors >= 3`)

## Diagnostic Steps

### 1. List Failing Crons
```bash
cron list --includeDisabled false
```
Filter for `lastRunStatus: "error"` and note `jobId`, `lastError`, `lastDiagnostics`.

### 2. Classify Failure Type

| Symptom | Likely Cause | Fix Path |
|---------|--------------|----------|
| `model-call-started` timeout | LLM provider misconfig or fallback chain broken | Check `openclaw.json` models.providers, fix invalid model IDs |
| `Process: \`<name>\` failed` | Isolated agent crashed (OOM, import error) | Check agent logs, fix script deps |
| `deliveryStatus: not-delivered` | Script succeeded but Telegram send failed | Verify bot token, chat ID, network |
| Script runs but wrong output | Business logic bug in Python script | Run script manually, debug |

### 3. Fix Model Provider Config (Most Common)

**File**: `~/.openclaw/openclaw.json`

**Problem**: nvidia provider contains models that return 404 (kimi-k2.5, glm-5.1, minimax-m2.5, etc.)

**Fix**: Remove invalid models, keep only verified ones:
```json
"nvidia": {
  "models": [
    {"id": "nvidia/nemotron-3-ultra-550b-a55b", ...},
    {"id": "nvidia/nemotron-3-super-120b-a12b", ...}
  ]
}
```

**Also fix fallback chain** in `agents.defaults.model.fallbacks`:
```json
"fallbacks": [
  "nvidia/nemotron-3-super-120b-a12b",
  "google/gemini-2.5-flash",
  "deepseek/deepseek-reasoner",
  "nvidia/nemotron-3-ultra-550b-a55b"
]
```

### 4. Pin Critical Crons to Stable Model

For each critical cron (premarket, news, auction), update job payload:
```bash
cron update <jobId> --patch '{"payload": {"model": "deepseek/deepseek-chat"}}'
```

Critical cron IDs (as of 2026-08):
- `10560fab-4a01-4bc0-8ba3-38407a7a2deb` — daily-premarket-analysis (06:00)
- `0f08dc3e-31d6-4537-9977-84595d52e1d2` — news-morning-reading (08:30)
- `c2ea158d-d903-43a8-8339-3b0c53ce236b` — auction-feed-0915 (09:15)
- `c99c3502-bc7b-4903-834e-64c9cc1d1c4b` — auction-analyze-0926 (09:26)

### 5. Restart Gateway to Reload Config

```bash
launchctl unload ~/Library/LaunchAgents/ai.openclaw.gateway.plist
sleep 2
launchctl load ~/Library/LaunchAgents/ai.openclaw.gateway.plist
sleep 3
openclaw gateway status
```

Verify: `Connectivity probe: ok`, version shows `2026.7.1-2`.

### 6. Verify Fix with Manual Run

```bash
cron run <jobId> --runMode force
```

Check `lastRunStatus: "ok"` and `lastDeliveryStatus: "delivered"`.

### 7. Fix Script-Level Bugs (If Needed)

**premarket_report.py** — audit segment showed `[?]`:
- `format_signal_audit()` must handle both `severity/msg` and `level/message` fields from `signal_audit.py`

**nodes.py** — BUY advice missing stop-loss:
- `rule_risk_advice()` must always include stop-loss (`price * 0.95`) and support (`price * 0.92`) for BUY actions

Run scripts manually to verify:
```bash
cd /Users/duguke/.openclaw/workspace
python3 analysis/premarket_report.py
python3 analysis/signal_audit.py --date $(date +%F)
python3 -m analysis.agent.run_agent
```

## Validation Checklist

After fixes, confirm:
- [ ] All 4 critical crons show `lastRunStatus: "ok"`
- [ ] Telegram delivery shows `delivered` for each
- [ ] Premarket report includes proper audit section (not `[?]`)
- [ ] BUY recommendations include stop-loss/support prices
- [ ] No `model-call-started` timeouts in diagnostics
- [ ] `consecutiveErrors` reset to 0

## References

- Workspace: `/Users/duguke/.openclaw/workspace`
- Config: `~/.openclaw/openclaw.json`
- Gateway service: `~/Library/LaunchAgents/ai.openclaw.gateway.plist`
- Daily reports: `analysis/daily/YYYY-MM-DD_premarket_report.md`
- Signal audits: `analysis/daily/YYYY-MM-DD_signal_audit.md`
