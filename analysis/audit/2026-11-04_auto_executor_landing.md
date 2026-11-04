# B-管道化 auto-executor 落地报告（2026-11-04）

> 用户拍板（16:27）：**B) 管道化 auto-executor（信号→次日开盘限价+影子双轨，同 bar 收盘前视退役）**

## 一、架构

```
信号日 T（15:40 扫描）
  门控链（tier/override/strat_switch/streak+cooldown/resonance/cluster/total/dedup/stale）
    ├─ 全过 → intents_buf[sym] = PENDING（不成交，enqueue-only）
    │           └─ 空仓卖出不入队（修 P2#5 噪声源）
    └─ 被拦 → HOLD（语义与旧一致，cap 拦截不排队保持 parity）

执行日 T+1（15:40 扫描，同一循环内先于当日信号门控）
  load_pending_intents() ← data/trade_intents/*_intents.json（status=PENDING 且日期 < 今日）
    ├─ 数据新鲜度：data_date 必须 == 今日（预跑/滞后 → 留待正式扫描）
    ├─ 开盘价 = df["open"].iloc[-1]，gap = open/信号日收盘 - 1（留痕）
    ├─ 执行日再校验：check_dedup（D3 单日 1 笔）→ execute_trade（030 限额/三因素门控/动态滑点内部全链重跑）
    └─ 状态机落盘：FILLED（含 fill.price/gap_pct）/ BLOCKED / CANCELLED / EXPIRED(>5 日历日) / SUPERSEDED
回写：flush_intent_updates()（文件级当日 .bak 先行）

影子双轨（不变）：lowopen-shadow-capture(18:45) + 19:41 执行审计 json 照常独立运行
```

## 二、关键修复（顺带产出）

**portfolio_core.py:41 WORKSPACE 硬编码 → getenv 模板化。**
这是 11-04 凌晨 e2e 直写生产事故（04:52/05:04 两度写 state+equity、冷却多减 2）的机制根因：
扫描写链上唯一硬编码 WORKSPACE 的模块就是 portfolio_core，env 沙箱对其失效。
修复后全链沙箱重定向可靠，e2e 防线从 size 比对升级为 **sha256 value-level 对照**。

## 三、行为变化（需知）

1. 成交时点：信号日收盘（前视）→ 执行日开盘（诚实），历史回测对比口径切换
2. 同日先成交后新信号：因 D3 单日 1 笔硬限延后一日（与已拍板 D3=A 一致）
3. 排队不再依赖 last_signal_date 同日守卫（防晨间误扫劫持排队窗口，重跑幂等覆盖）
4. 空仓卖出从「PENDING 永挂」变为「不入队 + 审计 SKIP 留痕」

## 四、回滚

- `AUTO_EXECUTOR=0`（env）→ legacy 同 bar 收盘路径，代码零改动
- 或 `git revert` 本次 commit；意向文件均有当日 .bak

## 五、验证

- 零污染 e2e `analysis/test_auto_executor_e2e.py`：7 场景全绿（成交/取消/过期/滞后/去重/回写/开关）+ 生产 4 文件 sha256 零变化
- 门禁：py_compile ✓ / script_path_guard 0 违规 ✓ / debug_residue ✓ / silent_except 无新增 ✓ / backup_guard 仅凌晨已知存量 2 文件（非本次）
- 今日 3 买已回填 PENDING（巨化 31.76 / 福耀 54.93 / 恒生 21.56，.bak-20261104-backfill 先行）

## 六、首跑验证点（11-05 15:40）

1. 3 条 PENDING 应以 11-05 开盘价成交（或显式 BLOCKED：三因素门控 45 线执行日再验）
2. `data/trade_intents/2026-11-04_intents.json` 状态应变为 FILLED/BLOCKED + 当日 .bak 生成
3. trades 底账新成交的 entry_price 应等于 11-05 开盘价（非 11-04 收盘）——管道化生效的硬证据
4. 明日组合日报「实际成交」应 >0（若无 BLOCKED 拦截）
