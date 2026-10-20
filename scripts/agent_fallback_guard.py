#!/usr/bin/env python3
"""agent 降级链守卫（2026-10-14）
根因：last_success.json 僵尸缓存（停 09-20）被盘前报告连日静默复用，
输出 24 天前的来伊份旧闻 + 「交易日:否」假标签，且报告不标缓存日期。
检查：
  1. premarket_pipeline 必须保留 _load_agent_fallback（两处降级点，禁止叉回裸 load_fallback）
  2. 降级结果必须携带 fallback_note（禁止无日期静默降级）
  3. run_agent 成功路径必须刷新 last_success.json（否则僵尸缓存问题复发）
  4. 运行态提醒（WARN 不阻断）：last_success.json 比最新 run 档案旧 >30 天
  5. 新闻源契约（2026-10-20 增）：llm_tool 禁止裸调 stock_news_em()（akshare 1.18.64
     官方默认 symbol='603777' 来伊份 → 相关度排序旧文霸榜）；nodes 新闻段必须带
     news_source 快照标签（修复史见 memory/2026-10-20.md）
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace"))
PIPE = WORKSPACE / "analysis" / "premarket_pipeline.py"
RUNS = WORKSPACE / "data" / "agent_runs"
LAST = RUNS / "last_success.json"
RUN_AGENT = WORKSPACE / "analysis" / "agent" / "run_agent.py"

errors = []
warns = []

src = PIPE.read_text(encoding="utf-8")
if "def _load_agent_fallback(" not in src:
    errors.append("缺少 _load_agent_fallback（agent 降级必须优先扫描最新 run 档案）")
n_calls = len(re.findall(r"_load_agent_fallback\(date\)", src))
if n_calls < 2:
    errors.append(f"_load_agent_fallback 调用点={n_calls}，应 >=2（非交易日跳过 + 关键失败降级两处）")
if "fallback_note" not in src:
    errors.append("降级结果未携带 fallback_note（禁止无日期静默降级）")

ra = RUN_AGENT.read_text(encoding="utf-8")
if "last_success.json" not in ra:
    errors.append("run_agent.py 无 last_success.json 刷新逻辑（僵尸缓存问题会复发）")

def latest_run_date():
    best = None
    if RUNS.exists():
        for p in RUNS.glob("run_*.json"):
            m = re.match(r"run_(\d{8})_\d{6}\.json", p.name)
            if m:
                d = m.group(1)
                ds = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                if best is None or ds > best:
                    best = ds
    return best

if LAST.exists():
    try:
        ls = json.loads(LAST.read_text(encoding="utf-8"))
        ls_date = str(ls.get("date") or ls.get("data_date") or "")[:10]
        run_date = latest_run_date()
        if ls_date and run_date:
            gap = (datetime.strptime(run_date, "%Y-%m-%d") - datetime.strptime(ls_date, "%Y-%m-%d")).days
            if gap > 30:
                warns.append(
                    f"last_success.json 已 {gap} 天未更新（缓存 {ls_date} / 最新 run {run_date}）"
                    "——新版 run_agent 会在下次成功运行刷新它；仍陈旧则查 agent job 是否连败"
                )
        # 2026-10-20 增：旧格式缓存识别（解析成功后才判定；无 news_source = 旧格式缓存）
        if not str(ls.get("news_source") or "").strip():
            warns.append("last_success.json 为旧格式（无 news_source）——缓存新闻段无快照标签，待下次 run_agent 成功后自动刷新")
    except Exception as e:
        warns.append(f"last_success.json 解析失败: {e}")

# ── 检查 5（2026-10-20）：新闻源契约，防"旧闻顶今日"复发 ──
LLM_TOOL = WORKSPACE / "analysis" / "llm_tool.py"
NODES = WORKSPACE / "analysis" / "agent" / "nodes.py"
lt = LLM_TOOL.read_text(encoding="utf-8") if LLM_TOOL.exists() else ""
_bad_calls = []
if lt:
    import ast as _ast
    for _node in _ast.walk(_ast.parse(lt)):
        if isinstance(_node, _ast.Call):
            _fn = _node.func
            _name = _fn.attr if isinstance(_fn, _ast.Attribute) else (
                _fn.id if isinstance(_fn, _ast.Name) else None)
            if _name == "stock_news_em":
                _bad_calls.append(getattr(_node, "lineno", "?"))
if _bad_calls:
    errors.append(f"llm_tool.py 第 {_bad_calls} 行调用了 stock_news_em（新闻必须走 data/news/raw/ 快照；旧路径=来伊份旧文霸榜已废弃 2026-10-20）")
nd = NODES.read_text(encoding="utf-8") if NODES.exists() else ""
if "news_source" not in nd:
    errors.append("nodes.py 新闻段缺少 news_source 快照标签（禁止无来源标注的旧闻复述）")

for w in warns:
    print(f"🟡 WARN: {w}")
for e in errors:
    print(f"🔴 FAIL: {e}")
if errors:
    sys.exit(1)
print("✅ agent_fallback_guard 通过")
