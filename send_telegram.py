#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Send messages to Telegram via Bot API
"""

import sys
import os
import requests
import json

# Bot Token and Chat ID
# 凭据从文件读取（避免硬编码令牌失效后需改代码）：
#   - BOT_TOKEN: credentials/telegram_bot_qwd.token（明文一行）
#   - token 文件缺失或为空时脚本报错退出，不会调用 Telegram，杜绝凭据落代码。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_TOKEN_FILE = os.path.join(_SCRIPT_DIR, "credentials", "telegram_bot_qwd.token")

def _load_bot_token():
    """从 credentials/telegram_bot_qwd.token 读取令牌；缺失或为空则报错退出。"""
    if os.path.isfile(_TOKEN_FILE):
        with open(_TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
            if token:
                return token
    raise SystemExit(f"错误: 令牌文件 {_TOKEN_FILE} 缺失或为空，无法发送 Telegram。")

BOT_TOKEN = _load_bot_token()
CHAT_ID = "626141741"
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── 传输层选择 ─────────────────────────────────────────────
# 🔑 本网络环境对 urllib/requests 做 TLS 指纹识别 → ConnectionReset(54)。
#    curl_cffi + impersonate="chrome" 可正常连通（2026-09-17 实测）。
#    故优先 curl_cffi，不可用时回退 requests。
try:
    from curl_cffi import requests as _cr_requests
    _HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover
    _cr_requests = None
    _HAS_CURL_CFFI = False

# 🔧 2026-10-15: 本地 DNS 将 api.telegram.org 解析到假 IP(104.23.124.189/198.18.x)，
#    curl_cffi 0.15.0 的 BoringSSL 也损坏(invalid library)。
#    实测系统 curl + --resolve 固定 Telegram 真 IP 可通，作为最终回退。
_TG_PINS = ["149.154.167.220", "149.154.166.110"]

# 🔧 2026-10-16: 日区潮汐 TLS 拦截（curl 35 / SSL EOF）统一兑底——直连失败→本机 7890
#    代理重试（10-15 实测代理路径 5/5 成功）→ 仍失败再走固定 IP curl。
_PROXY_URL = "http://127.0.0.1:7890"


def _proxy_available(host="127.0.0.1", port=7890, timeout=1.5):
    """快速 socket 探测本机代理端口，不可用时跳过代理阶段（避免白等超时）。"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _curl_subprocess_post(url, *, json=None, files=None, data=None, timeout=10):
    """系统 curl 回退：--resolve 固定已知可用 Telegram IP，绕开污染 DNS。
    ⚠️ 2026-11-08 实测：env 带 http_proxy（网关注入 7890）时 curl 走代理、--resolve 失效
    → 本级与代理级同隧道，并非独立路径。"""
    import subprocess
    import shutil
    import urllib.parse
    import json as _json
    curl_bin = shutil.which("curl")
    if not curl_bin:
        raise RuntimeError("curl not found")
    host = urllib.parse.urlparse(url).hostname or "api.telegram.org"
    cmd = [curl_bin, "-sS", "--max-time", str(int(timeout)), "-m", str(int(timeout)),
           "-X", "POST", "-w", "\\n%{http_code}"]
    for ip in _TG_PINS:
        cmd += ["--resolve", f"{host}:443:{ip}"]
    if json is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", _json.dumps(json)]
    else:
        for k, v in (data or {}).items():
            cmd += ["--form", f"{k}={v}"]
        for field, spec in (files or {}).items():
            if isinstance(spec, tuple):
                fname, payload = spec
                path = payload if isinstance(payload, str) else None
                if path is None:
                    cmd += ["--form", f"{field}={fname};type=application/octet-stream"]
                else:
                    cmd += ["--form", f"{field}=@{path};filename={fname}"]
            elif isinstance(spec, str):
                cmd += ["--form", f"{field}=@{spec}"]
            else:
                cmd += ["--form", f"{field}={spec}"]
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    if proc.returncode != 0:
        raise RuntimeError(f"curl exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    body, _, status = proc.stdout.rpartition("\n")

    class _Resp:
        def __init__(self, text, code):
            self.text, self.status_code = text, int(code or 0)
        def json(self):
            return _json.loads(self.text)

    return _Resp(body, status)


def _post_once(url, *, json=None, files=None, data=None, timeout=10, proxies=None):
    """单阶段 POST（curl_cffi 优先，不可用时回退 requests）；proxies 透传底层库。

    ⚠️ curl_cffi 的文件上传 API 与 requests 不同：
       必须用 CurlMime 对象 + multipart=，不支持 requests 风格 files={name: (fn, fh)}。
       files 接受两种形式：{field: path/bytes} 或 {field: (filename, path/bytes)}。
    """
    if _HAS_CURL_CFFI:
        if files:
            from curl_cffi import CurlMime
            mp = CurlMime()
            for k, v in (data or {}).items():
                mp.addpart(name=k, data=str(v))
            for field, spec in files.items():
                if isinstance(spec, tuple):
                    fname, payload = spec
                else:
                    payload, fname = spec, field
                if isinstance(payload, str):
                    with open(payload, "rb") as fh:
                        raw = fh.read()
                    if not isinstance(spec, tuple):
                        fname = os.path.basename(payload)
                else:
                    raw = payload
                mp.addpart(name=field, filename=fname, data=raw)
            try:
                return _cr_requests.post(url, multipart=mp, timeout=timeout,
                                         impersonate="chrome", proxies=proxies)
            finally:
                mp.close()
        return _cr_requests.post(url, json=json, data=data,
                                 timeout=timeout, impersonate="chrome", proxies=proxies)
    return requests.post(url, json=json, files=files, data=data,
                         timeout=timeout, proxies=proxies)


def _post(url, *, json=None, files=None, data=None, timeout=10):
    """统一 POST 回退链（2026-10-16 定，覆盖全部投递点）。

       ⚠️ 2026-11-08 实测定性（memory/2026-11-08.md）：三级实为「同一 Clash 隧道的三次
       尝试」，不是独立冗余——
         ① curl_cffi 默认信任 http_proxy/https_proxy env（网关注入 7890）→ 实走代理；
         ② 显式 7890；
         ③ 系统 curl 同样读 env 代理，--resolve 固定 IP 在代理模式下失效 → 仍走 7890。
       且本机 TUN 全劫持 + GFW 拦截 TG 直连（--interface en0 实测超时）→ 不存在绕开
       Clash 的独立路径。保留三级的价值 = 换库换错误形态、单库故障不全灭；小时级断流
       靠 proxy-watchdog 自动切换 + 恢复窗口补推，短时抖动靠 send_message 内部延迟重试。

       任何一级抛异常即降级下一级；代理端口不可用时自动跳过 ②。
    
    """
    try:
        return _post_once(url, json=json, files=files, data=data, timeout=timeout)
    except Exception as e:
        print(f"[send_telegram] 直连失败，尝试 7890 代理: {e}", file=sys.stderr)
    if _proxy_available():
        try:
            proxies = {"http": _PROXY_URL, "https": _PROXY_URL}
            return _post_once(url, json=json, files=files, data=data,
                              timeout=timeout, proxies=proxies)
        except Exception as e:
            print(f"[send_telegram] 代理失败，尝试固定 IP curl: {e}", file=sys.stderr)
    # ③ 固定 IP curl（异常继续向上抛，由调用方处理）
    return _curl_subprocess_post(url, json=json, files=files, data=data, timeout=timeout)


TG_MSG_LIMIT = 3900  # Telegram 单条硬限 4096，留余量防解析偏差

def _split_long_text(text, limit=TG_MSG_LIMIT):
    """按行边界切分超长文本（超 4096 会被 Telegram 400 拒收）

    2026-11-05：intraday-alert-watch-pm 连续 3 次 400 "message is too long"
    根因修复——在共享层统一分段，保护所有推送点。
    """
    if text is None or len(text) <= limit:
        return [text if text is not None else ""]
    chunks, cur = [], ""
    for line in text.split("\n"):
        # 单行超限的极端情况：硬切
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if not line:
            continue
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = cur + "\n" + line if cur else line
    if cur:
        chunks.append(cur)
    return chunks or [""]

def _send_message_once(text, parse_mode="", disable_web_page_preview=True):
    url = f"{API_URL}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview
    }
    try:
        resp = _post(url, json=payload, timeout=15)
        return resp.json()
    except Exception as e:
        print(f"发送消息失败: {e}")
        return {"ok": False, "error": str(e)}

def send_message(text, parse_mode="", disable_web_page_preview=True, retry_wait=30):
    """Send a text message to Telegram

    默认 parse_mode='' (纯文本)，避免 Markdown 实体解析报错 (如 .token、括号、代码符号触发 400)。
    需要 Markdown 时显式传入 parse_mode="Markdown" 或 "HTML"。
    超长文本（>3900 字符）自动按行边界分段发送。
    2026-11-08: 任一分段失败 → 等 retry_wait 秒后仅重试失败分段（默认 1 轮；0=禁用）。
    _post 三级回退实为同一 Clash 隧道（见 _post docstring），分钟级 TLS 潮汐抖动靠本层
    兜底；只重试失败分段、不重发已成功分段，无重复消息风险。小时级断流仍需
    proxy-watchdog 自动切换 + 恢复窗口补推。
    """
    chunks = _split_long_text(text)
    last_fail = None
    pending = list(range(len(chunks)))
    rounds = 2 if retry_wait and retry_wait > 0 else 1
    for attempt_no in range(rounds):
        if attempt_no and pending:
            time.sleep(retry_wait)
        still_pending = []
        for i in pending:
            r = _send_message_once(chunks[i], parse_mode=parse_mode,
                                   disable_web_page_preview=disable_web_page_preview)
            if r.get("ok"):
                continue
            print(f"分段推送失败(块 {i + 1}/{len(chunks)}): "
                  f"{r.get('description') or r.get('error')}", file=sys.stderr)
            last_fail = r
            still_pending.append(i)
        pending = still_pending
        if not pending:
            break
    if pending:
        return last_fail or {"ok": False, "error": "all chunks failed"}
    return {"ok": True}

import time

def send_with_retry(text, parse_mode="", max_retries=3, retry_delay=2):
    """发送消息带重试，用于关键定时任务推送"""
    for attempt in range(max_retries):
        result = send_message(text, parse_mode=parse_mode)
        if result.get("ok"):
            return result
        print(f"发送失败(尝试{attempt+1}/{max_retries}): {result.get('error')}")
        if attempt < max_retries - 1:
            time.sleep(retry_delay)
    return result

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python send_telegram.py <message> [file_path]")
        sys.exit(1)
    
    message = sys.argv[1]
    result = send_message(message)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    if len(sys.argv) > 2:
        file_path = sys.argv[2]
        if os.path.exists(file_path):
            if file_path.endswith(('.png', '.jpg', '.jpeg', '.gif')):
                result = send_photo(file_path)
            else:
                result = send_document(file_path)
            print(json.dumps(result, ensure_ascii=False, indent=2))