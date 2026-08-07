# 调参对照信号差异榜 2026-08-05

> 对比「上轮参数」与「本轮新参数」的每股双策略信号，量化参数调优改变了哪些操作建议。
> 数据源：baostock 历史K线（当日延迟1日）+ 最新行情。

📊 **调参对照信号差异榜** (2026-08-05)
- 旧参数源: `validation+默认参数(回退基线)`（`adaptive_params_prev.json` 缺省，回退 validation 策略级前二）
- 新参数源: `data/adaptive_params.json`

## 🔁 方向反转 (最重要)
（无）

## 🔴 新出现卖出信号 (旧持有/买入 → 新卖出)
（无）

## 🟢 新出现买入信号 (旧持有/卖出 → 新买入)
- **雷科防务(002413)**: 旧[ema_cross 持有, bollinger 持有] → 新[ema_cross 持有, bollinger 🟢买入]
- **科华数据(002335)**: 旧[ema_cross 持有, ema_obv 持有] → 新[ema_obv 持有, macd 🟢买入]
- **巨化股份(600160)**: 旧[macd 持有, ema_cross 持有] → 新[ema_cross 持有, macd 🟢买入]

## 🔄 策略切换但信号一致 (均持有)
- 亿纬锂能(300014): bollinger,ema_obv → bollinger,macd
- 中信建投(601066): macd,bollinger → macd,ema_cross
- 汇川技术(300124): ema_cross,bollinger → macd,bollinger
- 中金公司(601995): macd,ema_cross → kdj_cci,macd
- 通富微电(002156): ema_obv,ema_cross → macd,ema_cross
- 恒生电子(600570): bollinger,ema_cross → bollinger,macd
- 福莱蒽特(605566): macd,ema_cross → macd,kdj_cci
- 福莱特(601865): ema_obv,kdj_cci → macd,kdj_cci
- 福耀玻璃(600660): macd,bollinger → macd,ema_cross
- 恒立液压(601100): ema_cross,kdj_cci → ema_cross,bollinger
- 中信特钢(000708): ema_cross,kdj_cci → ema_cross,macd

---
📊 差异榜摘要：**反转 0 / 新买 3 / 新卖 0 / 策略切换 11 / 无差异 15**

> ⚠️ 注：`adaptive_params_prev.json` 当前缺省，本轮为「validation 基线 → 调参后」首轮对照。下次 weekly cron 运行时（步骤2 会先从 `adaptive_params.json` 备份出 prev），将形成真实的「调前 vs 调后」连续对照。
