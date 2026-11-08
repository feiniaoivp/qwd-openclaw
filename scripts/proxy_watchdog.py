#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
proxy_watchdog.py — FlClash 代理断流看门狗：健康巡检 + 自动切换节点

立项盘点（2026-11-08）：grep scripts/ 证实此前无代理健康/切换类脚本，本脚本是首份。
职责边界：
- 只做「健康巡检 → 断流时经 mihomo REST API 切换节点 → 验证 → 告警」。
- 不做 Telegram 推送（断流时推送通道本身不可用）；告警 = macOS 通知 + 本日志。
- FlClash 外部控制未开启时自动降级为「仅巡检告警」模式，不切换。
- 语义判定（缺数据显式中性，不猜）：API 不可达 = 不切换只告警；
  当前节点仍存活（延迟表里有它）= 问题在别处，不切只报。

用法：
  python3 scripts/proxy_watchdog.py              # 默认：巡检，必要时自动切换
  python3 scripts/proxy_watchdog.py --dry-run    # 巡检 + 给出切换方案，不实际 PUT
  python3 scripts/proxy_watchdog.py --check      # 只做健康检查（exit 0=健康 1=断流）
  python3 scripts/proxy_watchdog.py --rehearse   # 演练：对当前选中组原样回写（零状态变更）验证写权限
  python3 scripts/proxy_watchdog.py --status     # 打印内部状态与控制器信息

设计要点：
- 健康判定：两个探测 URL 都失败才算断流（单站故障不误切；中途睡 4s 再测第二个）。
- 切换目标：从「未被其他组引用的顶层组」沿 now 链下钻，取链上最深的 Selector 组；
  对该组全体成员跑延迟测试，选延迟最低的存活节点，PUT /proxies/<组>。
- 防误切：当前节点仍在延迟表里 → 节点活着，不切；候选为空 → 不切。
- 本机 API 调用一律 trust_env=False，绝不吃代理 env（防 loopback 502 伪影，见 TOOLS.md）。
- 状态文件写前先 .bak 备份（backup_guard 棘轮要求）；通知去重（同事件 30 分钟内不重发）。
- 单实例锁（flock），防 cron 重叠。
"""
import os
import sys
import json
import time
import shutil
import logging
import subprocess
import urllib.parse

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import requests

# 本机会话绝不消费 http_proxy 等 env（防 loopback 502 伪影，见 TOOLS.md）
SESSION = requests.Session()
SESSION.trust_env = False

FLCLASH_DIR = os.path.expanduser("~/Library/Application Support/com.follow.clash")
FLCLASH_CONFIG = os.path.join(FLCLASH_DIR, "config.yaml")
FLCLASH_PLIST = os.path.expanduser("~/Library/Preferences/com.follow.clash.plist")
STATE_FILE = os.path.join(WORKSPACE, "data", "proxy_watchdog_state.json")
LOG_FILE = os.path.join(WORKSPACE, "logs", "proxy_watchdog.log")
LOCK_FILE = "/tmp/proxy_watchdog.lock"

PROXY = "http://127.0.0.1:7890"
PROXIES = {"http": PROXY, "https": PROXY}
TEST_URLS = [
    "https://www.gstatic.com/generate_204",
    "https://cp.cloudflare.com/generate_204",
]
PROBE_TIMEOUT = 8          # 单次探测超时（秒）
RETRY_GAP = 4              # 两个探测 URL 之间的间隔（秒）
DELAY_TIMEOUT_MS = 3000    # 节点延迟测试超时
API_TIMEOUT = 12
GROUP_DELAY_TIMEOUT = 60   # 整组延迟测试的总超时（秒）
HISTORY_FRESH_SECONDS = 900  # 节点健康历史新鲜度（15 分钟）：优先用历史选点，避免 500+ 节点整组探测
EXCLUDE_NODES = {"COMPATIBLE", "DIRECT", "REJECT", "PASS"}  # mihomo 占位/特殊节点不作切换目标
NOTIFY_REPEAT_SECONDS = 1800
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_KEEP_BYTES = 256 * 1024

GROUP_TYPES = {"Selector", "URLTest", "Fallback", "LoadBalance", "Relay"}

log = logging.getLogger("proxy_watchdog")


def rotate_log() -> None:
    """日志超限则整体改名留档（os.replace 原子，不做 open('w') 重写）。"""
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".old")
    except OSError as e:
        # 轮转失败不阻断主流程
        print(f"log rotate failed: {e}", file=sys.stderr)


def setup_logging() -> None:
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        fh = logging.FileHandler(LOG_FILE)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError as e:
        print(f"log file handler failed: {e}", file=sys.stderr)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)


# ---------------- FlClash 控制器配置 ----------------

def _normalize_controller(addr, secret):
    if isinstance(addr, str):
        addr = addr.strip().strip('"').strip("'")
    if not addr or addr in ("close", ""):
        return None, secret or ""
    if not addr.startswith("http"):
        addr = "http://" + addr
    return addr, secret or ""


def _controller_from_plist():
    """FlClash 0.9.x 设置实体在 plist 的 flutter.config(JSON 字符串) blob；
    config.yaml 只是核心启动时写入的快照，热更新开关不一定回写。"""
    import plistlib
    with open(FLCLASH_PLIST, "rb") as f:
        data = plistlib.load(f)
    raw = data.get("flutter.config")
    cfg = json.loads(raw) if isinstance(raw, str) else (raw or {})
    patch = cfg.get("patchClashConfig") or {}
    return _normalize_controller(
        patch.get("external-controller"), patch.get("secret") or ""
    )


def _controller_from_config_yaml():
    """启动快照兜底：读 config.yaml 的 external-controller 与 secret。"""
    try:
        with open(FLCLASH_CONFIG, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        log.warning("读不到 FlClash config.yaml: %s", e)
        return None, ""
    try:
        import yaml
        data = yaml.safe_load(text) or {}
        addr = data.get("external-controller")
        secret = data.get("secret") or ""
    except Exception as e:  # yaml 缺失/解析失败 → 正则兜底（可见告警）
        log.warning("yaml 解析失败(%s)，用正则兜底", e)
        import re
        m = re.search(r'^external-controller:\s*"?([^"\r\n]*)"?', text, re.M)
        addr = m.group(1).strip() if m else None
        m2 = re.search(r'^secret:\s*"?([^"\r\n]*)"?', text, re.M)
        secret = (m2.group(1).strip() if m2 else "")
    return _normalize_controller(addr, secret)


def load_controller():
    """读 external-controller 与 secret。优先级：plist(实时设置) → config.yaml(启动快照)。
    返回 (base_url or None, secret, note)。"""
    try:
        addr, secret = _controller_from_plist()
        if addr:
            return addr, secret, "ok(plist)"
    except FileNotFoundError:
        log.warning("plist 不存在: %s", FLCLASH_PLIST)
    except Exception as e:
        log.warning("plist 解析失败，退回 config.yaml: %s", e)
    addr, secret = _controller_from_config_yaml()
    if addr:
        return addr, secret, "ok(config.yaml 快照)"
    return None, secret or "", "外部控制未开启"


# ---------------- 探测与 API ----------------

def probe_once(url):
    t0 = time.time()
    r = SESSION.get(url, proxies=PROXIES, timeout=PROBE_TIMEOUT)
    return r.status_code, round(time.time() - t0, 2)


def is_healthy():
    """两个 URL 都失败才判断流。返回 (healthy, detail)。"""
    details = []
    for i, url in enumerate(TEST_URLS):
        if i:
            time.sleep(RETRY_GAP)
        try:
            code, _ = probe_once(url)
            if code == 204:
                return True, f"{url} -> 204"
            details.append(f"{url} -> HTTP {code}")
        except Exception as e:
            details.append(f"{url} -> {type(e).__name__}: {e}")
    return False, "; ".join(details)


def api(base, secret, method, path, timeout=API_TIMEOUT, **kw):
    headers = kw.pop("headers", {})
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    return SESSION.request(
        method, base + path, headers=headers, timeout=timeout, **kw
    )


def _parse_iso(s):
    """mihomo history 的 time 字段（RFC3339）→ unix 秒；解析失败 → None。"""
    if not s:
        return None
    try:
        from datetime import datetime, timezone
        s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _fresh_delay(proxies_map, name):
    """节点最近一次健康检查延迟；无记录/超龄/失败 → None。"""
    info = proxies_map.get(name) or {}
    hist = info.get("history") or []
    if not hist:
        return None
    entry = hist[-1] or {}
    d = entry.get("delay")
    ts = _parse_iso(entry.get("time") or "")
    if not isinstance(d, (int, float)) or d <= 0 or ts is None:
        return None
    if time.time() - ts > HISTORY_FRESH_SECONDS:
        return None
    return float(d)


def get_graph(base, secret):
    """返回 (proxies_map, chains)。chains 去重：[{top, chain, target, current}]。
    target = 当前选中链上最深的 Selector 组；current = 链末端的实际节点名。
    注意：GLOBAL 的 all 含所有组，算 referenced 时必须跳过，否则顶层组恒空。"""
    r = api(base, secret, "GET", "/proxies")
    r.raise_for_status()
    proxies_map = (r.json() or {}).get("proxies") or {}
    groups = {n: i for n, i in proxies_map.items() if i.get("type") in GROUP_TYPES}
    referenced = set()
    for name, info in groups.items():
        if name == "GLOBAL":
            continue
        for m in (info.get("all") or []):
            referenced.add(m)
    tops = [n for n in groups if n != "GLOBAL" and n not in referenced]
    seen = set()
    out = []
    for t in sorted(tops):
        chain = [t]
        cur = t
        for _ in range(10):
            now = (groups.get(cur) or {}).get("now")
            if not now or now in chain or now not in groups:
                break
            chain.append(now)
            cur = now
        leaf = (groups.get(chain[-1]) or {}).get("now")
        target = next(
            (g for g in reversed(chain) if (groups.get(g) or {}).get("type") == "Selector"),
            None,
        )
        if not target or not leaf:
            continue
        key = (target, leaf)
        if key in seen:
            continue
        seen.add(key)
        out.append({"top": t, "chain": chain, "target": target, "current": leaf})
    return proxies_map, out


def try_fix(base, secret, ch, dry_run, proxies_map, extra_exclude=frozenset()):
    """对一个链路做存活判定与切换。返回 (action, msg, info)。
    选点策略：历史延迟优先（15 分钟内），历史不可用才整组测延迟。
    历史说当前节点活着时，用单节点实测复核定夺（探测优先于旧历史）。"""
    target, current = ch["target"], ch["current"]
    exclude = set(extra_exclude) | EXCLUDE_NODES | {current}
    test = urllib.parse.quote(TEST_URLS[0], safe="")
    cur_delay = _fresh_delay(proxies_map, current)
    if cur_delay is not None:
        try:
            qcur = urllib.parse.quote(current, safe="")
            rc = api(base, secret, "GET",
                     f"/proxies/{qcur}/delay?url={test}&timeout={DELAY_TIMEOUT_MS}",
                     timeout=20)
            body = rc.json() if rc.status_code == 200 else {}
            if isinstance(body, dict) and body.get("delay", 0) and body["delay"] > 0:
                return "node-alive", (
                    f"{target} 当前节点「{current}」实测 {body['delay']}ms 存活，"
                    "断流在别处（DNS/订阅/上游），不切换"
                ), {}
        except Exception as e:
            log.warning("单节点复测失败（按已死处理）: %s", e)
    members = (proxies_map.get(target) or {}).get("all") or []
    # 1) 历史存活候选
    candidates = {}
    for name in members:
        if name in exclude:
            continue
        d = _fresh_delay(proxies_map, name)
        if d is not None:
            candidates[name] = d
    # 2) 历史不可用 → 整组延迟测试（mihomo 内部并发，结果同时刷新各节点历史）
    if not candidates:
        q = urllib.parse.quote(target, safe="")
        try:
            r = api(base, secret, "GET",
                    f"/group/{q}/delay?url={test}&timeout={DELAY_TIMEOUT_MS}",
                    timeout=GROUP_DELAY_TIMEOUT)
        except Exception as e:
            return "delay-test-error", f"{target}: {type(e).__name__}: {e}", {}
        if r.status_code != 200:
            return "delay-test-failed", f"{target}: HTTP {r.status_code} {r.text[:120]}", {}
        delays = r.json() or {}
        if isinstance(delays, dict) and current in delays:
            return "node-alive", (
                f"{target} 当前节点「{current}」延迟 {delays[current]}ms 仍存活，"
                "断流在别处（DNS/订阅/上游），不切换"
            ), {}
        candidates = {
            k: float(v) for k, v in delays.items()
            if isinstance(v, (int, float)) and v > 0 and k not in exclude
        }
    if not candidates:
        return "no-candidate", f"{target} 组内无存活候选节点（排除占位节点后）", {}
    best_name, best_ms = min(candidates.items(), key=lambda kv: kv[1])
    if dry_run:
        return "dry-run", f"{target}: 「{current}」已死 → 将切到「{best_name}」({int(best_ms)}ms)", {}
    q = urllib.parse.quote(target, safe="")
    try:
        r2 = api(base, secret, "PUT", f"/proxies/{q}", json={"name": best_name})
    except Exception as e:
        return "switch-error", f"{target} PUT {best_name}: {type(e).__name__}: {e}", {}
    if r2.status_code not in (200, 204):
        return "switch-failed", f"{target} PUT {best_name}: HTTP {r2.status_code} {r2.text[:120]}", {}
    return ("switched",
            f"{target}: 「{current}」→「{best_name}」({int(best_ms)}ms)",
            {"target": target, "node": best_name})


def rehearse(base, secret):
    """零变更演练：对第一个链路的 target 组原样回写当前选中值。"""
    proxies_map, chains = get_graph(base, secret)
    if not chains:
        return False, "未找到可演练的 Selector 链路"
    ch = chains[0]
    q = urllib.parse.quote(ch["target"], safe="")
    r = api(base, secret, "PUT", f"/proxies/{q}", json={"name": ch["current"]})
    ok = r.status_code in (200, 204)
    return ok, (f"PUT {ch['target']} <- 「{ch['current']}」(原样回写) HTTP {r.status_code} | 发现链路 {len(chains)} 条")


# ---------------- 状态 / 通知 ----------------

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}  # 首次运行属正常初始化，非异常
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as e:
        log.warning("状态文件损坏，按空状态处理: %s", e)
        return {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        if os.path.exists(STATE_FILE):
            shutil.copy2(STATE_FILE, STATE_FILE + ".bak")
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log.warning("状态写盘失败: %s", e)


def notify(msg, state, force=False):
    now = time.time()
    last = state.get("last_notify_ts", 0)
    if not force and now - last < NOTIFY_REPEAT_SECONDS:
        log.info("通知去重（%ds 内已发过）: %s", NOTIFY_REPEAT_SECONDS, msg)
        return
    safe = msg.replace('\\', '').replace('"', "'")
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe}" with title "Proxy 看门狗" sound name "Glass"'],
            timeout=10, capture_output=True,
        )
    except Exception as e:
        log.warning("macOS 通知失败: %s", e)
    state["last_notify_ts"] = now
    log.warning("通知: %s", msg)


# ---------------- 主流程 ----------------

def acquire_lock():
    import fcntl
    fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return fh


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    check_only = "--check" in args
    rehearse_mode = "--rehearse" in args
    status_mode = "--status" in args

    rotate_log()
    setup_logging()
    lock = acquire_lock()
    if lock is None:
        log.info("另一实例运行中，跳过")
        return 0

    state = load_state()
    base, secret, note = load_controller()

    if status_mode:
        print(json.dumps({
            "controller": base, "controller_note": note,
            "secret_configured": bool(secret),
            "state": state,
        }, ensure_ascii=False, indent=1))
        return 0

    if rehearse_mode:
        if not base:
            log.warning("外部控制未开启，无法演练（%s）", note)
            return 2
        ok, detail = rehearse(base, secret)
        log.info("演练%s: %s", "成功" if ok else "失败", detail)
        return 0 if ok else 1

    healthy, detail = is_healthy()
    if check_only:
        log.info("check: %s", detail)
        return 0 if healthy else 1

    if healthy:
        if state.get("status") == "bad":
            log.info("已恢复: %s", detail)
            notify("代理已恢复 ✅" + (f"（{state.get('last_action', '')}）" if state.get("last_action") else ""), state, force=True)
        else:
            state["ok_runs"] = state.get("ok_runs", 0) + 1
            if state["ok_runs"] % 100 == 0:
                log.info("连续 ok x%d（%s）", state["ok_runs"], detail)
        state["status"] = "ok"
        state["fail_streak"] = 0
        state["unresolved"] = False
        save_state(state)
        return 0

    # ---- 断流 ----
    state["fail_streak"] = state.get("fail_streak", 0) + 1
    state["status"] = "bad"
    log.warning("断流(%d): %s", state["fail_streak"], detail)

    if not base:
        state["unresolved"] = True
        notify("代理断流，外部控制未开启，无法自动切换——请在 FlClash: 工具→通用→入站→外部控制 打开", state)
        save_state(state)
        return 0

    try:
        proxies_map, chains = get_graph(base, secret)
    except Exception as e:
        state["unresolved"] = True
        log.warning("获取代理链失败: %s", e)
        notify(f"代理断流，且读代理链失败（{type(e).__name__}），请人工检查", state)
        save_state(state)
        return 0

    if not chains:
        state["unresolved"] = True
        notify("代理断流，未找到可切换的 Selector 链路，请人工检查", state)
        save_state(state)
        return 0

    last_tried = (state.get("last_tried") or {}) if state.get("fail_streak", 0) > 1 else {}
    actions = []
    switched_info = []
    for ch in chains:
        extra = {last_tried[ch["target"]]} if last_tried.get(ch["target"]) else set()
        try:
            action, msg, info = try_fix(base, secret, ch, dry_run, proxies_map, extra)
        except Exception as e:
            action, msg, info = "exception", f"{ch.get('target')}: {type(e).__name__}: {e}", {}
        log.warning("%s: %s", action, msg)
        actions.append({"action": action, "msg": msg})
        if action == "switched" and info:
            switched_info.append(info)

    switched = [a for a in actions if a["action"] == "switched"]
    if dry_run:
        state["unresolved"] = True
        notify("代理断流（dry-run 不切换）: " + "; ".join(a["msg"] for a in actions), state)
    elif switched:
        ok2, detail2 = is_healthy()
        summary = "; ".join(a["msg"] for a in switched)
        if switched_info:
            state["last_tried"] = {
                **(state.get("last_tried") or {}),
                **{i["target"]: i["node"] for i in switched_info},
            }
        state["last_action"] = summary
        state["last_switch_ts"] = time.time()
        if ok2:
            state["unresolved"] = False
            log.info("切换后复测健康: %s", detail2)
            notify(f"代理断流已自动修复 ✅ {summary}", state, force=True)
        else:
            state["unresolved"] = True
            log.warning("切换后复测仍断流: %s", detail2)
            notify(f"已切换节点（{summary}）但仍断流，下轮继续尝试/请留意", state, force=True)
    else:
        state["unresolved"] = True
        notify("代理断流，未执行切换: " + "; ".join(a["msg"] for a in actions), state)

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
