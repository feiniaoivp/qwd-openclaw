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
    """系统 curl 回退：--resolve 固定已知可用 Telegram IP，绕开污染 DNS。"""
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
    """统一 POST 三级回退链（2026-10-16 定，覆盖全部投递点）：

       ① 直连（curl_cffi → requests）
       ② 本机 7890 代理 —— 日区潮汐 TLS 拦截兜底（10-15 实测代理路径多轮成功）
       ③ 系统 curl + 固定 IP —— DNS 污染 / curl_cffi BoringSSL 损坏场景

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


def send_message(text, parse_mode="", disable_web_page_preview=True):
    """Send a text message to Telegram
    
    默认 parse_mode='' (纯文本)，避免 Markdown 实体解析报错 (如 .token、括号、代码符号触发 400)。
    需要 Markdown 时显式传入 parse_mode="Markdown" 或 "HTML"。
    """
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

import time

def send_document(file_path, caption=None, parse_mode=""):
    """Send a document/file to Telegram"""
    url = f"{API_URL}/sendDocument"
    try:
        data = {'chat_id': CHAT_ID}
        if caption:
            data['caption'] = caption
            data['parse_mode'] = parse_mode
        resp = _post(url, files={'document': (os.path.basename(file_path), file_path)},
                     data=data, timeout=60)
        return resp.json()
    except Exception as e:
        print(f"发送文件失败: {e}")
        return {"ok": False, "error": str(e)}

def send_photo(file_path, caption=None, parse_mode=""):
    """Send a photo/image to Telegram"""
    url = f"{API_URL}/sendPhoto"
    try:
        data = {'chat_id': CHAT_ID}
        if caption:
            data['caption'] = caption
            data['parse_mode'] = parse_mode
        resp = _post(url, files={'photo': (os.path.basename(file_path), file_path)},
                     data=data, timeout=60)
        return resp.json()
    except Exception as e:
        print(f"发送图片失败: {e}")
        return {"ok": False, "error": str(e)}

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