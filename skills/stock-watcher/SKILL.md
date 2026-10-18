---
name: stock-watcher
description: Manage and monitor a personal stock watchlist with support for adding, removing, listing stocks, and summarizing their recent performance using real-time quotes from hq.sinajs.cn (新浪行情). Use when the user wants to track specific stocks, get performance summaries, or manage their watchlist.
---

# Stock Watcher Skill

This skill provides comprehensive stock watchlist management capabilities, allowing users to track their favorite stocks and get performance summaries using real-time data from 新浪行情 (hq.sinajs.cn).

## 自选股行情查看

当你要求查看自选股行情时，系统会直接显示以下信息：
- 每只股票的代码和名称
- 近期表现指标（涨跌幅等关键数据）
- 详细信息链接（可点击查看）

无需额外命令，直接为你呈现简洁明了的行情概览。

## 管理自选股

### 添加股票
使用股票代码（6位数字）添加到自选股：
- 例如：添加 600053 九鼎投资

### 删除股票  
通过股票代码删除自选股：
- 例如：删除 600053

### 查看自选股列表
显示当前所有自选股的完整列表

### 清空自选股列表
完全清空所有自选股

## 数据来源

主要使用**新浪实时行情 (hq.sinajs.cn)** 作为数据源（2026-10-11 起，替代同花顺静态页抓取，commit c98be34）：
- **行情接口**: `https://hq.sinajs.cn/list={sh|sz|bj}{stock_code}`，需带请求头 `Referer: https://finance.sina.com.cn`
- **市场前缀**: 6/8 开头→sh，0/3 开头→sz；其余（4/8 开头北交所）返回 bj 占位——hq.sinajs.cn 不支持北交所，宁可显式报错也不猜市场
- 提供收盘价、涨跌幅、数据日期等实时行情字段
- 输出头部统一标注 `(ok/N 只) 数据日期: … 来源: sina_hq hq.sinajs.cn`

## 自选股管理

### 文件格式
自选股存储在 `~/.clawdbot/stock_watcher/watchlist.txt`：
```
600053|九鼎投资
600018|上港集团
688785|恒运昌
```

### 支持操作
1. **添加股票**: 验证股票代码格式并添加到自选股
2. **删除股票**: 按股票代码精确匹配删除
3. **查看列表**: 显示当前自选股
4. **清空列表**: 完全清空自选股
5. **行情总结**: 获取所有股票的最新数据并提供简洁摘要

## 行情摘要特点

- 直接显示关键行情指标，无冗余信息
- 输出头部带数据日期与来源标注，便于核验
- 自动处理网络错误和数据异常
- 合理控制请求频率（每秒1次）

## 注意事项

- **股票代码格式**: 使用6位数字代码（如 `600053`）
- **数据延迟**: 行情可能有1-3分钟延迟
- **网络依赖**: 需要网络连接获取实时数据
- **市场范围**: 沪市主板/科创板、深市主板/创业板（北交所 hq.sinajs.cn 不支持，会显式失败）

## 安装与卸载

### 安装
运行 `scripts/install.sh` 脚本自动创建必要的目录结构。

### 卸载  
运行 `scripts/uninstall.sh` 脚本完全移除所有相关文件。

## 脚本说明

所有脚本都使用统一的配置文件 `config.py` 来管理存储路径，确保路径一致性：
- `add_stock.py` - 添加股票到自选股
- `remove_stock.py` - 从自选股删除股票
- `list_stocks.py` - 列出所有自选股
- `clear_watchlist.py` - 清空自选股列表
- `summarize_performance.py` - 获取股票行情摘要