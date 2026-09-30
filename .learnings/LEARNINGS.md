# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

## [LRN-20260916-IMP] Python 脚本「定义顺序」与「重复实现」两类静默缺陷

**Logged**: 2026-09-16
**Category**: best_practice
**Area**: code-maintenance

### What happened
第二轮「代码维护/清技术债」审计用 AST 扫描 + 逐文件 import 冒烟测试，一次性挖出 4 处真实缺陷：
1. `backtest_power_overseas.py` **模块级 NameError**：第 40 行 `sys.path.insert(0, os.path.join(WORKSPACE, ...))` 与第 45 行 `log.warning(...)` 都引用了**在其之后才定义**的 `WORKSPACE`(L58) / `log`(L67)，且 `Falsee` 拼写错误。整个脚本 import 即崩，从未成功跑过。
2. `challenge_review.py` **未定义名 `log`**：`_zhonglian_strat2_holding()` 的 except 分支调用 `log.warning`，但文件从未 import logging / 定义 log。仅在 `zhonglian_state.json` 读取失败时触发 → 恰好是「出错时再抛一个 NameError」，掩盖真实原因。
3. `backtest_strategies.py` **护栏统计口径错误**（未提交改动）：`compare_to_random(int(avg_wr*total_trades), total_trades, ...)` 把「平均胜率」转成整数命中数再重算比率，小样本取整把 58.9% 压成 40%，导致每个策略都被误判「❌ 未过」。且混淆了「交易胜率」与「随机基准命中率」两个不同统计对象。
4. `validate_strategies.py` **日期口径不一致**：重构后的 `fetch_long_history` 用 `df['date'] >= start`(str) 与 datetime64 列比较；另 `__main__` 解析了 `--write-map/--arbitrate` 但未传入 `main()`（重构时遗漏）。

### Do differently next time
- 批量审计用 **AST 未定义名扫描**（比 pyflakes 轻量、零依赖）覆盖 `analysis/` + `scripts/`，可秒级定位 NameError 类缺陷。
- 模块级代码（import 时执行的部分）必须保证「引用的名字已在其上方定义」；把配置/logger 定义提到最前。
- 「可选依赖 import 失败」分支里调用 logger 前，先确保 logger 已存在（否则失败时二次崩溃）。
- 回测护栏口径：**比率 vs 比率**直接对照（用 Wilson CI），不要 `int(rate*n)` 再除回去——取整会系统性低估小样本比率。
- `main()` 参数化重构后，务必同步 `__main__` 的传参，别留下「解析了却没用」的死参数。
- 中间态：同一 workspace 的未提交 diff 可能是半成品，审计时应视为「待验证假设」而非既成事实。

---

## 2026-09-29 — cron `lastStatus=ok` 可能是空转假成功（必须看 lastDurationMs）

**What happened**
3 个新闻 cron job 静默停摆 **21 天**（新闻归档断在 09-08），
但 `openclaw cron list` 一直显示 `ok`，cron 健康巡检完全没报警。

**Root cause**
这 3 个 job 是 `sessionTarget: main` + `payload.kind: systemEvent`：
只把命令文本注入长驻主会话当系统事件，**不执行**；主会话模型回合失败
（`[assistant turn failed before producing content]`）后无人消费注入事件，
静默丢弃。runner 认为投递完成即 `ok`，`lastDurationMs` 仅 **2–5ms**
（对照组正常的 isolated+agentTurn 是 **9548ms**）。

**What to do differently**
1. **判定 cron 真健康 = `lastStatus==ok` AND `lastDurationMs` 合理**。
   当 payload 需要跑脚本，若 `lastDurationMs < 100ms` → 标记"空转假成功"并告警。
2. **需要真执行命令的 cron 一律用 `isolated` + `agentTurn`（或 `--command` 直跑）**，
   不要用 `main` + `systemEvent` 来"跑脚本"。
3. 巡检脚本（`cron_health_check.py`）需新增该判据。

**Pattern-Key:** cron.silent-noop-fake-ok
