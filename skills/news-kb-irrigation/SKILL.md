---
name: "news-kb-irrigation"
description: "Backfill, merge, and safely grow the news knowledge base (data/news/knowledge_base.md) in verified batches with zero-loss gates"
---

# News KB Irrigation — Plan → Queue → Synthesize → Write → Verify

Grow and maintain `data/news/knowledge_base.md` (the news knowledge base) in verified
batches without losing existing entries. Every step below is sourced from the
2026-10-26 irrigation session (batches 4–13, 19 commits), in which the KB was rebuilt
from 8,207 stacked lines to a deduplicated ~4.8k-line file and Detailed coverage grew
from 24 to 100+ entries.

## When to use

- A batch of verified facts (earnings, institutional flows, announcements) needs to
  become Detailed KB entries.
- The KB has accumulated stacked/duplicate entries and needs variant merging or an
  audit.
- Any KB write that must not lose existing titles.

## Files and layout

- KB file: `data/news/knowledge_base.md` — bullet entries (•/-/*) with sub-lines
  labeled `Details:` / `Data:` / `Insight:` / `Related:`.
- Priority queue: `data/news/expansion_priority.json` (built by the planner).
- Batch queue: `data/news/current_backfill_queue.json` (written by the backfiller).
- Progress ledger: `data/news/backfill_progress.json` (completed titles, keyed by
  title).
- Synthesis payloads: `data/news/*_synthesized.json` (agent-written per batch).
- Toolchain scripts (workspace root): `scripts/kb_expansion_planner.py`,
  `scripts/kb_backfiller.py`, `scripts/kb_writer_v2.py`,
  `scripts/kb_variant_merge.py`, `scripts/kb_content_audit.py`,
  `scripts/kb_linker.py`, `scripts/kb_repair_v1.py` (parser used by merge).
- Zero-loss gate (support file of this proposal): `scripts/verify_kb_zero_loss.py`.

## Pipeline (one batch ≈ 5–20 items)

### 1. Plan — score what deserves irrigation

```bash
python3 scripts/kb_expansion_planner.py
```

Rebuilds `data/news/expansion_priority.json` from current KB state (Top-100 style
value scoring). It imports the watchlist SSOT `analysis.fib_extension_scan.WATCHLIST`
— never hardcode a stock list.

### 2. Queue — take the next batch

```bash
python3 scripts/kb_backfiller.py
```

Scheduler only: filters titles already present in `backfill_progress.json`, takes the
top 20 (hardcoded `batch_size = 20`), prints them, and writes
`data/news/current_backfill_queue.json` for the agent loop. It does NOT synthesize
content — its `synthesize_detailed_content()` returns a `PENDING_SYNTHESIS` stub;
real synthesis is agent work (step 3).

### 3. Synthesize — agent research per item

For each queued title, research with web_search/web_fetch and write a
`data/news/<batch>_synthesized.json` payload:

- Format: Conclusion → Data → Impact; sub-lines use the labels
  `Details:` / `Data:` / `Insight:` / `Related:`.
- Headline numbers need two independent sources when obtainable (the session
  cross-checked earnings against dragon-tiger lists and industry aggregates).
- Honest reporting: state n / confidence intervals; annotate missing readings as
  missing (e.g. 天齐 Q3 机构读数缺失如实标注) instead of inventing them.
- Annotate conflicts with existing entries instead of silently overwriting them
  (e.g. capex-expansion vs HBM-capacity divergence notes).
- Do NOT irrigate queue noise: leftover event-variants, financing announcements, and
  resignation items were explicitly judged not worth watering (噪声不可灌). Prefer
  variant merge (step 6) or stop the batch instead.

### 4. Write — apply payloads to the KB

```bash
cp data/news/knowledge_base.md data/news/knowledge_base.md.bak-<label>
python3 scripts/kb_writer_v2.py
```

- Always back up first (the session used `.bak-<label>` / timestamped copies).
- `kb_writer_v2.py` scans `data/news/*_synthesized.json`, applies each, then
  auto-runs `scripts/kb_linker.py` to refresh associations.
- v2 write rules (fixed after the v1 stacking bug): replace scope = title line plus
  ALL old sub-lines (no residue), and append-fallback dedups by normalized title to
  prevent cross-section double-writes. Do not use the old `kb_writer.py` v1.

### 5. Zero-loss gate — prove nothing vanished

```bash
python3 scripts/verify_kb_zero_loss.py <backup> data/news/knowledge_base.md
```

- Gate: every unique title in the backup must still exist in the current file
  (missing = 0). The current file may legitimately gain titles; raw line count may
  legitimately shrink from dedup — line counts are report-only, never a gate.
- Exit 0 → proceed to commit. Exit 1 → adjudicate each missing title before doing
  anything else; exit 1 means review, not automatically data loss.
- Adjudication rule: locate each missing title in the current KB (grep its key
  numbers or stock name). If the content survived under a re-punctuated/rewritten
  title (variant merge strips colons/commas/同比 clauses/`机构风向标|` prefixes) or
  was legitimately updated (中报→Q3 rewrite), record the adjudication in the commit
  message and proceed. If the content is truly gone, restore from the backup and fix
  the writer before re-running.
- First real-world run (2026-10-27, pre-rebuild snapshot `5eac742^` vs current):
  exit 1 with 6 missing titles → all 6 adjudicated as variant-merge rewrites with
  identical numbers — zero loss. The gate correctly surfaced title changes.
- Session precedent: an 8,207→4,862-line "shrink" was proven legitimate by
  unique-title comparison (backup 1,526 unique titles missing = 0; current had +91
  new titles); the real defect found was writer stacking/cross-section double-writes.

### 6. Variant merge — dedup same-event title variants

```bash
python3 scripts/kb_variant_merge.py            # dry run: prints merge plan
python3 scripts/kb_variant_merge.py --apply    # apply with per-line gate
```

- Merges punctuation / prefix / 同比-clause title variants into the richest block.
- Deletion is index-based with a per-line gate; a backup (`.bak-variantmerge`) is
  taken before apply. Do not "improve" it back to id()-based tracking: id() tracking
  leaked blocks (211→196 caught by the gate); the index rewrite restored 211→211
  zero loss.
- Session result: 20 variant groups merged, 4,860→4,839 lines, zero loss.

### 7. Audit + commit — close the batch

```bash
python3 scripts/kb_content_audit.py
```

Prints total entries, with-content coverage %, grade distribution, and samples. Then:

- Record completed titles in `data/news/backfill_progress.json` (the backfiller
  filters on this; Detailed entries naturally score 0 in the next planning round, so
  the queue advances on its own).
- One descriptive git commit per batch (session pattern:
  `feat(kb): batch_N 回填N条…`), including judgment calls (证伪结论、冲突标注、
  零丢失裁决记录).

## Hard rules (each paid for in the source session)

1. Scripts schedule; agents synthesize. Never treat scheduler output as content.
2. Back up before every write; verify with unique-title sets, not line counts.
3. Unique-title set comparison is the zero-loss SSOT; line counts are context.
4. Dedup operations must be index-based and gated per line; keep the gate.
5. Agent self-reports (已修复/已写入) are claims, not facts — verify via the gate or
   `git diff` before repeating them.
6. Key numbers need dual sources or an explicit single-source annotation.
7. Batch discipline: plan → queue → synthesize → write → gate → merge → audit →
   commit. Small batches keep the gate meaningful.

## Failure modes seen (do not repeat)

- v1 writer stacked old sub-lines (replace without full block scope) and double-wrote
  across sections → use `kb_writer_v2.py`.
- id()-based block tracking lost blocks silently → index-based deletion + gate.
- Raw line-count panic (8,207→4,862) without set comparison → verify titles first.
- Treating gate exit 1 as automatic failure → it means review: adjudicate each
  missing title (variant/rewrite vs true loss) before fixing anything.
- Irrigating queue noise (variants/financing/resignations) wastes the batch → merge
  or skip.
