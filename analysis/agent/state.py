from typing import TypedDict, List, Optional, Dict, Any, Tuple
import pandas as pd

class AgentState(TypedDict, total=False):
    # 基础信息
    date: str                       # 今天日期（YYYY-MM-DD）
    is_trading_day: bool
    data_date: str                  # 实际行情日期（可能是最近交易日）

    # 原始行情
    spot_data: List[Dict[str, Any]] # 来自 get_spot_scan 的原始列表
    market_overview: Dict[str, Any] # up/down/total_amount 等

    # 计算得到的技术面信号（每只股一个 dict）
    signals: List[Dict[str, Any]]   # 每项包含 symbol、name、signal.level 等

    # 中联重科专用
    zhonglian: Optional[Dict[str, Any]]
    zhonglian_actions: List[Tuple[str, str]]   # (strategy, BUY/SELL)
    zhonglian_messages: List[str]

    # 外部知识
    news_summary: str               # LLM 生成的今日热点摘要
    health_score: int               # 0‑10 的 030 市场健康度
    health_comment: str             # LLM 对健康度的解读

    # 风险与决策
    risk_flag: Optional[str]        # 若触发致命风险等，这里存放关键词
    final_advice: str               # LLM 综合后的操作建议（买入/卖出/观望等）

    # 控制字段
    run_id: str                     # 本次运行的唯一标识（时间戳+随机）
    timestamp: str                  # ISO 格式时间
    error_count: int                # 累计节点执行失败次数
    done: bool                      # 是否满足完成定义（DoD）