#!/usr/bin/env python3
"""
模拟盘周报（两账户聚合，单一事实来源）
========================================
聚合两个账户型模拟盘的周度账本：
  ① 全组合模拟盘 (portfolio_sim, 参考本金 270 万)
     - 期初/期末净值 (data/portfolio_equity.csv)
     - 本周成交 (data/portfolio_sim_trades.json)
     - 信号→执行漏斗 (analysis/daily/{date}_portfolio_sim.md 解析)
  ② 中联重科双策略盘 (zhonglian_monitor, 10 万 × 2)
     - 持仓/账户现值 (data/zhonglian_state.json)
     - 周内价格 (analysis/daily/{date}_dual.md 解析)
     - 本周成交 (data/zhonglian_trades.json, 2026-10-31 起有台账)

用法:
  python3 analysis/portfolio_weekly.py                    # 打印摘要
  python3 analysis/portfolio_weekly.py --week-end 2026-10-30
  python3 analysis/portfolio_weekly.py --out              # 写 md 到 analysis/daily/
  python3 analysis/portfolio_weekly.py --push             # md + Telegram 推送

设计约定:
  - weekly_review_push.py (周六复盘) 复用 build_report()，勿在本脚本外重复实现账本逻辑
  - 不硬编码任何 6 位股票代码（防写入工具脱敏层啃字符），股票识别全靠解析
"""
import os
import sys
import json
import re
import csv
import argparse
from datetime import datetime, timedelta

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA_DIR = os.path.join(WORKSPACE, "data")
DAILY_DIR = os.path.join(WORKSPACE, "analysis", "daily")
EQUITY_FILE = os.path.join(DATA_DIR, "portfolio_equity.csv")
MAIN_TRADES_FILE = os.path.join(DATA_DIR, "portfolio_sim_trades.json")
ZH_STATE_FILE = os.path.join(DATA_DIR, "zhonglian_state.json")
ZH_TRADES_FILE = os.path.join(DATA_DIR, "zhonglian_trades.json")

ZH_INITIAL_TOTAL = 200_000   # 中联盘两策略各 10 万
ZH_NAME_KEY = "中联重科"


# ---------------- 周区间 ----------------

def resolve_week_end(today=None):
    """默认报告期终点：周五=当天; 周六=昨天; 周日=前天; 工作日中间天=当天(部分周)"""
    d = today or datetime.now().date()
    wd = d.weekday()  # Mon=0 .. Sun=6
    if wd == 6:
        d = d - timedelta(days=2)
    elif wd == 5:
        d = d - timedelta(days=1)
    return d


def week_range(week_end):
    """返回 (monday, week_end) 的 date 元组"""
    monday = week_end - timedelta(days=week_end.weekday())
    return monday, week_end


def _dstr(d):
    return d.strftime("%Y-%m-%d")


# ---------------- 主模拟盘 ----------------

def _load_equity():
    rows = []
    with open(EQUITY_FILE) as f:
        for r in csv.DictReader(f):
            rows.append({
                "date": r["date"],
                "total_value": float(r["total_value"]),
                "total_return_pct": float(r["total_return_pct"]),
                "positions_held": int(float(r.get("positions_held", 0) or 0)),
                "cash_total": float(r.get("cash_total", 0) or 0),
            })
    rows.sort(key=lambda x: x["date"])
    return rows


def _load_main_trades():
    with open(MAIN_TRADES_FILE) as f:
        return json.load(f)


def _parse_daily_sim_md(path):
    """解析单日 portfolio_sim.md：信号漏斗 + 成交 + 持仓"""
    out = {
        "buy_n": 0, "sell_n": 0, "exec_n": 0,
        "buy_signals": [], "sell_signals": [],
        "executed": [],          # (name, code, action, reason)
        "holdings": set(),       # codes
    }
    if not os.path.exists(path):
        return None
    with open(path) as f:
        lines = f.read().splitlines()

    m = re.compile(r"🟢买入\s*(\d+)\s*\|\s*🔴卖出\s*(\d+)\s*\|\s*实际成交\s*(\d+)\s*笔")
    sig_line = re.compile(r"[•]\s*(.+?)\((\d{6})\)\s*(.*)")
    exec_line = re.compile(r"\*\*(.+?)\((\d{4}-\d{2}-\d{2})\)\s*(买入|卖出)\*\*")
    hold_line = re.compile(r"\|\s*(.+?)\((\d{6})\)\s*\|")

    section = None  # 'buy' | 'sell' | 'exec' | 'exec_reason' | 'hold'
    pending_exec = None
    name2code = {}
    for ln in lines:
        fm = m.search(ln)
        if fm:
            out["buy_n"], out["sell_n"], out["exec_n"] = int(fm.group(1)), int(fm.group(2)), int(fm.group(3))
            continue
        if "🟢 买入信号" in ln:
            section = "buy"; continue
        if "🔴 卖出信号" in ln:
            section = "sell"; continue
        if "今日成交" in ln:
            section = "exec"; continue
        if "持仓明细" in ln:
            section = "hold"; continue
        if ln.startswith("## ") or (ln.startswith("🎯") and section not in ("exec", "exec_reason")):
            if section not in ("exec", "exec_reason"):
                section = None
            continue

        sm = sig_line.search(ln)
        em = exec_line.search(ln)
        hm = hold_line.search(ln)
        if section == "buy" and sm:
            out["buy_signals"].append({"name": sm.group(1), "code": sm.group(2), "text": sm.group(3)})
            name2code.setdefault(sm.group(1), sm.group(2))
        elif section == "sell" and sm:
            out["sell_signals"].append({"name": sm.group(1), "code": sm.group(2), "text": sm.group(3)})
            name2code.setdefault(sm.group(1), sm.group(2))
        elif section == "exec" and em:
            out["executed"].append({"name": em.group(1), "code": "", "action": em.group(3), "reason": ""})
            pending_exec = out["executed"][-1]
            section = "exec_reason"
            continue
        elif section == "exec_reason" and pending_exec is not None:
            rm = re.search(r"理由[:：]\s*(.*)", ln)
            if rm:
                pending_exec["reason"] = rm.group(1).strip()
                pending_exec = None
                section = "exec"
            elif ln.strip() == "":
                pending_exec = None
                section = "exec"
        elif section == "hold" and hm:
            out["holdings"].add(hm.group(2))
            name2code.setdefault(hm.group(1), hm.group(2))

    # 两阶段：成交条目代码统一回填（成交段在信号段之前，解析时无法就地回查）
    for e in out["executed"]:
        if not e["code"]:
            e["code"] = name2code.get(e["name"], "")

    # 兜底：漏斗行缺失时用明细推
    if out["buy_n"] == 0 and out["sell_n"] == 0 and (out["buy_signals"] or out["sell_signals"]):
        out["buy_n"] = len(out["buy_signals"])
        out["sell_n"] = len(out["sell_signals"])
    return out


def _non_exec_reason(sig, day):
    """未执行信号归因（启发式，详见当日日报）"""
    if sig["action"] == "SELL":
        if sig["code"] and sig["code"] not in day["holdings"]:
            return "空仓（无持仓可卖，信号对着 0 股发）"
        return "持仓中未执行（详见当日日报）"
    # BUY
    txt = sig.get("text", "")
    if "冷却" in txt:
        return "止损冷却期拦截"
    if "涨停" in txt:
        return "涨停/买不进约束"
    return "风控/条件未满足（详见当日日报）"


def build_main_section(monday, week_end):
    rows = _load_equity()
    in_week = [r for r in rows if monday.strftime("%Y-%m-%d") <= r["date"] <= _dstr(week_end)]
    before = [r for r in rows if r["date"] < monday.strftime("%Y-%m-%d")]
    start_row = before[-1] if before else (in_week[0] if in_week else None)
    end_row = in_week[-1] if in_week else None

    trades = [t for t in _load_main_trades()
              if monday.strftime("%Y-%m-%d") <= t.get("date", "") <= _dstr(week_end)]
    realized = round(sum(t.get("pnl", 0) for t in trades if t.get("action") == "SELL"), 2)

    # 逐日漏斗
    funnel = []
    for r in in_week:
        day = _parse_daily_sim_md(os.path.join(DAILY_DIR, f"{r['date']}_portfolio_sim.md"))
        if day:
            funnel.append({"date": r["date"], **day})

    # 未执行信号清单
    non_exec = []
    for day in funnel:
        executed_codes = {(e["name"], e.get("code", "")) for e in day["executed"]}
        for s in day["buy_signals"]:
            if (s["name"], s["code"]) not in executed_codes:
                non_exec.append({"date": day["date"], "action": "BUY", **s,
                                 "reason": _non_exec_reason({"action": "BUY", "text": s["text"]}, day)})
        for s in day["sell_signals"]:
            if (s["name"], s["code"]) not in executed_codes:
                non_exec.append({"date": day["date"], "action": "SELL", **s,
                                 "reason": _non_exec_reason({"action": "SELL", "code": s["code"]}, day)})

    return {
        "start": start_row, "end": end_row,
        "days": in_week,
        "trades": trades,
        "realized_pnl": realized,
        "funnel": funnel,
        "non_exec": non_exec,
    }


# ---------------- 中联重科盘 ----------------

def _load_zh_state():
    with open(ZH_STATE_FILE) as f:
        return json.load(f)


def _load_zh_trades():
    if not os.path.exists(ZH_TRADES_FILE):
        return []
    with open(ZH_TRADES_FILE) as f:
        return json.load(f)


def _zh_prices(monday, week_end):
    """从 dual.md 提取周内每日中联重科收盘价与涨跌幅

    两轮扫描：先找「现价¥X (+Y%)」详述行；无则回退表格行（| ... | ¥X | Y% |）。
    注意：首个含股名的行可能是表格行（无现价字样），不能见股名就 break。
    """
    prices = []
    pat_detail = re.compile(r"现价¥([\d.]+)\s*\(([-+]?[\d.]+)%\)")
    pat_table = re.compile(r"\|\s*¥([\d.]+)\s*\|\s*([-+]?[\d.]+)%")
    d = monday
    while d <= week_end:
        path = os.path.join(DAILY_DIR, _dstr(d) + "_dual.md")
        if os.path.exists(path):
            with open(path) as f:
                content = f.read()
            got = None
            for ln in content.splitlines():
                if ZH_NAME_KEY not in ln:
                    continue
                pm = pat_detail.search(ln)
                if pm:
                    got = {"date": _dstr(d), "close": float(pm.group(1)), "chg_pct": float(pm.group(2))}
                    break
            if got is None:
                for ln in content.splitlines():
                    if ZH_NAME_KEY not in ln:
                        continue
                    pm = pat_table.search(ln)
                    if pm:
                        got = {"date": _dstr(d), "close": float(pm.group(1)), "chg_pct": float(pm.group(2))}
                        break
            if got:
                prices.append(got)
        d += timedelta(days=1)
    return prices


def build_zh_section(monday, week_end):
    state = _load_zh_state()
    trades = [t for t in _load_zh_trades()
              if monday.strftime("%Y-%m-%d") <= str(t.get("data_date", "")) <= _dstr(week_end)]
    prices = _zh_prices(monday, week_end)

    first, last = (prices[0] if prices else None), (prices[-1] if prices else None)
    prev_week_close = None
    if first:
        prev_week_close = round(first["close"] / (1 + first["chg_pct"] / 100), 4)

    def acct_value(s, price):
        v = s.get("capital", 0) or 0
        if s.get("position") and s.get("shares"):
            v += s["shares"] * (price if price else 0)
        return v

    has_week_trades = len(trades) > 0
    s1, s2 = state.get("strategy1", {}), state.get("strategy2", {})
    val_end = val_start = None
    if last:
        val_end = acct_value(s1, last["close"]) + acct_value(s2, last["close"])
        if prev_week_close and not has_week_trades:
            val_start = acct_value(s1, prev_week_close) + acct_value(s2, prev_week_close)

    return {
        "state": state,
        "trades": trades,
        "prices": prices,
        "prev_week_close": prev_week_close,
        "last_close": last["close"] if last else None,
        "val_start": val_start,
        "val_end": val_end,
        "has_week_trades": has_week_trades,
    }


# ---------------- 报告组装 ----------------

def build_report(week_end=None):
    we = week_end or resolve_week_end()
    if isinstance(we, str):
        we = datetime.strptime(we, "%Y-%m-%d").date()
    monday, week_end = week_range(we)
    main = build_main_section(monday, week_end)
    zh = build_zh_section(monday, week_end)

    sig_total = sum(d["buy_n"] + d["sell_n"] for d in main["funnel"])
    exec_total = sum(d["exec_n"] for d in main["funnel"])

    return {
        "monday": _dstr(monday), "week_end": _dstr(week_end),
        "main": main, "zh": zh,
        "funnel_total": {"signals": sig_total, "executed": exec_total,
                         "non_exec": sig_total - exec_total},
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def format_md(rep):
    L = []
    m, zh = rep["main"], rep["zh"]
    L.append(f"# 模拟盘周报 {rep['monday']} ~ {rep['week_end']}")
    L.append(f"生成: {rep['generated_at']} | 数据源: portfolio_equity.csv / portfolio_sim_trades.json / zhonglian_state.json / zhonglian_trades.json / 逐日 *_portfolio_sim.md + *_dual.md")
    L.append("")

    L.append("## ① 全组合模拟盘（参考本金 270 万）")
    if m["start"] and m["end"]:
        delta = round(m["end"]["total_value"] - m["start"]["total_value"], 2)
        pct = (m["end"]["total_value"] / m["start"]["total_value"] - 1) * 100 if m["start"]["total_value"] else 0
        L.append(f"- 净值: {m['start']['date']} ¥{m['start']['total_value']:,.2f} → {m['end']['date']} ¥{m['end']['total_value']:,.2f} | 周变动 **{delta:+,.2f} ({pct:+.3f}%)**")
    else:
        L.append("- 净值: 本周无权益快照")
    L.append(f"- 本周成交 **{len(m['trades'])} 笔**（买 {sum(1 for t in m['trades'] if t['action']=='BUY')} / 卖 {sum(1 for t in m['trades'] if t['action']=='SELL')}）| 已实现盈亏 **{m['realized_pnl']:+,.2f}**")
    for t in m["trades"]:
        note = f" | {t['note']}" if t.get("note") else ""
        L.append(f"  - {t['date']} {t['action']} {t['name']}({t['symbol']}) ¥{t['price']} × {t.get('shares', 0)}股 盈亏 {t.get('pnl', 0):+,.2f} ({t.get('pnl_pct', 0):+.2f}%){note}")
    if not m["trades"]:
        L.append("  - （无成交）")
    L.append("")

    L.append("## ② 信号→执行漏斗")
    L.append(f"- 本周合计: **{rep['funnel_total']['signals']} 条信号 → {rep['funnel_total']['executed']} 笔成交**（未执行 {rep['funnel_total']['non_exec']}）")
    for d in m["funnel"]:
        L.append(f"  - {d['date']}: 买信号 {d['buy_n']} | 卖信号 {d['sell_n']} | 实际成交 {d['exec_n']}")
    if m["non_exec"]:
        L.append("- 未执行明细:")
        for s in m["non_exec"]:
            L.append(f"  - {s['date']} {s['action']} {s['name']}({s['code']}): {s['reason']}")
    L.append("")

    L.append("## ③ 中联重科双策略盘（10 万 × 2）")
    s1 = zh["state"].get("strategy1", {})
    s2 = zh["state"].get("strategy2", {})
    if zh["last_close"]:
        L.append(f"- 周内价格: {zh['prices'][0]['date']} ¥{zh['prices'][0]['close']} → {zh['prices'][-1]['date']} ¥{zh['last_close']}（上周五收 ≈¥{zh['prev_week_close']}）")
    else:
        L.append("- 周内价格: dual.md 无可用行情")
    if s1.get("position"):
        cur = zh["last_close"]
        unreal = (cur / s1["entry_price"] - 1) * 100 if (cur and s1.get("entry_price")) else None
        u_txt = f"，按记入价浮亏 {unreal:+.2f}%" if unreal is not None else ""
        L.append(f"- 策略1 [{s1.get('name')}]: 持有 {s1.get('shares', 0):,} 股 @¥{s1.get('entry_price')}（{s1.get('entry_date')} 建）现价 ¥{cur}{u_txt}")
    else:
        L.append(f"- 策略1 [{s1.get('name')}]: 空仓，现金 ¥{s1.get('capital', 0):,.2f}")
    L.append(f"- 策略2 [{s2.get('name')}]: {'持有' if s2.get('position') else '空仓'}，现金 ¥{s2.get('capital', 0):,.2f}")
    if zh["val_end"] is not None:
        v_txt = f" → ¥{zh['val_end']:,.2f}"
        if zh["val_start"] is not None:
            dv = zh["val_end"] - zh["val_start"]
            dp = dv / zh["val_start"] * 100 if zh["val_start"] else 0
            v_txt += f"（周变动 {dv:+,.2f} / {dp:+.2f}%）"
        L.append(f"- 账户合计: 期初 ≈¥{zh['val_start']:,.2f}{v_txt} 对本金 {ZH_INITIAL_TOTAL:,.0f}（{(zh['val_end']/ZH_INITIAL_TOTAL-1)*100:+.2f}%）" if zh["val_start"] is not None else f"- 账户现值: ¥{zh['val_end']:,.2f}")
    L.append(f"- 本周成交: {len(zh['trades'])} 笔")
    for t in zh["trades"]:
        L.append(f"  - {t.get('data_date')} {t.get('action')} {t.get('strategy_name')} ¥{t.get('fill_price')} × {t.get('shares') or t.get('shares_closed', 0)}股 盈亏 {t.get('pnl', 0):+,.2f}")
    L.append("")

    L.append("## ④ 口径注释")
    L.append("- 中联盘成本口径: state 记入价与台账建立前资金占用存在差异（台账 2026-10-31 起补齐，此前成本以 state 记入价为准，历史已实现盈亏不可回溯）")
    L.append("- 主盘净值基期含 2026-10-20 测试污染损耗与更早现金修复残差，不做回溯修正（污染买入已由冲销分录对冲）")
    L.append("- 信号漏斗为启发式归因（空仓/冷却/其他），精确风控路径见各日日报")
    L.append("")
    return "\n".join(L)


def format_telegram(rep):
    m, zh, ft = rep["main"], rep["zh"], rep["funnel_total"]
    L = []
    L.append(f"📊 模拟盘周报 {rep['monday']}~{rep['week_end']}")
    L.append("")
    if m["start"] and m["end"]:
        delta = round(m["end"]["total_value"] - m["start"]["total_value"], 2)
        pct = (m["end"]["total_value"] / m["start"]["total_value"] - 1) * 100
        L.append(f"① 全组合模拟盘(270万): ¥{m['start']['total_value']:,.0f} → ¥{m['end']['total_value']:,.0f}（周 {delta:+,.2f} / {pct:+.3f}%）")
    L.append(f"成交 {len(m['trades'])} 笔，已实现 {m['realized_pnl']:+,.2f}")
    for t in m["trades"]:
        L.append(f"  {t['date']} {t['action']} {t['name']} ¥{t['price']}×{t.get('shares', 0)} 盈亏 {t.get('pnl', 0):+,.2f} ({t.get('pnl_pct', 0):+.2f}%)")
    L.append(f"信号漏斗: {ft['signals']} 条 → 成交 {ft['executed']}，未执行 {ft['non_exec']}"
             + (f"（其中对空仓发 {sum(1 for s in m['non_exec'] if '空仓' in s['reason'])} 条）" if m["non_exec"] else ""))
    L.append("")
    s1 = zh["state"].get("strategy1", {})
    s2 = zh["state"].get("strategy2", {})
    zh_txt = f"现值 ≈¥{zh['val_end']:,.0f}" if zh["val_end"] is not None else "现值未知"
    if zh["val_start"] is not None:
        dp = (zh["val_end"] / zh["val_start"] - 1) * 100
        zh_txt += f"（周 {dp:+.2f}%）"
    L.append(f"② 中联双策略盘(10万×2): {zh_txt} 对本金 -{(1 - zh['val_end']/ZH_INITIAL_TOTAL)*100:.2f}%" if zh["val_end"] is not None else "② 中联双策略盘: 数据缺失")
    if s1.get("position"):
        cur = zh["last_close"]
        unreal = (cur / s1["entry_price"] - 1) * 100 if (cur and s1.get("entry_price")) else 0
        L.append(f"  策略1 持 {s1.get('shares', 0):,} 股 @¥{s1.get('entry_price')}（{s1.get('entry_date')} 建）现价 ¥{cur} 浮动 {unreal:+.2f}%")
    L.append(f"  本周成交 {len(zh['trades'])} 笔")
    L.append("")
    L.append(f"完整版: analysis/daily/{rep['week_end']}_portfolio_weekly.md")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week-end", default=None, help="报告期终点 YYYY-MM-DD（默认按今天推算）")
    ap.add_argument("--out", action="store_true", help="写 md 到 analysis/daily/")
    ap.add_argument("--push", action="store_true", help="推送 Telegram（需先 --out 或单独保存）")
    args = ap.parse_args()

    rep = build_report(args.week_end)
    md = format_md(rep)
    tg = format_telegram(rep)

    out_path = None
    if args.out or args.push:
        out_path = os.path.join(DAILY_DIR, f"{rep['week_end']}_portfolio_weekly.md")
        if os.path.exists(out_path):
            import shutil
            shutil.copy(out_path, out_path + ".bak")
        with open(out_path, "w") as f:
            f.write(md + "\n")
        with open(out_path) as f:
            if len(f.read()) < len(md) - 10:
                print("⚠️ 周报写后校验异常（内容缩短），请人工检查", file=sys.stderr)
        print(f"📄 周报已保存: {out_path}")

    if args.push:
        from send_telegram import send_with_retry
        resp = send_with_retry(tg, parse_mode="")
        ok = bool(isinstance(resp, dict) and resp.get("ok"))
        print(f"Telegram 推送: {'✅ ok' if ok else '❌ ' + str(resp)}")
        if not ok:
            sys.exit(1)

    if not (args.out or args.push):
        print(tg)


if __name__ == "__main__":
    main()
