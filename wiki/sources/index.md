# Wiki Sources 知识库索引

> 自动生成于 2026-08-30 | 基于 Obsidian Vault 34 篇笔记归纳

---

## 📚 知识库文件清单

| 文件 | 核心领域 | 关键词 | 版本 |
|------|----------|--------|------|
| [technical-analysis-core.md](technical-analysis-core.md) | **技术分析核心** | 指标白名单/支撑压力/成交量/均线/MACD/ATR/斐波那契/EMA组合/交易系统/close_scan重构指导 | v1.0 |
| [value-investing-framework.md](value-investing-framework.md) | **价值投资与商业模式** | 护城河五基因/竞争vs垄断/段永平复利/7级投资天梯/牛散铁律/估值方法 | v1.0 |
| [macro-geopolitical-framework.md](macro-geopolitical-framework.md) | **宏观经济与地缘货币** | 人民币汇率四维模型/债务周期终局/科技巨头Capex传导/低信任社会陷阱 | v1.0 |
| [psychology-decision-framework.md](psychology-decision-framework.md) | **投资心理与决策** | 反脆弱/杠铃策略/蒂尔反竞争/认知层级/决策清单/Skin in the Game | v1.0 |

---

## 🔗 核心概念交叉引用

```
技术分析白名单
    │
    ├── 支撑压力 + 成交量 + 均线  →  三大原子指标
    │
    ├── MACD(零轴分多空) + ATR(仅风控) + 斐波那契(共振)  →  核心辅助
    │
    └── 移除: RSI/KDJ/CCI/布林带/其他震荡类
            │
            ▼
    价值投资验证
            │
            ├── 护城河评分 ≥ 60 分  →  确认基本面
            ├── 7不买/3不卖  →  纪律过滤
            └── 安全边际 6-7折  →  估值确认
            │
            ▼
    宏观环境背景
            │
            ├── 汇率/债务/利差  →  大类资产配置
            ├── 科技Capex传导  →  板块轮动机会
            └── 低信任社会  →  认知陷阱预警
            │
            ▼
    心理决策执行
            │
            ├── 杠铃策略(90/10)  →  组合结构
            ├── 非对称性检查  →  单笔决策
            ├── 认知圈内操作  →  避免超圈
            └── Skin in the Game  →  利益绑定验证
```

---

## 🎯 快速导航

### 按任务查找
| 任务 | 直接阅读 |
|------|----------|
| 写扫描脚本/信号生成 | `technical-analysis-core.md` §5 |
| 选股/估值/卖出决策 | `value-investing-framework.md` §3-5 |
| 资产配置/板块轮动 | `macro-geopolitical-framework.md` §1,3 |
| 克服心理偏差/执行纪律 | `psychology-decision-framework.md` §5 |

### 按角色查找
| 角色 | 核心文件 |
|------|----------|
| **量化工程师** | technical-analysis-core.md |
| **基本面分析师** | value-investing-framework.md |
| **宏观策略师** | macro-geopolitical-framework.md |
| **风控/决策官** | psychology-decision-framework.md |

---

## 📝 维护规范

1. **单一事实来源**：核心结论只在对应 `.md` 中维护，不在多处重复
2. **版本化**：重大修改更新文件头版本号 + 此索引表
3. **交叉引用**：用相对链接 `[]()` 引用本库内其他文件
4. **来源溯源**：每条结论保留「来源笔记」字段，便于回溯原始素材

---

## 🔄 更新日志

| 日期 | 操作 | 涉及文件 |
|------|------|----------|
| 2026-08-30 | 初始化建库：4大核心文件 + 索引 | 全部 |