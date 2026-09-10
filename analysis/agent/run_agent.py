#!/usr/bin/env python3
"""
Agent 入口脚本：编译 LangGraph 工作流并执行，
输出报告文本（供 cron 捕获并通过 Telegram 推送）。
"""

import os, sys, json, re, traceback, socket
from datetime import datetime

# 防御性兜底：本环境对 quotes.sina.cn 等外部接口存在间歇性 TLS 握手挂起
# （实测 SSL do_handshake 在 poll 上无限阻塞、绕过 per-call timeout，导致 cron SIGKILL）。
# 对所有新建 socket（含 https/SSL 握手）设置全局默认超时，确保任何一步都绝不会无限阻塞。
socket.setdefaulttimeout(15)

# 确保可以从工作区根导入模块
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from analysis.agent.graph import build_graph
from analysis.agent.state import AgentState


def build_report_text(state) -> str:
    """从最终 state 直接拼装报告文本（不依赖 graph 内 node_make_report 的字段传播，
    避免 LangGraph 在条件边/状态合并时丢字段导致 report_text 缺失）。"""
    L = []
    L.append(f"=== AI 智能体每日盯盘报告 ({state.get('date')}) ===")
    L.append(f"交易日状态: {'是' if state.get('is_trading_day') else '否（使用最近交易日数据）'}")
    L.append(f"数据日期: {state.get('data_date')}")

    ov = state.get("market_overview") or {}
    if isinstance(ov, dict) and ov:
        L.append(f"市场概览: 上涨{ov.get('up')}只 / 下跌{ov.get('down')}只 / 成交额{ov.get('total_amount_billion')}亿")
    else:
        L.append("市场概览: (无数据)")

    ss = state.get("signal_summary") or {}
    # 优先从 signals 实际统计（signal_summary 的 key 可能带 \ufffd 损坏无法用.匹配）
    signals = state.get("signals") or []
    strong_buy = watch = neutral = caution = strong_sell = fail = 0
    for s in signals:
        if "error" in s:
            fail += 1
            continue
        lv = str(s.get("signal", {}).get("level", ""))
        n = re.sub(r"[\ufffd\ufeff\s]", "", lv)
        if "强烈买入" in n:
            strong_buy += 1
        elif "关注" in n:
            watch += 1
        elif "强烈卖出" in n:
            strong_sell += 1
        elif "谨慎" in n:
            caution += 1
        else:
            neutral += 1
    L.append(f"信号分布(来自signals): 强烈买入{strong_buy} / 关注{watch} / 中性{neutral}"
             f" / 谨慎{caution} / 强烈卖出{strong_sell} / 失败{fail}")

    hs = state.get("health_score")
    L.append(f"030 健康度: {hs if hs is not None else 'N/A'}/10")
    L.append(f"健康度解读: {state.get('health_comment') or '(无)'}")
    L.append("今日热点新闻:")
    L.append(str(state.get("news_summary") or "(无)"))
    L.append(f"风险标记: {state.get('risk_flag') or '无'}")
    adv = state.get("final_advice")
    L.append("操作建议(JSON):")
    L.append(str(adv) if adv else "[]")
    L.append("中联重科双策略:")
    L.append(json.dumps(state.get("zhonglian") or {}, ensure_ascii=False, indent=2))
    tp = state.get("trader_picks")
    L.append("操盘手选股:")
    L.append(json.dumps(tp or [], ensure_ascii=False, indent=2))

    # 斐波那契波段止盈参考（独立小节，复用已锁定基线，纯参考不画靶）
    try:
        from analysis.fib_track import build_take_profit_section
        prices = {}
        for s in (signals or []):
            if isinstance(s, dict) and s.get("symbol") and s.get("price"):
                prices[s["symbol"]] = s["price"]
        fib_note = build_take_profit_section(prices)
        L.append("")
        L.append(fib_note)
    except Exception as e:
        L.append("")
        L.append("📐 斐波那契波段止盈参考: (生成失败: %s)" % e)
    return "\n".join(L)


def main():
    final_state = {}
    try:
        graph = build_graph()
        init_state: AgentState = {}
        final_state = graph.invoke(init_state)

        report = final_state.get("report_text")
        if not report:
            # 兜底：直接用 final_state 里的字段拼报告
            report = build_report_text(final_state)

        # 写入运行日志（便于审计）
        out_dir = os.path.join(WORKSPACE, "data", "agent_runs")
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(out_dir, f"run_{ts}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(final_state, f, ensure_ascii=False, indent=2)

        # 打印到标准输出（cron 会捕获并通过 Telegram 推送）
        print(report)

        error_count = final_state.get("error_count", 0)
        if error_count >= 3:
            print("\n[ALERT] Agent 错误次数 >= 3，请检查日志！")
    except Exception as e:
        print(f"[FATAL] Agent 运行异常: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
