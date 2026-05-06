#!/usr/bin/env python3
"""
telegram_notify.py - Telegram Bot API 알림 헬퍼

Library:
    from telegram_notify import send
    send("⚠ Daily loss exceeded threshold")

CLI:
    echo "message body" | python3 scripts/telegram_notify.py
    python3 scripts/telegram_notify.py --text "ad-hoc alert"

환경변수:
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

설계:
- stdlib(urllib)만 사용
- 토큰/chat_id 미설정 시 silently False 반환 (Fail-soft)
- HTML/Markdown parse_mode 지원
- 메시지 길이 4096자 초과 시 자동 분할
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_LEN = 4096
TIMEOUT = 10.0


def is_configured() -> bool:
    return bool(os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def _split(text: str, limit: int = MAX_LEN) -> list[str]:
    """Telegram 4096자 제한에 맞춰 분할. 단순 슬라이스."""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    s = text
    while len(s) > limit:
        # 마지막 개행 위치 기준 자름
        cut = s.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(s[:cut])
        s = s[cut:]
    if s:
        out.append(s)
    return out


def send(text: str, parse_mode: str = "Markdown",
         disable_notification: bool = False) -> bool:
    """
    텍스트 메시지 전송. 성공 True / 실패·미구성 False.
    parse_mode: "Markdown" | "MarkdownV2" | "HTML" | None
    """
    token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.debug("[telegram] not configured, skipping")
        return False

    url = f"{API_BASE}/bot{token}/sendMessage"
    ok_all = True
    for chunk in _split(text):
        payload: dict = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_notification": disable_notification,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        data = urllib.parse.urlencode(payload).encode()
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read().decode()
                obj = json.loads(body)
                if not obj.get("ok"):
                    logger.warning(f"[telegram] api not ok: {obj}")
                    ok_all = False
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            logger.warning(f"[telegram] send failed: {e}")
            ok_all = False
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"[telegram] response parse failed: {e}")
            ok_all = False
    return ok_all


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--text", help="Message body (omit to read from stdin)")
    p.add_argument("--parse-mode", default="Markdown",
                   choices=["Markdown", "MarkdownV2", "HTML", "none"])
    p.add_argument("--silent", action="store_true",
                   help="disable_notification=true (push without sound)")
    args = p.parse_args()

    text = args.text if args.text is not None else sys.stdin.read()
    text = text.strip()
    if not text:
        print("error: empty message", file=sys.stderr)
        return 2

    if not is_configured():
        print("error: TELEGRAM_TOKEN/TELEGRAM_CHAT_ID not set", file=sys.stderr)
        return 3

    parse_mode = None if args.parse_mode == "none" else args.parse_mode
    ok = send(text, parse_mode=parse_mode, disable_notification=args.silent)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
