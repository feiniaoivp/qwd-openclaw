# RSSHub + blogwatcher 完整部署文档

> 生成时间：2026-11-02
> 状态：✅ 全部落地，生产可用

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    RSSHub (localhost:1200)                   │
│  launchd 托管 • 自动重启 • 端口冲突自愈                      │
├─────────────────────────────────────────────────────────────┤
│  /cls/depth           → 财联社深度                            │
│  /caixin/article      → 财新网                                │
│  /jin10/important     → 金十数据重要快讯 (自写路由)           │
│  /eastmoney/report/   → 东财 4 大研报分类                     │
│    ├── strategyreport → 策略研报                              │
│    ├── macresearch    → 宏观研报                              │
│    ├── brokerreport   → 券商晨报                              │
│    └── industry       → 行业研报                              │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    blogwatcher (RSS 聚合)                     │
│  13 个订阅源 • 统一扫描 • 去重 • 噪声清理 • 摘要推送          │
├─────────────────────────────────────────────────────────────┤
│  RSSHub 源 (7)          │  直连 RSS 源 (6)                   │
│  ─────────────────      │  ─────────────────                │
│  财联社深度             │  A股动态 (Google News)             │
│  财新网                 │  FT中文网镜像 (Google News)        │
│  金十数据快讯           │  中国财经证券 (Google News)        │
│  东财策略研报           │  中国新闻网财经                    │
│  东财宏观研报           │  中国新闻网要闻                    │
│  东财券商晨报           │  博客园新闻                        │
│  东财行业研报           │                                   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              blogwatcher_daily_scan.py (每日摘要)             │
│  • 06:30 定时执行                                            │
│  • 关注池命中 (36 只 WATCHLIST)                               │
│  • 宏观要点 (换锚/逆回购/立案/回购贷款等 15+ 关键词)         │
│  • 噪声清理 (空标题/导航页/栏目页/早报/晚报/快讯/直播)        │
│  • 去重 + Telegram 推送 (含代理兜底)                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 核心组件清单

### 2.1 RSSHub (launchd 服务)

| 文件 | 路径 | 说明 |
|------|------|------|
| LaunchAgent | `~/Library/LaunchAgents/com.duguke.rsshub.plist` | 开机自启、崩溃重启、端口冲突自愈 |
| 启动脚本 | `/usr/local/lib/node_modules/rsshub/start_rsshub.sh` | 杀占用进程 → 等待端口释放 → 启动 |
| 日志目录 | `~/Library/Logs/rsshub/` | `rsshub.log` / `rsshub.error.log` |
| 工作目录 | `/usr/local/lib/node_modules/rsshub/` | npm 全局安装目录 |

**关键路由 (7 条)**：
```
GET /cls/depth              → 财联社深度 (官方 API)
GET /caixin/article         → 财新网首页
GET /jin10/important        → 金十数据重要快讯 (自写，含 JSONP 解析 + x-app-id header)
GET /eastmoney/report/strategyreport  → 东财策略研报 (JSONP 解析)
GET /eastmoney/report/macresearch     → 东财宏观研报
GET /eastmoney/report/brokerreport    → 东财券商晨报
GET /eastmoney/report/industry        → 东财行业研报
```

### 2.2 blogwatcher 订阅源 (13 个)

| # | 名称 | Feed URL | 类型 | 备注 |
|---|------|----------|------|------|
| 1 | A股动态 | Google News 搜索 | RSS | 7天窗口 |
| 2 | FT中文网镜像 | Google News 搜索 | RSS | 7天窗口 |
| 3 | 中国财经证券 | Google News 搜索 | RSS | - |
| 4 | 博客园新闻 | https://feed.cnblogs.com/news/rss | RSS | 直连 |
| 5 | 中国新闻网要闻 | https://www.chinanews.com.cn/rss/importnews.xml | RSS | 直连 |
| 6 | 中国新闻网财经 | https://www.chinanews.com.cn/rss/finance.xml | RSS | 直连 |
| 7 | 财联社深度 | http://localhost:1200/cls/depth | RSSHub | - |
| 8 | 财新网 | http://localhost:1200/caixin/article | RSSHub | - |
| 9 | 金十数据快讯 | http://localhost:1200/jin10/important | RSSHub | 自写路由 |
| 10 | 东财策略研报 | http://localhost:1200/eastmoney/report/strategyreport | RSSHub | JSONP |
| 11 | 东财宏观研报 | http://localhost:1200/eastmoney/report/macresearch | RSSHub | JSONP |
| 12 | 东财券商晨报 | http://localhost:1200/eastmoney/report/brokerreport | RSSHub | JSONP |
| 13 | 东财行业研报 | http://localhost:1200/eastmoney/report/industry | RSSHub | JSONP |

### 2.3 关键脚本

| 脚本 | 路径 | 功能 | 调度 |
|------|------|------|------|
| `blogwatcher_daily_scan.py` | `scripts/blogwatcher_daily_scan.py` | 每日扫描→去重→噪声清理→关注池/宏观摘要→Telegram推送 | 每日 06:30 |
| `rsshub_health_check.py` | `scripts/rsshub_health_check.py` | 检查 7 条核心路由 HTTP 200 + XML 格式 | 每 30 分钟 |
| `send_telegram.py` | workspace 根目录 | Telegram 推送 (含 127.0.0.1:7890 代理兜底) | 被调用 |

---

## 3. 定时任务 (Cron Jobs)

| Job ID | 名称 | 表达式 | 说明 |
|--------|------|--------|------|
| `d77fad30-...` | blogwatcher每日扫描推送 | `30 6 * * *` (Asia/Shanghai) | 每日 06:30 执行完整扫描+摘要推送 |
| `539cc4df-...` | RSSHub 健康检查 | `*/30 * * * *` | 每 30 分钟检查 7 条核心路由 |
| `5fd4a3c1-...` | blogwatcher 扫描健康检查 | `5 * * * *` | 每小时第 5 分检查扫描是否报错 |

**手动触发**：
```bash
# 立即执行每日扫描
cron run --jobId d77fad30-... --runMode force

# 立即执行健康检查
cron run --jobId 539cc4df-... --runMode force
```

---

## 4. 关键配置文件

### 4.1 RSSHub 启动脚本 (`/usr/local/lib/node_modules/rsshub/start_rsshub.sh`)
```bash
#!/bin/bash
PORT=1200
RSSHUB_DIR="/usr/local/lib/node_modules/rsshub"
LOG_FILE="/Users/duguke/Library/Logs/rsshub/rsshub.log"

# 强制释放端口
lsof -ti:$PORT | xargs -r kill -9 2>/dev/null
for i in {1..10}; do
    if ! lsof -ti:$PORT >/dev/null 2>&1; then break; fi
    sleep 0.5
done

cd "$RSSHUB_DIR"
exec /usr/local/opt/node/bin/node lib/index.js >> "$LOG_FILE" 2>&1
```

### 4.2 LaunchAgent (`~/Library/LaunchAgents/com.duguke.rsshub.plist`)
- `RunAtLoad: true`
- `KeepAlive: true`
- `ThrottleInterval: 30` (秒，防止疯狂重启)
- 环境变量：`PUPPETEER_SKIP_DOWNLOAD=1`, `NODE_ENV=production`

### 4.3 东财研报路由 (JSONP 处理)
所有 4 个路由统一模式：
```javascript
const response = await got({...});
const jsonMatch = response.data.match(/^datatable\((.*)\)$/);
const data = JSON.parse(jsonMatch[1]);
const list = data.data || [];
```

---

## 5. blogwatcher_daily_scan.py 核心逻辑

### 噪声清理规则 (`JUNK_EXACT` + `JUNK_CONTAINS`)
```python
JUNK_EXACT = {"-", "E", "R", ""}
JUNK_CONTAINS = (
    "每日一詞", "每日一词", "一周新闻小测", "双语电台", "偏好设置",
    "高端订阅", "标准订阅", "文章档案", "双语阅读", "一周热门文章",
    "精选财经资讯", "新闻联播", "早报", "晚报", "快讯：", "直播：",
)
NAV_TITLE_RE = re.compile(r"^(FT ?中文网|FT 金融时报)\s*-\s*(FT Chinese|Financial Times Chinese)$")
```

### 宏观关键词 (15+)
```python
MACRO_KWS = ("央行", "证监会", "降准", "降息", "IPO", "回购", "吸并", "换股",
             "减持", "万亿", "北向", "油价", "美联储", "关税", "稀土",
             "换锚", "逆回购", "MLF", "LPR", "存款准备金", "回购贷款",
             "立案", "财务造假", "三季报", "分红", "增持", "注销式回购")
```

### 关注池 (WATCHLIST SSOT)
从 `analysis/fib_extension_scan.py` 导入 36 只股票名称。

### 输出格式
```
📰 blogwatcher 每日扫描 2026-11-02
未读 1289 条 | 清噪声 9 | 去重后 1280 条

【关注池命中 15】
• 中信建投 | 2026-11-02 | 【券商聚焦】中信建投：A股中期底部...

【宏观要点 147】
• 2026-11-02 | 债市早参|央行政策利率"换锚"落地...
• 2026-11-02 | 奥精医疗涉嫌财务造假被证监会立案...

其余 991 条见 blogwatcher articles
```

---

## 6. 常用运维命令

### RSSHub 服务
```bash
# 重启服务
launchctl unload ~/Library/LaunchAgents/com.duguke.rsshub.plist
launchctl load ~/Library/LaunchAgents/com.duguke.rsshub.plist

# 查看日志
tail -f ~/Library/Logs/rsshub/rsshub.log
tail -f ~/Library/Logs/rsshub/rsshub.error.log

# 检查进程
lsof -i :1200
```

### blogwatcher 操作
```bash
# 扫描所有源
blogwatcher scan

# 查看未读文章
blogwatcher articles
blogwatcher articles --blog "财联社深度"

# 标记已读
blogwatcher read <id>
blogwatcher read-all

# 添加/删除源
blogwatcher add "名称" "主页URL" --feed-url "FeedURL"
blogwatcher remove "名称"
```

### 手动执行脚本
```bash
# 每日扫描 (dry-run 先验证)
python3 scripts/blogwatcher_daily_scan.py --dry-run
python3 scripts/blogwatcher_daily_scan.py

# 健康检查
python3 scripts/rsshub_health_check.py
```

### Cron 管理
```bash
# 查看所有任务
cron list

# 查看单个任务详情
cron get --jobId <id>

# 手动触发
cron run --jobId <id> --runMode force

# 查看运行历史
cron runs --jobId <id>
```

---

## 7. 故障排查速查

| 现象 | 排查步骤 |
|------|----------|
| RSSHub 端口占用 | `lsof -i :1200` → `pkill -9 -f "node lib/index.js"` → 重启 launchctl |
| blogwatcher 连接拒绝 | 检查 RSSHub 进程是否存活 → `curl http://localhost:1200/cls/depth` |
| Telegram 推送失败 | 检查 `send_telegram.py` 代理兜底 (127.0.0.1:7890) |
| 东财路由返回空 | API 返回 JSONP 格式变化 → 检查 `datatable(...)` 正则 |
| Cron 任务不执行 | `cron get --jobId` 确认 `enabled: true` / `nextRunAtMs` 合理 |

---

## 8. 扩展指南

### 新增 RSSHub 路由
1. 在 `/usr/local/lib/node_modules/rsshub/lib/routes/` 创建目录
2. 写 `index.js` (handler) + `router.js` (导出 router 实例)
3. 无需重启 RSSHub (热加载)，或重启 launchctl 保险

### 新增 blogwatcher 源
```bash
blogwatcher add "名称" "主页URL" --feed-url "FeedURL"
```

### 调整噪声/宏观规则
编辑 `scripts/blogwatcher_daily_scan.py` 中的 `JUNK_CONTAINS` / `MACRO_KWS`。

---

## 9. 文件清单

```
/Users/duguke/.openclaw/workspace/
├── scripts/
│   ├── blogwatcher_daily_scan.py      # 每日扫描主脚本
│   ├── rsshub_health_check.py         # RSSHub 健康检查
│   └── send_telegram.py               # Telegram 推送 (含代理)
├── logs/
│   ├── blogwatcher_scan.log           # 每日扫描日志
│   └── rsshub_health.log              # 健康检查日志
├── analysis/
│   └── fib_extension_scan.py          # WATCHLIST SSOT
├── RSSHUB_BLOGWATCHER_DEPLOYMENT.md   # 本文档
└── ...
```

---

## 10. 验证清单 (部署后必跑)

- [ ] `launchctl list | grep rsshub` → 显示 PID
- [ ] `lsof -i :1200` → 仅 1 个 node 进程监听
- [ ] `curl -s http://localhost:1200/cls/depth | head -5` → 返回 XML
- [ ] `blogwatcher scan` → 13 源全部显示 `Source: RSS | Found: xx | New: xx`
- [ ] `python3 scripts/blogwatcher_daily_scan.py --dry-run` → 输出摘要无报错
- [ ] `python3 scripts/rsshub_health_check.py` → 7/7 ✅
- [ ] `cron list` → 3 个任务均 `enabled: true`
- [ ] 手动触发每日扫描 → Telegram 收到推送
- [ ] `blogwatcher articles` → 显示未读文章

---

**文档版本**：v1.0 (2026-11-02)
**维护者**：OpenClaw Assistant
**下次审查**：2026-11-09 或重大变更时
