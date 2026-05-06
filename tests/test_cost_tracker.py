"""cost_tracker: 가격 산출, 소프트/하드 캡, 상태 영속, 일자 롤오버."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone


def test_calc_cost_opus(tracker):
    # 100k input + 1k output @ Opus 4.7 (in $15, out $75 per MTok)
    # = 100k/1M * 15 + 1k/1M * 75 = 1.5 + 0.075 = 1.575
    cost = tracker._calc_cost("claude-opus-4-7", 100_000, 1_000)
    assert abs(cost - 1.575) < 1e-9


def test_calc_cost_haiku(tracker):
    cost = tracker._calc_cost("claude-haiku-4-5-20251001", 1_000_000, 100_000)
    # = 1.0 + 0.5 = 1.5
    assert abs(cost - 1.5) < 1e-9


def test_calc_cost_unknown_model_falls_back_to_opus_pricing(tracker):
    cost = tracker._calc_cost("totally-made-up", 1_000_000, 0)
    assert abs(cost - 15.0) < 1e-9


def test_select_model_under_soft_cap_returns_primary(tracker):
    assert tracker.select_model() == "claude-opus-4-7"


def test_select_model_above_soft_cap_returns_fallback(tracker, monkeypatch):
    monkeypatch.setenv("CLAUDE_DAILY_COST_USD_MAX", "1.0")
    tracker.record_usage("claude-opus-4-7", 100_000, 1_000)  # ~$1.575
    assert tracker.select_model() == "claude-haiku-4-5-20251001"


def test_select_model_above_hard_cap_returns_none(tracker, monkeypatch):
    monkeypatch.setenv("CLAUDE_DAILY_COST_HARD_STOP", "1.0")
    tracker.record_usage("claude-opus-4-7", 100_000, 1_000)
    assert tracker.select_model() is None


def test_record_usage_accumulates_across_calls(tracker):
    tracker.record_usage("claude-opus-4-7", 1_000, 0)  # 0.015
    tracker.record_usage("claude-opus-4-7", 1_000, 0)  # 0.015
    state = tracker.get_state()
    assert state["calls"] == 2
    assert state["input_tokens"] == 2_000
    assert abs(state["cost_usd"] - 0.030) < 1e-9


def test_record_usage_persists_to_disk(tracker, cache_dir):
    tracker.record_usage("claude-haiku-4-5-20251001", 1_000_000, 0)
    assert tracker.STATE_FILE.exists()
    raw = json.loads(tracker.STATE_FILE.read_text())
    assert raw["calls"] == 1
    assert abs(raw["cost_usd"] - 1.0) < 1e-9


def test_record_usage_per_model_breakdown(tracker):
    tracker.record_usage("claude-opus-4-7", 100, 100)
    tracker.record_usage("claude-haiku-4-5-20251001", 100, 100)
    state = tracker.get_state()
    by_model = state["by_model"]
    assert "claude-opus-4-7" in by_model
    assert "claude-haiku-4-5-20251001" in by_model
    assert by_model["claude-opus-4-7"]["calls"] == 1
    assert by_model["claude-haiku-4-5-20251001"]["calls"] == 1


def test_state_resets_on_new_utc_day(tracker):
    """다른 날짜의 state는 무시되고 새로 시작."""
    yesterday = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%d")
    tracker.STATE_FILE.write_text(
        json.dumps(
            {
                "date": yesterday,
                "input_tokens": 999_999,
                "output_tokens": 999_999,
                "cost_usd": 999.0,
                "calls": 999,
                "by_model": {},
            }
        )
    )
    fresh = tracker.get_state()
    assert fresh["date"] != yesterday
    assert fresh["cost_usd"] == 0.0
    assert fresh["calls"] == 0


def test_corrupt_state_file_is_recovered(tracker):
    tracker.STATE_FILE.write_text("{not valid json")
    state = tracker.get_state()
    assert state["calls"] == 0
    assert state["cost_usd"] == 0.0


def test_select_model_uses_env_overrides(tracker, monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("CLAUDE_MODEL_FALLBACK", "custom-fallback")
    assert tracker.select_model() == "claude-sonnet-4-6"
    monkeypatch.setenv("CLAUDE_DAILY_COST_USD_MAX", "1.0")
    # Sonnet pricing: in $3/MTok → 1M in tokens = $3 ≫ $1 cap
    tracker.record_usage("claude-sonnet-4-6", 1_000_000, 0)
    assert tracker.select_model() == "custom-fallback"
