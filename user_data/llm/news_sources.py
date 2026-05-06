"""
news_sources.py - LLM 컨텍스트용 뉴스 헤드라인 fetcher

설계:
1. 외부 의존성 최소 — urllib 표준 라이브러리만 사용
2. fail-soft — 모든 실패 시 빈 리스트 반환, 호출자(claude_client) 흐름 영향 X
3. 페어별 5분 캐시 (rate limit 보호)
4. LLM_NEWS_PROVIDER=none 일 때 즉시 빈 리스트 반환 (비용/네트워크 0)

지원 프로바이더:
- cryptopanic (CRYPTOPANIC_TOKEN 필요, 무료 tier)
- none (기본값)
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

NEWS_CACHE_DIR = Path(os.getenv("LLM_CACHE_BASE_DIR", "/freqtrade/user_data/llm/cache")) / "news"
try:
    NEWS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # 컨테이너 외부 import 시 /freqtrade가 없을 수 있음. 캐시 쓰기 단계에서 또 실패해도 fail-soft.
    pass
NEWS_CACHE_TTL = int(os.getenv("LLM_NEWS_CACHE_TTL_SECONDS", "300"))


@dataclass
class NewsItem:
    title: str
    source: str = ""
    published_at: str = ""
    url: str = ""
    votes_positive: int = 0
    votes_negative: int = 0


def pair_to_currency(pair: str) -> str:
    """BTC/USDT:USDT -> BTC"""
    base = pair.split("/")[0].split(":")[0]
    return base.upper()


class _NoneProvider:
    name = "none"

    def fetch(self, pair: str, limit: int = 5) -> list[NewsItem]:
        return []


class _CryptoPanicProvider:
    """https://cryptopanic.com/api/free/v1/posts/ — free tier"""

    name = "cryptopanic"
    BASE = "https://cryptopanic.com/api/free/v1/posts/"

    def __init__(self, token: str) -> None:
        self._token = token

    def fetch(self, pair: str, limit: int = 5) -> list[NewsItem]:
        currency = pair_to_currency(pair)
        params = urllib.parse.urlencode(
            {
                "auth_token": self._token,
                "currencies": currency,
                "public": "true",
                "kind": "news",
            }
        )
        url = f"{self.BASE}?{params}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kai-freqtrade/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            logger.warning(f"[news/cryptopanic] HTTP failed for {currency}: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.warning(f"[news/cryptopanic] decode failed for {currency}: {e}")
            return []
        except Exception as e:
            logger.warning(f"[news/cryptopanic] unexpected for {currency}: {e}")
            return []

        items: list[NewsItem] = []
        for r in (payload.get("results") or [])[:limit]:
            votes = r.get("votes") or {}
            src = r.get("source") or {}
            items.append(
                NewsItem(
                    title=str(r.get("title", ""))[:200],
                    source=str(src.get("title") or src.get("domain") or ""),
                    published_at=str(r.get("published_at", "")),
                    url=str(r.get("url") or r.get("original_url") or ""),
                    votes_positive=int(votes.get("positive", 0) or 0),
                    votes_negative=int(votes.get("negative", 0) or 0),
                )
            )
        return items


def _provider_for(name: str):
    name = (name or "none").lower()
    if name == "cryptopanic":
        token = os.getenv("CRYPTOPANIC_TOKEN", "")
        if not token:
            logger.warning(
                "[news] LLM_NEWS_PROVIDER=cryptopanic but CRYPTOPANIC_TOKEN missing — falling back to none"
            )
            return _NoneProvider()
        return _CryptoPanicProvider(token)
    return _NoneProvider()


def _cache_path(pair: str, provider: str) -> Path:
    safe = pair.replace("/", "_").replace(":", "_")
    return NEWS_CACHE_DIR / f"{provider}_{safe}.json"


def fetch_recent_news(pair: str, limit: int = 5) -> list[NewsItem]:
    """
    페어에 대한 최근 뉴스 헤드라인 반환. 캐시 우선.
    실패는 모두 빈 리스트로 흡수 (fail-soft).
    """
    provider_name = (os.getenv("LLM_NEWS_PROVIDER") or "none").lower()
    if provider_name == "none":
        return []

    cache_file = _cache_path(pair, provider_name)
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                data = json.load(f)
            age = time.time() - float(data.get("timestamp", 0))
            if age < NEWS_CACHE_TTL:
                return [NewsItem(**i) for i in data.get("items", [])][:limit]
        except Exception as e:
            logger.warning(f"[news] cache read failed for {pair}: {e}")

    provider = _provider_for(provider_name)
    try:
        items = provider.fetch(pair, limit=limit)
    except Exception as e:
        logger.warning(f"[news] provider {provider_name} raised: {e}")
        items = []

    try:
        with open(cache_file, "w") as f:
            json.dump(
                {
                    "pair": pair,
                    "provider": provider_name,
                    "timestamp": time.time(),
                    "items": [asdict(i) for i in items],
                },
                f,
            )
    except Exception as e:
        logger.warning(f"[news] cache write failed for {pair}: {e}")

    return items


def format_for_prompt(items: list[NewsItem], max_items: int = 5) -> str:
    """LLM 프롬프트에 삽입할 텍스트 블록으로 변환."""
    if not items:
        return "(no recent news available)"
    lines: list[str] = []
    for idx, n in enumerate(items[:max_items], 1):
        votes = ""
        if n.votes_positive or n.votes_negative:
            votes = f" +{n.votes_positive}/-{n.votes_negative}"
        src = f"[{n.source}]" if n.source else ""
        title = n.title.strip().replace("\n", " ")
        lines.append(f"{idx}. {src}{votes} {title}")
    return "\n".join(lines)
