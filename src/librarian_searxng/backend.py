"""`SearxngBackend`: librarian's search-backend interface over SearXNG.

Registered under the `librarian.search_backends` entry point as "searxng".
librarian discovers it with `load_backend()`; nothing in librarian imports
this package directly.

It answers three categories, mapped to SearXNG's own:

    web     -> general  (paged)
    video   -> videos   (one page)
    science -> science  (paged, the whole pool)

and returns results in SearXNG's order, unfiltered. The topical gate,
dedup, ranking and caps are librarian's (research/gather.py) and apply to
every backend alike.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from librarian.research.backends import BACKEND_API_VERSION, SearchResponse
from librarian.research.providers import SearchResult
from librarian.research.search_cache import CachePolicy

from . import client
from .config import settings
from .container import container

_SEARXNG_CATEGORY = {"web": "general", "video": "videos", "science": "science"}


def _result(item: dict) -> SearchResult:
    url = item["url"]
    return SearchResult(url=url, title=item.get("title") or url, snippet=item.get("content") or "")


class SearxngBackend:
    name = "searxng"
    api_version = BACKEND_API_VERSION
    categories = frozenset({"web", "video", "science"})

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.base_url).rstrip("/")

    def search(self, query: str, count: int, category: str = "web", *,
               cache: Optional[CachePolicy] = None) -> SearchResponse:
        if category not in self.categories:
            raise ValueError(f"searxng backend has no category {category!r}")
        degraded: list[tuple[str, str]] = []

        if category == "video":
            # One page. The request is exactly the pre-split one (no pageno),
            # so its cached responses still match. The media-URL filter is
            # librarian's, applied after this returns.
            payload, degraded = client.search_json(
                self.base_url, {"q": query, "format": "json", "categories": "videos"},
                20, "videos", cache=cache)
            items = [i for i in payload.get("results", []) if i.get("url")]
            return SearchResponse([_result(i) for i in items], degraded)

        # web: no `categories` parameter, SearXNG's default (general), as
        # before the split, so cache keys are unchanged.
        categories = "science" if category == "science" else None
        items = client.paged_search(self.base_url, query, count, categories=categories,
                                    unresponsive=degraded, cache=cache)
        return SearchResponse([_result(i) for i in items], degraded)

    @contextmanager
    def session(self):
        """Start the container if needed; let it idle-stop afterwards."""
        container.acquire()
        try:
            yield self
        finally:
            container.release()

    def diagnose(self, category: str = "web") -> dict:
        """Refusing and silently empty engines for one category. Uncached."""
        cat = _SEARXNG_CATEGORY.get(category, category)
        return {
            "refusing": client.engine_health(self.base_url, categories=cat),
            "silent": client.silent_engines(self.base_url, categories=cat),
        }
