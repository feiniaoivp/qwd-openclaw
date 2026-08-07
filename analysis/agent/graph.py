#!/usr/bin/env python3
"""
LangGraph 图的编译与入口。
"""

from langgraph.graph import StateGraph, END
from analysis.agent.state import AgentState
from analysis.agent.nodes import (
    node_init,
    node_check_trading_day,
    node_fetch_spot,
    node_calc_signals,
    node_zhonglian,
    node_fetch_external,
    node_fetch_trader_picks,
    node_apply_risk,
    node_make_report,
    node_check_done,
)

def build_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    # � 添加节点
    workflow.add_node("init", node_init)
    workflow.add_node("check_trade", node_check_trading_day)
    workflow.add_node("fetch_spot", node_fetch_spot)
    workflow.add_node("calc_signals", node_calc_signals)
    workflow.add_node("zhonglian", node_zhonglian)
    workflow.add_node("fetch_external", node_fetch_external)
    workflow.add_node("fetch_trader_picks", node_fetch_trader_picks)
    workflow.add_node("apply_risk", node_apply_risk)
    workflow.add_node("make_report", node_make_report)
    workflow.add_node("check_done", node_check_done)

    # 设置入口
    workflow.set_entry_point("init")

    # 线性流程
    workflow.add_edge("init", "check_trade")
    workflow.add_edge("check_trade", "fetch_spot")
    workflow.add_edge("fetch_spot", "calc_signals")
    workflow.add_edge("calc_signals", "zhonglian")
    workflow.add_edge("zhonglian", "fetch_external")
    workflow.add_edge("fetch_external", "fetch_trader_picks")
    workflow.add_edge("fetch_trader_picks", "apply_risk")
    workflow.add_edge("apply_risk", "make_report")
    workflow.add_edge("apply_risk", "make_report")
    workflow.add_edge("make_report", "check_done")

    # 条件边（2026-08-07 修复：单遍线性执行，不做整图重试循环）
    # 原逻辑 error_count<3 会整图重跑（每次重跑都重新执行 30 只全扫描+操盘手选股~30s），
    # 导致多轮循环直至超时（实测跑 8+ 轮被 kill）。数据层偶发失败已由内部重试兜底。
    def should_continue(state: AgentState) -> str:
        return END

    workflow.add_conditional_edges(
        "check_done",
        should_continue,
        {
            "fetch_spot": "fetch_spot",
            END: END,
        },
    )

    return workflow.compile()

# � 若想直接以脚本形式运行
if __name__ == "__main__":
    graph = build_graph()
    init_state: AgentState = {}   # 空字典，节点内部会补全必�填字段
    final_state = graph.invoke(init_state)
    # 只输出报告文本，实际推送由外�层脚本决定
    print(final_state.get("report_text", "(无报告)"))