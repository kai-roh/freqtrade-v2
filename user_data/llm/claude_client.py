"""
claude_client.py - Claude API 통합 (Messages API 직접 호출, SDK 미사용)

핵심 원칙:
1. 매 캔들마다 호출 금지. 캐시 TTL 기반 (기본 30분)
2. 이벤트 트리거 호출 (변동성 급증 시) 별도 분리
3. 응답 파싱 실패 시 기본값(중립 0.0) 반환, 거래 영향 최소화
4. JSON Schema 강제로 환각 차단 — 시스템 프롬프트 + assistant prefill
5. 일일 비용 상한(cost_tracker) 도달 시 폴백 모델 또는 호출 차단
6. 외부 의존성 0 — stdlib(urllib)만 사용 → freqtrade 이미지 그대로 사용 가능
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from user_data.llm import cost_tracker

logger = logging.getLogger(__name__)

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_TIMEOUT = float(os.getenv("ANTHROPIC_HTTP_TIMEOUT", "15"))

CACHE_DIR = Path(os.getenv("LLM_CACHE_BASE_DIR", "/freqtrade/user_data/llm/cache"))
FAILURE_DIR = CACHE_DIR / "_failures"
try:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    FAILURE_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # 컨테이너 외부 import 보호 — 실제 쓰기 단계에서 또 실패해도 fail-soft.
    pass

CACHE_TTL_SECONDS = int(os.getenv("CLAUDE_CACHE_TTL_SECONDS", "1800"))  # 30분
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# 시스템 프롬프트로 JSON-only 강제 (assistant 프리필 + stop_sequences 보조)
_SENTIMENT_SYSTEM = (
    "You are a strict JSON producer. Output ONLY a single JSON object on one line. "
    "Never include code fences, prose, or trailing text. The schema is: "
    '{"sentiment": <float in [-1.0, 1.0]>, "confidence": <float in [0.0, 1.0]>, '
    '"reason": "<<=120 chars>"}. '
    'If unsure, output {"sentiment": 0.0, "confidence": 0.0, "reason": "insufficient context"}.'
)

_EVENT_SYSTEM = (
    "You are a strict JSON producer. Output ONLY a single JSON object on one line. "
    "Never include code fences, prose, or trailing text. The schema is: "
    '{"action": "buy"|"sell"|"hold", "confidence": <float in [0.0, 1.0]>, '
    '"reason": "<<=120 chars>"}.'
)


def _cache_path(pair: str) -> Path:
    safe = pair.replace("/", "_").replace(":", "_")
    return CACHE_DIR / f"sentiment_{safe}.json"


def _log_failure(tag: str, raw: str, exc: Exception | None = None) -> None:
    """파싱 실패한 raw 응답을 디스크에 기록 (디버깅용)."""
    try:
        ts = time.strftime("%Y%m%dT%H%M%S")
        fp = FAILURE_DIR / f"{tag}_{ts}.txt"
        with open(fp, "w") as f:
            if exc is not None:
                f.write(f"# exception: {exc!r}\n")
            f.write(raw or "")
        logger.warning(f"[Claude] parse failed, raw saved: {fp}")
    except Exception as e:
        logger.warning(f"[Claude] failure-log write failed: {e}")


def _strict_parse(raw: str) -> dict | None:
    """코드펜스/잡음 제거 후 JSON 파싱. 실패 시 None."""
    if not raw:
        return None
    s = raw.strip()
    # 코드펜스 제거 (모델이 system 무시하고 ```json 붙이는 경우 대비)
    if s.startswith("```"):
        s = s[3:]
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.lstrip()
        if s.endswith("```"):
            s = s[:-3].rstrip()
    # 가장 바깥 { ... } 만 추출
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None


def _call_claude(system: str, user: str, max_tokens: int) -> tuple[str, dict] | None:
    """
    Claude Messages API 직접 호출 (urllib, SDK 미사용).
    비용 가드 + JSON 강제(system prompt) + 사용량 기록.
    반환: (raw_text, usage_dict) or None

    NOTE: claude-opus-4-7 등 일부 신규 모델은 assistant prefill 메시지를 받지 않음
    ("This model does not support assistant message"). 시스템 프롬프트의 JSON-only
    지시 + _strict_parse 의 코드펜스/꼬리잡음 제거로 안정성 확보.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set")
        return None

    model = cost_tracker.select_model()
    if model is None:
        # 하드 캡 도달
        return None

    payload = json.dumps(
        {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [
                {"role": "user", "content": user},
            ],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=ANTHROPIC_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 4xx/5xx — 응답 본문 보존해 디버깅
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = str(e)
        logger.error(f"[Claude] HTTP {e.code}: {err_body[:500]}")
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        logger.error(f"[Claude] network error: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"[Claude] response decode failed: {e}")
        return None
    except Exception as e:
        logger.error(f"[Claude] unexpected error: {e}")
        return None

    # content 추출
    try:
        text = body["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"[Claude] unexpected response shape: {e}, body={body!r}")
        return None

    # 프리필이 적용된 경우 응답이 "..." 처럼 시작 — '{' 보충
    if not text.lstrip().startswith("{"):
        text = "{" + text

    # 사용량 기록 (응답의 usage 필드)
    usage_obj = body.get("usage") or {}
    usage = {
        "input_tokens": int(usage_obj.get("input_tokens", 0) or 0),
        "output_tokens": int(usage_obj.get("output_tokens", 0) or 0),
    }
    try:
        cost_tracker.record_usage(model, usage["input_tokens"], usage["output_tokens"])
    except Exception as e:
        logger.warning(f"[Claude] usage record failed: {e}")

    return text, usage


def get_cached_sentiment(pair: str) -> float:
    """
    캐시된 감성 점수 반환. 캐시 만료 시 새로 호출.
    반환값: -1.0(매우 부정) ~ +1.0(매우 긍정), 0.0=중립/실패
    """
    cache_file = _cache_path(pair)

    if cache_file.exists():
        try:
            with open(cache_file) as f:
                data = json.load(f)
            age = time.time() - float(data.get("timestamp", 0))
            if age < CACHE_TTL_SECONDS:
                return float(data.get("sentiment", 0.0))
        except Exception as e:
            logger.warning(f"Cache read failed for {pair}: {e}")

    sentiment = _fetch_sentiment(pair)

    try:
        with open(cache_file, "w") as f:
            json.dump({"pair": pair, "sentiment": sentiment, "timestamp": time.time()}, f)
    except Exception as e:
        logger.warning(f"Cache write failed for {pair}: {e}")

    return sentiment


def _fetch_sentiment(pair: str) -> float:
    """
    Claude API 호출. 페어별 최근 뉴스 헤드라인을 user 프롬프트에 주입.
    뉴스 0건이면 LLM에게 confidence를 낮추도록 명시 (안전 폴백).
    """
    try:
        from user_data.llm.news_sources import fetch_recent_news, format_for_prompt

        items = fetch_recent_news(pair, limit=5)
    except Exception as e:
        logger.warning(f"[Claude] news fetch failed for {pair}: {e}")
        items = []
        try:
            from user_data.llm.news_sources import format_for_prompt
        except Exception:

            def format_for_prompt(_items, max_items=5):  # type: ignore
                return "(no recent news available)"

    news_block = format_for_prompt(items)
    if items:
        logger.info(f"[Claude] {pair} news context: {len(items)} headlines")

    user = (
        f"Asset: {pair}\n"
        f"Recent news headlines (most recent first):\n{news_block}\n\n"
        "Task: estimate short-term (next ~1h) crypto market sentiment for this asset, "
        "weighted toward the headlines above. "
        "If headlines are absent, irrelevant, or stale, output confidence <= 0.3. "
        "Return a single JSON per the schema."
    )
    result = _call_claude(_SENTIMENT_SYSTEM, user, max_tokens=200)
    if result is None:
        return 0.0
    raw, _ = result

    data = _strict_parse(raw)
    if data is None:
        _log_failure(f"sentiment_{pair.replace('/', '_').replace(':', '_')}", raw)
        return 0.0

    try:
        sentiment = float(data.get("sentiment", 0.0))
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError) as e:
        _log_failure(f"sentiment_{pair.replace('/', '_').replace(':', '_')}", raw, e)
        return 0.0

    # 신뢰도가 낮으면 영향력 축소
    if confidence < 0.5:
        sentiment *= max(0.0, confidence * 2)

    sentiment = max(-1.0, min(1.0, sentiment))
    logger.info(f"[Claude] {pair} sentiment={sentiment:.3f} confidence={confidence:.2f}")
    return sentiment


def event_triggered_call(pair: str, context: str) -> dict:
    """
    이벤트 트리거 시 즉시 호출 (캐시 무시).
    예: 변동성 급증, 큰 청산, 주요 뉴스.

    반환: {"action": "buy"|"sell"|"hold", "confidence": float, "reason": str}
    """
    user = f"Asset: {pair}\nContext: {context}\nDecide the immediate action."
    result = _call_claude(_EVENT_SYSTEM, user, max_tokens=300)
    if result is None:
        return {"action": "hold", "confidence": 0.0, "reason": "no_call"}
    raw, _ = result

    data = _strict_parse(raw)
    if data is None:
        _log_failure(f"event_{pair.replace('/', '_').replace(':', '_')}", raw)
        return {"action": "hold", "confidence": 0.0, "reason": "parse_fail"}

    action = str(data.get("action", "hold")).lower()
    if action not in ("buy", "sell", "hold"):
        action = "hold"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(data.get("reason", ""))[:120]
    return {"action": action, "confidence": confidence, "reason": reason}


def get_cost_state() -> dict:
    """현재 일일 비용 상태 조회 (운영 도구에서 사용)."""
    return cost_tracker.get_state()
