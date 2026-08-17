# 调参对照信号差异榜 — 2026-08-08

> 对比基准: 调前参数 `data/adaptive_params_prev.json` (8/4) vs 调后参数 `data/adaptive_params.json` (本次调优+修复)
> 生成: param_signal_diff.py | 最新行情

## 摘要
- 🔁 方向反转: **0**
- 🔴 新出现卖出信号: **0**
- 🟢 新出现买入信号: **0**
- 🔄 策略切换但信号一致(均持有): **0**
- 无差异: 27 | 错误: 1 (巨化股份600160 baostock数据拉取失败，未能对比)

## 结论
本次调参未在操作建议层面产生任何信号转变（无方向反转、无新增买卖信号、无已持有策略切换）。
实际调参涉及：通富微电(002156 macd→ema_cross)、天齐锂业(002466 参数调整)、中信证券(600030 参数调整)、巨化股份(600160 ema_cross→macd，本项因数据失败未对比)。

## JSON (PARAM_SIGNAL_DIFF)
```json
{
  "date": "2026-08-08",
  "prev_source": "adaptive_params_prev.json",
  "summary": {"reversal": 0, "new_sell": 0, "new_buy": 0, "switch_hold": 0, "no_diff": 27, "errors": 1},
  "reversal": [],
  "new_sell": [],
  "new_buy": [],
  "switch_hold": []
}
```
