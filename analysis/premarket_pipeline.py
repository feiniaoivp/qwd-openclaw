#!/usr/bin/env python3
"""
盘前分析 Pipeline 编排器
==========================
将原本单体的 premarket_report.py 拆分为 5 个独立阶段：
  1. 新闻抓取 (daily_news_reader.py)
  2. 全市场扫描 (close_scan_v2.py --full)
  3. 双策略扫描 (adaptive_dual.py)
  4. AI 智能体研判 (agent/run_agent.py)
  5. 信号交叉审计 (signal_audit.py)

每阶段：
- 独立 subprocess，超时可配置
- 失败自动重试（指数退避）
- 关键阶段失败时读取上一次成功产出作为降级
- 产出 JSON 到 stdout（用标记包裹，便于解析）

最终汇总生成 Markdown 报告，并通过 send_telegram 自推送（绕过 gateway TLS 问题）。
"""

# ── sys.path 样板（TOOLS.md 强制；2026-09-19）──
# 脚本模式下 sys.path[0] 是脚本所在目录而非工作区根，
# 必须显式插入工作区根（供 `from analysis.xxx`）+ analysis/（供子包引用）。
import os
import sys
import shutil
_WORKSPACE = "/Users/duguke/.openclaw/workspace"
for _p in (_WORKSPACE, os.path.join(_WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import subprocess
import time
import re
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(_WORKSPACE)

# 网络兜底
try:
    from analysis.net_guard import install_default_timeout
    install_default_timeout()
except Exception:
    pass

# 交易日判断（复用 service.py）
from analysis.service import is_trading_day

# ══════════════════════════════════════
# 阶段配置
# ══════════════════════════════════════

STAGES = [
    {
        "name": "news",
        "script": "data/news/daily_news_reader.py",
        "args_tmpl": ["--date", "{date}", "--all-stock-newest", "--save"],
        "timeout": 120,
        "max_retry": 2,
        "required": False,  # 新闻失败不阻断
        "marker_start": "=====NEWS_RESULT=====",
        "marker_end": "=====NEWS_END=====",
        "fallback_file": "data/news/daily_{date}.md",
    },
    {
        "name": "scan",
        "script": "analysis/close_scan_v2.py",
        "args_tmpl": [],
        # 2026-10-07: baostock 慢时实测 ~405s（昨日 214s），240s 两次超时导致 scan 降级"无数据"→ 放宽到 600s
        "timeout": 600,
        "max_retry": 2,
        "required": True,
        "marker_start": "=====CLOSE_SCAN_RESULT=====",
        "marker_end": "=====CLOSE_SCAN_END=====",
        "fallback_file": None,
    },
    {
        "name": "dual",
        "script": "analysis/adaptive_dual.py",
        "args_tmpl": [],
        "timeout": 300,
        "max_retry": 1,
        "required": True,
        "marker_start": "=====DUAL_SCAN_RESULT=====",
        "marker_end": "=====DUAL_SCAN_END=====",
        "fallback_file": "data/adaptive_dual_last.json",
    },
    {
        "name": "agent",
        "script": "analysis/agent/run_agent.py",
        "args_tmpl": [],
        "timeout": 600,
        "max_retry": 1,
        "required": True,
        "marker_start": "=====AGENT_RESULT=====",
        "marker_end": "=====AGENT_END=====",
        "fallback_file": "data/agent_runs/last_success.json",
    },
    {
        "name": "audit",
        "script": "analysis/signal_audit.py",
        "args_tmpl": [],
        "timeout": 60,
        "max_retry": 1,
        "required": False,
        "marker_start": "=====SIGNAL_AUDIT_RESULT=====",
        "marker_end": "=====SIGNAL_AUDIT_END=====",
        "fallback_file": None,
    },
    {
        # 2026-10-26 新增：关注池 PPO/CCI 量化预筛（a-share-analyst Workflow C）
        # 数据日自动探测（盘前=上一交易日收盘），非关键阶段，失败不阻断主链。
        # fallback_file 仅文档口径（非 required 阶段不走降级分支），成功产出由
        # save_success_output 自动落 data/pipeline_cache/ppo_cci_last.json。
        "name": "ppo_cci",
        "script": "analysis/po_ppo_cci_screen.py",
        "args_tmpl": ["--json"],
        "timeout": 480,
        "max_retry": 1,
        "required": False,
        "marker_start": "=====PPO_CCI_RESULT=====",
        "marker_end": "=====PPO_CCI_END=====",
        "fallback_file": "data/pipeline_cache/ppo_cci_last.json",
    },
]

# 成功产出缓存目录
CACHE_DIR = WORKSPACE / "data" / "pipeline_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def parse_json_from_output(output: str, marker_start: str, marker_end: str):
    """从输出中提取 JSON（支持标记包裹）"""
    if marker_start and marker_end:
        start = output.find(marker_start)
        end = output.rfind(marker_end)
        if start != -1 and end != -1:
            json_str = output[start + len(marker_start):end].strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass
    # 尝试直接解析整个输出
    try:
        return json.loads(output.strip())
    except json.JSONDecodeError:
        return None


def load_fallback(fallback_path: str, date: str):
    """读取降级文件"""
    if not fallback_path:
        return None
    path = WORKSPACE / fallback_path.format(date=date)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _load_agent_fallback(date: str):
    """agent 阶段降级数据（2026-10-14 修复）：
    优先取 data/agent_runs/ 下**最新**的 run_*.json（当日 run 往往是好的，只是
    pipeline 侧超时/解析失败被判死），其次才 last_success.json。
    根因：last_success.json 停留在 2026-09-20，导致盘前报告连日静默输出
    24 天前的僵尸缓存（来伊份旧闻循环 + 「交易日:否」假标签）且无日期提示。
    返回 (data, note)；note 必须带缓存日期，禁止无日期静默降级。"""
    runs_dir = WORKSPACE / "data" / "agent_runs"
    try:
        candidates = sorted(runs_dir.glob("run_*.json"), reverse=True)
        for p in candidates:
            m = re.match(r"run_(\d{8})_\d{6}\.json", p.name)
            if not m:
                continue
            rd = m.group(1)
            run_date = f"{rd[:4]}-{rd[4:6]}-{rd[6:]}"
            if run_date > date:  # 防御：未来日期档案不用
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and (data.get("signals") or data.get("data_date")):
                if run_date == date:
                    return data, f"降级使用当日 run（{p.name}，数据日期 {data.get('data_date', '?')}）"
                return data, f"⚠️ 降级使用 {run_date} 的旧 run（{p.name}，非当日，时效性打折）"
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"⚠️ agent run 档案扫描失败: {e}\n")
    return None, None


def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


def save_success_output(name: str, data: dict, date: str):
    """保存成功产出供后续降级"""
    if name == "news":
        # 新闻已由自身保存 md，这里只缓存元信息
        return
    path = CACHE_DIR / f"{name}_last.json"
    try:
        _bak(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"date": date, "data": data}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def run_stage(stage: dict, date: str) -> dict:
    """运行单个阶段，返回 {ok, data, error, duration}"""
    script_path = WORKSPACE / stage["script"]
    if not script_path.exists():
        return {"ok": False, "error": f"脚本不存在: {script_path}", "data": None}

    args = [a.format(date=date) for a in stage["args_tmpl"]]
    cmd = [sys.executable, str(script_path)] + args
    timeout = stage["timeout"]
    max_retry = stage["max_retry"]
    marker_start = stage.get("marker_start")
    marker_end = stage.get("marker_end")

    for attempt in range(max_retry + 1):
        try:
            env = {**os.environ, "PYTHONPATH": str(WORKSPACE) + os.pathsep + os.environ.get("PYTHONPATH", "")}
            start = time.time()
            result = subprocess.run(
                cmd, cwd=WORKSPACE, capture_output=True, text=True, timeout=timeout, env=env
            )
            duration = time.time() - start
            
            if result.returncode == 0:
                # 提取 JSON
                data = parse_json_from_output(result.stdout, marker_start, marker_end) if marker_start else None
                if data is None and result.stdout.strip():
                    # 尝试直接解析
                    try:
                        data = json.loads(result.stdout.strip())
                    except Exception:
                        data = {"raw_output": result.stdout.strip()}
                
                return {"ok": True, "data": data, "error": None, "duration": duration, "stdout": result.stdout}
            else:
                raise RuntimeError(f"rc={result.returncode}: {result.stderr[-500:]}")
                
        except subprocess.TimeoutExpired:
            err = f"超时 ({timeout}s)"
        except Exception as e:
            err = f"异常: {e}"
        
        if attempt < max_retry:
            wait = 10 * (attempt + 1)
            time.sleep(wait)
        else:
            return {"ok": False, "error": f"重试耗尽: {err}", "data": None, "duration": None}

    return {"ok": False, "error": f"重试耗尽", "data": None, "duration": None}


def _try_scan_archive(date: str):
    """昨夜 18:30 收盘扫描全量档案（data/pipeline_cache/close_scan_{最近交易日}.json）。
    盘前 06:00 的 spot/K线与昨夜收盘扫描等价（都截至上一交易日收盘），直接复用可
    省一次全池重扫并消除盘前数据源抖动；无档案（昨夜 job 失败/首日）返回 None 走现扫。
    2026-10-11(#1) 新增。"""
    try:
        from datetime import timedelta
        from analysis.service import is_trading_day

        d = datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)
        expected = None
        for _ in range(10):
            ds = d.strftime("%Y-%m-%d")
            if is_trading_day(ds):
                expected = ds
                break
            d -= timedelta(days=1)
        if not expected:
            return None
        path = CACHE_DIR / f"close_scan_{expected}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("watchlist_scan"):
                return data
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"⚠️ scan 档案读取失败(转现扫): {e}\n")
    return None


def _unwrap_cached_data(data):
    """解包缓存数据：可能是直接的脚本输出，也可能是缓存包装器 {date, data}"""
    if not data:
        return None
    # 如果是缓存包装器格式
    if isinstance(data, dict) and "data" in data and len(data) <= 2:
        return data["data"]
    return data

def build_report(date: str, results: dict) -> str:
    """生成最终 Markdown 报告（复用 premarket_report.py 的格式化逻辑）"""
    # 先解包各阶段数据（run_stage 返回 {ok, data, ...}，data 是脚本完整输出）
    scan_data = _unwrap_cached_data(results.get("scan", {}).get("data"))
    dual_data = _unwrap_cached_data(results.get("dual", {}).get("data"))
    agent_data = _unwrap_cached_data(results.get("agent", {}).get("data"))
    audit_data = _unwrap_cached_data(results.get("audit", {}).get("data"))
    news_data = _unwrap_cached_data(results.get("news", {}).get("data"))
    
    # 这里内联必要的格式化函数，避免循环导入
    def fmt_market_overview(data):
        if not data:
            return "❌ 无数据"
        ov = data.get("market_overview", {})
        sm = data.get("summary", {})
        health = data.get("health_score", 6)
        return f"""| 指标 | 数值 |
|------|------|
| 上涨/下跌 | {ov.get('up', '?')} / {ov.get('down', '?')} |
| 总成交额 | {ov.get('total_amount_billion', '?')} 亿 |
| 数据日期 | {data.get('data_date', '?')} |
| 强烈买入 | {sm.get('strong_buy', 0)} 只 |
| 关注 | {sm.get('watch', 0)} 只 |
| 中性 | {sm.get('neutral', 0)} 只 |
| 谨慎 | {sm.get('caution', 0)} 只 |
| 强烈卖出 | {sm.get('strong_sell', 0)} 只 |
| **030健康度** | **{health}/10** |"""

    def fmt_dual_scan(data):
        if not data:
            return "❌ 无数据"
        lines = []
        lines.append(f"📊 **双策略最优扫描** ({data.get('date', '?')})")
        lines.append(f"覆盖 {data.get('stock_count', '?')} 只 | 每股跑 score 前 2 策略")
        stats = data.get("stats", {})
        lines.append(f"🟢双买 {stats.get('buy', 0)} | 🔴双卖 {stats.get('sell', 0)} | ⚪双持有 {stats.get('hold', 0)} | ⚡分歧 {stats.get('disagree', 0)} | ❌失败 {stats.get('error', 0)}")
        lines.append("")
        div = data.get("divergence_stocks", [])
        if not div:
            for r in data.get("details", []):
                if "strategies" in r and len(r["strategies"]) >= 2:
                    acts = [("买入" in x.get("action", "")) for x in r["strategies"]]
                    sells = [("卖出" in x.get("action", "")) for x in r["strategies"]]
                    if sum(acts) == 1 or sum(sells) == 1:
                        div.append(r)
        if div:
            lines.append("⚡ **双策略分歧（重点关注）**")
            lines.append("| 股票 | 策略1 | 策略2 | 现价 |")
            lines.append("|:---|:---|:---|:---:|")
            for d in div:
                s1, s2 = d["strategies"][0], d["strategies"][1]
                lbl1 = s1.get("strategy_label", "-")
                lbl2 = s2.get("strategy_label", "-")
                a1 = s1.get("action", "").split(" ")[0]
                a2 = s2.get("action", "").split(" ")[0]
                price = d.get("price", 0) or 0
                lines.append(f"| {d['name']}({d['symbol']}) | {lbl1} {a1} | {lbl2} {a2} | ¥{price:.2f} |")
            lines.append("")
        dist = data.get("strategy_usage", {})
        if dist:
            lines.append("📈 **策略使用分布**")
            label_map = {"bollinger": "📊 布林带+ATR", "kdj_cci": "🎯 KDJ+RSI", "ema_obv": "📈 EMA+OBV", "ema_cross": "💹 EMA12/26", "macd": "📉 纯MACD"}
            for k, v in sorted(dist.items(), key=lambda x: -x[1]):
                if v > 0:
                    lines.append(f"  {label_map.get(k, k)}: {v} 次")
        return "\n".join(lines)

    def fmt_agent_report(data):
        if not data:
            return "❌ 无数据"
        raw = data.get("raw_output", "")
        if raw:
            lines = ["🤖 **AI 智能体综合研判**"]
            # 解析关键字段
            for pattern, label in [
                (r'交易日状态[:：]\s*(\S+)', "交易日"),
                (r'数据日期[:：]\s*(\S+)', "数据日期"),
                (r'市场概览[:：]\s*([^\n]+)', "市场概览"),
                (r'信号分布[^\n]*[:：]\s*([^\n]+)', "信号分布"),
                (r'030健康度[:：]\s*(\S+)', "030健康度"),
                (r'健康度解读[:：]\s*([^\n]+)', "健康度解读"),
            ]:
                m = re.search(pattern, raw)
                if m:
                    lines.append(f"{label}: {m.group(1).strip()}")
            lines.append("")
            # 解析操作建议 JSON
            json_match = re.search(r'\[\s*\{.*?\}\s*\]', raw, re.DOTALL)
            if json_match:
                try:
                    actions = json.loads(json_match.group())
                    for act_type, label in [("BUY", "✅ 建议买入/加仓"), ("HOLD", "⏸ 建议持有/观望"), ("SELL", "🔴 建议卖出/减仓")]:
                        items = [a for a in actions if a.get("action") == act_type]
                        if items:
                            lines.append(f"{label} ({len(items)}只)")
                            for a in items[:10]:
                                lines.append(f"  - **{a['symbol']}**: {a['reason']}")
                            if len(items) > 10:
                                lines.append(f"  ... 及其他 {len(items)-10} 只")
                            lines.append("")
                except Exception:
                    pass
            # 中联重科
            zl_match = re.search(r'中联重科双策略:\s*(\{.*?\})', raw, re.DOTALL)
            if zl_match:
                try:
                    zl = json.loads(zl_match.group(1))
                    lines.append("🏭 **中联重科(000157) 双策略**")
                    pf = zl.get("portfolio", {})
                    lines.append(f"  日期: {zl.get('date')} | 价格: ¥{zl.get('price')} | 涨跌: {zl.get('change_pct', 0):+.2f}%")
                    sig1 = zl.get('signals', {}).get('strategy1', {}).get('desc', '?')
                    sig2 = zl.get('signals', {}).get('strategy2', {}).get('desc', '?')
                    lines.append(f"  策略1(MACD+RSI<50): {sig1}")
                    lines.append(f"  策略2(综合最优): {sig2}")
                    lines.append(f"  总资金: ¥{pf.get('total_value', 0):,.0f} | 总收益: {pf.get('total_return_pct', 0):.2f}%")
                except Exception:
                    pass
            return "\n".join(lines)
        # 直接使用结构化字段（agent cache 格式）
        lines = ["🤖 **AI 智能体综合研判**"]
        lines.append(f"交易日: {'是' if data.get('is_trading_day') else '否'} | 数据日期: {data.get('data_date', '?')}")
        
        # 市场概览
        ov = data.get('market_overview', {})
        if ov:
            lines.append(f"市场概览: 上涨{ov.get('up', '?')}只 / 下跌{ov.get('down', '?')}只 / 成交额{ov.get('total_amount_billion', '?')}亿")
        else:
            lines.append("市场概览: (无数据)")
        
        # 信号分布（从 signals 统计）
        signals = data.get('signals', [])
        strong_buy = watch = neutral = caution = strong_sell = fail = 0
        for s in signals:
            if "error" in s:
                fail += 1
                continue
            lv = str(s.get("signal", {}).get("level", ""))
            n = re.sub(r"[\ufffd\ufeff\s]", "", lv)
            if "强烈买入" in n:
                strong_buy += 1
            elif "关注" in n:
                watch += 1
            elif "强烈卖出" in n:
                strong_sell += 1
            elif "谨慎" in n:
                caution += 1
            else:
                neutral += 1
        lines.append(f"信号分布: 强烈买入{strong_buy} / 关注{watch} / 中性{neutral} / 谨慎{caution} / 强烈卖出{strong_sell} / 失败{fail}")
        
        hs = data.get('health_score')
        lines.append(f"030健康度: {hs if hs is not None else 'N/A'}/10")
        lines.append(f"健康度解读: {data.get('health_comment') or '(无)'}")
        _ns = data.get('news_source')
        lines.append(f"关注池要闻 [{_ns}]:" if _ns else "关注池要闻(近48h):")
        lines.append(str(data.get('news_summary') or '(无)'))
        lines.append(f"风险标记: {data.get('risk_flag') or '无'}")
        adv = data.get('final_advice')
        lines.append("操作建议(JSON):")
        lines.append(str(adv) if adv else "[]")
        lines.append("中联重科双策略:")
        lines.append(json.dumps(data.get('zhonglian') or {}, ensure_ascii=False, indent=2))
        tp = data.get('trader_picks')
        lines.append("操盘手选股:")
        lines.append(json.dumps(tp or [], ensure_ascii=False, indent=2))
        
        return "\n".join(lines)

    def fmt_signal_audit(data):
        if not data:
            return "❌ 无数据"
        lines = []
        lines.append("🔍 **信号交叉审计**")
        lines.append(f"发现 {data.get('total_findings', 0)} 项 | 🔴高危{data.get('high', 0)} 🟡中危{data.get('medium', 0)} 🟢低危{data.get('low', 0)} | 持仓 {data.get('positions_held', '?')} 只")
        findings = data.get("findings", [])
        if findings:
            for f in findings:
                level = f.get("severity", f.get("level", "?"))
                msg = f.get("msg", f.get("message", ""))
                tag = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(level, "⚪")
                lines.append(f"  {tag} [{level}] {msg}")
        else:
            lines.append("  ✅ 未发现信号冲突/持仓矛盾/数据异常")
        return "\n".join(lines)

    def fmt_news(date_str):
        news_file = WORKSPACE / "data" / "news" / f"daily_{date_str}.md"
        if not news_file.exists():
            return "📰 **当日新闻**: 暂无生成"
        content = news_file.read_text(encoding="utf-8")
        if len(content) > 2000:
            content = content[:2000] + "\n... (完整版见 daily_新闻文件)"
        return f"📰 **当日新闻摘要 ({date_str})**\n\n{content}"

    def fmt_ppo_cci(data, skipped=False):
        if skipped:
            return "ℹ️ 非交易日，PPO/CCI 预筛跳过（数据日同上一交易日，无增量）"
        if not data:
            return "❌ 无数据（ppo_cci 阶段失败，详见 pipeline 日志 stderr）"
        lines = []
        cov = data.get("coverage", {})
        lines.append(f"📈 **PPO/CCI 量化预筛** (数据日 {data.get('data_day', '?')} | 覆盖 {cov.get('ok', '?')}/{cov.get('total', '?')})")
        rows = data.get("rows", [])

        def _pick(tag, limit=5):
            got = [r for r in rows if tag in (r.get("tags") or [])]
            return "、".join(f"{r['name']}({r['cci']})" for r in got[:limit]) if got else "无"

        lines.append(f"🔴 超买(防回调): {_pick('超买⚠回调')}")
        lines.append(f"✅ PPO金叉: {_pick('PPO金叉✓')}")
        lines.append(f"🟢 超卖(观察反弹): {_pick('超卖(观察反弹)')}")
        lines.append(f"⚡ 量价异动: {_pick('🔴量价异动')}")
        bad = data.get("bad") or []
        if bad:
            lines.append("⚠️ 无数据/滞后: " + "、".join(f"{b['name']}({b.get('date', '?')})" for b in bad[:5]))
        return "\n".join(lines)

    # === 组装最终报告 ===
    md_parts = []
    md_parts.append(f"# 📊 A股每日盘前深度分析报告 | {date}")
    md_parts.append(f"> 生成时间: {datetime.now().strftime('%H:%M:%S')} | 数据基准: 前一交易日收盘")
    md_parts.append("")

    # 2026-09-29: 降级阶段显式标注在报告头部（不再静默用旧缓存冒充正常数据）
    degraded_in_report = [name for name, r in results.items()
                          if isinstance(r, dict) and r.get("fallback")]
    if degraded_in_report:
        md_parts.append(f"⚠️ **降级数据标注**: 以下阶段本次失败、使用历史缓存降级 → {', '.join(degraded_in_report)}（数据时效性打折，谨慎参考）")
        # 2026-10-14: 降级必须写明数据来源与缓存日期，禁止只标"降级"不标多旧
        for n in degraded_in_report:
            _note = results[n].get("fallback_note")
            if _note:
                md_parts.append(f"> - {n}: {_note}")
        md_parts.append("")

    md_parts.append("## 🎯 市场全景")
    md_parts.append(fmt_market_overview(scan_data))
    md_parts.append("")

    md_parts.append("## 🎯 双策略最优扫描")
    md_parts.append(fmt_dual_scan(dual_data))
    md_parts.append("")

    md_parts.append("## 🤖 AI 智能体综合研判")
    md_parts.append(fmt_agent_report(agent_data))
    md_parts.append("")

    md_parts.append("## 🔍 信号交叉审计")
    md_parts.append(fmt_signal_audit(audit_data))
    md_parts.append("")

    md_parts.append("## 📈 PPO/CCI 量化预筛")
    md_parts.append(fmt_ppo_cci(
        _unwrap_cached_data(results.get("ppo_cci", {}).get("data")),
        skipped=results.get("ppo_cci", {}).get("skipped_non_trading", False),
    ))
    md_parts.append("")

    md_parts.append("## 📰 盘前关键资讯")
    md_parts.append(fmt_news(date))
    md_parts.append("")

    md_parts.append("---")
    md_parts.append(f"*📁 详细数据文件: `analysis/daily/` `data/news/` | ⚙️ 由 `premarket_pipeline.py` 自动生成 | 耗时统计: { {k: v.get('duration') for k, v in results.items()} }*")

    return "\n".join(md_parts)


def main():
    date = datetime.now().strftime("%Y-%m-%d")

    # 1. 交易日判断：非交易日跳过 agent 阶段（避免 LLM 调用超时）
    is_trade_day = is_trading_day()
    if not is_trade_day:
        print(f"ℹ️ {date} 非交易日，跳过 agent 研判阶段")
    
    results = {}
    critical_failed = False
    degraded_stages = []  # 2026-09-29: 记录降级阶段，报告头部显式标注（不再静默用旧数据冒充正常）

    for stage in STAGES:
        name = stage["name"]
        
        # 非交易日跳过 agent 阶段（使用降级数据；2026-10-14: 优先最新 run 档案并标注日期）
        if not is_trade_day and name == "agent":
            fb_data, fb_note = _load_agent_fallback(date)
            if fb_data is None:
                fb_data = load_fallback(stage.get("fallback_file"), date)
                if fb_data is not None:
                    _d = fb_data.get("data_date") or fb_data.get("date") if isinstance(fb_data, dict) else None
                    fb_note = f"⚠️ 降级使用 last_success.json（缓存日期 {_d}，可能严重过时）"
            if fb_data is not None:
                results[name] = {"ok": True, "data": fb_data, "fallback": True, "error": "非交易日跳过", "fallback_note": fb_note}
                print(f"  ⏭️ {name}: 非交易日，使用降级数据 | {fb_note}")
            else:
                results[name] = {"ok": False, "data": None, "error": "非交易日且无降级数据"}
            continue

        # 非交易日跳过 ppo_cci 预筛（数据日与上一交易日相同，重跑无增量；2026-10-26 新增）
        if not is_trade_day and name == "ppo_cci":
            results[name] = {"ok": True, "data": None, "skipped_non_trading": True, "error": None}
            print(f"  ⏭️ {name}: 非交易日，跳过 PPO/CCI 预筛")
            continue
        
        # 2026-10-11(#1): scan 阶段优先消费昨夜 18:30 收盘扫描全量档案
        # （06:00 开盘前 spot/K线与昨夜扫描数据等价，省一次全池重扫+消抖；缺档转现扫）
        if name == "scan":
            archived = _try_scan_archive(date)
            if archived is not None:
                results[name] = {"ok": True, "data": archived, "duration": 0.0, "from_archive": True}
                try:
                    save_success_output(name, archived, date)
                except Exception as e:  # noqa: BLE001
                    sys.stderr.write(f"⚠️ scan 档案缓存写入失败: {e}\n")
                print(f"  📦 scan: 使用昨夜 18:30 档案（data_date={archived.get('data_date')}），跳过现扫")
                continue

        result = run_stage(stage, date)
        results[name] = result

        if result["ok"]:
            if result["data"]:
                save_success_output(name, result["data"], date)
            elif result.get("fallback"):
                # fallback 数据也保存
                save_success_output(name, result["data"], date)
        else:
            # 关键阶段失败 → 尝试降级
            if stage["required"]:
                critical_failed = True
                fb_data, fb_note = (None, None)
                if name == "agent":
                    # 2026-10-14: agent 失败先找最新 run 档案（run_agent 成功即落盘，
                    # pipeline 侧超时不代表数据不存在），last_success 仅兜底且必须带日期
                    fb_data, fb_note = _load_agent_fallback(date)
                if fb_data is None:
                    fb_data = load_fallback(stage.get("fallback_file"), date)
                    if fb_data is not None:
                        _d = fb_data.get("data_date") or fb_data.get("date") if isinstance(fb_data, dict) else None
                        fb_note = f"⚠️ 降级使用 last_success.json（缓存日期 {_d}，可能严重过时）"
                if fb_data is not None:
                    results[name] = {"ok": True, "data": fb_data, "fallback": True, "error": result["error"], "fallback_note": fb_note}
                    degraded_stages.append(f"{name}({result['error']})")
                    sys.stderr.write(f"⚠️ 阶段 {name} 失败已降级旧缓存: {result['error']} | {fb_note}\n")
                else:
                    # 发送关键阶段失败告警
                    from send_telegram import send_message
                    alert = f"🚨 盘前Pipeline关键阶段失败: {name}\n{result['error']}\n将尝试降级产出"
                    send_message(alert, parse_mode="")
            else:
                # 非关键阶段失败仅记录
                pass

    # 2026-09-29: return 1 仅当全部阶段失败（含降级后仍无产出）；
    # 部分降级时继续产出报告，但降级信息显式标注在报告头部。
    if degraded_stages:
        print(f"⚠️ 降级阶段: {', '.join(degraded_stages)}")
    if critical_failed and not any(r.get("ok") for r in results.values()):
        return 1

    # 生成报告
    final_report = build_report(date, results)
    # 2026-10-11(#1): scan 数据来源透明标注
    if results.get("scan", {}).get("from_archive"):
        final_report += ("\n\n---\n📦 scan 阶段数据来源: 昨夜 18:30 收盘扫描档案"
                         "（口径等价，跳过盘前现扫）\n")

    # 保存报告
    report_dir = WORKSPACE / "analysis" / "daily"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"{date}_premarket_report.md"
    report_file.write_text(final_report, encoding="utf-8")

    # 标准输出（被 cron capture）
    print(final_report)

    # 自推送到 Telegram（绕过 gateway TLS 问题）——在报告生成后，避免老年人贫血
    # Telegram 单条消息限制 4096 字符，超长需分片
    # 2026-09-29: 改为 send_with_retry()（3 次退避），推送失败静默只写 stderr 不 panic 的
    #             隐患是「报告生成了但推不出去」被当成成功。现改为：重试耗尽后写告警文件
    #             并输出明确 stderr，同时升级为可被 cron 感知的失败标记。
    push_fail = []
    try:
        from send_telegram import send_with_retry
        max_len = 4000
        if len(final_report) <= max_len:
            resp = send_with_retry(final_report, parse_mode="", max_retries=3, retry_delay=2)
            if not (isinstance(resp, dict) and resp.get("ok")):
                push_fail.append(resp)
        else:
            # 分片发送（每片独立重试，避免一片失败拖垮整条）
            chunks = [final_report[i:i+max_len] for i in range(0, len(final_report), max_len)]
            for idx, chunk in enumerate(chunks):
                header = f"[分片 {idx+1}/{len(chunks)}]\n" if idx == 0 else ""
                resp = send_with_retry(header + chunk, parse_mode="", max_retries=3, retry_delay=2)
                if not (isinstance(resp, dict) and resp.get("ok")):
                    push_fail.append(resp)
                time.sleep(0.5)  # 避免频率限制
    except Exception as e:
        push_fail.append({"error": str(e)})
        sys.stderr.write(f"Telegram 推送异常: {e}\n")
    
    # 推送失败处理：写告警文件 + 明确 stderr（不静默）
    if push_fail:
        alert = f"🚨 盘前 Pipeline 报告已生成但 Telegram 推送失败（{len(push_fail)} 个分片/fragment）\n"
        alert += f"报告: {report_file}\n"
        alert += f"错误: {push_fail[0]}\n"
        alert_path = report_dir / f"{date}_push_alert.md"
        alert_path.write_text(alert, encoding="utf-8")
        sys.stderr.write(alert)
        # 推送失败不阻断报告产出（报告已在磁盘），但打标记供 cron 感知
        print("[[PUSH_FAILED]]", file=sys.stderr)
        # 尝试用 **独立** 通道发告警给上层（此处仅写库，不额外网络调用防雪崩）

    return 0


if __name__ == "__main__":
    sys.exit(main())
