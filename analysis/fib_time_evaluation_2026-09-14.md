# 斐波那契时间线（Fib Time Zones）评估报告

> 任务编号：⑬  
> 生成日期：2026-09-14  
> 评估目标：在现有 `fib_track.py` / `fib_extension_scan.py` 基础上，增加「斐波那契时间线」变盘时点预警模块的可行性与实现方案  
> 定位：**仅作时点预警辅助，不可作方向信号**（需配合价格维度"时价合一"）  
> 来源理念：Obsidian《使用斐波那契时间预测法》（09-11）—— 研究「**何时**」变盘，非「到哪」

---

## 一、现状盘点：现有价格维度斐波实现

### 1.1 `fib_extension_scan.py`（扫描层）

| 函数 | 职责 | 关键产出 |
|:---|:---|:---|
| `fetch_sina_kline(symbol, datalen=150)` | 新浪日K获取 | `{date,open,high,low,close,volume}` DataFrame |
| `find_latest_uptrend_swing(df, lookback=150)` | 识别最近上升波段 | `swing_high / swing_high_i / swing_low / swing_low_i / pullback_low / pullback_low_i / run_pct` |
| `compute_fib_levels(sw)` | 价格扩展/回撤位 | `{1.0, 1.272, 1.618, 2.618, 0.618, 0.786: price}` |
| `locate_price(price, levels)` | 现价区间定位 | `(zone, hint)` 字符串 |
| `scan(symbol)` | 单股扫描 | dict（含 levels/zone/hint） |
| `main()` | 全池并发扫描 | stdout JSON / `--save` 落盘 `data/fib_predictions_YYYY-MM-DD.json` |

**关键结构点**：`find_latest_uptrend_swing` 已返回 **`swing_high_i` / `swing_low_i` / `pullback_low_i`（DataFrame 内部行索引）**——这正是时间线计算的现成输入（索引差 = 交易日间隔），**无需新增高低点识别逻辑**。

### 1.2 `fib_track.py`（跟踪/验证层）

- `lock_baseline()`：无基线时锁定当日止盈位（**预先声明，避免射箭画靶**）
- `verify()`：每日对照验证触及/兑现/突破，计算命中率
- `build_take_profit_section()`：接入 `run_agent.py` 的收盘报告小节
- 状态文件：`data/fib_tracking_state.json`

**核心原则（必须延续）**：预测目标一旦锁定不回改；命中率低则弃用工具。

### 1.3 现有消费方

| 消费方 | 用法 |
|:---|:---|
| `portfolio_core.py::compute_fib_targets(df, lookback=150)` | 6 条信号腿各自挂 `fib_tp.targets`（1.0/1.272/1.618/2.618） |
| `adaptive_dual.py::compute_fib_targets` | 同上（双策略腿） |
| `portfolio_executor.py` | 导入 `compute_fib_targets` 写持仓 `fib_targets` |
| `analysis/agent/run_agent.py` | 调 `build_take_profit_section(prices)` 生成报告小节 |

> ⚠️ 注意：`compute_fib_targets` 在 `portfolio_core.py` 与 `adaptive_dual.py` 中**各有一份重复实现**（此前 `strategies.py` 共享模块只合并了策略腿，未合并 fib）。时间线模块落地时应**同时**收敛此重复。

---

## 二、斐波那契时间线原理（本模块的设计依据）

### 2.1 数列与时间间隔

斐波那契数列：`1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144 ...`  
**用法不是价格比例，而是"交易日间隔"**——从某个关键高低点起，往后数第 N 个交易日，是潜在的变盘/加速时点。

### 2.2 三种用法（本模块全部实现）

| 用法 | 定义 | 实现方式 |
|:---|:---|:---|
| **① 时间区间（Time Zones）** | 从**高/低点**画垂直时间线，预示转折/加速 | 从每个显著 swing 点索引 `i` 出发，标注 `i + fib_k` 的日期 |
| **② 时间扩展（Time Extension）** | A→B 的时长，从 C 往后推未来时点 | `C_idx + (B_idx - A_idx) × fib_ratio`，ratio ∈ {1.0,1.272,1.618,2.618} |
| **③ 周期汇聚（Confluence）** | 多条时间线**重合** → 变盘概率极高 | 统计所有候选时点的**日期重合度**，重合数 ≥ 2 标记为汇聚窗口 |

### 2.3 硬约束（不可违反）

- ⛔ **不可作方向信号**：只告"何时"，不告"涨跌"
- ✅ **必须配合价格指标**：时价合一 = 强支撑/压力位 + 斐波时间线 重合 → 最高胜率
- ✅ **误差容忍 ±1~2 K线**：日线级别的技术必然有误差，命中判定用窗口而非单点
- ✅ 周期优先级：**日线 > 周线 > 分钟线**（分钟线噪音大，不作主判据）

---

## 三、实现方案设计

### 3.1 新增函数：`compute_fib_time_targets(df, lookback=250)`

**位置建议**：`fib_extension_scan.py`（与 `compute_fib_levels` 同层，共享 `find_latest_uptrend_swing` 输出）

**签名**：
```python
def compute_fib_time_targets(df, lookback=250, fib_seq=None) -> dict:
    """
    输入：日线 DataFrame（含 date/open/high/low/close）
    输出：{
      "as_of": "2026-09-14",          # 计算基准日
      "as_of_idx": 249,               # 基准行索引
      "anchors": [                    # 每个锚点（高低点）出发的时间线
        {"anchor": "swing_low", "anchor_date": "2026-05-12", "anchor_idx": 120,
         "targets": [{"fib": 1, "date": "...", "idx": 121, "passed": true}, ...]}
      ],
      "confluence": [                 # 汇聚窗口
        {"window_start": "2026-09-20", "window_end": "2026-09-22",
         "hits": 3, "sources": ["swing_low@21", "swing_high@13", "ext(1.272)"],
         "strength": "high"}
      ],
      "next_window": {...},           # 最近的未来汇聚窗口（预警核心）
      "note": "仅时点预警，方向需配合价格指标（时价合一）"
    }
    """
```

### 3.2 算法伪代码

```
FIB_SEQ = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144]

def compute_fib_time_targets(df, lookback=250):
    d = df.tail(lookback).reset_index(drop=True)
    n = len(d)
    as_of_idx = n - 1
    anchor_idx = 找显著高低点索引集(d)      # 复用 is_high/is_low 逻辑, 取最近 3~5 个显著点
    source_lines = []   # 收集所有 (日期/索引, 来源标签)

    # ── 用法① 时间区间：每个锚点向后投射 FIB_SEQ ──
    for a in anchor_idx:
        for k in FIB_SEQ:
            t = a.idx + k
            if t <= as_of_idx: continue          # 只保留未来时点
            source_lines.append((t, f"{a.kind}@{k}"))

    # ── 用法② 时间扩展：最近 3 点 A→B→C ──
    if len(anchor_idx) >= 3:
        A, B, C = anchor_idx[-3], anchor_idx[-2], anchor_idx[-1]
        span = B.idx - A.idx
        for r in (1.0, 1.272, 1.618, 2.618):
            t = C.idx + round(span * r)
            if t > as_of_idx:
                source_lines.append((t, f"ext({r})"))

    # ── 用法③ 汇聚：按日期聚类（±1~2交易日窗口合并）──
    source_lines.sort()
    clusters = 用 window=2(交易日) 合并相邻 t
    confluence = [c for c in clusters if len(c.sources) >= 2]
    next_window = 最近的未来 confluence（或最近的候选时点）

    return {as_of, as_of_idx, anchors, confluence, next_window, note}
```

### 3.3 关键实现细节

| 细节 | 方案 |
|:---|:---|
| **锚点选择** | 复用 `fib_extension_scan.is_high/is_low`（±4 邻域极值），取最近 3~5 个显著点；同时纳入 `find_latest_uptrend_swing` 返回的 `swing_high_i / swing_low_i / pullback_low_i`（保证与价格维度锚点一致） |
| **未来时点过滤** | `t > as_of_idx`，只保留未来（预警才有意义）；同时输出已过时点用于回测命中检验 |
| **日期换算** | 索引 → 日期的映射依赖**交易日序列**（不做自然日跳算）。用 `d["date"].iloc[min(t, n-1)]`；若 `t` 超出序列长度，用交易日历外推（见下） |
| **超出序列外推** | 简单方案：仅输出"距今 N 个交易日内"的窗口（`N ≤ 55`）；复杂方案：接 `akshare.tool_trade_date_hist_sina()` 生成未来交易日历外推日期。**建议先用简单方案**，避免引入新数据依赖 |
| **汇聚窗口宽度** | ±1~2 交易日（文档约束），合并后标记 `hits`（来源数）与 `strength`（2=medium, ≥3=high） |
| **早停/裁剪** | `FIB_SEQ` 截断至 `max_fib`（默认 89 或 144），避免远未来噪音 |

---

## 四、与现有 `compute_fib_targets` 的集成方式

### 4.1 契约对齐（保持向后兼容）

现有 `compute_fib_targets(df) -> {"targets": {ratio: price}}`。新模块**不改动该契约**，而是**并行**输出时间维度：

```python
# portfolio_core.py / adaptive_dual.py 信号腿返回体（增量）
return {
    "action": ..., "reason": ..., "price": ...,
    "atr": ..., "atr_stop": ...,
    "fib_tp": {
        "targets": compute_fib_targets(df),        # 价格维度（不变）
        "time_windows": compute_fib_time_targets(df)["next_window"],  # 时间维度（新增）
    },
}
```

> **零破坏**：旧消费方读 `fib_tp["targets"]` 不受影响；新消费方读 `fib_tp["time_windows"]`。

### 4.2 落地路径（三阶段，可按需裁剪）

| 阶段 | 动作 | 风险 |
|:---|:---|:---|
| **P0（评估期·本次）** | 仅输出本设计文档 + 单测脚本，**不改生产** | 零 |
| **P1（影子模式）** | 新增 `compute_fib_time_targets` 到 `fib_extension_scan.py`；`fib_track.py` 在 `lock_baseline` 时**并行锁定时间窗口**（写入 `fib_tracking_state.json` 的 `time_baseline`，与价格基线同样"锁定不回改"）；报告小节增加"⏱️ 变盘时点预警" | 低（只增不改） |
| **P2（消费接入）** | `build_take_profit_section` 增补时间线小节；`run_agent.py` 自动带出；可选接入 `close_scan_v2.py` | 中（需回测验证后才允许进入决策） |

### 4.3 重复实现收敛（顺带清理）

`compute_fib_targets` 目前有两份（`portfolio_core.py` L248、`adaptive_dual.py` L451），**逻辑相同**。建议在 P1 阶段抽到 `analysis/fib_extension_scan.py` 或新建 `analysis/fib_common.py`，两处改为 import —— 与 09-11「`strategies.py` 共享抽象消除三脚本漂移」同型治理。

### 4.4 落盘格式（延续"预先声明"原则）

```json
{
  "time_baseline": {
    "600089": {
      "name": "特变电工",
      "locked_at": "2026-09-14",
      "anchors": [{"kind": "swing_low", "date": "2026-05-12", "idx": 120}],
      "pending_windows": [
        {"date": "2026-09-21", "hits": 2, "sources": ["swing_low@21", "swing_high@13"]},
        {"date": "2026-10-06", "hits": 3, "sources": ["swing_high@34", "ext(1.618)", "swing_low@55"]}
      ]
    }
  }
}
```

---

## 五、回测验证思路

> 原则同 `fib_track.py`：**预先声明 → 前瞻跟踪 → 事后检验**，禁止射箭画靶。

### 5.1 数据准备

- 标的：30 只关注池（`WATCHLIST`），日线 ≥ 500 根（baostock 可得，历史覆盖足够）
- 时间窗口：2024-01-01 ~ 2026-09-11
- 切分：每个交易日 `T` 只用 `T` 及之前数据计算，严禁未来函数

### 5.2 命中判定（核心）

```
对每个交易日 T：
  1. 用 [T-250, T] 数据算 compute_fib_time_targets → 得到未来窗口 W（如 T+21、T+34）
  2. 在 T 之后观察窗口 W ±2 交易日 内是否发生"变盘"：
     变盘定义（可调）：
       a) 方向反转：窗口内出现当日涨幅/跌幅 ≥ 3% 的 K 线，且随后 3 日未破该 K 线极值
       b) 趋势加速：窗口内 5 日均量 > 前 5 日均量 × 1.5 且价格创近 20 日新高/新低
       c) 结构破坏：窗口内收盘价跌破/突破前 20 日支撑/压力
  3. 命中 = 窗口 ±2 日内出现上述任一信号
```

### 5.3 评价指标

| 指标 | 计算 | 合格线 |
|:---|:---|:---|
| **命中率（Precision）** | 命中窗口数 / 预警窗口数 | ≥ 40%（与选择性入场的 40% 胜率基准对齐） |
| **基准对照** | 随机选同数量窗口的命中率 | 须**显著高于**随机基准 |
| **时价合一增强** | 分两组：仅时间线 vs 时间线∩价格关键位 | 后者命中率须 **高 ≥ 15pct**（验证"时价合一"） |
| **按 fib 序列分层** | 分别统计 21/34/55/89 | 找有效区间（预期 21-55 最有效，>89 失效） |
| **按周期分层** | 日线 vs 周线 | 日线须优于分钟线（若有无分钟数据） |

### 5.4 判定标准

- ✅ **通过**：命中率 ≥ 40% 且显著高于随机基准，且时价合一增强成立 → 可进 P2
- ⚠️ **观察**：命中率 30-40% → 仅作报告辅助，不进决策
- ❌ **弃用**：命中率 < 30% 或 ≈ 随机基准 → 归档为"无效工具"（与 09-10 RSI/RVI/VI 回测跑输同处置）

### 5.5 脚本

复用现有回测框架：`backtest_strategies.py` / `validate_strategies.py`。新增 `--fib-time` 开关跑本模块（不改默认路径）。

---

## 六、风险提示

| 风险 | 说明 | 缓解 |
|:---|:---|:---|
| **仅辅助，不可单用** | 时间线只告"何时"不告"涨跌"，单用 = 随机 | 强制需价格指标共振（时价合一）；报告标注"预警非信号" |
| **参数敏感性** | 锚点选择（±4 邻域）、窗口宽度（±2）、fib 截断（89/144）均影响结果 | 回测做参数敏感性网格；默认取保守值 |
| **未来函数风险** | 索引外推/日期换算若用全序列易引入未来数据 | 严格 `t > as_of_idx`；回测逐日切片 |
| **远未来衰减** | fib > 89 的时间线噪音大、命中率骤降 | 截断；或按 fib 分层加权 |
| **主观锚点** | 高低点识别有主观性（不同人标注不同） | 复用现有 `is_high/is_low` 客观规则，锚点可复现 |
| **过度拟合** | 历史上找到的"汇聚"可能是巧合 | 样本外验证（walk-forward）；要求 ≥ 2 次独立验证 |
| **依赖交易日历** | 精确未来日期需交易日历 | P1 用"距今 N 交易日"简化；P2 再接 akshare 日历 |

---

## 七、结论与建议

### 7.1 可行性判定：**✅ 技术可行，成本低，值得做 P1**

- **零新增数据依赖**：`find_latest_uptrend_swing` 已返回锚点索引，`datalen` 可从 150 提到 250 覆盖 fib 序列
- **零破坏集成**：新函数并行输出，不改 `compute_fib_targets` 契约
- **天然契合"预先声明"原则**：时间窗口可像价格止盈位一样锁定，不重算 → 不画靶

### 7.2 建议动作

| 优先级 | 动作 |
|:---|:---|
| 🔴 P1（建议先做） | 落地 `compute_fib_time_targets` 到 `fib_extension_scan.py` + 单测；`fib_track.py` 锁定 `time_baseline`；报告加"⏱️ 变盘时点预警"小节 |
| 🟡 P1.5 | 收敛 `compute_fib_targets` 两份重复实现 → 共享模块 |
| 🟡 P2（须先回测） | 跑 5.2 命中率验证；通过才接入 `close_scan_v2.py` 决策流 |
| ⚪ 观察 | 参数敏感性网格；周线版本 |

### 7.3 与 13 项台账的联动

- 本项 ⑬ 与 ③（斐波扩展止盈）、⑫（MACD 多周期）同属"**技术维度细化**"主线
- **建议**：⑬ 定位为**报告辅助层**（与 `build_take_profit_section` 同级），**不进入 030 决策链**，直至 P2 回测通过
- 与 ①（电力设备出海观察池）无直接冲突，可并行推进

---

## 附录 A：核心函数签名清单

```python
# fib_extension_scan.py 新增
def compute_fib_time_targets(df, lookback=250, fib_seq=(1,2,3,5,8,13,21,34,55,89)) -> dict
def _find_significant_anchors(df, max_anchors=5) -> list[dict]   # 复用 is_high/is_low
def _merge_confluence(source_lines, window=2) -> list[dict]      # 汇聚聚类

# fib_track.py 扩展
def lock_time_baseline(state) -> None       # 锁定时间窗口（与被锁价格基线同步）
def verify_time_windows(state) -> report    # 事后检验变盘命中

# 报告层
def build_fib_time_section(...) -> str      # 与 build_take_profit_section 并列
```

## 附录 B：数据需求清单

| 数据 | 来源 | 现状 |
|:---|:---|:---|
| 日线 OHLCV（≥250 根） | 新浪 `fetch_sina_kline(datalen=250)` / baostock | ✅ 已有（datalen 需从 150 提到 250） |
| 交易日序列（用于日期换算） | DataFrame 的 `date` 列 | ✅ 已有 |
| 未来交易日历（可选，P2） | `akshare.tool_trade_date_hist_sina()` | ⚠️ 需新增（P1 不必） |
| 显著高低点 | `is_high/is_low` 规则 | ✅ 已有 |
| 历史回测数据 | baostock（500+ 根） | ✅ 已有 |

---

*本报告为设计评估文档，不含生产代码改动。是否进入 P1 实现，待用户批准。*
