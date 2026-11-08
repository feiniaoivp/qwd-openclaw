#!/usr/bin/env python3
"""
Cron 健康度检查 & 推送成功率监控
===================================
每 4 小时运行一次，检查：
1. 各推送类 cron 任务的最近成功率（7天窗口）
2. 关键数据源健康状态
3. 失败阈值触发告警（通过 sessions_send 推送到主会话/Telegram）
4. 投递层半失败监控（2026-10-17）：status=ok 但 deliveryStatus=not-delivered 的
   「半失败」不计入成功率（本体成功），连续 >=2 次触发独立 warning 告警。
   背景：best-effort 改造后投递失败降级 diagnostic，status 不再反映投递层健康，
   本维度是投递链路（TLS 潮汐窗 / gateway announce 传输层）的唯一观测口。

输出：JSON 到 stdout（供 cron capture），同时若低于阈值主动推送告警。
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any

# sys.path 样板（TOOLS.md 强制）
WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis.net_guard import install_default_timeout
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)

install_default_timeout()

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# ══════════════════════════════════════
# 配置
# ══════════════════════════════════════

# 监控的关键 cron 任务（job_id -> 显示名/阈值）
MONITORED_JOBS = {
    "87c6b7f9-eb06-4ba6-9105-5d94b93ae8ad": {"name": "盘前深度分析", "min_success_rate": 0.8, "max_consecutive_failures": 3},
    "c7a8a955-ddb1-4a4b-8f5f-589507d5aa52": {"name": "中联重科盯盘(早)", "min_success_rate": 0.8, "max_consecutive_failures": 3},
    "c57f99fb-b3bc-4957-8c4b-c123dcec3ab3": {"name": "盘中预警(30min)", "min_success_rate": 0.7, "max_consecutive_failures": 5},
    "3950d3cc-0230-4241-a8ff-c0fdea3b33f2": {"name": "每日收盘推送", "min_success_rate": 0.9, "max_consecutive_failures": 2},
    "b954c808-fde1-46de-b2b4-8f83bb59d07d": {"name": "全组合模拟盘", "min_success_rate": 0.8, "max_consecutive_failures": 3},
    "ff8df2a8-6ee4-422a-b286-d88c16841bc1": {"name": "中联重科盯盘(收盘)", "min_success_rate": 0.8, "max_consecutive_failures": 3},
    "8da42083-63d4-4884-9909-938a3f5c5d12": {"name": "中联重科盯盘(午)", "min_success_rate": 0.8, "max_consecutive_failures": 3},
}

# 告警阈值
ALERT_COOLDOWN_HOURS = 6  # 同一任务告警冷却期

# 投递层半失败告警阈值（2026-10-17 新增）
# 单次 ok+not-delivered 属潮汐噪声（如 04:13 那轮），连续 >=2 次才说明投递链路持续被拦
DELIVERY_ALERT_MIN_CONSEC = 2

# 产物存在性检查（2026-10-21 新增）
# 背景：status=ok ≠ 产物存在。agentTurn 型 cron 的第三种「假 ok」变体：
#   模型收到命令型 prompt 后不执行、反而回问用户，run 照记 ok（23s），产物为零。
#   实证：2026-10-21 盘前报告静默缺失（transcript 坐实：模型 read 完
#   premarket_pipeline.py 后回复「需要我做什么？」即结束；同款 nemotron 的
#   zhonglian-morning 另有 4 次短时长可疑未确证）。
#   既有 cron_noop_guard 只能抓「毫秒级空转」，抓不住「LLM 花几十秒说废话」——
#   唯一可靠判据是产物本身。交易日 + 门时之后，产物必须在盘。
PRODUCT_ARTIFACT_CHECKS = [
    # (显示名, 相对 WORKSPACE 路径模板, 生效门时 HH:MM) — 路径带 {today} 日期戳
    ("盘前深度分析报告", "analysis/daily/{today}_premarket_report.md", "06:45"),
    ("收盘双策略日报", "analysis/daily/{today}_dual.md", "19:00"),
    ("竞价快照", "data/runtime/auction/{today}_9_25.json", "09:30"),
    ("竞价裁决", "data/runtime/auction/decision_{today}.json", "09:35"),
    ("量化日报", "analysis/backtest/{today}.md", "21:30"),
]

# 无日期戳的滚动覆盖型产物：按「当日 mtime >= fresh_after」判新鲜（2026-10-21 补）。
# gate = 何时开始检查；fresh_after = 当日 mtime 下限（略早于 job 预期写入时刻）。
PRODUCT_MTIME_CHECKS = [
    # (显示名, 相对 WORKSPACE 路径, 生效门时 HH:MM, 当日新鲜下限 HH:MM)
    ("盘前展望", "data/runtime/premarket_outlook_output.json", "08:45", "08:00"),
    ("中联盯盘(早)", "data/runtime/zhonglian_state.json", "09:20", "08:50"),
    ("中联盯盘(收盘)", "data/runtime/zhonglian_state.json", "19:00", "18:00"),
]


def missing_products(now: datetime = None, trading_day: bool = None,
                     exists_fn=None, mtime_fn=None) -> List[Dict[str, str]]:
    """纯函数：返回当前时刻「应存在却缺失」的交易日产物列表。

    now/trading_day/exists_fn/mtime_fn 均可注入（selftest 不触网、不读真实盘面）。
    trading_day 缺省时用 analysis.service.is_trading_day（SSOT，异常可见 + fail-open）。
    """
    now = now or datetime.now()
    if trading_day is None:
        try:
            from analysis.service import is_trading_day  # 交易日判定 SSOT（TOOLS.md 口径）
            trading_day = is_trading_day(now.date())
        except Exception as e:
            log.warning(f"交易日判定不可用（{e}），按交易日处理（fail-open，宁误报不漏报）")
            trading_day = True
    if not trading_day:
        return []
    exists_fn = exists_fn or os.path.exists
    mtime_fn = mtime_fn or os.path.getmtime
    missing = []
    today = now.strftime("%Y-%m-%d")
    for name, pattern, gate in PRODUCT_ARTIFACT_CHECKS:
        gate_h, gate_m = int(gate[:2]), int(gate[3:5])
        if (now.hour, now.minute) < (gate_h, gate_m):
            continue
        path = os.path.join(WORKSPACE, pattern.format(today=today))
        if not exists_fn(path):
            missing.append({"product": name, "expected": path, "gate": gate, "type": "exists"})
    for name, pattern, gate, fresh_after in PRODUCT_MTIME_CHECKS:
        gate_h, gate_m = int(gate[:2]), int(gate[3:5])
        if (now.hour, now.minute) < (gate_h, gate_m):
            continue
        path = os.path.join(WORKSPACE, pattern)
        fresh_h, fresh_m = int(fresh_after[:2]), int(fresh_after[3:5])
        fresh_dt = now.replace(hour=fresh_h, minute=fresh_m, second=0, microsecond=0)
        try:
            m = mtime_fn(path)
        except OSError:
            missing.append({"product": name, "expected": path, "gate": gate,
                            "type": "mtime", "reason": "文件不存在"})
            continue
        if m < fresh_dt.timestamp():
            missing.append({"product": name, "expected": path, "gate": gate,
                            "type": "mtime", "reason": f"当日 mtime 早于 {fresh_after}"})
    return missing

# ══════════════════════════════════════
# Cron API 封装
# ══════════════════════════════════════

def _gateway_request(action: str, payload: Dict = None) -> Any:
    """[已降级为兜底] 旧 HTTP API `POST /api/cron/{action}` 实测 404（2026-09-28）。
    曾因 except 只走 log.debug 静默吞掉 404，导致监控链路断 10 天无人知晓。
    现在失败必须 log.warning 可见（TOOLS.md「except Exception 禁止静默」）。"""
    try:
        import urllib.request
        import urllib.parse
        
        # gateway 默认端口 18789
        url = f"http://127.0.0.1:18789/api/cron/{action}"
        data = json.dumps(payload or {}).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        log.warning(f"[旧HTTP API 已404] gateway cron {action} 不可用(预期内): {type(e).__name__}")
        return None

def _cli_cron(args: List[str], retries: int = 2) -> Any:
    """通过 openclaw CLI 调 gateway cron API（权威路径：本地 CLI 自动带 token 走 WebSocket）。
    CLI 在 gateway 事件循环拥堵时可能间歇性失败/返回空/结构异常——重试 2 次，仍失败才降级。
    每次失败必须 log.warning 可见（TOOLS.md「except Exception 禁止静默」）。"""
    import subprocess
    last_err = ""
    for attempt in range(1, retries + 2):
        try:
            proc = subprocess.run(
                ["openclaw", "cron"] + args,
                capture_output=True, text=True, timeout=90,
            )
            if proc.returncode != 0:
                last_err = f"退出码 {proc.returncode}: {proc.stderr[:200]}"
            else:
                data = json.loads(proc.stdout)
                if isinstance(data, dict) and ("jobs" in data or "entries" in data):
                    return data
                last_err = f"返回结构异常: {str(data)[:120]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        log.warning(f"openclaw cron {' '.join(args[:2])} 第{attempt}次失败: {last_err}")
    return None

def cron_list() -> List[Dict]:
    """获取所有 cron 任务列表：CLI 优先（权威），HTTP 兜底"""
    result = _cli_cron(["list", "--json"])
    if result and result.get("jobs"):
        return result["jobs"]
    result = _gateway_request("list", {"includeDisabled": True})
    return result.get("jobs", []) if result else []

def get_cron_runs(job_id: str, limit: int = 100) -> List[Dict]:
    """获取指定任务的最近运行记录：CLI 优先（权威），HTTP 兜底"""
    result = _cli_cron(["runs", "--id", job_id, "--limit", str(limit)])
    if result is not None and "entries" in result:
        return result.get("entries", [])
    result = _gateway_request("runs", {"jobId": job_id, "limit": limit})
    return result.get("entries", []) if result else []


# ══════════════════════════════════════
# 备用方案：直接读取 cron 运行历史（如果有本地落盘）
# ══════════════════════════════════════

CRON_STATE_DIR = os.path.join(WORKSPACE, "data", "cron_runs")

def load_local_runs(job_id: str) -> List[Dict]:
    """从本地落盘读取运行记录（如果有）"""
    path = os.path.join(CRON_STATE_DIR, f"{job_id}.jsonl")
    if not os.path.exists(path):
        return []
    runs = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    runs.append(json.loads(line))
    except Exception as e:
        log.warning(f"读取本地 runs 失败 {job_id}: {e}")
    return runs

def save_local_run(job_id: str, run: Dict):
    """增量落盘单条运行记录（去重 by runAtMs）"""
    path = os.path.join(CRON_STATE_DIR, f"{job_id}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    # 读取现有
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        r = json.loads(line)
                        existing[r.get("runAtMs", 0)] = r
        except Exception:
            pass
    
    # 去重合并
    existing[run.get("runAtMs", 0)] = run
    
    # 写回（按时间排序）
    try:
        _bak(path)
        with open(path, "w") as f:
            for r in sorted(existing.values(), key=lambda x: x.get("runAtMs", 0)):
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"落盘本地 run 失败 {job_id}: {e}")


# ══════════════════════════════════════
# 核心检查逻辑
# ══════════════════════════════════════

def _is_delivery_failed(run: Dict) -> bool:
    """单条 run 是否为「半失败」：本体 ok 但投递未送达。
    deliveryStatus 四态：'delivered'=已送达 / 'not-delivered'=投递失败 /
    'not-requested'=未走 gateway 投递（脚本自推，正常） / 'unknown'=旧数据。
    兼容旧字段：deliveryStatus 缺失时看 delivered=False。"""
    ds = run.get("deliveryStatus")
    if ds == "not-delivered":
        return True
    if ds is None and run.get("delivered") is False:
        return True
    return False


def delivery_stats(recent: List[Dict]) -> Dict:
    """统计投递层健康度（纯函数，--selftest 可直接测，不触网）。
    - delivery_failures: 窗口内半失败总次数
    - consecutive_delivery_failures: 从最近一条往回的连续半失败数
      终止条件：error/unknown 状态、一次正常投递（delivered 或 not-requested）。
      unknown 保守停止——旧数据无法判定，宁可漏报不误报。"""
    delivery_failures = sum(
        1 for r in recent if r.get("status") == "ok" and _is_delivery_failed(r)
    )
    consecutive = 0
    for r in sorted(recent, key=lambda x: x.get("runAtMs", 0), reverse=True):
        if r.get("status") != "ok":
            break  # 本体失败交给 consecutive_failures 口径
        if _is_delivery_failed(r):
            consecutive += 1
        else:
            break  # delivered/not-requested/unknown → 停止
    return {
        "delivery_failures": delivery_failures,
        "consecutive_delivery_failures": consecutive,
    }


def analyze_job_health(job_id: str, config: Dict, gw_job: Dict = None) -> Dict:
    """分析单个任务健康度

    数据源优先级：gateway runs(CLI) > 本地 jsonl。
    2026-09-28 新增：用 gateway list 的 lastRunAtMs/nextRunAtMs 做权威陈旧度校验——
    修复「runs 采集链路断 + 本地数据停更 + 7天窗口滑出 → 全部 no_data → 误判正常」
    的监控盲区（实证：该监控 09-18 起实际已盲 10 天，期间一直报"正常"）。
    """
    # 优先从 gateway 获取（最新、权威）
    runs = get_cron_runs(job_id, limit=100)
    if not runs:
        # 回退本地
        runs = load_local_runs(job_id)
    else:
        # 同步落盘本地（增量去重可简化为全量覆盖）
        for r in runs:
            save_local_run(job_id, r)
    
    now = time.time()
    window_start = now - 7 * 24 * 3600  # 7天窗口（覆盖日频任务）
    
    recent = [r for r in runs if r.get("runAtMs", 0) / 1000 >= window_start]

    # ── 权威字段（来自 gateway list，不依赖 runs 历史）──
    gw_last_run_ms = (gw_job or {}).get("lastRunAtMs")
    gw_next_run_ms = (gw_job or {}).get("nextRunAtMs")
    gw_enabled = (gw_job or {}).get("enabled", True)
    gw_last_status = (gw_job or {}).get("lastRunStatus")
    # 调度未推进：计划时间已过 >1h 仍未更新 → 疑似错过计划运行
    missed_schedule = bool(gw_next_run_ms and gw_next_run_ms / 1000 < now - 3600)

    if not recent:
        if gw_last_run_ms and gw_last_run_ms / 1000 >= window_start:
            # runs 采集失败但 gateway 显示近期有运行 → 监控部分失明（不再伪装 no_data）
            status = "partial_data"
            message = "runs 历史不可用，但 gateway 显示近期有运行（监控数据链路部分失效）"
        elif missed_schedule:
            next_txt = f"{datetime.fromtimestamp(gw_next_run_ms/1000):%m-%d %H:%M}" if gw_next_run_ms else "?"
            status = "critical"
            message = f"调度未推进：计划时间 {next_txt} 已过且无任何运行记录"
        elif gw_last_run_ms:
            last_dt = datetime.fromtimestamp(gw_last_run_ms/1000).strftime("%Y-%m-%d %H:%M")
            if gw_enabled:
                status = "critical"
                message = f"7天窗口无运行记录；gateway 最后运行 {last_dt} ({gw_last_status})"
            else:
                status = "healthy"
                message = f"任务已停用；gateway 最后运行 {last_dt}"
        else:
            status = "no_data"
            message = "7天窗口无运行记录且 gateway 无 lastRun 字段"
        return {
            "job_id": job_id,
            "name": config["name"],
            "status": status,
            "message": message,
            "success_rate": None,
            "total_runs": 0,
            "ok_runs": 0,
            "failed_runs": 0,
            "delivery_failures": 0,
            "consecutive_delivery_failures": 0,
            "consecutive_failures": 0,
            "last_run": None,
            "gateway": {"enabled": gw_enabled, "last_status": gw_last_status},
        }
    
    # 成功/失败统计
    ok = [r for r in recent if r.get("status") == "ok"]
    failed = [r for r in recent if r.get("status") == "error"]
    success_rate = len(ok) / len(recent) if recent else 0
    
    # 连续失败数（倒序）
    consecutive_failures = 0
    for r in sorted(recent, key=lambda x: x.get("runAtMs", 0), reverse=True):
        if r.get("status") == "error":
            consecutive_failures += 1
        else:
            break
    
    last_run = max(recent, key=lambda x: x.get("runAtMs", 0)) if recent else None
    dstats = delivery_stats(recent)
    
    # 判定状态
    if success_rate < config["min_success_rate"]:
        status = "degraded"
    elif consecutive_failures >= config["max_consecutive_failures"]:
        status = "critical"
    elif missed_schedule:
        status = "degraded"  # 近期有运行但调度未推进（疑似错过计划时间）
    else:
        status = "healthy"
    
    return {
        "job_id": job_id,
        "name": config["name"],
        "status": status,
        "success_rate": round(success_rate, 3),
        "total_runs": len(recent),
        "ok_runs": len(ok),
        "failed_runs": len(failed),
        "delivery_failures": dstats["delivery_failures"],
        "consecutive_delivery_failures": dstats["consecutive_delivery_failures"],
        "consecutive_failures": consecutive_failures,
        "last_run": {
            "time": datetime.fromtimestamp(last_run["runAtMs"]/1000).strftime("%Y-%m-%d %H:%M:%S") if last_run else None,
            "status": last_run.get("status") if last_run else None,
            "error": last_run.get("error") if last_run else None,
        } if last_run else None,
        "thresholds": {
            "min_success_rate": config["min_success_rate"],
            "max_consecutive_failures": config["max_consecutive_failures"],
        },
        "message": ("调度未推进" if missed_schedule else ""),
        "gateway": {"enabled": gw_enabled, "last_status": gw_last_status},
    }


def check_data_sources() -> Dict:
    """检查关键数据源健康状态"""
    try:
        from analysis.data_layer.router import get_router
        router = get_router()
        health = router.health_check()
        return {
            "timestamp": datetime.now().isoformat(),
            "sources": health,
            "overall": all(health.values()) if health else False,
        }
    except Exception as e:
        log.warning(f"数据源健康检查失败: {e}")
        return {
            "timestamp": datetime.now().isoformat(),
            "sources": {},
            "overall": False,
            "error": str(e),
        }


def should_alert(job_id: str, status: str) -> bool:
    """判断是否需要告警（冷却期去重）"""
    if status not in ("degraded", "critical", "warning"):
        return False
    
    alert_file = os.path.join(CRON_STATE_DIR, f"_alert_{job_id}.json")
    now = time.time()
    
    if os.path.exists(alert_file):
        try:
            with open(alert_file, "r") as f:
                data = json.load(f)
            last_alert = data.get("last_alert_ts", 0)
            if now - last_alert < ALERT_COOLDOWN_HOURS * 3600:
                return False
        except Exception:
            pass
    
    # 记录告警时间
    try:
        os.makedirs(CRON_STATE_DIR, exist_ok=True)
        _bak(alert_file)
        with open(alert_file, "w") as f:
            json.dump({"last_alert_ts": now, "status": status}, f)
    except Exception:
        pass
    
    return True


def send_alert(message: str):
    """发送告警（尝试多种渠道）"""
    # 1. 尝试 sessions_send 到主会话
    try:
        from sessions_send import sessions_send
        sessions_send(message=message)
        log.info("告警已通过 sessions_send 发送")
        return True
    except Exception as e:
        log.debug(f"sessions_send 失败: {e}")
    
    # 2. 尝试 Telegram（如果配置了）
    try:
        from send_telegram import send_telegram
        send_telegram(message)
        log.info("告警已通过 Telegram 发送")
        return True
    except Exception as e:
        log.debug(f"Telegram 发送失败: {e}")
    
    # 3. 至少落盘
    alert_log = os.path.join(CRON_STATE_DIR, "_alerts.log")
    try:
        with open(alert_log, "a") as f:
            f.write(f"{datetime.now().isoformat()} | {message}\n")
    except Exception:
        pass
    
    log.warning(f"告警仅落盘: {message}")
    return False


def main():
    log.info("=== Cron 健康度检查开始 ===")
    
    os.makedirs(CRON_STATE_DIR, exist_ok=True)
    
    # 1. 检查各监控任务
    job_results = {}
    alerts = []
    
    # gateway 权威清单（lastRunAtMs/nextRunAtMs 用于陈旧度校验，不依赖 runs 历史）
    gw_jobs_index = {j.get("id"): j for j in cron_list()}
    if not gw_jobs_index:
        # 监控数据链路整体失效（CLI+HTTP 均不可用）——这正是曾静默 10 天的元故障，必须响
        send_alert("🚨 [Cron健康告警] 监控数据链路失效：gateway 任务清单获取失败（CLI+HTTP 均不可用），本次检查结果不可信")
    log.info(f"gateway 任务清单: {len(gw_jobs_index)} 个")
    
    for job_id, config in MONITORED_JOBS.items():
        result = analyze_job_health(job_id, config, gw_jobs_index.get(job_id))
        job_results[job_id] = result
        
        log.info(f"  {config['name']}: {result['status']} "
                 f"(成功率={result['success_rate']}, 连续失败={result['consecutive_failures']}, "
                 f"半失败={result['delivery_failures']}/连续={result['consecutive_delivery_failures']}, "
                 f"总运行={result['total_runs']})")
        
        if should_alert(job_id, result["status"]):
            rate_str = f"{result['success_rate']:.1%}" if result.get("success_rate") is not None else "N/A"
            msg = (f"🚨 [Cron健康告警] {config['name']} 状态异常\n"
                   f"状态: {result['status']} - {result.get('message', '')}\n"
                   f"成功率: {rate_str} (阈值 {config['min_success_rate']:.0%})\n"
                   f"连续失败: {result['consecutive_failures']} (阈值 {config['max_consecutive_failures']})\n"
                   f"最近运行: {result['last_run']}")
            alerts.append({"job_id": job_id, "message": msg, "level": "critical" if result["status"] == "critical" else "warning"})
            send_alert(msg)

        # 投递层半失败独立告警（2026-10-17）：本体 ok 但连续未送达
        if (result.get("consecutive_delivery_failures", 0) >= DELIVERY_ALERT_MIN_CONSEC
                and should_alert(f"{job_id}__delivery", "warning")):
            msg = (f"⚠️ [Cron投递告警] {config['name']} 连续 {result['consecutive_delivery_failures']} 次「本体成功但投递未送达」\n"
                   f"7天窗口半失败: {result['delivery_failures']} 次\n"
                   f"本体正常（成功率不受影响）；疑似 TLS 潮汐窗 / gateway announce 传输层持续拦截\n"
                   f"最近运行: {result['last_run']}")
            alerts.append({"job_id": job_id, "message": msg, "level": "warning"})
            send_alert(msg)
    
    # 2. 检查数据源
    ds_health = check_data_sources()
    log.info(f"数据源健康: {ds_health['overall']} | 详情: {ds_health.get('sources', {})}")
    
    if not ds_health["overall"] and should_alert("data_sources", "degraded"):
        msg = f"🚨 [数据源告警] 关键数据源不可用\n详情: {json.dumps(ds_health.get('sources', {}), ensure_ascii=False)}"
        alerts.append({"job_id": "data_sources", "message": msg, "level": "critical"})
        send_alert(msg)
    
    # 2.5 产物存在性检查（2026-10-21 新增：status=ok ≠ 产物存在）
    products_missing = missing_products()
    for item in products_missing:
        key = f"product_{item['product']}"
        if should_alert(key, "critical"):
            msg = (f"🚨 [产物缺失告警] 交易日产物未生成：{item['product']}\n"
                   f"期望路径: {item['expected']}\n"
                   f"门时 {item['gate']} 后应存在；cron 层可能显示 ok\n"
                   f"（假 ok 第三变体：agentTurn 模型未执行命令即结束，10-17/10-21 实证）\n"
                   f"处置: 查该 job isolated session transcript + data/runtime/agent_runs/ 当日 run 文件")
            alerts.append({"job_id": key, "message": msg, "level": "critical"})
            send_alert(msg)
    log.info(f"产物存在性: {'缺失 ' + str(len(products_missing)) + ' 项' if products_missing else 'OK'}")

    # 3. 汇总报告
    summary = {
        "timestamp": datetime.now().isoformat(),
        "jobs": job_results,
        "data_sources": ds_health,
        "products_missing": products_missing,
        "alerts_triggered": len(alerts),
        "alert_details": alerts,
    }
    
    # 输出 JSON（cron capture）
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    
    # 落盘历史
    hist_file = os.path.join(CRON_STATE_DIR, f"health_check_{datetime.now().strftime('%Y%m%d')}.jsonl")
    try:
        with open(hist_file, "a") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except Exception:
        pass
    
    log.info(f"=== Cron 健康度检查完成，触发 {len(alerts)} 条告警 ===")
    
    # 非零退出码表示有告警（可被外层监控感知）
    sys.exit(1 if alerts else 0)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        # 对抗性自测 delivery_stats（不触网，套用 guard --selftest 惯例）
        def _r(status, run_at, ds=None, delivered=None):
            return {"status": status, "runAtMs": run_at, "deliveryStatus": ds, "delivered": delivered}
        cases = [
            ("全 delivered -> 0/0", [_r("ok", 3, "delivered"), _r("ok", 2, "delivered")], 0, 0),
            ("not-requested(脚本自推) 不算半失败", [_r("ok", 3, "not-requested"), _r("ok", 2, "not-requested")], 0, 0),
            ("单次半失败 -> 1/1", [_r("ok", 3, "not-delivered"), _r("ok", 2, "delivered")], 1, 1),
            ("连续2次半失败 -> 2/2 触发线", [_r("ok", 3, "not-delivered"), _r("ok", 2, "not-delivered"), _r("ok", 1, "delivered")], 2, 2),
            ("成功投递打断连续计数", [_r("ok", 3, "delivered"), _r("ok", 2, "not-delivered"), _r("ok", 1, "delivered")], 1, 0),
            ("error 打断连续计数(交给本体口径)", [_r("error", 3), _r("ok", 2, "not-delivered"), _r("ok", 1, "not-delivered")], 2, 0),
            ("旧字段 delivered=False 兼容", [_r("ok", 3, None, False)], 1, 1),
            ("unknown 状态保守停止", [_r("ok", 3, "unknown"), _r("ok", 2, "not-delivered")], 1, 0),
            ("空列表 -> 0/0", [], 0, 0),
        ]
        failed = 0
        for name, runs, exp_f, exp_c in cases:
            got = delivery_stats(runs)
            ok = got["delivery_failures"] == exp_f and got["consecutive_delivery_failures"] == exp_c
            print(f"{'OK ' if ok else 'NG '} {name}: got={got} 期望=({exp_f},{exp_c})")
            failed += 0 if ok else 1

        # missing_products 纯函数自测（2026-10-21 新增；不触网：显式注入 now/trading_day/exists_fn/mtime_fn）
        from datetime import datetime as _dt
        _no = lambda p: False   # 模拟全部缺失
        _yes = lambda p: True   # 模拟全部存在
        def _mtime_missing(p):
            raise OSError(p)
        def _mtime_fresh(p):
            # 按产物分派「当日新鲜」mtime（均晚于各自 fresh_after）
            return _dt(2026, 10, 21, 8, 31).timestamp() if "outlook" in p else _dt(2026, 10, 21, 9, 1).timestamp()
        def _mtime_stale(p):
            return _dt(2026, 10, 20, 18, 30).timestamp()  # 昨日 mtime -> 陈旧
        cases2 = [
            ("非交易日 -> 永不告警", _dt(2026, 10, 17, 8, 0), False, _no, _mtime_fresh, 0),
            ("交易日门时前(05:00) -> 跳过", _dt(2026, 10, 21, 5, 0), True, _no, _mtime_missing, 0),
            ("交易日 08:00 全缺 -> 1 项（仅盘前门已过）", _dt(2026, 10, 21, 8, 0), True, _no, _mtime_missing, 1),
            ("交易日 08:00 全在 -> 0 项", _dt(2026, 10, 21, 8, 0), True, _yes, _mtime_fresh, 0),
            ("交易日 12:00 全缺 -> 5 项", _dt(2026, 10, 21, 12, 0), True, _no, _mtime_missing, 5),
            ("交易日 12:00 全在 -> 0 项", _dt(2026, 10, 21, 12, 0), True, _yes, _mtime_fresh, 0),
            ("交易日 12:00 mtime 陈旧 -> 2 项（mtime 类）", _dt(2026, 10, 21, 12, 0), True, _yes, _mtime_stale, 2),
            ("交易日 20:00 全缺 -> 7 项", _dt(2026, 10, 21, 20, 0), True, _no, _mtime_missing, 7),
        ]
        failed2 = 0
        for name, now_arg, td_arg, ex_fn, mt_fn, exp in cases2:
            got = missing_products(now=now_arg, trading_day=td_arg, exists_fn=ex_fn, mtime_fn=mt_fn)
            ok = len(got) == exp
            print(f"{'OK ' if ok else 'NG '} {name}: got={len(got)} 期望={exp}")
            failed2 += 0 if ok else 1
        print(f"自测结果: {'PASS' if (failed + failed2) == 0 else 'FAIL'} "
              f"(delivery {len(cases)} 例 + product {len(cases2)} 例, {failed + failed2} 失败)")
        sys.exit(1 if (failed + failed2) else 0)
    main()
