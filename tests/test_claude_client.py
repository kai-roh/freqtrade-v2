"""claude_client: JSON 파싱, 캐시, sentiment damping, event 응답 검증."""

from __future__ import annotations

import json


def test_strict_parse_clean_json(claude):
    assert claude._strict_parse('{"a": 1}') == {"a": 1}


def test_strict_parse_with_code_fence(claude):
    out = claude._strict_parse('```json\n{"sentiment": 0.5}\n```')
    assert out == {"sentiment": 0.5}


def test_strict_parse_with_trailing_text(claude):
    out = claude._strict_parse('{"sentiment": 0.3} -- explanation')
    assert out == {"sentiment": 0.3}


def test_strict_parse_garbage_returns_none(claude):
    assert claude._strict_parse("totally not json") is None
    assert claude._strict_parse("") is None
    assert claude._strict_parse("{") is None


def test_get_cached_sentiment_uses_cache(claude, monkeypatch):
    calls = {"n": 0}

    def fake_call(system, user, max_tokens):
        calls["n"] += 1
        return ('{"sentiment": 0.7, "confidence": 0.9}', {})

    monkeypatch.setattr(claude, "_call_claude", fake_call)
    a = claude.get_cached_sentiment("BTC/USDT:USDT")
    b = claude.get_cached_sentiment("BTC/USDT:USDT")
    assert calls["n"] == 1  # second call hits cache
    assert a == b == 0.7


def test_get_cached_sentiment_clamps_range(claude, monkeypatch):
    monkeypatch.setattr(
        claude, "_call_claude", lambda *a, **k: ('{"sentiment": 99, "confidence": 1.0}', {})
    )
    val = claude.get_cached_sentiment("ETH/USDT:USDT")
    assert val == 1.0


def test_low_confidence_dampens_sentiment(claude, monkeypatch):
    # confidence 0.25 < 0.5 → sentiment *= 0.5
    monkeypatch.setattr(
        claude, "_call_claude", lambda *a, **k: ('{"sentiment": 0.8, "confidence": 0.25}', {})
    )
    val = claude.get_cached_sentiment("SOL/USDT:USDT")
    assert abs(val - (0.8 * 0.5)) < 1e-9


def test_zero_confidence_zeroes_sentiment(claude, monkeypatch):
    monkeypatch.setattr(
        claude, "_call_claude", lambda *a, **k: ('{"sentiment": 0.8, "confidence": 0.0}', {})
    )
    val = claude.get_cached_sentiment("XRP/USDT:USDT")
    assert val == 0.0


def test_unparseable_response_logs_failure_and_returns_neutral(claude, monkeypatch):
    monkeypatch.setattr(claude, "_call_claude", lambda *a, **k: ("garbage out", {}))
    val = claude.get_cached_sentiment("BNB/USDT:USDT")
    assert val == 0.0
    failures = list((claude.FAILURE_DIR).glob("sentiment_*"))
    assert failures, "raw response should be persisted to _failures/"


def test_no_call_when_call_returns_none(claude, monkeypatch):
    """비용 캡 도달 등으로 _call_claude가 None을 반환하면 0.0 안전 폴백."""
    monkeypatch.setattr(claude, "_call_claude", lambda *a, **k: None)
    assert claude.get_cached_sentiment("BTC/USDT:USDT") == 0.0


def test_event_triggered_call_normalizes_action(claude, monkeypatch):
    monkeypatch.setattr(
        claude, "_call_claude", lambda *a, **k: ('{"action": "BUY", "confidence": 0.8}', {})
    )
    out = claude.event_triggered_call("BTC/USDT:USDT", "ATR spike")
    assert out["action"] == "buy"
    assert out["confidence"] == 0.8


def test_event_triggered_call_unknown_action_becomes_hold(claude, monkeypatch):
    monkeypatch.setattr(
        claude, "_call_claude", lambda *a, **k: ('{"action": "moon", "confidence": 0.9}', {})
    )
    out = claude.event_triggered_call("BTC/USDT:USDT", "x")
    assert out["action"] == "hold"


def test_event_triggered_call_clamps_confidence(claude, monkeypatch):
    monkeypatch.setattr(
        claude, "_call_claude", lambda *a, **k: ('{"action": "buy", "confidence": 5.0}', {})
    )
    out = claude.event_triggered_call("BTC/USDT:USDT", "x")
    assert out["confidence"] == 1.0


def test_event_triggered_call_handles_call_failure(claude, monkeypatch):
    monkeypatch.setattr(claude, "_call_claude", lambda *a, **k: None)
    out = claude.event_triggered_call("BTC/USDT:USDT", "x")
    assert out == {"action": "hold", "confidence": 0.0, "reason": "no_call"}


def test_news_context_is_included_in_user_prompt(claude, monkeypatch):
    """A-2: news_sources에서 받은 헤드라인이 프롬프트에 들어가는지."""
    captured = {}

    def fake_call(system, user, max_tokens):
        captured["user"] = user
        return ('{"sentiment": 0.0, "confidence": 0.0}', {})

    monkeypatch.setattr(claude, "_call_claude", fake_call)
    # news_sources를 monkeypatch해서 헤드라인 주입
    fake_items = [
        type(
            "N",
            (),
            {
                "title": "TestHeadline-XYZ",
                "source": "src",
                "votes_positive": 0,
                "votes_negative": 0,
            },
        )()
    ]
    monkeypatch.setattr(
        "user_data.llm.news_sources.fetch_recent_news", lambda pair, limit=5: fake_items
    )
    claude.get_cached_sentiment("BTC/USDT:USDT")
    assert "TestHeadline-XYZ" in captured["user"]
