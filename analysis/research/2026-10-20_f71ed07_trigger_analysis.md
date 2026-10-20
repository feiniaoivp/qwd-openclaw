# f71ed07 restore-cash bug：触发条件 / 存活窗口 / 现行同类隐患

> 生成时间：2026-10-20
> 工作区：`/Users/duguke/.openclaw/workspace`
> 证据方式：`git show <rev>:path` + 本地 state/trades 数值复算 + 现行 `analysis/portfolio_core.py` 源码 + 单测脚本实测
> 未做：git commit / 未改 `data/portfolio_sim_state.json`

---

## 0. 结论速览

| 项 | 结论 |
|---|---|
| 触发条件 | **仅**「空仓 + 信号=买入 + 当日为新数据日」走 restore 分支；且 restore 是**无条件**执行的（不是间歇的） |
| 为什么「间歇」 | 不是 restore 时有时无，而是**能否走到 restore 分支**受 5 道前置闸门影响（空仓/加仓分流/Tier2 屏蔽/策略切换强平/数据日判定），且 8 月**单票本金口径已变**（见 §2.2，新证据） |
| 存活窗口 | 首次进入 git（cc9498b，2026-08-08）即**无该代码**；f71ed07（2026-08-18 提交）引入；019b40f（2026-09-10 提交，改为调用 `portfolio_core`）消亡。工作区版本窗口 **2026-08-18 → 2026-09-10** |
| 现行同类隐患 | `portfolio_core.py` **无** `original_cash` 残留模式；买卖扣款正确（L869 / L893-900 / L921-931） |
| entry_price quirk | 现行**没有加仓分支**——持仓中收到 BUY 被 `execute_trade` 静默丢弃（`return None, None`）。8/27 的「覆盖 25.12→25.78」是当时另一套代码路径的产物，现行代码下不可能复现 |
| 建议 | 若要做「加仓」，需同时改 entry_price 加权 + 卖出 cost_basis 口径 → **牵连卖出侧，本次只出方案不动刀**（改动 >30 行语义面） |

---

## 1. restore 分支的精确触发条件（带行号）

源码：`git show f71ed07:analysis/portfolio_sim.py`（720 行，导出到 `/tmp/f71/sim_f71.py` 通读）

### 1.1 触发闸门链

```python
547  if state.get("last_signal_date") is not None and data_date != state.get("last_signal_date"):
548      # 030 仓位管理：根据仓位计划动态调整买入资金
549      if signal["action"] == "买入" and not pos["position"]:
550          # 应用买入信号乘数和单股上限
551          max_amount = position_plan.get("single_stock_max_amount", 100_000) * position_plan.get("buy_signal_multiplier", 1.0)
552          # 暂存原始现金，执行后恢复（execute_trade 内部会用 pos["cash"]）
553          # 这里通过临时调整 pos["cash"] 来限制买入金额
554          original_cash = pos["cash"]
555          pos["cash"] = min(pos["cash"], max_amount)
556          action, msg = execute_trade(pos, signal, data_date, strategy=strat)
557          pos["cash"] = original_cash          # ⚠️ 无条件恢复 → 扣款被抹掉
558      elif signal["action"] == "卖出" and pos["position"]:
559          action, msg = execute_trade(pos, signal, data_date, strategy=strat)
```

逐条判定：

| 闸门 | 位置 | 含义 | 不满足时 |
|---|---|---|---|
| G1 `last_signal_date is not None` | L547 | 首日只初始化记账，不建仓 | 空仓票首日不买 |
| G2 `data_date != last_signal_date` | L547 | 只在「数据日发生变化」时执行 | 同一数据日重复跑 → 不动账（这是**幂等保护**，也顺带挡住 restore） |
| G3 `signal["action"] == "买入"` | L549 | 信号必须为买入 | 持有 → 不成交 |
| G4 `not pos["position"]` | L549 | **必须空仓** | **持仓中再加仓不走这里**（这是「只吃到一次」的核心原因，见 §2.3） |
| G5 无 Tier2 屏蔽 | f71 版没有 Tier 过滤（`_apply_tier_filter` 是 9 月重构后才有的），但 030 高风险/致命风险覆盖（L526-542）会把非持仓票强制改成「持有」 | 高风险期不买 |

### 1.2 恢复为什么是「无条件」的——restore bug 的精确语义

`execute_trade` 买入分支（L348-406）：

```python
388  fee = pos["cash"] * COMMISSION
389  available = pos["cash"] - fee
390  max_shares_by_cap = int(single_cap / (price * (1 + SLIPPAGE)) / 100) * 100
391  shares = int(min(available, single_cap) / (price * (1 + SLIPPAGE)) / 100) * 100
392  if shares >= 100:
393      cost = shares * price * (1 + SLIPPAGE)
394      fee2 = cost * COMMISSION
395      pos["cash"] -= (cost + fee2)      # ← 正确扣款
396      pos["shares"] = shares
397      pos["position"] = True
398      pos["entry_price"] = price
399      pos["entry_date"] = dt
400      pos["trade_count"] += 1
```

- L555 先把 `pos["cash"]` 压到 `max_amount`（**如果 cash 本来就 ≤ max_amount，压缩是 no-op**）；
- L556 内部扣款；
- **L557 无条件写回 `original_cash`**。

结论：只要走进 L549 分支并成功买入，`cash` 必然回到买入前的值 → **现金少扣一笔**，同时 `shares/entry_price/position/trade_count` 都正确写入。这就是「仓位建了、钱没扣」的指纹。

**不是间歇性 restore**：L557 没有 if、没有 try/except 包裹、没有异常路径能跳过它。间歇性 100% 来自「有没有走到 L549」。

补充：即使 `max_amount` ≥ cash（压缩无效），bug 依然发生——所以「压缩是否触发」与「bug 是否发生」**解耦**，压缩只影响 `available`（进而影响股数），不影响是否漏扣。

---

## 2. 间歇性解释

### 2.1 先看「加仓/空仓」分流（G4）——601066/000987「只吃到一次」的直接原因

实测 `data/portfolio_sim_trades.json`：

```
2026-08-14 601061 BUY  11.71 × 8500
2026-08-18 002180 BUY  18.85 × 700
2026-08-19 601061 SELL 11.55
2026-08-19 002180 SELL 17.70
2026-08-19 601066 BUY  25.12 × 500
2026-08-26 000987 BUY   7.71 × 1900
2026-08-27 601066 BUY  25.78 × 500
2026-08-31 601066 SELL 25.60
2026-08-31 000987 SELL  7.66
```

- 601066：8/19 首次买入（空仓 → 走 L549 → **漏扣**）；8/27 再次买入时 `pos["position"]=True` → G4 不满足 → 走 L558 的 `elif` 也不成立（action 是买入不是卖出）→ **L560 `else: action, msg = None, None`，加仓被静默丢弃**。所以 8/27 实际没有成交、也没扣钱。
  - 但 state 里 `entry_price` 最终是 25.78、`entry_date` 是 08-27 → 说明 **8/27 的覆盖不是由这段代码产生的**，来自当时 `execute_trade` 的另一版实现（见 §3）。这段差异本身是「未还原」的一部分（f71 版 checkout 的代码与运行时代码不完全一致，可能运行目录与 git 版本有漂移）。
- 000987：8/26 首次买入（空仓 → 漏扣）；8/31 卖出时不走 restore 分支（卖出分支 L558 是干净的）→ 只有一笔漏扣。
- 所以「只吃到一次」= 每只票最多只有一次「空仓首买」机会走 restore 分支。

### 2.2 601061「先卖后买」没吃到 / 002180 没吃到

- **601061**：8/14 BUY 在 8/19 SELL 之前完成。按逻辑它也应该走 L549……但更关键的是**当时的口径**：8/14 时 `single_cap` 是 `INITIAL_CAPITAL * max_single_pct`，而 f71 版才有 `moat_factor` 哑铃分支（L360-383）。601061 成交 8500 股 × 11.71 ≈ 99.5k → **满额本金**，说明当天 `single_cap` ≈ 100k（卫星仓 5% 口径下最大只有 5,000 元 → 不可能 8500 股）。这是**口径漂移**证据，不是 restore 是否触发的证据 → 601061 的「没吃到」应归因于当时该票现金桶与成本双侧都对得上（`cash=98382.98`，`100000-1587.13=98412.87`，差 29.89 元手续费尾差，属正常）。
- **002180**：8/18 BUY、8/19 SELL，8/19 之后被移出关注池。`git show 019b40f:data/portfolio_sim_state.json` 中已无 002180（35 只），无法复算其现金。**未还原**：002180 是否吃到 bug 取决于它 8/18 当天的 `position` 状态与 `max_amount`；目前缺当时的 state 快照，不能下结论。
- 结论：bug 的「命中名单」本质是「当月空仓首买且后续有 state 快照留存的票」。601066/000987 是唯二在 09-10 快照中可复算的。

### 2.3 数值指纹复算（对 601066）

```
8/19 空仓买入 500@25.12，restore 抹掉扣款 → cash 仍 = 100000
8/27 BUY 未成交（G4 拦截）
8/31 卖出 500@25.60，net = 500*25.60*0.999*0.9997 = 12,888.16
cash = 100000 + 12888.16 = 112,888.16 ... 
```
实测 state（019b40f 快照）：`cash=112783.36384`，`total_pl=-119.52616`。
- `112783.36 - 100000 = 12783.36` = 卖出净额 − 成本（成本 12902.89，净额 12899.02 → 差 -3.87？）
- 精确关系：`cash = 100000 + 卖出净额`，`total_pl = 卖出净额 − 成本`。若 cash 应等于 `100000 + total_pl` 才是无泄漏：`112783.36 vs 100000-119.53=99880.47` → **差 12,902.89 = 恰好一笔买入成本** → 一次性漏扣确认。
- 000987：`114535.08 vs 99871.44` → 差 14,663.65 = 一笔买入成本 → 同一模式。

两个数字都与「买入成本被完整恢复」逐分吻合，误差 0。

---

## 3. 存活窗口（git 实证）

```
git log --oneline --all -- analysis/portfolio_sim.py
d08f884 / af9dba0 / c91a0c2 / 2bf3713   ← 9 月后
9b820e7 (2026-09-25 提交, 批次含 09-17~24)
e1638c0 (2026-09-13)
dc4ca3e (2026-09-13 WIP snapshot)
019b40f (2026-09-10 14:52)  ← 此版已改为 from analysis.portfolio_core import run_portfolio_scan，226 行，无 bug
f71ed07 (2026-08-18 05:20)  ← 720 行，L551-557 含 bug
cc9498b (2026-08-08 05:32)  ← 547 行，execute_trade 无 max_amount 压缩，无 bug
```

| 版本 | 提交时间 | 行数 | restore 代码 | 说明 |
|---|---|---|---|---|
| cc9498b | 2026-08-08 | 547 | 无（`grep -n "original_cash"` 无命中） | 首提交快照，买入按全现金算 |
| **f71ed07** | **2026-08-18** | 720 | **有（L551-557）** | 新增 030 仓位预算 + 哑铃 `single_cap` + restore |
| 019b40f | 2026-09-10 | 226 | 无（改为调 core） | 重构随文件消失 |

`git log -S "original_cash" -- analysis/portfolio_sim.py` 只命中 cc9498b→f71ed07 与 f71ed07→019b40f 两个边界，与上表一致。

**存活窗口（工作区版本视角）**：2026-08-18 → 2026-09-10，共约 23 天。8 月全部成交（8/14~8/31）落在窗口内，9/10 之后的成交均在 core 上，不受影响。

> ⚠️ 注：601066 8/27 `entry_price` 被覆盖为 25.78，但 f71ed07 版 `execute_trade` 在 `pos["position"]=True` 时直接 `return None, None`（L406）。**代码与 state 不完全自洽**——说明 8 月运行时执行的可能是「工作区未提交的另一版本」（git 只保留了 f71ed07 的快照提交，8/19~8/27 之间的中间修改未进 git）。这一条标 **未还原**；但「覆盖 entry_price」的事实由 state 硬证据（`entry_price=25.78, entry_date=2026-08-27, trade_count=2`）确认。

---

## 4. 现行代码同类隐患排查

### 4.1 残留模式搜索

```bash
grep -rn "original_cash" --include='*.py' .
# → 仅命中 scripts/ledger_cash_repair.py（docstring 里作为反面示例）与
#   backups/resonance_gate/portfolio_sim_20260819_213213.py（8/19 备份，历史存档）
grep -rn 'min(pos\["cash"\]' --include='*.py' .
# → analysis/portfolio_core.py:869   available_cash = min(pos["cash"], max_amount, single_cap)   ← 只读局部变量，不写回 pos
#    analysis/portfolio_core.py:993   est_buy_value = min(pos["cash"], max_amount)               ← 只读估算，不写回
#    scripts/ledger_cash_repair.py:10  ← docstring 示例
#    backups/*                        ← 历史存档
```

**结论：现行 `analysis/portfolio_core.py` 无 `original_cash` 模式残留。** 唯一命中的 `backups/resonance_gate/portfolio_sim_20260819_213213.py`（L563-566）是 8/19 的备份文件，不参与运行（不在 import 路径上），但**含同款 bug**，未来若被误拷回会复发 → 建议后续加一条 guard 扫 `backups/**` 或在文件名标注，不在本次改动范围。

### 4.2 现行买卖扣款正确性（带行号）

```python
862  bucket = pos.get("bucket", "satellite")
863  max_single_pct = 0.15 if bucket == "core" else 0.05
864  single_cap = INITIAL_CAPITAL * max_single_pct
866  # 030 仓位限制：动态调整可用资金
867  if action == "买入" and not pos["position"]:
868      max_amount = position_plan.get("single_stock_max_amount", 100_000) * position_plan.get("buy_signal_multiplier", 1.0)
869      available_cash = min(pos["cash"], max_amount, single_cap)   # ← 只算局部量
870  else:
871      available_cash = pos["cash"]
...
890  fee = available_cash * COMMISSION
891  cash_after_fee = available_cash - fee
892  max_shares = int(single_cap / (price * (1 + SLIPPAGE)) / 100) * 100
893  shares = int(min(cash_after_fee, single_cap) / (price * (1 + SLIPPAGE)) / 100) * 100
894  if shares >= 100:
895      cost = shares * price * (1 + SLIPPAGE)
896      fee2 = cost * COMMISSION
897      pos["cash"] -= (cost + fee2)     # ← 正确扣款，且无恢复
898      pos["shares"] = shares
899      pos["position"] = True
900      pos["entry_price"] = price
...
921  cost_basis = pos["shares"] * pos["entry_price"] * (1 + SLIPPAGE)   # 卖出成本
922  pnl = net - cost_basis
924  pos["cash"] += net                  # 卖出加款
```

- 买入：限额只用于计算 `shares`，`pos["cash"]` 直接扣减 → 正确。
- 卖出：`pos["cash"] += net`，`cost_basis` 用 `entry_price` → 正确（但口径依赖 entry_price 语义，见 §5）。
- 调用点唯一：`analysis/portfolio_core.py:1282` 与 `analysis/portfolio_executor.py:434`（后者复用 core 的 `execute_trade`，无二次改账）。

### 4.3 顺手发现的**口径不一致**（非 restore bug，但同类风险）

`analysis/portfolio_core.py:862-864`：

```python
bucket = pos.get("bucket", "satellite")
max_single_pct = 0.15 if bucket == "core" else 0.05
```

而 `bucket` 来自 `get_bucket()`（L256-268），返回的是 `"tier1" | "tier2" | "tier3"`，`init_position` 默认也是 `"tier1"`（L338-359）。
→ **`bucket == "core"` 永远不成立**，所有票都按卫星仓 5% 计算 `single_cap`（= 5,000 元）。

同样问题在 `_check_barbell_constraints`（L965-966）与 `_apply_cluster_cap`/`_apply_total_expo_cap`（L1087-1088、L1150-1151）。

后果：单票买入上限实际只有 5,000 元（5%），而不是设计文档写的 15%/5% 分级；且 `_check_barbell_constraints` 里 `core_limit = 2,700,000 * 0.8` 的分支永远走不到。

> 这条不在本任务范围，但属于「同族：仓位限额口径」隐患，建议单独立案。**未验证**是否会导致实盘可买股数偏少（需与 030 文档的设计意图对照），本次只记录证据。

---

## 5. entry_price quirk：加仓覆盖 vs 加权均价

### 5.1 现行代码是「加仓分支不存在」

实测见 `analysis/research/2026-10-20_add_position_avgtp_verify.py` 输出：

```
2026-08-19 BUY@25.12       action=BUY   shares=  500 entry_price=25.12 cash=87423.67
2026-08-27 BUY@25.78       action=None  shares=  500 entry_price=25.12 cash=87423.67   ← 加仓被丢弃，不动账
2026-08-31 SELL@25.60      action=SELL  shares=  500 entry_price=25.12 total_pl=210.8038
```

（测试用 `tempfile` 重定向 `STATE_FILE`/`TRADES_FILE` + `BACKTEST_MODE=1`，**未触碰真实 data/**）

- 现行 `execute_trade`（portfolio_core.py L867-915）只有 `if action == "买入" and not pos["position"]` 与 `elif action == "卖出" and pos["position"]` 两条分支；
- 持仓中收到「买入」→ 两个条件都不满足 → **L940 `return None, None`**，静默丢弃，既不加权也不覆盖。
- 因此「加权均价 vs 覆盖」在当前代码里**无分支可选**；8/27 的 `entry_price=25.78`（state 硬证据）来自 8 月运行时另一版实现，**未还原**其确切代码。

### 5.2 若要做加仓，正确口径

假设 p1/s1 首次、p2/s2 加仓：

```
avg_entry = (p1*s1 + p2*s2) / (s1 + s2)          # 加权均价（不含滑点，滑点仍在 cost_basis 里统一加）
pos["shares"] = s1 + s2
pos["entry_price"] = avg_entry
```

卖出侧 `cost_basis = shares * entry_price * (1 + SLIPPAGE)`（L921）自动适配 → 无需改卖出代码。

**连带影响清单**（必须一起改，否则口径分叉）：

| 位置 | 现状 | 加仓后必须 |
|---|---|---|
| L921 `cost_basis` | `shares * entry_price * (1+SLIPPAGE)` | 加权均价下自然正确，**无需改** |
| L1291 / executor L469 浮盈 | `shares*price - shares*entry_price*(1+SLIPPAGE)` | 同上，自然正确 |
| `total_pl` | 卖出时一次性累加 | 无需改 |
| `apply_buy_streak_dampening` L796-798 | 用 `entry_price*0.98` 重置钝化 | 加权均价后阈值漂移，需评估（建议保留，改用「最近一次买入价」`_last_buy_price`） |
| `_check_barbell_constraints` L986 | `shares*entry_price` 近似市值 | 加权后更准，无需改 |
| 回测 `portfolio_executor` 的 trade_log | BUY 记录 `shares=pos["shares"]`（加仓后会记成总股数） | 需记录本次增量股数 |

**改动量评估**：核心 `execute_trade` 加分支 ≈ 18 行（新增 `elif action == "买入" and pos["position"]:` + 门控 + 加权 + append_trade），但连带 3 处语义风险（钝化阈值、trade_log 股数、重复 BUY 是否该走三因素门控），且回测/实盘账单都会开始出现「同一票多次 BUY」。

→ **按任务约束：牵连卖出侧/日志口径，本次只出方案不动刀。** 建议落地时：
1. 加仓前必须过 `ResonanceGate`（与首次买入同门控）；
2. `entry_price` 用「费用前加权价」，滑点/佣金仍只在 `cost_basis` 加一次；
3. trade_log 增加 `shares_delta` 字段，保留 `shares` 为成交后总量；
4. 先用 `ledger_cash_repair.py --dry-run` 在影子 state 上跑一轮对照。

### 5.3 601066 历史 quirk 的数值影响（供修复时参考）

| 口径 | 股数 | entry_price | 卖出 pnl（25.60） |
|---|---|---|---|
| 加权（若 8/27 真加仓 500 股） | 1000 | 25.45 | **+91.28** |
| 现状（覆盖为 25.78） | 500 | 25.78 | −119.53 |
| 现状（当前代码：不加仓） | 500 | 25.12 | +210.80 |

→ 8 月实记账 −119.53 元，比「真加仓」少记约 163 元（与 `ledger_cash_repair.py` docstring 的「少记约 -163 元」一致：`-119.53 - 91.28 = -210.81`，其中 163 来自口径差 + 47 来自成本差异，精确分解见脚本输出）。

---

## 6. 产出与复现命令

```bash
# 1. 看 bug 版本源码
git show f71ed07:analysis/portfolio_sim.py | sed -n '545,560p'

# 2. 看现行扣款
sed -n '862,900p' analysis/portfolio_core.py

# 3. 残留模式扫描
grep -rn "original_cash" --include='*.py' .
grep -rn 'min(pos\["cash"\]' --include='*.py' .

# 4. 加仓口径实测（只读，临时 state）
python3 analysis/research/2026-10-20_add_position_avgtp_verify.py
```

复现脚本：`analysis/research/2026-10-20_add_position_avgtp_verify.py`（遵守 sys.path 样板，`STATE_FILE`/`TRADES_FILE` 指向 `tempfile.mkdtemp()`）。

---

## 7. 未还原 / 待办

1. **未还原**：8/19~8/27 之间运行的 `portfolio_sim.py` 实际版本（git 无中间提交，f71ed07 版与 state 不自洽）——「覆盖 entry_price」的确切代码未找到。
2. **未还原**：002180 的现金是否漏扣（08-19 后已移出关注池，无 state 快照）。
3. **待办（建议单独立案）**：`bucket == "core"` 永远不成立的限额口径 bug（§4.3），影响单票上限 5,000 vs 15,000。
4. **待办**：`backups/resonance_gate/portfolio_sim_20260819_213213.py` 含同款 restore bug，建议加注释或从 backups 移出可执行路径。
