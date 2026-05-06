"""공유 픽스처. 모든 테스트는 호스트 Python(stdlib + pytest)에서 실행된다."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """페어 테스트마다 격리된 캐시 디렉토리."""
    monkeypatch.setenv("LLM_CACHE_BASE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def tracker(cache_dir, monkeypatch):
    """깨끗한 cost_tracker — 매 테스트 모듈 재로드."""
    monkeypatch.setenv("CLAUDE_DAILY_COST_USD_MAX", "10.0")
    monkeypatch.setenv("CLAUDE_DAILY_COST_HARD_STOP", "20.0")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("CLAUDE_MODEL_FALLBACK", "claude-haiku-4-5-20251001")
    from user_data.llm import cost_tracker

    importlib.reload(cost_tracker)
    return cost_tracker


@pytest.fixture
def news(cache_dir, monkeypatch):
    monkeypatch.setenv("LLM_NEWS_PROVIDER", "none")
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)
    from user_data.llm import news_sources

    importlib.reload(news_sources)
    return news_sources


@pytest.fixture
def claude(cache_dir, monkeypatch):
    monkeypatch.setenv("LLM_NEWS_PROVIDER", "none")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from user_data.llm import claude_client, cost_tracker, news_sources

    importlib.reload(cost_tracker)
    importlib.reload(news_sources)
    importlib.reload(claude_client)
    return claude_client
