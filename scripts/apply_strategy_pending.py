#!/usr/bin/env python3
"""
apply_strategy_pending.py — 策略候审清单应用器（2026-10-11 #8）
================================================================
与 validate_strategies.py --pending-out 配套的人工闸门：

  周六自动模式 (--auto):
      逐条候审变更计算 DSR（best-of-N 运气基线，口径同 dsr_sharpe.py：
      trials=全部策略×窗口夏普拉平，显著=DSR≥0.95）：
        显著     → 自动写入 adaptive_strategy_map.json（版本化+备份）
        偏弱/不显著/无 trials → 留在候审清单，等人批准
  人工模式:
      --status                  查看候审清单
      --apply-symbol 002156,..  人工批准后应用指定股票
      --apply-all               应用全部候审（谨慎）
      --reject                  驳回全部候审（清空清单，记录到日志）

等待/恢复语义（TaskFlow 模式的文件实现）：候审清单存在=waiting；
--apply-*=resume；--reject=cancel。
"""

import os
import sys
import json
import shutil
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PENDING_FILE = os.path.join(WORKSPACE, "data", "strategy_pending.json")
MAP_FILE = os.path.join(WORKSPACE, "data", "adaptive_strategy_map.json")
REJECT_LOG = os.path.join(WORKSPACE, "data", "strategy_pending_rejected.jsonl")

# 与 dsr_sharpe.py 批量口径一致
DSR_SIGNIFICANT = 0.95


def _dsr_for_change(change: dict):
    """返回 (dsr, verdict, precise) 或 (None, reason, False)。"""
    trials_raw = change.get("dsr_trials") or []
    try:
        import numpy as np
        flat = [x for item in trials_raw for x in (item.get("sharpes") or [])
                if x is not None and np.isfinite(x)]
    except Exception:  # noqa: BLE001
        flat = [x for item in trials_raw for x in (item.get("sharpes") or []) if x is not None]
    if len(flat) < 2:
        return None, "trials<2 无法验证", False
    from analysis.dsr_sharpe import dsr_best_of_n
    try:
        _, out = dsr_best_of_n(flat, annual=252.0)
    except ValueError as exc:
        return None, f"skip:{exc}", False
    dsr = out.get("DSR")
    verdict = out.get("verdict", "")
    significant = "显著" in verdict and "不显著" not in verdict
    return dsr, verdict, significant


def _load_map():
    with open(MAP_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_map(cur, source: str, n_changes: int):
    """版本化写回 + 文件备份。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = MAP_FILE + f".bak-{stamp}"
    shutil.copy2(MAP_FILE, bak)
    try:
        from analysis.strategy_registry import create_version
        flat = {k: (v.get("strategy") if isinstance(v, dict) else v) for k, v in cur.items()}
        create_version(flat, source=source,
                       metadata={"applied_changes": n_changes,
                                 "pending_applied_at": stamp})
        print(f"  🗂️ registry 版本化 ok（备份 {os.path.basename(bak)}）")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"  ⚠️ registry 写入失败({e})，回退直接写文件\n")
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)


def _apply_symbols(pending: dict, symbols):
    cur = _load_map()
    applied = []
    remaining = []
    for ch in pending.get("changes", []):
        sym = ch["symbol"]
        if sym in symbols:
            old_v = cur.get(sym)
            if isinstance(old_v, dict):
                old_v["strategy"] = ch["new"]
            else:
                cur[sym] = ch["new"]
            applied.append(ch)
        else:
            remaining.append(ch)
    if applied:
        _save_map(cur, source="apply_strategy_pending", n_changes=len(applied))
        for ch in applied:
            print(f"  ✅ {ch['name']}({ch['symbol']}): {ch['old']} -> {ch['new']}（已写入 map）")
    pending["changes"] = remaining
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=1)
    print(f"候选清单剩余 {len(remaining)} 条")
    return len(applied)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="DSR 显著自动应用，其余留在候审")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--apply-symbol", default=None, help="逗号分隔股票代码")
    ap.add_argument("--apply-all", action="store_true")
    ap.add_argument("--reject", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(PENDING_FILE):
        print("ℹ️ 无候审清单（data/strategy_pending.json 不存在），无事可做")
        return 0
    with open(PENDING_FILE, encoding="utf-8") as f:
        pending = json.load(f)
    changes = pending.get("changes", [])
    if not changes:
        print("ℹ️ 候审清单为空")
        return 0

    if args.status:
        print(f"📮 候审清单（生成于 {pending.get('generated_at')}）—— {len(changes)} 条：")
        for ch in changes:
            dsr, verdict, sig = _dsr_for_change(ch)
            tag = f"DSR={dsr:.3f} {verdict}" if dsr is not None else verdict
            print(f"  {ch['name']}({ch['symbol']}): {ch['old']} -> {ch['new']} | {tag} | {ch['reason']}")
        return 0

    if args.reject:
        with open(REJECT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"rejected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "count": len(changes),
                                "changes": changes}, ensure_ascii=False) + "\n")
        os.remove(PENDING_FILE)
        print(f"🗑️ 已驳回全部 {len(changes)} 条候审（记录 → {os.path.basename(REJECT_LOG)}）")
        return 0

    if args.apply_all:
        n = _apply_symbols(pending, {ch["symbol"] for ch in changes})
        print(f"✅ 人工全量批准：应用 {n} 条")
        return 0

    if args.apply_symbol:
        syms = {s.strip() for s in args.apply_symbol.split(",") if s.strip()}
        n = _apply_symbols(pending, syms)
        print(f"✅ 人工指定批准：应用 {n} 条 {sorted(syms)}")
        return 0

    if args.auto:
        auto_syms = set()
        kept = []
        print(f"📮 候审 {len(changes)} 条，按 DSR 分流（显著≥{DSR_SIGNIFICANT} 自动应用）：")
        for ch in changes:
            dsr, verdict, sig = _dsr_for_change(ch)
            if dsr is not None and sig:
                auto_syms.add(ch["symbol"])
                kept.append((ch, True))
                print(f"  🟢 {ch['name']}({ch['symbol']}): {ch['old']} -> {ch['new']} | DSR={dsr:.3f} {verdict} → 自动应用")
            else:
                kept.append((ch, False))
                tag = f"DSR={dsr:.3f} {verdict}" if dsr is not None else verdict
                print(f"  🟡 {ch['name']}({ch['symbol']}): {ch['old']} -> {ch['new']} | {tag} → 留候审")
        if not auto_syms:
            print("ℹ️ 无 DSR 显著变更，全部留候审（map 未动）")
            return 0
        cur = _load_map()
        applied = []
        remaining = []
        for ch, is_auto in kept:
            if is_auto:
                old_v = cur.get(ch["symbol"])
                if isinstance(old_v, dict):
                    old_v["strategy"] = ch["new"]
                else:
                    cur[ch["symbol"]] = ch["new"]
                applied.append(ch)
            else:
                remaining.append(ch)
        _save_map(cur, source="apply_strategy_pending_auto", n_changes=len(applied))
        pending["changes"] = remaining
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(pending, f, ensure_ascii=False, indent=1)
        print(f"\n✅ 自动应用 {len(applied)} 条，剩余候审 {len(remaining)} 条（等人批准：--status / --apply-symbol）")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
