"""news_sources: 페어 매핑, 프로바이더 분기, 캐시, CryptoPanic mocking."""

from __future__ import annotations

import io
import json
import time
import urllib.error


def test_pair_to_currency_usdtm(news):
    assert news.pair_to_currency("BTC/USDT:USDT") == "BTC"
    assert news.pair_to_currency("ETH/USDT:USDT") == "ETH"
    assert news.pair_to_currency("sol/usdt:usdt") == "SOL"


def test_pair_to_currency_spot_form(news):
    assert news.pair_to_currency("BTC/USDT") == "BTC"


def test_none_provider_returns_empty(news):
    assert news.fetch_recent_news("BTC/USDT:USDT") == []


def test_format_for_prompt_empty(news):
    assert news.format_for_prompt([]) == "(no recent news available)"


def test_format_for_prompt_with_items(news):
    items = [
        news.NewsItem(title="Bitcoin hits ATH", source="coindesk",
                      votes_positive=10, votes_negative=2),
        news.NewsItem(title="Inflows surge", source="decrypt"),
    ]
    out = news.format_for_prompt(items)
    assert "1." in out and "2." in out
    assert "[coindesk]" in out
    assert "+10/-2" in out
    assert "Bitcoin hits ATH" in out
    assert "Inflows surge" in out


def test_format_for_prompt_truncates_to_max_items(news):
    items = [news.NewsItem(title=f"news{i}") for i in range(10)]
    out = news.format_for_prompt(items, max_items=3)
    assert "news0" in out and "news2" in out
    assert "news5" not in out


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload.encode() if isinstance(payload, str) else payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_urlopen(payload):
    def fake(req, timeout=None):
        return _FakeResp(payload)
    return fake


def test_cryptopanic_provider_parses_response(news, monkeypatch):
    monkeypatch.setenv("LLM_NEWS_PROVIDER", "cryptopanic")
    monkeypatch.setenv("CRYPTOPANIC_TOKEN", "tok")
    payload = json.dumps({
        "results": [
            {"title": "BTC pumps 5%", "source": {"title": "CoinDesk", "domain": "coindesk.com"},
             "published_at": "2026-05-07T01:00:00Z", "url": "https://x",
             "votes": {"positive": 12, "negative": 3}},
            {"title": "ETF inflows record", "source": {"domain": "decrypt.co"},
             "votes": {}},
        ]
    })
    monkeypatch.setattr("urllib.request.urlopen", _stub_urlopen(payload))
    items = news.fetch_recent_news("BTC/USDT:USDT", limit=5)
    assert len(items) == 2
    assert items[0].title == "BTC pumps 5%"
    assert items[0].source == "CoinDesk"
    assert items[0].votes_positive == 12
    assert items[1].source == "decrypt.co"


def test_cryptopanic_http_failure_returns_empty(news, monkeypatch):
    monkeypatch.setenv("LLM_NEWS_PROVIDER", "cryptopanic")
    monkeypatch.setenv("CRYPTOPANIC_TOKEN", "tok")

    def boom(req, timeout=None):
        raise urllib.error.URLError("dns died")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert news.fetch_recent_news("BTC/USDT:USDT") == []


def test_cryptopanic_invalid_json_returns_empty(news, monkeypatch):
    monkeypatch.setenv("LLM_NEWS_PROVIDER", "cryptopanic")
    monkeypatch.setenv("CRYPTOPANIC_TOKEN", "tok")
    monkeypatch.setattr("urllib.request.urlopen", _stub_urlopen("not-json"))
    assert news.fetch_recent_news("BTC/USDT:USDT") == []


def test_cryptopanic_missing_token_falls_back_to_none(news, monkeypatch):
    monkeypatch.setenv("LLM_NEWS_PROVIDER", "cryptopanic")
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)
    # 토큰 없이도 깨지지 않고 빈 리스트
    assert news.fetch_recent_news("BTC/USDT:USDT") == []


def test_cache_hit_avoids_second_http_call(news, monkeypatch):
    monkeypatch.setenv("LLM_NEWS_PROVIDER", "cryptopanic")
    monkeypatch.setenv("CRYPTOPANIC_TOKEN", "tok")
    payload = json.dumps({"results": [
        {"title": "first", "source": {}, "votes": {}},
    ]})
    calls = {"n": 0}

    def counting(req, timeout=None):
        calls["n"] += 1
        return _FakeResp(payload)

    monkeypatch.setattr("urllib.request.urlopen", counting)
    a = news.fetch_recent_news("BTC/USDT:USDT")
    b = news.fetch_recent_news("BTC/USDT:USDT")
    assert calls["n"] == 1
    assert [i.title for i in a] == [i.title for i in b] == ["first"]


def test_cache_expiry_re_fetches(news, monkeypatch):
    monkeypatch.setenv("LLM_NEWS_PROVIDER", "cryptopanic")
    monkeypatch.setenv("CRYPTOPANIC_TOKEN", "tok")
    monkeypatch.setattr(news, "NEWS_CACHE_TTL", 0)  # 즉시 만료
    calls = {"n": 0}

    def counting(req, timeout=None):
        calls["n"] += 1
        return _FakeResp(json.dumps({"results": []}))

    monkeypatch.setattr("urllib.request.urlopen", counting)
    news.fetch_recent_news("BTC/USDT:USDT")
    news.fetch_recent_news("BTC/USDT:USDT")
    assert calls["n"] == 2
