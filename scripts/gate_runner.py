#!/usr/bin/env python3
"""gate_runner — 全门禁统一执行器 + 分级临时开关（T1-治理层, 2026-11-04）

SSOT: config/system_manifest.json（15 门禁 / 8 分类）
开关: config/gate_overrides.json（按 category 整组、必须带 TTL，过期自动恢复）
审计: data/gate_overrides_log.jsonl（每次启用/禁用/到期自动留痕）

用法:
    python3 scripts/gate_runner.py --run                 # 跑全部（受 overrides 影响）
    python3 scripts/gate_runner.py --run --tier P0       # 只跑 P0
    python3 scripts/gate_runner.py --list                # 清单表
    python3 scripts/gate_runner.py --verify              # 清单完整性校验（路径/schema）
    python3 scripts/gate_runner.py --generate-cron       # 根据 auto_schedule 自动生成/更新 cron jobs
    python3 scripts/gate_runner.py --disable code_quality 4 "深度调试中"   # 临时禁 4 小时
    python3 scripts/gate_runner.py --enable code_quality  # 提前恢复

环境变量（一次性、无落盘）:
    GATE_DISABLE=code_quality,cron_health  # 本进程内等效临时禁用（自愈于进程结束）

供其它模块调用（周流水线 run_script 已接入）:
    from scripts.gate_runner import gate_enabled_for
    gate_enabled_for("scripts/xxx_guard.py") -> (True/False, reason)
"""
import os
import sys
import json
import time
import subprocess
from datetime import datetime, timedelta

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MANIFEST_FILE = os.path.join(WORKSPACE, "config", "system_manifest.json")
OVERRIDES_FILE = os.path.join(WORKSPACE, "config", "gate_overrides.json")
OVERRIDE_LOG = os.path.join(WORKSPACE, "data", "gate_overrides_log.jsonl")

# P0 安全级分类：禁用时 reason 必须含 APPROVE（人为确认标记）
P0_SECURE_CATEGORIES = {"supply_chain", "market_data_citation"}


def _now():
    return datetime.now()


def load_manifest():
    with open(MANIFEST_FILE, encoding="utf-8") as f:
        return json.load(f)


def _load_overrides():
    if not os.path.exists(OVERRIDES_FILE):
        return {}
    try:
        with open(OVERRIDES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("overrides", {})
    except Exception as e:
        print(f"⚠️ overrides 读取失败（按全部启用处理）: {e}")
        return {}


def _save_overrides(overrides: dict):
    os.makedirs(os.path.dirname(OVERRIDES_FILE), exist_ok=True)
    # 重写型写点备份保护（backup_guard 铁律）
    if os.path.exists(OVERRIDES_FILE):
        import shutil
        try:
            shutil.copy2(OVERRIDES_FILE, OVERRIDES_FILE + ".bak")
        except Exception as e:
            print(f"⚠️ overrides 备份失败（继续写入）: {e}")
    tmp = OVERRIDES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"updated_at": _now().isoformat(), "overrides": overrides},
                  f, ensure_ascii=False, indent=2)
    os.replace(tmp, OVERRIDES_FILE)


def _log_override(action: str, category: str, detail: dict):
    """开关变更审计（永不停用审计本身）"""
    try:
        os.makedirs(os.path.dirname(OVERRIDE_LOG), exist_ok=True)
        entry = {"ts": _now().isoformat(), "action": action,
                 "category": category, **detail}
        with open(OVERRIDE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️ 开关审计写入失败: {e}")


def gate_enabled_for(script_path: str, manifest: dict = None):
    """查询某门禁脚本当前是否启用。返回 (enabled: bool, reason: str)。

    消费方：gate_runner --run / 周流水线 run_script / 任何门禁调用点。
    判定顺序：manifest 未知脚本 → 启用（新门禁默认开）；
              category 级 override（TTL 未过期）→ 禁用；
              环境变量 GATE_DISABLE → 禁用（进程内一次性）。
    """
    rel = script_path if not os.path.isabs(script_path) else os.path.relpath(script_path, WORKSPACE)
    m = manifest or load_manifest()
    gate = next((g for g in m.get("gates", []) if g["path"] == rel), None)
    if gate is None:
        return True, "not-in-manifest(默认启用)"

    cat = gate.get("category", "")
    # 1) 文件级 override（带 TTL）
    overrides = _load_overrides()
    ov = overrides.get(cat)
    if ov:
        until = ov.get("disabled_until")
        try:
            if until and _now() < datetime.fromisoformat(until):
                return False, f"override[{cat}] 至 {until}: {ov.get('reason','')}"
            # TTL 过期 → 自动恢复 + 留痕
            overrides.pop(cat, None)
            _save_overrides(overrides)
            _log_override("auto_expire", cat, {"expired_until": until})
            print(f"♻️ 门禁组 [{cat}] override 已到期，自动恢复")
        except ValueError:
            return False, f"override[{cat}] disabled_until 格式非法: {until}（按禁用处理，请人工修复）"
    # 2) 环境变量一次性开关
    env_dis = os.getenv("GATE_DISABLE", "")
    if cat in [c.strip() for c in env_dis.split(",") if c.strip()]:
        return False, f"env GATE_DISABLE[{cat}]（进程内一次性）"
    return True, "enabled"


def _run_gate(gate: dict, verbose: bool = True) -> dict:
    """单门禁执行（带超时；返回结构化结果）"""
    path = os.path.join(WORKSPACE, gate["path"])
    t0 = time.time()
    if not os.path.exists(path):
        return {"id": gate["id"], "status": "MISSING", "exit": None,
                "dur_s": 0, "msg": f"脚本不存在: {gate['path']}"}
    try:
        cmd = [sys.executable, path] + list(gate.get("args", []))
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=gate.get("timeout_s", 120), cwd=WORKSPACE)
        dur = round(time.time() - t0, 2)
        status = "PASS" if r.returncode == 0 else "FAIL"
        return {"id": gate["id"], "status": status, "exit": r.returncode,
                "dur_s": dur, "msg": (r.stdout or r.stderr or "").strip().splitlines()[-1] if (r.stdout or r.stderr) else ""}
    except subprocess.TimeoutExpired:
        return {"id": gate["id"], "status": "TIMEOUT", "exit": None,
                "dur_s": round(time.time() - t0, 2), "msg": f"超时 >{gate.get('timeout_s',120)}s"}
    except Exception as e:
        return {"id": gate["id"], "status": "ERROR", "exit": None,
                "dur_s": round(time.time() - t0, 2), "msg": str(e)[:120]}


def cmd_run(args):
    m = load_manifest()
    tier = args.tier
    gates = [g for g in m["gates"] if (tier is None or g["tier"] == tier)]
    results, disabled = [], []
    for g in gates:
        ok, reason = gate_enabled_for(g["path"], m)
        if not ok:
            disabled.append({"id": g["id"], "reason": reason})
            if args.verbose:
                print(f"⏸️  {g['id']:<22} SKIPPED  {reason}")
            continue
        if args.verbose:
            print(f"▶️  {g['id']:<22} running...", flush=True)
        r = _run_gate(g, args.verbose)
        r["severity"] = g.get("severity", "warn")
        r["tier"] = g.get("tier")
        results.append(r)
        if args.verbose:
            emoji = {"PASS": "✅", "FAIL": "❌", "TIMEOUT": "⏱️", "ERROR": "💥", "MISSING": "❓"}.get(r["status"], "·")
            print(f"   {emoji} {r['status']:<8} {r['dur_s']}s  {r['msg'][:100]}")

    print("\n═══ 汇总 ═══")
    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_fail = [r for r in results if r["status"] in ("FAIL", "TIMEOUT", "ERROR", "MISSING")]
    print(f"通过 {n_pass}/{len(results)} | 失败 {len(n_fail)} | 跳过(禁用) {len(disabled)}")
    for r in n_fail:
        print(f"  ❌ [{r['severity']}/{r['tier']}] {r['id']}: {r['msg'][:120]}")
    for d in disabled:
        print(f"  ⏸️ {d['id']}: {d['reason']}")

    # block 级失败 → exit 1；warn 级失败 → exit 0（仅报告）
    block_fails = [r for r in n_fail if r["severity"] == "block"]
    if block_fails:
        print(f"\n🔴 {len(block_fails)} 个 block 级门禁失败")
        return 1
    return 0


def cmd_list():
    m = load_manifest()
    overrides = _load_overrides()
    print(f"{'id':<22}{'tier':<5}{'category':<22}{'severity':<9}{'状态':<10}描述")
    print("─" * 110)
    for g in m["gates"]:
        ok, reason = gate_enabled_for(g["path"], m)
        status = "⏸️ 禁用" if not ok else "✅ 启用"
        print(f"{g['id']:<22}{g['tier']:<5}{g['category']:<22}{g['severity']:<9}{status:<10}{g['description'][:40]}")
    print(f"\n共 {len(m['gates'])} 门禁 / {len(m['categories'])} 分类")
    if overrides:
        print("当前 overrides:")
        for cat, ov in overrides.items():
            print(f"  {cat}: 至 {ov.get('disabled_until')} — {ov.get('reason','')}")


def cmd_verify():
    m = load_manifest()
    errs = []
    seen_ids = set()
    cats = set(m.get("categories", {}).keys())
    for g in m["gates"]:
        if g["id"] in seen_ids:
            errs.append(f"重复 id: {g['id']}")
        seen_ids.add(g["id"])
        if not os.path.exists(os.path.join(WORKSPACE, g["path"])):
            errs.append(f"{g['id']}: 脚本不存在 {g['path']}")
        if g["category"] not in cats:
            errs.append(f"{g['id']}: 未登记分类 {g['category']}")
        if g["severity"] not in ("block", "warn"):
            errs.append(f"{g['id']}: 非法 severity {g['severity']}")
        if g["tier"] not in ("P0", "P1", "P2"):
            errs.append(f"{g['id']}: 非法 tier {g['tier']}")
    if errs:
        print("❌ manifest 校验失败:")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(f"✅ manifest 校验通过: {len(m['gates'])} 门禁路径/分类/tier 全部合法")
    return 0


def cmd_generate_cron():
    """根据 manifest 中的 auto_schedule 自动生成/更新 cron jobs"""
    m = load_manifest()
    print("🔧 根据 auto_schedule 生成 cron jobs...")
    
    cron_jobs = []
    for g in m["gates"]:
        auto_sched = g.get("auto_schedule")
        if not auto_sched:
            continue
        kind = auto_sched.get("kind")
        if kind != "cron":
            print(f"  ⚠️ {g['id']}: auto_schedule.kind={kind} 暂不支持，跳过")
            continue
        expr = auto_sched.get("expr")
        tz = auto_sched.get("tz", "Asia/Shanghai")
        tier = g.get("tier", "P1")
        
        # 构建 cron job 定义
        job_name = f"gate-{g['id']}"
        job = {
            "name": job_name,
            "displayName": f"Gate Runner: {g['id']} ({tier})",
            "schedule": {"kind": "cron", "expr": expr, "tz": tz},
            "sessionTarget": "isolated",
            "payload": {
                "kind": "agentTurn",
                "message": f"python3 /Users/duguke/.openclaw/workspace/scripts/gate_runner.py --run --tier {tier} --quiet",
                "timeoutSeconds": g.get("timeout_s", 120),
                "toolsAllow": ["read", "edit", "write", "apply_patch", "exec", "process", "cron", "filesystem__read_text_file", "filesystem__write_file", "filesystem__list_directory", "filesystem__search_files", "memory_search", "memory_get", "web_search", "web_fetch"]
            },
            "delivery": {"mode": "none"},
            "enabled": True,
            "description": f"Auto-generated from system_manifest.json: {g['description']}"
        }
        cron_jobs.append(job)
        print(f"  📋 {job_name}: {expr} ({tz}) tier={tier} -> {g['path']}")
    
    # 输出 cron jobs JSON 供人工审核/注册
    import json as _json
    print(f"\n✅ 生成 {len(cron_jobs)} 个 cron job 定义")
    print("\n=== 可直接注册的 cron jobs (JSON) ===")
    for job in cron_jobs:
        print(_json.dumps(job, ensure_ascii=False))
    
    return 0


def cmd_disable(category: str, hours: float, reason: str):
    m = load_manifest()
    if category not in m.get("categories", {}):
        print(f"❌ 未知分类: {category}（可选: {', '.join(m['categories'])}）")
        return 1
    # P0 安全级：需人工确认标记
    cat_gates = [g for g in m["gates"] if g["category"] == category]
    if any(g["category"] in P0_SECURE_CATEGORIES for g in cat_gates) and "APPROVE" not in reason.upper():
        print(f"🔴 安全级分类 [{category}] 禁用需 reason 含 APPROVE 标记（人为确认）")
        return 1
    overrides = _load_overrides()
    until = (_now() + timedelta(hours=hours)).isoformat(timespec="seconds")
    overrides[category] = {"disabled_until": until, "reason": reason,
                           "created_at": _now().isoformat(timespec="seconds"),
                           "gates": [g["id"] for g in cat_gates]}
    _save_overrides(overrides)
    _log_override("disable", category, {"until": until, "hours": hours, "reason": reason})
    print(f"⏸️ 分类 [{category}] 已临时禁用至 {until}（{hours}h）")
    print(f"   影响门禁: {', '.join(g['id'] for g in cat_gates)}")
    print(f"   原因: {reason}")
    print(f"   恢复: python3 scripts/gate_runner.py --enable {category}")
    return 0


def cmd_enable(category: str):
    overrides = _load_overrides()
    if category in overrides:
        overrides.pop(category)
        _save_overrides(overrides)
        _log_override("enable", category, {})
        print(f"✅ 分类 [{category}] 已恢复启用")
    else:
        print(f"ℹ️ 分类 [{category}] 无活跃 override")
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="全门禁统一执行器 + 分级临时开关")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--generate-cron", action="store_true")
    ap.add_argument("--tier", choices=["P0", "P1", "P2"], default=None)
    ap.add_argument("--disable", metavar="CATEGORY", default=None)
    ap.add_argument("--hours", type=float, default=4.0)
    ap.add_argument("--reason", default="")
    ap.add_argument("--enable", metavar="CATEGORY", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    args.verbose = not args.quiet

    if args.disable:
        return cmd_disable(args.disable, args.hours, args.reason)
    if args.enable:
        return cmd_enable(args.enable)
    if args.list:
        cmd_list()
        return 0
    if args.verify:
        return cmd_verify()
    if args.generate_cron:
        return cmd_generate_cron()
    if args.run:
        return cmd_run(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
