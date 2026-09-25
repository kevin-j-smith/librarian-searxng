"""HTTP client for a SearXNG instance: paced, paged, cached, health-aware.

Moved here from librarian's `research/providers.py` when the SearXNG
integration became a plugin. Behaviour is unchanged. Request URLs and
parameters, the pacing buckets, and the cache keys are exactly what they
were, so responses cached before the split are still served.
"""
from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from pathlib import Path

import httpx

from librarian.research import search_cache
from librarian.research.search_cache import CachePolicy

from .config import settings

logger = logging.getLogger("librarian_searxng.client")

# SearXNG's bot detection logs "X-Forwarded-For nor X-Real-IP header is set!"
# as an error for every request without a client-IP header. This client only
# ever talks to its own instance over loopback, so it says so.
_LOCAL_HEADERS = {"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"}


# --- cache namespace ---------------------------------------------------------

_fingerprint: str | None = None


def namespace() -> str:
    """A short hash of the SearXNG settings file, or "default" if unreadable.

    Cache keys carry this so that enabling or disabling an engine invalidates
    the cache instead of replaying results gathered from a *different engine
    set*. Computed once per process from a local file, so it holds offline
    and a replay works with the container stopped.
    """
    global _fingerprint
    if _fingerprint is None:
        try:
            _fingerprint = hashlib.sha256(Path(settings.settings_path).read_bytes()).hexdigest()[:12]
        except Exception:
            _fingerprint = "default"
    return _fingerprint


class CachedResponse:
    """Quacks like the httpx.Response the callers use, and nothing more."""

    from_cache = True

    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


# --- pacing ------------------------------------------------------------------
# Keyed by SearXNG category, because the categories map to disjoint upstream
# engine sets in settings.yml: a `science` query spends arXiv's and
# Crossref's goodwill, not google's, so making a science page wait on a
# general one would cost throughput for no protection.

_pace_lock = threading.Lock()
_next_allowed: dict[str, float] = {}


def _pace(bucket: str) -> None:
    interval = settings.min_interval_seconds
    if interval <= 0:
        return
    delay = interval + random.uniform(0, max(0.0, settings.jitter_seconds))
    with _pace_lock:
        now = time.monotonic()
        slot = max(now, _next_allowed.get(bucket, 0.0))
        _next_allowed[bucket] = slot + delay
    # Computed under the lock, slept outside it, so concurrent callers queue
    # for distinct slots instead of serialising on one holder.
    wait = slot - time.monotonic()
    if wait > 0:
        logger.debug("pacing %s query by %.1fs", bucket, wait)
        time.sleep(wait)


def throttled_get(url: str, params: dict, timeout: float, category: str | None,
                  *, cache: CachePolicy | None = None, use_cache: bool = True):
    """One HTTP GET to SearXNG: served from cache if possible, else paced.

    A cache hit returns *before* the pacing sleep: nothing goes upstream, so
    nothing is owed to an engine. Only successful, parseable responses are
    stored; a cached 429 would turn one bad minute into a bad day.
    """
    if use_cache:
        payload = search_cache.lookup(url, params, namespace(), cache)
        if payload is not None:
            logger.debug("cache hit %s", params.get("q"))
            return CachedResponse(payload)
    _pace(category or "general")
    resp = httpx.get(url, params=params, timeout=timeout, headers=_LOCAL_HEADERS)
    if use_cache and search_cache.enabled(cache):
        try:
            resp.raise_for_status()
            search_cache.store(url, params, namespace(), resp.json(), cache)
        except Exception:
            pass
    return resp


def paged_search(base_url: str, query: str, count: int, categories: str | None = None,
                 max_pages: int | None = None, unresponsive: list | None = None,
                 cache: CachePolicy | None = None):
    """Yield raw SearXNG result dicts, walking pages until *count* is met.

    SearXNG returns roughly 20 results per page, and asking for more than one
    is not greed: 9 of the 10 documents WebSearch found for "surface code
    error correction thresholds" were in SearXNG's own index on pages 2 and
    3. Stops early on an empty page. *unresponsive* collects the engines
    SearXNG reports as refusing, from the searches that actually ran.
    """
    if max_pages is None:
        max_pages = settings.max_pages
    seen: set[str] = set()
    reported: set[str] = set()
    for page in range(1, max_pages + 1):
        params = {"q": query, "format": "json", "pageno": page}
        if categories:
            params["categories"] = categories
        resp = throttled_get(f"{base_url}/search", params, 20, categories, cache=cache)
        resp.raise_for_status()
        payload = resp.json()
        if unresponsive is not None:
            for entry in payload.get("unresponsive_engines") or []:
                name = str(entry[0])
                if name not in reported:
                    reported.add(name)
                    unresponsive.append((name, str(entry[1]) if len(entry) > 1 else ""))
        items = payload.get("results", [])
        if not items:
            return
        for item in items:
            url = item.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            yield item
            if len(seen) >= count:
                return


# --- health ------------------------------------------------------------------

def engine_health(base_url: str | None = None, query: str = "test",
                  categories: str | None = None) -> list[tuple[str, str]]:
    """Which engines refused to answer, as (engine, reason) pairs.

    A failing engine is invisible in the results; it just narrows the pool,
    and a narrower pool reads exactly like an index that lacks the material.
    Never cached: a cached health check is not a health check.
    """
    base = (base_url or settings.base_url).rstrip("/")
    params = {"q": query, "format": "json"}
    if categories:
        params["categories"] = categories
    try:
        resp = throttled_get(f"{base}/search", params, 30, categories, use_cache=False)
        resp.raise_for_status()
        return [(str(e[0]), str(e[1]) if len(e) > 1 else "")
                for e in resp.json().get("unresponsive_engines") or []]
    except Exception:
        return []


# Engines enabled in `general` that are not general web indexes: infobox and
# utility engines that legitimately answer nothing for most queries. Without
# this, `silent_engines` reports six false positives on every probe.
NON_INDEX_ENGINES = frozenset({
    "wikipedia", "wikidata", "currency", "dictzone", "lingva",
    "mymemory translated", "tineye",
})

# Deliberately unrelated to each other: an engine that is merely thin on one
# subject still answers one of these.
PROBE_QUERIES = ("camera tracking", "surface code threshold", "school district boundaries")


def silent_engines(base_url: str | None = None, queries: tuple[str, ...] = PROBE_QUERIES,
                   categories: str | None = None) -> list[str]:
    """Enabled engines that return nothing across every probe query and never
    report a failure. (mojeek did exactly this for a day in August 2026.)
    Costs one request per query, so it is a manual diagnostic."""
    base = (base_url or settings.base_url).rstrip("/")
    try:
        # Not paced: /config is answered from SearXNG's own settings and never
        # touches an upstream engine.
        config = httpx.get(f"{base}/config", timeout=30, headers=_LOCAL_HEADERS).json()
    except Exception:
        return []

    wanted = {c.strip() for c in (categories or "general").split(",")}
    enabled = {
        e["name"] for e in config.get("engines") or []
        if e.get("enabled") and wanted & set(e.get("categories") or [])
        and e["name"] not in NON_INDEX_ENGINES
    }
    if not enabled:
        return []

    contributed: set[str] = set()
    reported: set[str] = set()
    for query in queries:
        params = {"q": query, "format": "json"}
        if categories:
            params["categories"] = categories
        try:
            payload = throttled_get(f"{base}/search", params, 30, categories, use_cache=False).json()
        except Exception:
            return []  # can't tell silence from an instance that is down
        for key in ("results", "infoboxes", "answers"):
            for item in payload.get(key) or []:
                if not isinstance(item, dict):
                    continue
                names = item.get("engines") or ([item["engine"]] if item.get("engine") else [])
                contributed.update(str(n) for n in names)
        # A refusing engine is engine_health's to report, not a silent one.
        reported.update(str(e[0]) for e in payload.get("unresponsive_engines") or [])
    return sorted(enabled - contributed - reported)
