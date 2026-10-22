#!/usr/bin/env python3
"""
周回测全流水线运行器（含 030 自动微调）
===========================================
整合：单窗口回测 → 参数调优 → 跨周期验证 → 调参差异榜 → 赛后验证 → 030自动微调 → Telegram推送

运行方式：python3 analysis/weekly_full_pipeline.py
建议：将此脚本作为 command cron 运行，避免 agentTurn LLM 超时问题
"""

import os
import sys
import json
import glob
import subprocess
import time
from datetime import datetime
from typing import List, Dict, Any

# 网络兜底：进程级 socket 默认超时（防数据源挂起导致永久阻塞）
try:
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()
except ImportError:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
ANALYSIS_DIR = os.path.join(WORKSPACE, "analysis")
DATA_DIR = os.path.join(WORKSPACE, "data")

# 确保目录存在
os.makedirs(ANALYSIS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


def _emit_dual_output(text: str):
    """双版本输出：纯文本给 Telegram + Markdown 给 WebChat/存档。"""
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = os.path.join(ANALYSIS_DIR, "daily")
    os.makedirs(daily_dir, exist_ok=True)
    # 1) 纯文本版（Telegram 用）
    tg_path = os.path.join(daily_dir, f"{today}_brief.txt")
    # 2) Markdown 版（WebChat/存档用）
    md_path = os.path.join(daily_dir, f"{today}_brief.md")
    try:
        with open(tg_path, "w", encoding="utf-8") as f:
            f.write(text)
        # Markdown 版：把 " · " 分隔的表格行转成合法 Markdown 表格
        md_rows = []
        for ln in text.splitlines():
            if " · " in ln:
                cells = [c.strip() for c in ln.split(" · ")]
                md_rows.append("| " + " | ".join(cells) + " |")
            else:
                md_rows.append(ln)
        md_body = "\n".join(md_rows)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_body)
    except Exception as e:
        print(f"[weekly pipeline] 双版本输出失败: {e}")


def run_script(script: str, args: List[str] = None, timeout: int = 600) -> Dict[str, Any]:
    """运行脚本并返回结果"""
    cmd = [sys.executable, os.path.join(ANALYSIS_DIR, script)]
    if args:
        cmd.extend(args)
    
    print(f"\n{'='*60}")
    print(f"🚀 运行: {' '.join(cmd)}")
    print(f"{'='*60}")
    
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        elapsed = time.time() - start
        
        if result.returncode == 0:
            print(f"✅ {script} 完成 ({elapsed:.1f}s)")
            return {"success": True, "stdout": result.stdout, "stderr": result.stderr, "elapsed": elapsed}
        else:
            print(f"❌ {script} 失败 ({elapsed:.1f}s): {result.stderr[:200]}")
            return {"success": False, "stdout": result.stdout, "stderr": result.stderr, "elapsed": elapsed}
    except subprocess.TimeoutExpired:
        print(f"⏱️ {script} 超时 ({timeout}s)")
        return {"success": False, "stdout": "", "stderr": f"Timeout after {timeout}s", "elapsed": timeout}
    except Exception as e:
        print(f"💥 {script} 异常: {e}")
        return {"success": False, "stdout": "", "stderr": str(e), "elapsed": 0}


RESUME_SKIP_KEYS = {"backtest", "param_tune", "validate", "dsr_audit",
                    "param_signal_diff", "param_evaluate_backup", "param_evaluate", "auto_adjust"}


def _load_resume_record():
    """--resume [latest|path]（2026-10-11 #7）：读上次周流水线记录，只补跑失败的慢步骤。
    仅认 2 天内的记录（防止捡到 ancient record 全量重跑）；无待补跑项返回 'clean'
    （主流程优雅退出，不重推简报）。门禁与简报永远重跑，不在 RESUME_SKIP_KEYS 内。"""
    if "--resume" not in sys.argv:
        return None
    idx = sys.argv.index("--resume")
    target = (sys.argv[idx + 1] if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("-")
              else "latest")
    if target == "latest":
        import glob as _g
        files = sorted(_g.glob(os.path.join(DATA_DIR, "weekly_pipeline_*.json")))
        path = files[-1] if files else None
    else:
        path = target if os.path.isabs(target) else os.path.join(DATA_DIR, target)
    if not path or not os.path.exists(path):
        print(f"⚠️ --resume: 找不到运行记录 {path}，退出（补跑仅在有记录时工作）")
        return "clean"
    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ --resume: 记录解析失败 {e}，退出")
        return "clean"
    rec_date = rec.get("date", "")
    try:
        age_days = (datetime.now() - datetime.strptime(rec_date, "%Y-%m-%d")).days
    except Exception:  # noqa: BLE001
        age_days = 99
    if age_days > 2:
        print(f"⚠️ --resume: 最新记录 {rec_date} 已超 2 天，退出（避免拾旧记录全量重跑）")
        return "clean"
    failed = [k for k, v in rec.get("steps", {}).items()
              if not v.get("success") and k in RESUME_SKIP_KEYS]
    if not failed:
        print("✅ --resume: 上次运行慢步骤全部成功，无需补跑，退出")
        return "clean"
    print(f"🔁 --resume 补跑模式（{os.path.basename(path)}），重跑: {failed}；门禁+简报照常")
    rec["_resumed_failed"] = failed
    return rec


def _resume_skip(key, resume_record):
    """补跑模式下该慢步骤上次成功 → 返回跳过伪结果；否则 None 照常执行。"""
    if not resume_record or resume_record == "clean":
        return None
    if key in resume_record.get("_resumed_failed", []):
        return None  # 失败步骤要重跑
    if resume_record.get("steps", {}).get(key, {}).get("success"):
        return {"success": True, "stdout": "", "stderr": "skipped(resume: 上次成功)",
                "elapsed": 0, "skipped": True}
    return None  # 上次也没跑成 → 重跑


def main():
    # 2026-10-11(#7): 补跑模式——沿用原运行日产物路径（validation/backtest/简报都按原日期找）
    resume_record = _load_resume_record()
    if resume_record == "clean":
        print("🔚 无待补跑项，退出（周末补跑 job 仅在有失败慢步骤时工作）")
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    run_date = today
    # 2026-10-11(#7) 注：补跑模式不改造日期——重跑步骤按自然运行日写最新产物，
    # 被跳过步骤沿用上次的旧日期产物；下游消费靠 latest 回退（见 3b/简报）。
    if resume_record is not None:
        print(f"🔁 补跑模式：重跑失败慢步骤并写新日期产物，成功步骤跳过")
    print(f"\n📅 周回测全流水线启动 (run={run_date})")

    results = {}

    # 步骤 0: sys.path 样板门禁（2026-09-19 新增）
    # 先用最快最便宜的方式抓“脚本模式会崩”的文件，避免白跑后面的长任务。
    # 背景：同类坑已踩 2 次（08-23、09-19），故固化为机器门禁。
    print("\n🚦 步骤 0/8: sys.path 样板门禁 (script_path_guard.py)")
    r = run_script("../scripts/script_path_guard.py", timeout=120)
    results["path_guard"] = r
    if not r["success"]:
        print("⚠️ sys.path 样板门禁未通过（详见上方违规清单），本次运行可能受影响...")

    # 步骤 0b: 技能供应链静态扫描（2026-09-19 新增，纯静态秒级）
    # 旧 bash 版逐文件调用 grep/file（≈3800 次进程启动）会卡死，已于 09-19 重写为
    # 单进程 Python 版并加 --offline；否则无法接入自动化。
    print("\n🛡️ 步骤 0b/8: 技能供应链静态扫描 (skill_supply_scan.py --offline)")
    r = run_script("../scripts/skill_supply_scan.py", ["--offline", "--quiet"], timeout=120)
    results["skill_scan"] = r
    if not r["success"]:
        print("⚠️ 供应链扫描发现致命高危，请人工核查（不阻断流水线）...")

    # 步骤 0c: 关注池口径一致性门禁（2026-09-27 新增）
    # 背景：曾出现「代码已是35只、文档还写27只」的静默不一致，人工按旧清单误判。
    # 仅在写回映射前校验，失败仅告警不阻断（防误伤主流程）。
    # 步骤 0b: 命令型 Cron 真实性审计 (cron_truth_guard.py)
    # 缘起：2026-09-29/10-06/10-22 发现 lastStatus=ok 的 Fake OK 假成功
    # 逻辑：调度 OK 必须对应产物 (mtime/非空) 存在，否则报红 (不阻断流水线)
    print("\n🚦 步骤 0b/9: Cron 真实性审计 (cron_truth_guard.py)")
    r = run_script("../scripts/cron_truth_guard.py", timeout=120)
    results["cron_truth"] = r
    if not r["success"]:
        print("⚠️ 发现 Cron 任务静默失效 (Fake OK)，调度 OK 但无真实产物更新 (不阻断流水线)...")

    # 步骤 0c: 关注池口径一致性门禁（2026-09-27 新增）
    # 背景：曾出现「代码已是35只、文档还写27只」的静默不一致，人工按旧清单误判。
    # 仅在写回映射前校验，失败仅告警不阻断（防误伤主流程）。
    print("\n🚦 步骤 0c/9: 关注池口径一致性门禁 (strategy_map_guard.py)")
    r = run_script("../scripts/strategy_map_guard.py", ["--quiet"], timeout=120)
    results["map_guard"] = r
    if not r["success"]:
        print("⚠️ 关注池口径不一致（见上方），请人工核对文档/映射（不阻断流水线）...")

    # 步骤 0d: 市场数据引用验证门禁（2026-09-28 新增）
    # 背景：曾出现「编造 9/27 成交额 1.45 万亿、北向 8129 亿」的先写后验事故。
    # 强制验证：引用日期为交易日、来源在授权清单、字段匹配。
    # 注：周六 06:00 运行时当天 dual 日报尚未生成，固定 today 路径会因"文件不存在"误报
    # （10-03、10-10 两轮均如此）。门禁意图是验证"最新日报"，故取 daily/ 下最新的
    # *_dual.md；仅当 daily/ 无任何 dual 文件时才回退 today 路径（此时报错合理）。
    _dual_files = sorted(glob.glob(os.path.join(ANALYSIS_DIR, "daily", "*_dual.md")), reverse=True)
    _dual_report = _dual_files[0] if _dual_files else os.path.join(ANALYSIS_DIR, "daily", f"{today}_dual.md")
    print("\n🚦 步骤 0d/9: 市场数据引用验证门禁 (verify_market_data.py)")
    r = run_script("../scripts/verify_market_data.py", ["--report", _dual_report], timeout=60)
    results["market_data_verify"] = r
    if not r["success"]:
        print("⚠️ 最新日报存在未验证的市场数据引用（见上方），请人工核对（不阻断流水线）...")

    # 步骤 0e: 数据新鲜度门禁（2026-09-28 新增）
    # 背景：日报「现价」实为上一交易日收盘（上游 baostock/新浪日K 滞后），
    # 报告未标注数据日期 → 消费方静默使用滞后数据（前视偏差）。
    print("\n🚦 步骤 0e/9: 数据新鲜度门禁 (data_freshness_guard.py)")
    r = run_script("../scripts/data_freshness_guard.py", ["--quiet"], timeout=60)
    results["data_freshness"] = r
    if not r["success"]:
        print("⚠️ 日报数据新鲜度未通过（见上方），请人工核查数据日期标注（不阻断流水线）...")

    # 步骤 0f: 静默异常吞噬门禁（2026-09-28 新增，AST 静态秒级，棘轮模式）
    # 背景：cron_health_check.py 的 `except Exception` 只走 log.debug，
    # 把 gateway HTTP API 404 静默吞掉 10 天，监控全盲还报"正常"。
    # 棘轮：存量静默仅报告（历史债务不阻断），新增静默才 fail。
    print("\n🚦 步骤 0f/9: 静默异常吞噬门禁 (silent_except_guard.py)")
    r = run_script("../scripts/silent_except_guard.py", timeout=120)
    results["silent_except"] = r
    if not r["success"]:
        print("⚠️ 新增静默异常吞噬（见上方），请人工修复（不阻断流水线）...")

    # 步骤 0g: promote 预算淘汰门禁（2026-10-05 新增）
    # 背景：memory-core 插件 compactMemoryForBudget() 默认 1e4 字符预算，
    # 超预算时按日期静默丢弃最老 promote 区块且 journal 无 dropped 字段。
    # 口径：promote=易失缓冲，长期保留靠人工写入人写区；本门禁监测通道异常中断。
    print("\n🚦 步骤 0g/9: promote 淘汰门禁 (promotion_guard.py)")
    r = run_script("../scripts/promotion_guard.py", timeout=120)
    results["promotion_guard"] = r
    if not r["success"]:
        print("⚠️ promote 通道异常（见上方），需人工将关键教训写入 MEMORY.md 人写区（不阻断流水线）...")

    # 步骤 0h: cron 空转假成功门禁（2026-10-06 新增，直读 sqlite，0.13s）
    # 背景：3950d3cc 长期 status=ok 但毫秒返回、脚本从未执行（main+systemEvent 注入即 ok）。
    # cron_health_check 只看成功率，毫秒级 ok 永远"健康"——结构性盲区。棘轮基线：
    # 存量 9 个已入 data/cron_noop_baseline.json 仅报告，基线外新增才 exit 1。
    print("\n🚦 步骤 0h/9: cron 空转假成功门禁 (cron_noop_guard.py)")
    r = run_script("../scripts/cron_noop_guard.py", timeout=60)
    results["cron_noop_guard"] = r
    if not r["success"]:
        print("⚠️ 发现基线外新增空转假成功 cron 任务（见上方），需人工改 command+isolated 或清理（不阻断流水线）...")

    # 步骤 0i: 调试残留门禁（2026-10-11 新增，AST 静态秒级）
    # 背景：python-debugpy 技能落实。breakpoint()/pdb.set_trace 提交后会让 cron
    # 无人值守进程挂死等 stdin；debugpy.listen 会在本机开调试端口。
    # AST 扫描不误报注释/docstring；HIGH=阻断式调试入口 → exit 1。
    print("\n🚦 步骤 0i/9: 调试残留门禁 (debug_residue_guard.py)")
    r = run_script("../scripts/debug_residue_guard.py", ["--quiet"], timeout=60)
    results["debug_residue"] = r
    if not r["success"]:
        print("⚠️ 发现阻断式调试残留（见上方），交互调试完成后必须移除（不阻断流水线）...")

    # 步骤 0w: 记忆耐久性门禁（2026-10-21 新增，缘起日记 0 字节事故·假 ok 第 4 变体
    # 「验证表达式自毁被验对象」：open(p,'w') 出现在 and/三元/print 实参中，求值即截断）
    print("\n🚦 步骤 0w/9: 记忆耐久性门禁 (memory_note_durability_guard.py)")
    r = run_script("../scripts/memory_note_durability_guard.py", ["--quiet"], timeout=60)
    results["memory_durability"] = r
    if not r["success"]:
        print("⚠️ 发现写模式 open 处于表达式上下文（求值即写入/截断），见上方（不阻断流水线）...")

    # 步骤 0j: agent 降级链门禁（2026-10-14 新增，缘起 last_success.json 僵尸缓存连日静默
    # 输出 24 天前旧闻事故：盘前 agent 段降级必须优先最新 run 档案 + 必须带缓存日期标注）
    print("\n🚦 步骤 0j/9: agent 降级链门禁 (agent_fallback_guard.py)")
    r = run_script("../scripts/agent_fallback_guard.py", timeout=60)
    results["agent_fallback"] = r
    if not r["success"]:
        print("⚠️ agent 降级链防护被破坏（见上方），盘前报告可能再次静默复用僵尸缓存（不阻断流水线）...")

    # 步骤 0x: 测试隔离门禁（2026-10-21 新增，缘起 10-20 e9fb539 功能验证冒烟直调
    # execute_trade 未重定向 STATE/TRADES/EQUITY 三个 FILE → 两笔假成交写进生产底账）
    print("\n🚦 步骤 0x/9: 测试隔离门禁 (test_isolation_guard.py)")
    r = run_script("../scripts/test_isolation_guard.py", timeout=90)
    results["test_isolation"] = r
    if not r["success"]:
        print("⚠️ 有测试/研究脚本直调副作用函数未隔离生产文件（见上方），账本可能再次被污染（不阻断流水线）...")

    # 步骤 0y: 状态文件耐久性门禁（2026-10-21 立项、2026-10-22 第五轮升级后接入；
    # 缘起 portfolio_equity.csv 冻结 22 交易日——重写型脚本必须有先读后写 + .bak + 防倒退。
    # 第五轮加路径指纹判别后消除 2 例假阳性，具备流水线资格）
    print("\n🚦 步骤 0y/9: 状态文件耐久性门禁 (state_durability_guard.py)")
    r = run_script("../scripts/state_durability_guard.py", timeout=120)
    results["state_durability"] = r
    if not r["success"]:
        print("⚠️ 发现清空式重写高危（同目标先写后读），历史数据可能在异常路径下静默归零（不阻断流水线）...")

    # 步骤 0z: 交易日判定 SSOT 门禁（2026-10-22 第五轮新增；缘起 10-01 一天 3 文件踩
    # date vs str 坑，根因三份实现互不复用；本轮同步收敛 chip_freshness 直调 sina 日历）
    print("\n🚦 步骤 0z/9: 交易日 SSOT 门禁 (trading_day_ssot_guard.py)")
    r = run_script("../scripts/trading_day_ssot_guard.py", timeout=60)
    results["trading_day_ssot"] = r
    if not r["success"]:
        print("⚠️ 发现交易日判定/日历获取绕过 SSOT（见上方），节假日误判可能复发（不阻断流水线）...")

    # 步骤 0l: 外部数据 astype(float) 裸转棘轮门禁（2026-10-22 第六轮新增；缘起 10-08
    # 外部源空串 '' 非裸转 ValueError 崩批；68 处存量冻结，新增拦截，修复自动降基线）
    print("\n🚦 步骤 0l/9: astype 裸转棘轮门禁 (astype_ratchet_guard.py)")
    r = run_script("../scripts/astype_ratchet_guard.py", timeout=120)
    results["astype_ratchet"] = r
    if not r["success"]:
        print("⚠️ 新增 astype(float) 裸转（见上方），外部数据空串可能崩掉整批扫描（不阻断流水线）...")

    # 步骤 0a: 网络调用缺 timeout 门禁（2026-10-22 第六轮新增；缘起 err.dist-io-no-timeout；
    # 零存量直接 HIGH 型，防回潮）
    print("\n🚦 步骤 0a/9: 网络超时门禁 (net_timeout_guard.py)")
    r = run_script("../scripts/net_timeout_guard.py", timeout=120)
    results["net_timeout"] = r
    if not r["success"]:
        print("⚠️ 网络调用缺 timeout（见上方），存在永久挂死风险（不阻断流水线）...")

    # 步骤 0k: 五维评分 None 契约门禁（2026-10-17 新增，纯逻辑合成输入秒级）
    # 背景：10-08 立项「隐性 5.0 填充」改造（98820ca + 3977840）。缺数据必须 None，
    # 禁止 or 0/or 50/5.0 托底；RSI<30 不加分口径一并锁定。防未来改码回潮。
    print("\n🚦 步骤 0k/9: 五维评分 None 契约门禁 (picks_none_contract_test.py)")
    r = run_script("../scripts/picks_none_contract_test.py", timeout=60)
    results["picks_none_contract"] = r
    if not r["success"]:
        print("⚠️ 五维评分 None 契约被破坏（见上方），缺失数据可能再次伪装成中性/强势分（不阻断流水线）...")

    # 步骤 0m: 基准池 CSV 增量刷新（2026-10-18 新增，非门禁而是数据供应）
    # 背景：/Users/duguke/stock_data 是 alpha_beta_attr 等权基准池数据源，原为一次性脚本产物
    # 无刷新方，10-18 实测 54/54 陈旧（最新 10-08）。本步骤每周增量刷新+格式迁移+桩自愈。
    print("\n🔄 步骤 0m/9: 基准池 CSV 增量刷新 (refresh_stock_data.py)")
    r = run_script("../scripts/refresh_stock_data.py", timeout=420)
    results["refresh_stock_data"] = r
    if not r["success"]:
        print("⚠️ 基准池刷新有失败项（见上方），alpha_beta_attr 可能用过期基准（不阻断流水线）...")

    # 步骤 0s: chip 缓存刷新（2026-10-18 新增，非门禁而是数据供应）
    # 背景：chip 第 5 维复活后接进生产，但 chip_/chipmetrics_ 两处缓存均为无条件命中、
    # cron 无任何刷新方 → 缓存会冻结，cost_bias 拿新价减旧成本 → −0.20 档静默失效。
    # 本步骤幂等增量刷新（无新数据全部 skip，实测 1s；有新增才重算尾部）。
    # 必须排在 0t 门禁之前，否则门禁被旧数据错误判红。
    print("\n🔄 步骤 0s/9: chip 缓存增量刷新 (chip_distribution_study.py --refresh)")
    r = run_script("../scripts/chip_distribution_study.py", args=["--refresh"], timeout=600)
    results["chip_refresh"] = r
    if not r["success"]:
        print("⚠️ chip 刷新有失败项（见上方），chip 维可能陈旧（不阻断流水线）...")

    # 步骤 0t: chip 缓存新鲜度契约门禁（2026-10-18 新增，纯逻辑 + 临时目录隔离）
    # 锁：陈旧阈值分档 / 陈旧到不可信 → 该维 None（禁止用旧数据打分）/ 增量==全量 /
    # refresh 同步判据含全市场基准（两边同时落后也必须追，截断测试曾在这里假绿）。
    print("\n🚦 步骤 0t/9: chip 新鲜度契约门禁 (chip_freshness_test.py)")
    r = run_script("../scripts/chip_freshness_test.py", timeout=60)
    results["chip_freshness"] = r
    if not r["success"]:
        print("⚠️ chip 新鲜度契约被破坏（见上方），第 5 维可能拿旧筹码打分（不阻断流水线）...")

    # 步骤 0u: 对撞 U 型假设样本监测（2026-10-19 新增，10-18 反事实报告遗留 TODO）
    # --replay 先重放刷新评估日明细（样本能自然增长的唯一途径），失败降级用旧明细；
    # 计数 >100 自动跑 U 型重测并落盘报告。监测型步骤，不阻断。
    print("\n📈 步骤 0u/9: 对撞 U 型假设样本监测 (clash_ushape_monitor.py --replay)")
    r = run_script("../scripts/clash_ushape_monitor.py", args=["--replay"], timeout=1200)
    results["clash_ushape_monitor"] = r
    if not r["success"]:
        print("⚠️ 对撞样本监测失败（评估明细缺失/损坏？见上方），不阻断流水线...")

    # 步骤 0v: 评分器组件/维度证据审计刷新（2026-10-20 新增，research 刷新 warn-only）
    # 背景：组件审计(v4 反证→反向落地) + LODO(fund 最弱环节) 证据需按周刷新，
    # 追踪组件变更后的边际是否漂移；纯本地秒级~分钟级，失败不阻断。
    print("\n📈 步骤 0v/9: 评分器组件证据审计 (score_component_audit.py)")
    r = run_script("../scripts/score_component_audit.py", timeout=300)
    results["score_component_audit"] = r
    if not r["success"]:
        print("⚠️ 组件审计刷新失败（不阻断；生产口径变更后应同步此脚本镜像）...")
    print("\n📈 步骤 0v/9: 评分器留一维审计 (dim_leave_out_audit.py)")
    r = run_script("../scripts/dim_leave_out_audit.py", timeout=300)
    results["dim_leave_out_audit"] = r
    if not r["success"]:
        print("⚠️ LODO 审计刷新失败（不阻断）...")

    # 步骤 0n: 单簇市值暴露上限契约门禁（2026-10-18 新增，纯逻辑合成输入秒级）
    # 背景：只数≤2 口径在组合评分卡(ef81ed2)中证明对收益/回撤均无保护 →
    # 升级为组合层单簇市值占比≤25%(持仓期持续生效)。本门禁锁 12 场景语义:
    # fail-open / 只管买入 / 空组合豁免 / 齐涨漂移拦截 / mv_map 当日市值优先。
    print("\n🚦 步骤 0n/9: 单簇市值暴露契约门禁 (cluster_expo_cap_test.py)")
    r = run_script("../scripts/cluster_expo_cap_test.py", timeout=60)
    results["cluster_expo_cap"] = r
    if not r["success"]:
        print("⚠️ 单簇暴露上限契约被破坏（见上方），同相关簇约束可能回潮为建仓瞬时口径（不阻断流水线）...")

    # 步骤 0o: 组合总仓位上限契约门禁（2026-10-18 新增，纯逻辑合成输入秒级）
    # 背景：约束层盘点发现 030 total_limit_amount 无消费方（哑铃 80%+20% 可满仓）→
    # 接线 _apply_total_expo_cap 执法。本门禁锁执法/fail-open/开关回退语义。
    print("\n🚦 步骤 0o/9: 总仓位上限契约门禁 (total_expo_cap_test.py)")
    r = run_script("../scripts/total_expo_cap_test.py", timeout=60)
    results["total_expo_cap"] = r
    if not r["success"]:
        print("⚠️ 总仓位上限契约被破坏（见上方），030 动态总仓上限可能再次静默失效（不阻断流水线）...")

    # 步骤 0p: 方向分配上限契约门禁（2026-10-18 新增，纯逻辑+离线供给）
    # 背景：direction_allocation 曾因 refresh 传空 watchlist 恒为 {} → 方向上限死代码。
    # 本门禁同时锁「供给端非空」与「已满拦截/空 fail-open」两端语义。
    print("\n🚦 步骤 0p/9: 方向分配上限契约门禁 (direction_cap_test.py)")
    r = run_script("../scripts/direction_cap_test.py", timeout=90)
    results["direction_cap"] = r
    if not r["success"]:
        print("⚠️ 方向分配上限契约被破坏（见上方），方向级资金上限可能再次静默失效（不阻断流水线）...")

    # 步骤 0q: 三因素门控缺失语义契约门禁（2026-10-18 新增，纯离线桩）
    # 背景：原实现「缺失→中性50」使技术90+双缺=68分恒放行；已改 None 传播 +
    # GATE_MISSING_POLICY(fail_closed 默认)。本门禁锁缺失语义 + 门槛 + 回退路径。
    print("\n🚦 步骤 0q/9: 门控缺失语义契约门禁 (gate_missing_policy_test.py)")
    r = run_script("../scripts/gate_missing_policy_test.py", timeout=90)
    results["gate_missing_policy"] = r
    if not r["success"]:
        print("⚠️ 三因素门控缺失语义契约被破坏（见上方），缺失可能又退化为中性分（不阻断流水线）...")

    # 步骤 0r: Tier 屏蔽留痕契约门禁（2026-10-18 新增，纯离线 + 临时目录隔离）
    # 背景：Tier2/3 静默吞掉 20.6% 买入信号，评估口径看不见 → 改「配置+显式标注」。
    # 锁：Tier3 空仓买入必须拦（原漏拦）+ suppressed_* 字段 + jsonl 追加。
    print("\n🚦 步骤 0r/9: Tier屏蔽留痕契约门禁 (tier_suppression_test.py)")
    r = run_script("../scripts/tier_suppression_test.py", timeout=60)
    results["tier_suppression"] = r
    if not r["success"]:
        print("⚠️ Tier 屏蔽留痕契约被破坏（见上方），被吞信号可能再次静默消失（不阻断流水线）...")

    # 步骤 1: 单窗口回测
    print("\n📊 步骤 1/8: 单窗口回测 (backtest_strategies.py)")
    r = _resume_skip("backtest", resume_record)
    if r is None:
        r = run_script("backtest_strategies.py", timeout=600)  # 10分钟上限，当前约3.5分钟跑完，留足缓冲
    results["backtest"] = r
    if not r["success"]:
        print("⚠️ 回测失败，继续后续步骤...")
    
    # 步骤 2: 参数调优 —— R5 双周制(2026-10-17 用户批准): 偶数 ISO 周才重调，跳过周其余步骤照常
    # (diff/evaluate 对未变参数自动报「无变更/不可归因」，数据链路不受影响；--force-tune 可强制)
    import datetime as _dt
    _iso_week = _dt.date.today().isocalendar()[1]
    if (_iso_week % 2 != 0) and ("--force-tune" not in sys.argv):
        print(f"\n⏭️ 步骤 2/8: 双周调参跳过周 (ISO 第{_iso_week}周) —— 参数不重调 (--force-tune 可强制)")
        results["param_tune"] = {"success": True, "skipped": "biweekly", "stdout": f"skipped iso_week={_iso_week}"}
    else:
        print("\n⚙️ 步骤 2/8: 参数调优快速模式 (param_tune.py --fast)")
        r = _resume_skip("param_tune", resume_record)
        if r is None:
            r = run_script("param_tune.py", ["--fast"], timeout=1800)  # 30分钟上限，足够覆盖快速模式(约6分钟)+缓冲
        results["param_tune"] = r
    if not r["success"]:
        print("⚠️ 参数调优失败，继续后续步骤...")
    
    # 步骤 3: 跨周期验证 — 最后跑，带置信度仲裁写回（拥有最终决定权）
    print("\n🔍 步骤 3/9: 跨周期验证 + 置信度仲裁 (validate_strategies.py --arbitrate --pending-out)")
    # 2026-10-11(#8): 不再直接 --write-map —— 变更先进候审清单，由步骤 3c 按 DSR 分流，
    # DSR 不显著票的策略切换不再自动生效（记忆规约的强制化）。
    r = _resume_skip("validate", resume_record)
    if r is None:
        r = run_script("validate_strategies.py",
                       ["--arbitrate", "--pending-out", "data/strategy_pending.json"], timeout=1800)
    results["validate"] = r
    if not r["success"]:
        print("⚠️ 验证失败，继续后续步骤...")

    # 步骤 3b: DSR 运气基线体检（2026-10-05 新增，接自 frank-quant EP007 方法论）
    # 背景：「得分高≠可用」已固化为护栏，但护栏不看「best-of-N 里有多少是运气」。
    # 本步读步骤 3 刚产出的 validation_*.json（含 dsr_trials 逐窗口夏普），
    # 全池出 DSR 体检报告到 analysis/dsr_audit_<date>.md，不显著票数进周报。
    # 失败仅告警不阻断（防误伤主流程）。
    print("\n🎲 步骤 3b/9: DSR 运气基线体检 (dsr_sharpe.py)")
    val_file = os.path.join(ANALYSIS_DIR, f"validation_{today}.json")
    val_file = os.path.join(ANALYSIS_DIR, f"validation_{today}.json")
    if not os.path.exists(val_file):
        # 2026-10-11(#7) 补跑跨天回退：validation 取最新（被跳过的 validate 产物是原运行日的）
        _vals = sorted(glob.glob(os.path.join(ANALYSIS_DIR, "validation_*.json")))
        if _vals:
            val_file = _vals[-1]
            print(f"ℹ️ 未找到当日 validation，回退最新: {os.path.basename(val_file)}")
    if os.path.exists(val_file):
        r = _resume_skip("dsr_audit", resume_record)
        if r is None:
            r = run_script("dsr_sharpe.py",
                       ["--validation", val_file,
                        "--out", os.path.join(ANALYSIS_DIR, f"dsr_audit_{today}.md")],
                       timeout=300)
        results["dsr_audit"] = r
        if not r["success"]:
            print("⚠️ DSR 体检失败（不阻断流水线）...")
    else:
        print(f"⚠️ 未找到 {val_file}（步骤 3 未产出？），跳过 DSR 体检")
        results["dsr_audit"] = {"success": False, "stdout": "", "stderr": "validation 文件缺失", "elapsed": 0}
    
    # 步骤 3c: 策略候审闸门（2026-10-11 #8 新增）
    # 候审清单按 DSR 分流：显著自动写 map，偏弱/不显著留候审等人批准（apply --apply-symbol）。
    print("\n🚦 步骤 3c/9: 策略候审闸门 (apply_strategy_pending.py --auto)")
    r = run_script("../scripts/apply_strategy_pending.py", ["--auto"], timeout=120)
    results["apply_pending"] = r
    if not r["success"]:
        print("⚠️ 候审分流失败（不阻断；清单保留待人工处理）...")

    # 步骤 4: 调参差异榜
    print("\n⚖️ 步骤 4/8: 调参差异榜 (param_signal_diff.py)")
    r = _resume_skip("param_signal_diff", resume_record)
    if r is None:
        r = run_script("param_signal_diff.py", timeout=300)
    results["param_signal_diff"] = r
    if not r["success"]:
        print("⚠️ 差异榜失败，继续后续步骤...")
    
    # 步骤 5: 赛后验证（备份当前参数）
    print("\n🔄 步骤 5/8: 赛后验证 - 备份参数 (param_evaluate.py --backup)")
    r = _resume_skip("param_evaluate_backup", resume_record)
    if r is None:
        r = run_script("param_evaluate.py", ["--backup"], timeout=120)
    results["param_evaluate_backup"] = r
    if not r["success"]:
        print("⚠️ 参数备份失败，继续后续步骤...")
    
    # 步骤 6: 赛后验证（评估前 14 天）
    print("\n📈 步骤 6/8: 赛后验证 - 评估前 14 天 (param_evaluate.py)")
    r = _resume_skip("param_evaluate", resume_record)
    if r is None:
        r = run_script("param_evaluate.py", timeout=120)
    results["param_evaluate"] = r
    if not r["success"]:
        print("⚠️ 赛后验证失败，继续后续步骤...")
    
    # 步骤 7: 030 自动仓位微调 ⭐ 新增
    print("\n🔧 步骤 7/8: 030 自动仓位微调 (auto_adjust_position.py)")
    r = _resume_skip("auto_adjust", resume_record)
    if r is None:
        r = run_script("auto_adjust_position.py", timeout=60)
    results["auto_adjust"] = r
    if not r["success"]:
        print("⚠️ 自动微调失败（可能成交不足），继续...")

    # 步骤 8: 生成简报并推送 Telegram（纯文本，绕开 LLM 与 Markdown 实体问题）
    print("\n📤 步骤 8/8: 生成周报简报并推送 Telegram")
    brief = build_brief(results, as_of=today)
    results["brief"] = push_brief(brief)

    # 汇总
    print(f"\n{'='*60}")
    print("📋 周回测全流水线完成汇总")
    print(f"{'='*60}")
    
    all_success = True
    for step, result in results.items():
        status = "✅" if result["success"] else "❌"
        elapsed = result.get("elapsed", 0)
        print(f"  {status} {step}: {elapsed:.1f}s")
        if not result["success"]:
            all_success = False
    
    # 保存运行记录
    run_record = {
        "date": run_date,
        "artifact_date": today,
        "resumed": resume_record is not None,
        "steps": results,
        "all_success": all_success,
        "total_elapsed": sum(r.get("elapsed", 0) for r in results.values()),
    }
    
    record_path = os.path.join(DATA_DIR, f"weekly_pipeline_{run_date}.json")
    with open(record_path, "w") as f:
        json.dump(run_record, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"\n📄 运行记录已保存: {record_path}")
    
    if all_success:
        print("\n🎉 所有步骤成功！")
        return 0
    else:
        print("\n⚠️ 部分步骤失败，请检查日志")
        return 1


def _read_json_file(path):
    """安全读取 JSON；失败返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _latest_validation():
    """取最新的 validation_YYYY-MM-DD.json（用于策略变更数）。"""
    try:
        files = [f for f in os.listdir(ANALYSIS_DIR) if f.startswith("validation_") and f.endswith(".json")]
        if not files:
            return None
        files.sort(reverse=True)
        return _read_json_file(os.path.join(ANALYSIS_DIR, files[0]))
    except Exception:
        return None


def _parse_brief_bool(line):
    """异彩榜提炼：去掉 markdown 表格符号只留数值行。"""
    return line


def build_brief(results=None, as_of=None):
    """读取各步骤产物生成纯文本周报简报。results 可含 param_signal_diff 的 stdout。
    as_of: 补跑模式传入原运行日，保证 backtest/validation 产物路径对齐（2026-10-11 #7）。"""
    today = (as_of or datetime.now().strftime("%Y-%m-%d"))
    lines = []
    lines.append("每周A股策略回测更新简报 " + today)
    lines.append("")

    # 一、策略回测（从 backtest 报告提炼策略榜，去 markdown 符号）
    bt_path = os.path.join(ANALYSIS_DIR, "backtest", today + ".md")
    if not os.path.exists(bt_path):
        # 2026-10-11(#7) 补跑跨天回退：backtest 报告取最新
        import glob as _g
        _bts = sorted(_g.glob(os.path.join(ANALYSIS_DIR, "backtest", "*.md")))
        if _bts:
            bt_path = _bts[-1]
            print(f"ℹ️ 未找到当日 backtest 报告，回退最新: {os.path.basename(bt_path)}")
    try:
        if os.path.exists(bt_path):
            with open(bt_path, encoding="utf-8") as f:
                content = f.read()
            # 抽整体概况后的策略总分/胜率关键行
            seg = content.split("## 整体概况")[-1].split("## 各策略")[0]
            rows = []
            for ln in seg.splitlines():
                ln = ln.strip()
                if ln.startswith("|") and "平均收益" not in ln and "指标" not in ln and ln.replace("|", "").replace("-", "").strip():
                    rows.append("  " + ln.strip(" |").replace("|", " · "))
            # 从表头行推导策略数（排除 '指标' 列）
            strategy_count = 0
            for ln in seg.splitlines():
                ln = ln.strip()
                if ln.startswith("|") and "指标" in ln:
                    strategy_count = len([c for c in ln.split("|") if c.strip()]) - 2  # 减去首尾空列
                    break
            if strategy_count:
                lines[1] = f"一、{strategy_count}策略全量回测"  # 修正标题
            lines.append("\n".join(rows[:9]) if rows else "(无策略榜行)")
            # 2026-10-11(#9): 小样本策略列标注——总交易次数<3 的列统计不可信，
            # 防止"交易1次、胜率82.86%、盈亏比52922"这类列被误读为真实实力。
            try:
                header_cells = None
                trades_cells = None
                for ln in seg.splitlines():
                    ln_s = ln.strip()
                    if ln_s.startswith("|") and "指标" in ln_s and header_cells is None:
                        header_cells = [c.strip() for c in ln_s.strip("|").split("|")]
                    if ln_s.startswith("|") and "总交易次数" in ln_s and trades_cells is None:
                        trades_cells = [c.strip() for c in ln_s.strip("|").split("|")]
                if header_cells and trades_cells and len(header_cells) == len(trades_cells):
                    small = [header_cells[i] for i in range(1, len(header_cells))
                             if trades_cells[i].isdigit() and int(trades_cells[i]) < 3]
                    if small:
                        lines.append(f"  ⚠️ 小样本策略列（交易<3次，统计不可信）: {', '.join(small)}")
                    else:
                        lines.append("  ✅ 全部策略交易次数≥3，无小样本列")
            except Exception as e:  # noqa: BLE001
                lines.append(f"  (小样本标注解析失败: {e})")
        else:
            lines.append("(backtest 报告未生成)")
    except Exception as e:
        lines.append(f"(回测摘要解析失败: {e})")
    lines.append("")

    # 二、策略/参数变更（三分类: 真变参/策略增删/仅分数漂移 —— 审计 R4, 2026-10-17）
    # 旧口径 dict 直接比对含分数 → 分数漂移也计入「参数变更」，N 只虚高且误导
    lines.append("二、策略/参数变更")
    if isinstance(results.get("param_tune"), dict) and results["param_tune"].get("skipped"):
        lines.append("⏭️ 本周为双周调参跳过周（R5），参数未重调；下列对比为与基线的存量差异")
    try:
        from param_evaluate import classify_changes, _load_stocks_map  # 同目录复用, 口径单一
        new_stocks = _load_stocks_map(os.path.join(DATA_DIR, "adaptive_params.json"))
        prev_stocks = _load_stocks_map(os.path.join(DATA_DIR, "adaptive_params_prev.json"))
        cls = classify_changes(prev_stocks, new_stocks)
        _base = None
        try:
            with open(os.path.join(DATA_DIR, "adaptive_params_prev.json"), encoding="utf-8") as _f:
                _base = (json.load(_f) or {}).get("date")
        except Exception:
            _base = None
        if cls["param_changed"]:
            lines.append(f"真参数变更 {len(cls['param_changed'])} 只: " + ", ".join(sorted(cls['param_changed'])[:12]))
        else:
            lines.append("真参数变更 0 只")
        if cls["set_changed"]:
            lines.append(f"策略增删 {len(cls['set_changed'])} 只: " + ", ".join(sorted(cls['set_changed'])[:8]))
        else:
            lines.append("策略增删 0 只")
        lines.append(f"仅分数漂移 {len(cls['score_only'])} 只（窗口右移所致, 非调参效果, 不改变信号）｜对照基线: {_base or '未知'}")
    except Exception as e:
        lines.append(f"(变更统计失败: {e})")
    lines.append("")

    # 三、调参差异榜（优先用 pipeline 捕获的 stdout；否则读文件）
    lines.append("三、调参对照信号差异榜")
    diff_txt = ""
    if results and results.get("param_signal_diff"):
        diff_txt = results["param_signal_diff"].get("stdout", "") or ""
    if not diff_txt:
        diff_path = os.path.join(ANALYSIS_DIR, "daily", f"{today}_param_signal_diff.md")
        if os.path.exists(diff_path):
            try:
                with open(diff_path, encoding="utf-8") as f:
                    diff_txt = f.read()
            except Exception:
                diff_txt = ""
    if diff_txt:
        # 提炼核心：方向反转/新卖/新买/策略切换 + 摘要
        keep = []
        for ln in diff_txt.splitlines():
            ln = ln.strip()
            # 明细行（如 "中信证券(600030): 旧[...] -> 新[...]"）不含关键词，曾全被过滤，
            # 导致简报只剩表头无明细、读者看不到新卖/切换具体是哪几只（2026-10-17 修复）
            if any(k in ln for k in ["方向反转", "新出现卖出", "新出现买入", "策略切换但", "差异榜摘要", "🔁", "🔴 新", "🟢 新", "🔄 策略", "跳过", "无差异", "反转", "新卖", "新买"]) or ("旧[" in ln and "新[" in ln):
                keep.append(ln)
        lines.append("\n".join(keep[:28]) if keep else diff_txt[:400].strip())
    else:
        lines.append("(param_signal_diff 无输出/未生成)")
    lines.append("")

    # 四、调参赛后验证
    lines.append("四、调参赛后验证(08-08以来)")
    eval_path = os.path.join(ANALYSIS_DIR, "daily", f"{today}_param_eval.md")
    if os.path.exists(eval_path):
        try:
            with open(eval_path, encoding="utf-8") as f:
                eval_txt = f.read()
            highlights = [l for l in eval_txt.splitlines() if any(k in l for k in ["被调参", "建议回滚", "有成交", "观察中"])]
            if highlights:
                for hline in highlights[:6]:
                    lines.append(hline.strip())
                # 带出恶化明细
                for l in eval_txt.splitlines():
                    if l.strip().startswith("🔴") or l.strip().startswith("⚠️ **建议回滚"):
                        lines.append(l.strip())
            else:
                lines.append(eval_txt.strip()[:500])
        except Exception as e:
            lines.append(f"(赛后验证读取失败: {e})")
    else:
        lines.append("(param_eval 未生成)")
    lines.append("")

    # 五、DSR 显著名单（2026-10-05 新增；读步骤 3b 产出的 dsr_audit_<date>.json）
    # 意义：这些票的「最优策略历史夏普」高过「同池 N 次挑选纯运气的最高值」，
    # 策略信号与个股特质匹配是真实的；未上榜 ≠ 废票，只是策略选择环节 ≈ 随机。
    lines.append("五、DSR显著名单(历史夏普≠挑选运气)")
    dsr_path = os.path.join(ANALYSIS_DIR, f"dsr_audit_{today}.json")
    try:
        if os.path.exists(dsr_path):
            with open(dsr_path, encoding="utf-8") as f:
                dsr_rows = json.load(f)
            sig = [r for r in dsr_rows
                   if r.get("status") == "ok" and "不显著" not in r.get("verdict", "") and "显著" in r.get("verdict", "")]
            n_ok = sum(1 for r in dsr_rows if r.get("status") == "ok")
            if sig:
                sig.sort(key=lambda x: -x.get("DSR", 0))
                for r in sig:
                    lines.append(f"  {r['name']}({r['symbol']}) {r.get('selected_display') or r.get('selected')}"
                                 f" 夏普{r['observed_sharpe_annual']} vs 运气基线{r['expected_max_sharpe_annual_by_luck']}"
                                 f" DSR={r['DSR']}")
                lines.append(f"  共 {len(sig)}/{n_ok} 只显著;未上榜≠废票,仅策略选择≈随机,信号降权看待")
            else:
                lines.append(f"  本轮 0/{n_ok} 只显著(全池最优策略均无法与运气区分,重要信号)")
        else:
            lines.append("(dsr_audit 未生成,检查步骤 3b)")
    except Exception as e:
        lines.append(f"(DSR名单读取失败: {e})")
    lines.append("")

    # 六、候审策略变更（2026-10-11 #8 新增；读步骤 3c 分流后的 strategy_pending.json）
    # 等待/恢复语义：候审存在=waiting；用户批准后 apply --apply-symbol=resume。
    lines.append("六、策略变更候审(DSR不显著,待人工批准)")
    try:
        pend_path = os.path.join(DATA_DIR, "strategy_pending.json")
        if os.path.exists(pend_path):
            with open(pend_path, encoding="utf-8") as f:
                pend = json.load(f)
            pend_ch = pend.get("changes", [])
            if pend_ch:
                for ch in pend_ch:
                    lines.append(f"  {ch['name']}({ch['symbol']}) {ch['old']} -> {ch['new']}")
                lines.append("  ⏸️ 以上变更未生效；批准=回复时说明,驳回=不处理/明示驳回 (apply_strategy_pending.py)")
            else:
                lines.append("  无候审变更")
        else:
            lines.append("  无候审变更")
    except Exception as e:
        lines.append(f"(候审清单读取失败: {e})")
    lines.append("")

    lines.append("—— 由 weekly_full_pipeline.py 自动生成（command cron 直跑，不经 LLM）")
    text = "\n".join(lines)
    _emit_dual_output(text)
    return text


def push_brief(brief_text):
    """用 send_telegram.py 纯文本推送到 Telegram；失败返回错误信息。

    ⚠️ 2026-09-17 修复：原先在此内联 `requests.post`，而本网络对 requests/urllib
    做 TLS 指纹识别 → ConnectionReset(54)，推送长期静默失败。
    现统一走 send_telegram 的传输层（curl_cffi 优先，自动回退 requests）。
    """
    try:
        import sys as _sys
        _sys.path.insert(0, WORKSPACE)
        if os.getenv("WFP_SKIP_PUSH"):
            # 2026-10-11(#7): 测试钩子——补跑干测时不重复推 Telegram
            print("(WFP_SKIP_PUSH=1，跳过推送)")
            return {"success": True, "skipped": True}
        from send_telegram import _post, API_URL, CHAT_ID
        # parse_mode="" 纯文本：含 () / 冒号易触发 Markdown 实体错误
        payload = {"chat_id": CHAT_ID, "text": brief_text, "parse_mode": ""}
        resp = _post(f"{API_URL}/sendMessage", json=payload, timeout=30)
        data = resp.json()
        if data.get("ok"):
            return {"success": True, "message_id": data.get("result", {}).get("message_id")}
        return {"success": False, "error": data.get("description", "unknown")}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    sys.exit(main())
