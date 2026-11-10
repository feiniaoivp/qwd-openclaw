#!/usr/bin/env python3
"""
L4 盘前人工核验推送（2026-11-10 任务①，用户批准接 Telegram）
============================================================
职责: 每交易日 08:45 把最新 L3 候选名单（前一交易日 15:35 产出）推送 Telegram，
     供人工核验（四层漏斗第 L4 层），含筹码/种子库上下文。

设计:
  - 数据源: data/universe/candidates_daily.json（L3 产物，只读）
  - 交易日判定: analysis.service.is_trading_day（SSOT）
  - 推送: send_telegram.send_with_retry（workspace 根共享层）
  - --dry-run: 只打印不发送（测试用）
  - cron: command+isolated, delivery=none（脚本自推，复核纪律）

产出留痕: data/universe/l4_push_YYYY-MM-DD.json（推送摘要，审计用）
"""
#!/usr/bin/env python3
import os
import sys
import json
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CAND_PATH = os.path.join(WORKSPACE, "data", "universe", "candidates_daily.json")
SEED_PATH = os.path.join(WORKSPACE, "data", "universe", "seed_pool.json")
AUDIT_DIR = os.path.join(WORKSPACE, "data", "universe")


def _fmt_pct(x, digits=1):
    try:
        return f"{x/1e8:.{digits}f}亿" if x is not None else "-"
    except Exception:
        return "-"


def build_message():
    if not os.path.exists(CAND_PATH):
        return None, "candidates_daily.json 不存在（L3 未产出）"
    d = json.load(open(CAND_PATH, encoding="utf-8"))
    cands = d.get("candidates", [])
    regime = d.get("regime", {})
    as_of = d.get("as_of", "?")

    lines = []
    lines.append(f"☰ L4 盘前人工核验 {as_of}")
    lines.append(f"政权: {regime.get('regime','?')} (boost ×{regime.get('boost','?')}) | "
                 f"候选 {len(cands)}/{d.get('n_scored','?')} 只")
    lines.append(f"口径: {d.get('active_dims_note','')}")
    lines.append("── 候选名单（按 L3 综合分） ──")
    for i, c in enumerate(cands, 1):
        dims = c.get("dims", {})
        cd = c.get("chip_display") or {}
        if cd.get("winner_pct") is not None:
            chip_part = f" 筹码(获利{cd['winner_pct']:.0%}/偏离{cd.get('cost_bias') or 0:+.1%})"
        else:
            chip_part = ""
        lines.append(
            f"{i:>2}. {c['symbol']} {c.get('name','')[:8]} "
            f"{c['total_score']:.1f}分 "
            f"[趋{dims.get('trend','-'):.0f} 量{dims.get('volume','-'):.0f}"
            f" 波{dims.get('volatility','-'):.0f}]"
            f"{chip_part} "
            f"价{c.get('close','-')} 额{_fmt_pct(c.get('amount_20d'))}"
        )
    # 种子库动态（若有）
    try:
        seed = json.load(open(SEED_PATH, encoding="utf-8"))
        watch = seed.get("watch", [])
        observe = seed.get("observe", [])
        lines.append("── 种子库 ──")
        lines.append(f"watch {len(watch)} / observe {len(observe)} / candidate {seed.get('candidate_n','-')}")
    except Exception:
        pass
    lines.append("── 核验要点 ──")
    lines.append("· 排查近期利空公告/减持/问询函（新闻侧人工判断）")
    lines.append("· 确认流动性可承接计划仓位（20 日均成交额列）")
    lines.append("· 剔除个人不碰的行业/事件票后回传精选，进入实盘观察")
    return "\n".join(lines), None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印不发送")
    ap.add_argument("--force", action="store_true", help="跳过交易日判定（测试）")
    args = ap.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    if not args.force:
        from analysis.service import is_trading_day
        if not is_trading_day(today):
            print(f"📅 {today} 非交易日，L4 推送跳过")
            return

    msg, err = build_message()
    if err:
        print(f"❌ L4 推送中止: {err}")
        sys.exit(1)

    if args.dry_run:
        print("[DRY-RUN] 以下为将推送内容:")
        print(msg)
        return

    from send_telegram import send_with_retry
    r = send_with_retry(msg)
    ok = bool(r.get("ok"))
    # 审计留痕
    audit = {"date": today, "sent_at": datetime.now().isoformat(),
             "ok": ok, "candidates": len(json.load(open(CAND_PATH, encoding="utf-8")).get("candidates", []))}
    with open(os.path.join(AUDIT_DIR, f"l4_push_{today}.json"), "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)
    print(f"{'✅' if ok else '❌'} L4 盘前核验推送: ok={ok} → l4_push_{today}.json")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
