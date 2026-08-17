#!/usr/bin/env python3
"""
每日盘前深度分析报告生成器
整合：新闻阅读 + 全市场扫描 + 双策略扫描 + AI智能体研判 + 信号审计
输出：格式化 Markdown，供 Telegram 推送
"""

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

WORKSPACE = Path("/Users/duguke/.openclaw/workspace")


def run_script(script_path, args=None, capture=True, timeout=120):
    """运行脚本，返回 (success, stdout, stderr)"""
    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)
    try:
        result = subprocess.run(
            cmd, cwd=WORKSPACE, capture_output=capture, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"Timeout after {timeout}s"
    except Exception as e:
        return False, "", str(e)


def parse_json_from_output(output, marker_start=None, marker_end=None):
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


def parse_dual_scan_output(output):
    """解析 adaptive_dual.py 的输出，提取 JSON"""
    return parse_json_from_output(output, "=====DUAL_SCAN_RESULT=====", "=====DUAL_SCAN_END=====")


def parse_agent_output(output):
    """解析 AI 智能体输出，提取结构化信息"""
    return {"raw_output": output}


def parse_signal_audit_output(output):
    """解析信号审计输出"""
    return parse_json_from_output(output, "=====SIGNAL_AUDIT_RESULT=====", "=====SIGNAL_AUDIT_END=====")


def format_market_overview(data):
    """格式化市场概览"""
    if not data:
        return "❌ 无数据"
    ov = data.get("market_overview", {})
    summary = data.get("summary", {})
    health = data.get("health_score", 6)
    return f"""| 指标 | 数值 |
|------|------|
| 上涨/下跌 | {ov.get('up', '?')} / {ov.get('down', '?')} |
| 总成交额 | {ov.get('total_amount_billion', '?')} 亿 |
| 数据日期 | {data.get('data_date', '?')} |
| 强烈买入 | {summary.get('strong_buy', 0)} 只 |
| 关注 | {summary.get('watch', 0)} 只 |
| 中性 | {summary.get('neutral', 0)} 只 |
| 谨慎 | {summary.get('caution', 0)} 只 |
| 强烈卖出 | {summary.get('strong_sell', 0)} 只 |
| **030健康度** | **{health}/10** |"""


def format_dual_scan(data):
    """格式化双策略扫描"""
    if not data:
        return "❌ 无数据"

    lines = []
    lines.append(f"📊 **双策略最优扫描** ({data.get('date', '?')})")
    lines.append(f"覆盖 {data.get('stock_count', '?')} 只 | 每股跑 score 前 2 策略")
    stats = data.get("stats", {})
    lines.append(f"🟢双买 {stats.get('buy', 0)} | 🔴双卖 {stats.get('sell', 0)} | ⚪双持有 {stats.get('hold', 0)} | ⚡分歧 {stats.get('disagree', 0)} | ❌失败 {stats.get('error', 0)}")
    lines.append("")

    # 分歧表
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
            s1 = d["strategies"][0]
            s2 = d["strategies"][1]
            lbl1 = s1.get("strategy_label", "-")
            lbl2 = s2.get("strategy_label", "-")
            a1 = s1.get("action", "").split(" ")[0]
            a2 = s2.get("action", "").split(" ")[0]
            price = d.get("price", 0) or 0
            lines.append(f"| {d['name']}({d['symbol']}) | {lbl1} {a1} | {lbl2} {a2} | ¥{price:.2f} |")
        lines.append("")

    # 策略使用分布
    dist = data.get("strategy_usage", {})
    if dist:
        lines.append("📈 **策略使用分布**")
        label_map = {
            "bollinger": "📊 布林带+ATR",
            "kdj_cci": "🎯 KDJ+RSI",
            "ema_obv": "📈 EMA+OBV",
            "ema_cross": "💹 EMA12/26",
            "macd": "📉 纯MACD",
        }
        for k, v in sorted(dist.items(), key=lambda x: -x[1]):
            if v > 0:
                lines.append(f"  {label_map.get(k, k)}: {v} 次")

    return "\n".join(lines)


def format_agent_report(data):
    """格式化 AI 智能体报告"""
    if not data:
        return "❌ 无数据"

    raw = data.get("raw_output", "")
    if raw:
        lines = ["🤖 **AI 智能体综合研判**"]

        # 交易日状态
        m = re.search(r'交易日状态[:：]\s*(\S+)', raw)
        if m:
            lines.append(f"交易日: {m.group(1)} | ")

        # 数据日期
        m = re.search(r'数据日期[:：]\s*(\S+)', raw)
        if m:
            lines[-1] = lines[-1].rstrip() + f"数据日期: {m.group(1)}"

        # 市场概览
        m = re.search(r'市场概览[:：]\s*([^\n]+)', raw)
        if m:
            lines.append(f"市场概览: {m.group(1).strip()}")

        # 信号分布
        m = re.search(r'信号分布[^\n]*[:：]\s*([^\n]+)', raw)
        if m:
            lines.append(f"信号分布: {m.group(1).strip()}")

        # 030健康度
        m = re.search(r'030健康度[:：]\s*(\S+)', raw)
        health = m.group(1) if m else "6/10"
        lines.append(f"030健康度: {health}")

        # 健康度解读
        m = re.search(r'健康度解读[:：]\s*([^\n]+)', raw)
        if m:
            lines.append(f"健康度解读: {m.group(1).strip()}")

        lines.append("")

        # 解析操作建议 JSON
        json_match = re.search(r'\[\s*\{.*?\}\s*\]', raw, re.DOTALL)
        if json_match:
            try:
                actions = json.loads(json_match.group())
                buys = [a for a in actions if a.get("action") == "BUY"]
                holds = [a for a in actions if a.get("action") == "HOLD"]
                sells = [a for a in actions if a.get("action") == "SELL"]

                if buys:
                    lines.append(f"✅ **建议买入/加仓 ({len(buys)}只)**")
                    for a in buys:
                        lines.append(f"  - **{a['symbol']}**: {a['reason']}")
                    lines.append("")

                if holds:
                    lines.append(f"⏸ **建议持有/观望 ({len(holds)}只)**")
                    for a in holds[:10]:
                        lines.append(f"  - {a['symbol']}: {a['reason']}")
                    if len(holds) > 10:
                        lines.append(f"  ... 及其他 {len(holds)-10} 只")
                    lines.append("")

                if sells:
                    lines.append(f"🔴 **建议卖出/减仓 ({len(sells)}只)**")
                    for a in sells:
                        lines.append(f"  - **{a['symbol']}**: {a['reason']}")
                    lines.append("")
            except json.JSONDecodeError:
                pass

        # 中联重科
        zl_match = re.search(r'中联重科双策略:\s*(\{.*?\})', raw, re.DOTALL)
        if zl_match:
            try:
                zl = json.loads(zl_match.group(1))
                lines.append("🏭 **中联重科(000157) 双策略**")
                portfolio = zl.get("portfolio", {})
                lines.append(f"  日期: {zl.get('date')} | 价格: ¥{zl.get('price')} | 涨跌: {zl.get('change_pct', 0):+.2f}%")
                sig1 = zl.get('signals', {}).get('strategy1', {}).get('desc', '?')
                sig2 = zl.get('signals', {}).get('strategy2', {}).get('desc', '?')
                lines.append(f"  策略1(MACD+RSI<50): {sig1}")
                lines.append(f"  策略2(综合最优): {sig2}")
                lines.append(f"  总资金: ¥{portfolio.get('total_value', 0):,.0f} | 总收益: {portfolio.get('total_return_pct', 0):.2f}%")
            except:
                pass

        return "\n".join(lines)

    # 否则用结构化字段（备用）
    lines = []
    lines.append("🤖 **AI 智能体综合研判**")
    lines.append(f"交易日: {'是' if data.get('is_trading_day') else '否'} | 数据日期: {data.get('data_date', '?')}")
    lines.append(f"市场概览: 上涨{data.get('market_up', '?')}只 / 下跌{data.get('market_down', '?')}只 / 成交额{data.get('market_amount', '?')}亿")
    lines.append(f"信号分布: 强烈买入{data.get('strong_buy', 0)} / 关注{data.get('watch', 0)} / 中性{data.get('neutral', 0)} / 谨慎{data.get('caution', 0)} / 强烈卖出{data.get('strong_sell', 0)}")
    lines.append(f"030健康度: {data.get('health_score', 6)}/10")
    lines.append(f"健康度解读: {data.get('health_interpretation', '')}")
    lines.append("")

    actions = data.get("actions", [])
    if actions:
        buys = [a for a in actions if a.get("action") == "BUY"]
        holds = [a for a in actions if a.get("action") == "HOLD"]
        sells = [a for a in actions if a.get("action") == "SELL"]

        if buys:
            lines.append(f"✅ **建议买入/加仓 ({len(buys)}只)**")
            for a in buys:
                lines.append(f"  - **{a['symbol']}**: {a['reason']}")
            lines.append("")

        if holds:
            lines.append(f"⏸ **建议持有/观望 ({len(holds)}只)**")
            for a in holds[:10]:
                lines.append(f"  - {a['symbol']}: {a['reason']}")
            if len(holds) > 10:
                lines.append(f"  ... 及其他 {len(holds)-10} 只")
            lines.append("")

        if sells:
            lines.append(f"🔴 **建议卖出/减仓 ({len(sells)}只)**")
            for a in sells:
                lines.append(f"  - **{a['symbol']}**: {a['reason']}")
            lines.append("")

    zl = data.get("zhonglian", {})
    if zl:
        lines.append("🏭 **中联重科(000157) 双策略**")
        portfolio = zl.get("portfolio", {})
        lines.append(f"  日期: {zl.get('date')} | 价格: ¥{zl.get('price')} | 涨跌: {zl.get('change_pct', 0):+.2f}%")
        lines.append(f"  策略1(MACD+RSI<50): {zl.get('signals', {}).get('strategy1', {}).get('desc', '?')}")
        lines.append(f"  策略2(综合最优): {zl.get('signals', {}).get('strategy2', {}).get('desc', '?')}")
        lines.append(f"  总资金: ¥{portfolio.get('total_value', 0):,.0f} | 总收益: {portfolio.get('total_return_pct', 0):.2f}%")

    return "\n".join(lines)


def format_signal_audit(data):
    """格式化信号审计"""
    if not data:
        return "❌ 无数据"

    # 兼容两种字段名：新版用 severity/msg，旧版用 level/message
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


def format_news_summary(date_str):
    """读取并格式化当日新闻摘要"""
    news_file = WORKSPACE / "data" / "news" / f"daily_{date_str}.md"
    if not news_file.exists():
        return "📰 **当日新闻**: 暂无生成"

    content = news_file.read_text(encoding="utf-8")
    if len(content) > 1500:
        content = content[:1500] + "\n... (完整版见 daily_新闻文件)"
    return f"📰 **当日新闻摘要 ({date_str})**\n\n{content}"


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"🚀 开始生成 {today} 盘前深度分析报告...")
    print("=" * 60)

    # 1. 新闻阅读（保存每日 md）
    print("📰 [1/5] 抓取并生成当日新闻...")
    ok, out, err = run_script(
        WORKSPACE / "data" / "news" / "daily_news_reader.py",
        ["--date", today, "--all-stock-newest", "--save"],
        timeout=60
    )
    if not ok:
        print(f"   ⚠️ 新闻抓取失败: {err}")

    # 2. 全市场扫描
    print("📊 [2/5] 全市场技术面扫描...")
    ok, out, err = run_script(WORKSPACE / "analysis" / "close_scan_v2.py", timeout=120)
    scan_data = parse_json_from_output(out) if ok else None
    if not ok:
        print(f"   ⚠️ 扫描失败: {err}")

    # 3. 双策略扫描
    print("🎯 [3/5] 双策略最优扫描...")
    ok, out, err = run_script(WORKSPACE / "analysis" / "adaptive_dual.py", timeout=180)
    dual_data = parse_dual_scan_output(out) if ok else None
    if not ok:
        print(f"   ⚠️ 双策略失败: {err}")

    # 4. AI 智能体研判
    print("🤖 [4/5] AI 智能体综合研判...")
    ok, out, err = run_script(WORKSPACE / "analysis" / "agent" / "run_agent.py", timeout=180)
    agent_data = parse_agent_output(out) if ok else None
    if not ok:
        print(f"   ⚠️ 智能体失败: {err}")

    # 5. 信号审计
    print("🔍 [5/5] 信号交叉审计...")
    ok, out, err = run_script(WORKSPACE / "analysis" / "signal_audit.py", timeout=60)
    audit_data = parse_signal_audit_output(out) if ok else None
    if not ok:
        print(f"   ⚠️ 审计失败: {err}")

    # === 生成最终 Markdown 报告 ===
    print("\n📝 生成格式化报告...")

    md_parts = []
    md_parts.append(f"# 📊 A股每日盘前深度分析报告 | {today}")
    md_parts.append(f"> 生成时间: {datetime.now().strftime('%H:%M:%S')} | 数据基准: 前一交易日收盘")
    md_parts.append("")

    # 市场概览
    md_parts.append("## 🎯 市场全景")
    md_parts.append(format_market_overview(scan_data))
    md_parts.append("")

    # 双策略
    md_parts.append("## 🎯 双策略最优扫描")
    md_parts.append(format_dual_scan(dual_data))
    md_parts.append("")

    # AI 研判
    md_parts.append("## 🤖 AI 智能体综合研判")
    md_parts.append(format_agent_report(agent_data))
    md_parts.append("")

    # 信号审计
    md_parts.append("## 🔍 信号交叉审计")
    md_parts.append(format_signal_audit(audit_data))
    md_parts.append("")

    # 新闻摘要
    md_parts.append("## 📰 盘前关键资讯")
    md_parts.append(format_news_summary(today))
    md_parts.append("")

    # 尾部
    md_parts.append("---")
    md_parts.append("*📁 详细数据文件: `analysis/daily/` `data/news/` | ⚙️ 由 `premarket_report.py` 自动生成*")

    final_report = "\n".join(md_parts)

    # 保存报告
    report_dir = WORKSPACE / "analysis" / "daily"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"{today}_premarket_report.md"
    report_file.write_text(final_report, encoding="utf-8")
    print(f"✅ 报告已保存: {report_file}")

    # 标准输出（被 Telegram announce 捕获）
    print(final_report)

    return 0


if __name__ == "__main__":
    sys.exit(main())