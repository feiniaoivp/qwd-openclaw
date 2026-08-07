# 文件整理工作流模板

> 配置了 Filesystem MCP 后可用的日常 Prompt 模板。
> 授权路径：`/Users/duguke/.openclaw/workspace` `/Users/duguke/Downloads` `/Users/duguke/Projects`

---

## 📁 场景一：Downloads 自动分类与归档

```text
请扫描我的 /Users/duguke/Downloads 目录：

找出 7 天以前下载的所有文件。

将 PDF 报告移动到 Downloads/PDFs/ 目录；
将图片移动到 Downloads/Images/ 目录；
将 ZIP/RAR 压缩包移动到 Downloads/Archives/ 目录；
将 APK/DMG/IMG 安装包移动到 Downloads/Installers/ 目录。

列出清理和移动的日志保存到工作区的 cleanup_log.txt。
```

**变体：按月份归档**
```text
扫描我的 Downloads 目录，将 30 天前下载的文件按年月归类到 Downloads/Archive/YYYY-MM/ 子目录中。
```

---

## 📊 场景二：多文件数据提取与汇总

```text
读取 /Users/duguke/Downloads 目录下最新的几个 CSV/Excel 文件，
提取其中的关键数据（如列名、数据行数和核心指标），
在工作区生成一份 data_analysis.md 格式的汇总报告。
```

**变体：合并多个表格**
```text
我在 Downloads 目录下有多个季度报表 CSV，
读取所有以 "report_" 开头的 CSV 文件，合并为一个统一表格，
保存为 workspace/data/merged_report.csv。
```

**变体：关键数据提取**
```text
读取 Downloads/ 下的所有 .csv 和 .xlsx 文件，
汇总每个文件的列头、行数、数值列的最大/最小/平均值，
以表格形式输出到工作区的 data_summary.md。
```

---

## 🔍 场景三：文档重命名与去重

```text
检查工作区下的所有 .md 格式文档，找出标题或内容高度重复的文件，
帮我整合为一个统一文档，并把原文件归档到 /archive 文件夹。
```

**变体：批量重命名**
```text
扫描工作区 analysis/ 目录下的所有 CSV 文件，
按 "策略名称_日期.csv" 的格式批量重命名（从文件内容/修改时间推断），
列出重命名前后的对照表。
```

**变体：清理临时文件**
```text
扫描工作区根目录，找出所有 .tmp .log .bak 和 ~结尾的临时文件，
列出清单确认后清理到 Trash。
```

---

## 🛠 快捷诊断命令

```bash
# 检查 MCP 连接状态
openclaw mcp doctor --probe

# 热重载 MCP 配置
openclaw mcp reload

# 查看完整 MCP 状态
openclaw mcp status --verbose
```

---

*最后更新: 2026-07-30*
