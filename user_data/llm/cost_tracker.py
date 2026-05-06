"""
cost_tracker.py - Claude API 일일 비용 추적 및 모델 강등

목표:
1. 호출별 input/output 토큰을 누적 → 일일 비용 산출
2. CLAUDE_DAILY_COST_USD_MAX 도달 시 폴백 모델(Haiku)로 자동 강등
3. CLAUDE_DAILY_COST_HARD_STOP 도달 시 호출 자체 차단 (0.0/hold 반환)
4. 상태는 디스크(JSON)에 영속화 → 컨테이너 재시작에도 유지

가격(2026-05 기준, $/MTok):
- claude-opus-4-7:        in $15.00, out $75.00
- claude-sonnet-4-6:      in  $3.00, out $15.00
- claude-haiku-4-5-...:   in  $1.00, out  $5.00
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_DIR = Path("/freqtrade/user_data/llm/cache")
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "_cost_state.json"

# $/MTok (input, output)
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

_DEFAULT_PRIMARY = "claude-opus-4-7"
_DEFAULT_FALLBACK = "claude-haiku-4-5-20251001"

_lock = threading.Lock()


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"date": _today_utc(), "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0, "calls": 0, "by_model": {}}
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        if data.get("date") != _today_utc():
            return {"date": _today_utc(), "input_tokens": 0, "output_tokens": 0,
                    "cost_usd": 0.0, "calls": 0, "by_model": {}}
        return data
    except Exception as e:
        logger.warning(f"cost_tracker: state read failed, resetting: {e}")
        return {"date": _today_utc(), "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0, "calls": 0, "by_model": {}}


def _save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        tmp.replace(STATE_FILE)
    except Exception as e:
        logger.warning(f"cost_tracker: state write failed: {e}")


def _calc_cost(model: str, in_tok: int, out_tok: int) -> float:
    price = PRICING.get(model)
    if price is None:
        logger.warning(f"cost_tracker: unknown model '{model}', defaulting to opus pricing")
        price = PRICING[_DEFAULT_PRIMARY]
    in_price, out_price = price
    return (in_tok / 1_000_000) * in_price + (out_tok / 1_000_000) * out_price


def _soft_cap_usd() -> float:
    return float(os.getenv("CLAUDE_DAILY_COST_USD_MAX", "10.0"))


def _hard_cap_usd() -> float:
    return float(os.getenv("CLAUDE_DAILY_COST_HARD_STOP", "20.0"))


def select_model() -> Optional[str]:
    """
    호출 시 사용할 모델 결정.
    반환:
      - 기본 모델 식별자 (예: claude-opus-4-7) — 정상 운용
      - 폴백 모델 식별자 — 소프트 캡 도달 시
      - None — 하드 캡 도달 시 (호출 차단)
    """
    primary = os.getenv("CLAUDE_MODEL", _DEFAULT_PRIMARY)
    fallback = os.getenv("CLAUDE_MODEL_FALLBACK", _DEFAULT_FALLBACK)

    with _lock:
        state = _load_state()
        cost = float(state.get("cost_usd", 0.0))

    if cost >= _hard_cap_usd():
        logger.error(
            f"[cost_tracker] HARD CAP reached: ${cost:.2f} >= ${_hard_cap_usd():.2f}. "
            f"All Claude calls blocked for {_today_utc()}."
        )
        return None
    if cost >= _soft_cap_usd():
        if primary != fallback:
            logger.warning(
                f"[cost_tracker] SOFT CAP reached: ${cost:.2f} >= ${_soft_cap_usd():.2f}. "
                f"Falling back from {primary} to {fallback}."
            )
        return fallback
    return primary


def record_usage(model: str, input_tokens: int, output_tokens: int) -> dict:
    """호출 후 사용량 기록. 갱신된 state 반환."""
    cost = _calc_cost(model, input_tokens, output_tokens)
    with _lock:
        state = _load_state()
        state["input_tokens"] = int(state.get("input_tokens", 0)) + int(input_tokens)
        state["output_tokens"] = int(state.get("output_tokens", 0)) + int(output_tokens)
        state["cost_usd"] = float(state.get("cost_usd", 0.0)) + cost
        state["calls"] = int(state.get("calls", 0)) + 1
        by_model = state.setdefault("by_model", {})
        m = by_model.setdefault(model, {"calls": 0, "input_tokens": 0,
                                        "output_tokens": 0, "cost_usd": 0.0})
        m["calls"] += 1
        m["input_tokens"] += int(input_tokens)
        m["output_tokens"] += int(output_tokens)
        m["cost_usd"] += cost
        state["updated_at"] = time.time()
        _save_state(state)
    logger.info(
        f"[cost_tracker] +${cost:.4f} ({model}, in={input_tokens}, out={output_tokens}) "
        f"daily=${state['cost_usd']:.4f}"
    )
    return state


def get_state() -> dict:
    with _lock:
        return _load_state()
