# Upstream issue draft (openclaw repo) — 2026-10-05 已核实/可加粗提交流程

**Title**: memory.promotion.applied event should record droppedDates/budgetChars (budget compaction is unobservable)

**Body**:

`applyShortTermPromotions()` calls `compactMemoryForBudget()` before writing a promoted
section to MEMORY.md. When the file exceeds `budgetChars`
(`DEFAULT_MEMORY_FILE_MAX_CHARS = 1e4` by default), the oldest promotion blocks are
dropped by date to make room.

Problem: `compaction.droppedDates` only exists in the in-memory return value. The
journal event `memory.promotion.applied` (memory/.dreams/events.jsonl) records only
`applied`/`candidates` — no `droppedDates`, no `budgetChars`. Result: every promote
reports "success" while older promotions are silently evicted. In our workspace
(203KB MEMORY.md, human-written region ~117KB >> 10K budget), 371 historical promoted
keys collapsed to 6 live markers, yet all 66 journal events claimed success.

Also note: `options.memoryFileMaxChars` exists in the function signature but neither
call site (dreaming sweep, cli.runtime promote) passes it, and the memory-core plugin
config schema exposes no such key — so users cannot even raise the budget via config.

**Suggested fix**:
1. Record `droppedDates` + effective `budgetChars` in `memory.promotion.applied`.
2. Optionally expose `memoryFileMaxChars` through the dreaming config surface.
