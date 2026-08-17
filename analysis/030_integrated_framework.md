# 030哲学内化选股策略框架

> **核心目标**：将030复盘体系的"风险第一、仓位矩阵、信号冲突裁决、市场健康度"四大核心机制，内化到现有量化选股体系中，实现从"单纯信号生成"到"风险感知型交易系统"的跃迁。

---

## 一、 设计原则对齐表

| 030核心理念 | 现有系统对应模块 | 整合动作 |
|------------|----------------|----------|
| **致命风险→无条件清仓** | `signal_audit.py` 审计、`portfolio_sim.py` 模拟盘 | 新增 `fatal_risk_detector.py`，强制覆盖所有买入信号 |
| **仓位矩阵(阶段×情绪)** | `adaptive_trader.py` 评分、`portfolio_sim.py` 固定10万/股 | 引入动态仓位计算器，替代固定资金分配 |
| **信号冲突裁决规则** | `adaptive_dual.py` 双策略分歧、`signal_audit.py` 冲突检测 | 统一裁决引擎：致命风险 > 负反馈 > 量能 > 封单 > 超预期 |
| **市场健康度评分(1-10)** | `market_health_score.py` 030健康度 | 健康度直接映射为仓位上限乘数、买入信号降级器 |
| **隔日概率化推演** | `close_scan_v2.py` 信号、`overnight_outlook.py` | 输出三情景+概率+对应操作，替代单一"买入/卖出" |

---

## 二、 核心组件设计

### 2.1 致命风险检测器 (`fatal_risk_detector.py`)

```python
"""
独立模块，每日收盘/盘前运行，输出 FATAL_RISK_JSON
供所有下游系统(adaptive_dual, portfolio_sim, signal_audit)强制引用
"""
class FatalRiskDetector:
    def check(self, market_data: dict) -> dict:
        """
        返回: {
            "triggered": bool,
            "conditions": [{"type": "核心辨识度一字跌停", "seal_amount": 65e8, "threshold": 60e8}, ...],
            "action": "FORCE_CLEAR_ALL" | "HIGH_RISK_DEFENSE" | "NONE",
            "recovery_condition": "次日竞价无新增一字跌停+封单回落30亿+大盘未低开",
            "min_cash_days": 1
        }
        """
```

**触发条件（硬编码，不可配置）：**
- 核心辨识度票一字跌停封单 ≥ 60亿
- 核心大票一字跌停封单 ≥ 100亿  
- 多个近期辨识度标的同时一字跌停

**输出接口：** 文件 `data/fatal_risk_YYYY-MM-DD.json` + stdout JSON

---

### 2.2 动态仓位计算器 (`position_sizer.py`)

```python
"""
替代 portfolio_sim.py 中固定 10万/股 的分配逻辑
输入: 市场阶段(上升/震荡/下跌) + 情绪周期(冰点/修复/高潮) + 健康度分(1-10) + 致命风险状态
输出: {
    "total_cap_limit_pct": 0.0-1.0,      # 总仓位上限
    "single_stock_max_pct": 0.05-0.15,   # 单股上限
    "direction_allocation": {...},        # 方向级分配建议
    "buy_signal_multiplier": 0.0-1.0,     # 买入信号强度乘数
}
"""

POSITION_MATRIX = {
    "上升趋势": {"冰点": 0.5, "修复": 0.75, "高潮": 0.5},
    "震荡筑底": {"冰点": 0.3, "修复": 0.5, "高潮": 0.3},
    "下跌趋势": {"冰点": 0.15, "修复": 0.25, "高潮": 0.05},
    "致命风险": {"冰点": 0.0, "修复": 0.0, "高潮": 0.0},
}

HEALTH_MULTIPLIER = {  # 健康度分 → 仓位乘数
    (8, 10): 1.0, (6, 7): 0.8, (4, 5): 0.5, (1, 3): 0.2
}
```

**集成点：**
- `portfolio_sim.py` → `execute_trade()` 前调用，动态计算可用资金
- `adaptive_trader.py` / `adaptive_dual.py` → 信号输出时附带 `position_advice`
- `signal_audit.py` → 审计时检查实际仓位是否超限

---

### 2.3 统一信号裁决引擎 (`signal_arbitrator.py`)

```python
"""
解决：adaptive_dual 双策略分歧、signal_audit 冲突检测、030 裁决规则 三者不一致
单一权威入口，所有子系统信号汇聚此处裁决
"""
class SignalArbitrator:
    HIERARCHY = [
        "fatal_risk",      # 致命风险（强制清仓/禁买）
        "negative_feedback", # 负反馈（核心中军≤-3%等）
        "volume",          # 量能信号
        "seal_direction",  # 封单方向
        "surprise",        # 超预期
    ]
    
    def arbitrate(self, signals: list, context: dict) -> dict:
        """
        signals: [
            {"source": "adaptive_dual", "symbol": "600584", "action": "买入", "strength": 2, "reason": "MACD金叉"},
            {"source": "signal_audit", "symbol": "600584", "action": "卖出", "strength": 1, "reason": "出货形态"},
            ...
        ]
        context: {"fatal_risk": {...}, "market_health": 6, "market_stage": "上升趋势", "emotion_cycle": "修复"}
        
        返回: {"final_action": "买入/卖出/持有/强制清仓", "confidence": 0-1, "reason": "...", "position_mult": 0.5}
        """
```

**裁决逻辑：**
1. 若 `fatal_risk.triggered` → `final_action="强制清仓"`，`position_mult=0`
2. 若 `negative_feedback` 存在 → `final_action="卖出/不开新仓"`，`position_mult=0.3`
3. 其余按信号强度加权，乘以 `health_multiplier × position_matrix_mult`

---

### 2.4 隔日推演生成器 (`next_day_simulator.py`)

```python
"""
替代当前单一信号输出，生成 030 标准的三情景推演
输入：今日收盘全量数据、健康度、仓位矩阵、核心标的表现
输出：标准化推演报告（Markdown + JSON）
"""
class NextDaySimulator:
    def generate(self, today_data: dict) -> dict:
        return {
            "date": "2026-08-18",
            "market_stage": "上升趋势",
            "emotion_cycle": "修复", 
            "health_score": 7,
            "position_plan": {"total_limit": "70%", "allocation": {...}},
            "directions": [
                {
                    "name": "半导体封测",
                    "core_stocks": ["长电科技", "通富微电"],
                    "scenarios": {
                        "A_normal": {"prob": 60, "desc": "早盘分歧午后确认", "action": "核心标的回调低吸"},
                        "B_surprise": {"prob": 25, "desc": "消息催化直线拉升", "action": "竞价确认强则追首板"},
                        "C_disappoint": {"prob": 15, "desc": "量能萎缩核心跳水", "action": "跌破5日线减半仓"}
                    }
                }
            ],
            "risk_warnings": ["10年期美债收益率4.7%压制估值", "北向连续流出"],
            "stop_loss_map": {"600584": 67.5, "002156": 57.0, ...}
        }
```

---

## 三、 现有脚本改造清单

| 脚本 | 改动要点 | 优先级 |
|------|---------|--------|
| `fatal_risk_detector.py` | **新建** 核心风控模块 | P0 |
| `position_sizer.py` | **新建** 动态仓位计算 | P0 |
| `signal_arbitrator.py` | **新建** 统一裁决引擎 | P0 |
| `next_day_simulator.py` | **新建** 隔日推演输出 | P1 |
| `adaptive_dual.py` | 接入 fatal_risk + position_sizer + signal_arbitrator；输出附带仓位建议 | P0 |
| `portfolio_sim.py` | 替换固定10万→动态仓位；引入致命风险强平；记录每笔交易的仓位上下文 | P0 |
| `signal_audit.py` | 新增审计项：仓位超限、致命风险未执行、裁决引擎一致性 | P1 |
| `close_scan_v2.py` | 新增健康度、仓位矩阵、推演摘要到 stdout JSON | P1 |
| `market_health_score.py` | 输出标准化 `market_stage` `emotion_cycle` 供 position_sizer 使用 | P1 |
| `daily-a-share-telegram-push` cron | 串联：fatal_risk → dual_scan → position_size → arbitrate → portfolio_sim → simulator → push | P0 |

---

## 四、 数据流向重构

```
┌─────────────────┐
│  fatal_risk     │ ◄─── 新浪实时/龙虎榜/涨停池
│  _detector.py   │
└────────┬────────┘
         │ FATAL_RISK_JSON
         ▼
┌─────────────────┐     ┌──────────────────┐
│ market_health   │     │ position_sizer   │
│ _score.py       │────►│ .py              │
└────────┬────────┘     └────────┬─────────┘
         │ HEALTH+STAGE           │ POSITION_PLAN
         ▼                        ▼
┌─────────────────────────────────────────────┐
│          adaptive_dual.py (双策略扫描)        │
│  输出: 每股 {双策略信号, 验证分, 护城河分}     │
└──────────────────┬──────────────────────────┘
                   │ RAW_SIGNALS
                   ▼
┌─────────────────────────────────────────────┐
│        signal_arbitrator.py (统一裁决)        │
│  输入: RAW_SIGNALS + FATAL_RISK + POSITION   │
│  输出: FINAL_SIGNALS {action, conf, pos_mult}│
└──────────────────┬──────────────────────────┘
                   │ FINAL_SIGNALS
         ┌─────────┴─────────┐
         ▼                   ▼
┌──────────────────┐ ┌──────────────────┐
│ portfolio_sim.py │ │ next_day_simul-  │
│ (实盘模拟+成交流水)│ │ ator.py (推演)   │
└────────┬─────────┘ └────────┬─────────┘
         │                    │
         └─────────┬──────────┘
                   ▼
┌─────────────────────────────────────────────┐
│     signal_audit.py (事后审计+一致性校验)     │
└──────────────────┬──────────────────────────┘
                   │ AUDIT_REPORT
                   ▼
┌─────────────────────────────────────────────┐
│  daily-a-share-telegram-push (统一推送)       │
└─────────────────────────────────────────────┘
```

---

## 五、 关键参数表（可调，但有护栏）

| 参数 | 默认值 | 调整范围 | 护栏 |
|------|--------|----------|------|
| 致命风险_核心辨识度封单阈值 | 60亿 | 50-80亿 | 不可低于50亿 |
| 致命风险_核心大票封单阈值 | 100亿 | 80-150亿 | 不可低于80亿 |
| 核心中军竞价跌幅触发线 | -3% | -2% to -5% | 不可宽于-2% |
| 健康度<4 买入禁入 | 禁入 | 仅观察/禁入 | 不可放开 |
| 健康度4-5 买入降仓 | 50% | 30-70% | 不可超70% |
| 单日单方向仓位上限 | 总仓60% | 50-70% | 不可超70% |
| 单股最大仓位 | 15% | 10-20% | 不可超20% |
| 验证评分护栏_最低分 | 25 | 20-30 | 不可低于20 |
| 验证评分护栏_最低夏普 | 0.25 | 0.15-0.4 | 笔数<6时需≥0.4 |

---

## 六、 验收标准

1. **致命风险测试**：模拟核心票一字跌停60亿 → 所有买入信号被强制覆盖为"强制清仓/禁买"，portfolio_sim 触发强平
2. **仓位矩阵测试**：市场阶段=下跌趋势+情绪=高潮+健康度=3 → 总仓位上限≤5%，单股≤1%
3. **裁决一致性测试**：同一只股票 adaptive_dual 给买入，signal_audit 检测出货形态 → 最终裁决为"观望/降级"，且理由包含"出货形态质疑"
4. **隔日推演测试**：输出包含三情景、概率和=100%、每情景对应明确操作、止损位图
5. **审计零误报**：连续5个交易日 signal_audit 高危=0、中危≤2

---

## 七、 实施路线图

| 周期 | 任务 | 产出 |
|------|------|------|
| Day 1-2 | 新建 fatal_risk_detector.py + position_sizer.py + signal_arbitrator.py | 3核心模块 + 单测 |
| Day 3 | 改造 adaptive_dual.py 接入三模块 | 双策略扫描输出含仓位建议 |
| Day 4 | 改造 portfolio_sim.py 动态仓位+强平 | 模拟盘真实反映风控 |
| Day 5 | 新建 next_day_simulator.py + 改造 close_scan_v2.py | 标准化推演输出 |
| Day 6 | 改造 signal_audit.py 新增审计项 | 事后审计闭环 |
| Day 7 | 串联 cron、端到端验证、文档更新 | 生产就绪 |

---

## 八、 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| 新浪实时接口不稳定导致致命风险漏判 | 中 | 高 | 双源校验(新浪+akshare龙虎榜)；漏判时兜底"高风险防御"而非完全放行 |
| 仓位矩阵参数过拟合历史 | 中 | 中 | 保留参数可调但设硬护栏；周度验证自动回滚 |
| 裁决引擎成为单点故障 | 低 | 高 | 保留各子系统原始信号输出，裁决仅作最终建议，不破坏原始数据 |
| 推演输出过于模板化 | 高 | 低 | 强制要求理由字段包含当日具体数据点(价格、量能、健康度变化) |

---

> **核心原则**：030不是叠加在量化系统之上的"皮肤"，而是重写系统的"骨架"。每个交易决策都必须能追溯到：市场阶段、情绪周期、健康度、致命风险状态、仓位矩阵允许额度、信号裁决层级。无溯源，不交易。