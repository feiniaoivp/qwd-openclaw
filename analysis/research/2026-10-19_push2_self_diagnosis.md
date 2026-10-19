# push2 家族本体诊断（B）— 2026-10-19

脚本: `scripts/push2_self_diagnosis.py`（可重跑，`--quick` 模式 ~7s，日检销案探针）
产物: `2026-10-19_push2_self_diagnosis.json`

## 结论（一句话）

**push2 家族本体完全健康；被掐死的只有 fflow（资金流）专属路径 —— 服务端 path 级封禁，全网性，与本机无关。** 10-19 04:12「404 链路通」是裸根探针的误读（裸根 `/` 三 host 健康时也回 404）。

## 诊断矩阵关键证据

| 层 | 结果 | 证据 |
|---|---|---|
| DNS | ✅ 正常 | push2=117.184.38.132 / push2his=101.226.30.221 / push2delay=114.80.72.189，单 IP 干净解析，无污染特征（对比 FT 案 Facebook IP） |
| TCP/TLS/HTTP | ✅ 正常 | `/api/qt/stock/get`（基础行情）三 host 全 **200**（0.03s）；`/api/qt/ulist.np/get`（列表）三 host 全 **200** |
| 裸根 `/` | 404 = 常态 | 三 host 裸根全 404 —— 裸根探针无诊断价值，**「404 链路通」作废** |
| fflow 实时 `klt=1` | ❌ 三 host 全灭 | curl 56 Connection closed abruptly，0.02~0.03s 快速失败 |
| fflow 历史 `klt=101` | ❌ 三 host 全灭 | 同上 |
| 指纹轴 | 无效 | curl_cffi Chrome（生产口径）与裸 curl（curl 52 Empty reply）同灭 → 非 TLS 指纹问题 |
| 出口轴 | 无效 | 直连与 7890 代理同灭 → 非本机出口被针对 |
| 官方库对照 | ❌ 同灭 | 官方 akshare 1.18.64 `stock_individual_fund_flow`（同走 push2his daykline）同 URL 失败（requests 走 env 代理包装为 ProxyError，根因相同）→ **全网性退化，无新接口可迁** |
| 对照组 | ✅ | datacenter-web `/api/data/v1/get` 200（兜底链路健康）；hq.sinajs.cn 403 为缺 Referer 头的探针写法问题，与本案无关 |

## 对「销案」口径的修正

- 旧观察（10-17 三时段「主+delay 全灭」/ 10-18「三 host 全 000」）混合了 fflow 路径与裸根/连接级探针，**口径不统一**。
- 销案标准重定义：**连续 ≥2 日多时段，fflow 真实路径（klt=1 实时 + klt=101 daykline）返回 200 且 data 非空**。判定工具 = 本脚本（裸根/基础路径一律不计入）。
- 当前状态：**fflow path 级封禁持续，未恢复，未销案** —— 但属「已知且已完整兜底」的稳态：
  - 资金维：moneyflow_em 熔断器 + datacenter 兜底早已接线（10-09），生产不受影响；
  - chip 维：已改自算筹码（10-18 f397d9a），不依赖东财端点；
  - push2 本体（行情/列表）从未受损，无需任何恢复动作。

## 后续

1. `push2_self_diagnosis.py --quick` 纳入日检（可挂周六流水线观察项，非阻断）；
2. fflow 恢复的判据以本脚本输出为准，恢复后需回填核对 datacenter 半口径期数据一致性（10-16 dreaming 报告遗留观察项）。
