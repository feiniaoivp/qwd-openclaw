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


def _post(url, *, json=None, files=None, data=None, timeout=10):
    """统一 POST（curl_cffi 优先，回退 requests）

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
                                         impersonate="chrome")
            finally:
                mp.close()
        return _cr_requests.post(url, json=json, data=data,
                                 timeout=timeout, impersonate="chrome")
    return requests.post(url, json=json, files=files, data=data, timeout=timeout)


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