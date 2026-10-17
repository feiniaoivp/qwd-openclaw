# TradingAgents-CN-studio 借鉴分析（2026-10-17）

> 来源: https://github.com/frank-quant/TradingAgents-CN-studio（用户指定 URL，直接检视，clone 至 /tmp/ta-cn）
> 定位: TradingAgents-CN（hsliuping，多智能体 A 股分析平台）的**外围增强套件**，3320 行 Python，MIT，零侵入只读集成。

## 仓库四个模块

| 模块 | 解决的问题 | 实现要点 |
|---|---|---|
| digest | 15万字报告读不完 | LLM 提炼成 180~240 字四段式简报（结论/信号/风险/动作），HARD_LIMIT=400 字失控重试 |
| notify | 跑完不吭声 | 注册式多渠道（飞书/钉钉/企微/TG/webhook），`type#别名` 多群，cron 管道常驻 |
| compare | 换模型麻烦 | 同票同深度 N 模型并跑，采集 耗时/token/成本/决策 硬指标出对比表 |
| replay | 辩论没法看 | `SPEAKER_RE` 按说话人前缀切轮次，LLM 话题配对多空对垒，单文件 HTML 回放 |

## 值得抄的实现模式（代码级）

1. **digest/condenser.py 重试阶梯**: attempt1 收紧 max_tokens(→800) 治输出超长；attempt2 加倍(→8192) 治推理模型思考吃满 token 返回空。对应我们 cron agentTurn 空输出/超时老坑。
2. **digest/templates.py 四段式 prompt**: 硬性字数+段落格式+"只用报告里的事实，不编造数字"——与我们 pre_response_guard 数字红线天然兼容。
3. **core/textutil.py**: `ast.literal_eval` 清洗 dict 转储 + 迭代 `_unescape`（\\n 双重转义）。补强我们 10-06"成对标记解析"教训的工具箱。
4. **compare/metrics.py 决策提取**: 只看报告末尾 3000 字符 + 词表正则（买入|看多→看多 等），避免被中间讨论带偏。
5. **replay/debate.py 多空轮次解析**: `^(Bull|Bear|Risky|Safe|Neutral)\s+Analyst:\s*` 切轮次，history 缺失时用双方各自 history 拼回。

## 落实清单（对照本地系统）

- **P0-A 盘后简报四段式**: `analysis/digest_brief.py`，输入当日 `_dual.md`/trade_plan，LLM 压缩 200 字，接 15:30 推送。LLM 调用走 isolated agentTurn（08-10 教训：cron 沙箱内脚本直连 LLM 不稳）。
- **P0-B 信号股多空对垒**: 对当日产生信号的 3-5 只票，一条 prompt 生成 多头/空头/裁决，并入日报。TradingAgents 核心思想（对抗性辩论）的轻量落地，成本 ~1%。
- **P1-C 周末多模型对比实验**: 同一批收盘数据 N 模型各出交易计划，按 compare 口径（决策/置信度/耗时）对比，校准模型链选型。
- **P1-D textutil 清洗函数**: 收进本地解析工具，增强 LLM 输出解析健壮性。
- **P2 不做**: 整套部署 TradingAgents-CN（重、贵、与我们数据/回测/风控层重复）；飞书/钉钉多渠道（用户在 TG）；replay HTML 回放页。

## 红线一致性

- digest 只压缩不改数字；简报内数字必须可溯源到当日报告 → 发送前过 pre_response_guard。
- 新 LLM 环节禁用脚本内直连 API（本机无 LLM key，走 OpenClaw agentTurn）。
