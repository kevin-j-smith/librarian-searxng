"""How this backend uses librarian's response cache (research/search_cache.py).

Responses are cached, because pacing fixes access and not reproducibility.

Pacing made *access* consistent - every enabled engine now answers every
query. It cannot make results consistent, because the web changes underneath,
so a difference between two runs stayed ambiguous. Freezing the response is
what removes the ambiguity: a replayed run is byte-identical by construction,
and any difference from it is a deliberate refresh rather than a mystery.

This is the same mechanism that makes the built-in WebSearch tool return
identical result sets hours apart - it serves an index snapshot, and the
giveaway is a page title staler than the live page's.
"""
import json
import time

import pytest

from librarian.config import settings as core
from librarian.research import search_cache
from librarian.research.search_cache import CachePolicy
from librarian_searxng import client as providers
from librarian_searxng.config import settings


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "search_cache_enabled", True)
    monkeypatch.setattr(core, "search_cache_dir", str(tmp_path / "c"))
    monkeypatch.setattr(core, "search_cache_ttl_seconds", 86400.0)
    monkeypatch.setattr(providers, "_fingerprint", "testns")
    search_cache.reset_cache_stats()
    yield tmp_path


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _counting_http(monkeypatch, payload=None):
    calls = []

    def get(url, params=None, timeout=None, **kw):
        calls.append(params)
        return _Resp(payload if payload is not None else
                     {"results": [{"url": f"https://a/{len(calls)}"}]})
    monkeypatch.setattr(providers.httpx, "get", get)
    return calls


def test_a_repeated_query_is_served_from_cache(cache, monkeypatch):
    calls = _counting_http(monkeypatch)
    first = providers.throttled_get("http://x/search", {"q": "a"}, 20, "general").json()
    second = providers.throttled_get("http://x/search", {"q": "a"}, 20, "general").json()
    assert len(calls) == 1
    assert first == second


def test_a_cache_hit_does_not_pay_the_pacing_delay(cache, monkeypatch):
    """The whole point of replaying: nothing goes upstream, so nothing is owed
    to an engine and nothing needs waiting for. A replayed eval costs no
    goodwill and no wall clock."""
    monkeypatch.setattr(settings, "min_interval_seconds", 0.4)
    monkeypatch.setattr(settings, "jitter_seconds", 0.0)
    providers._next_allowed.clear()
    _counting_http(monkeypatch)
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    start = time.monotonic()
    for _ in range(5):
        providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    assert time.monotonic() - start < 0.2
    providers._next_allowed.clear()


def test_different_queries_do_not_collide(cache, monkeypatch):
    calls = _counting_http(monkeypatch)
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    providers.throttled_get("http://x/search", {"q": "b"}, 20, "general")
    assert len(calls) == 2


def test_the_page_number_is_part_of_the_key(cache, monkeypatch):
    """Every page is a distinct upstream fan-out and a distinct answer.
    Collapsing pages would replay page 1 for the whole paged walk."""
    calls = _counting_http(monkeypatch)
    for page in (1, 2, 3):
        providers.throttled_get("http://x/search", {"q": "a", "pageno": page},
                                 20, "general")
    assert len(calls) == 3


def test_the_engine_set_namespaces_the_cache(cache, monkeypatch):
    """Enabling or disabling an engine has to invalidate the cache. Replaying
    results gathered from a *different engine set* would be the most confusing
    possible failure of a layer whose whole job is making runs comparable."""
    calls = _counting_http(monkeypatch)
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    monkeypatch.setattr(providers, "_fingerprint", "a-different-config")
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    assert len(calls) == 2


def test_an_expired_entry_is_refetched(cache, monkeypatch):
    calls = _counting_http(monkeypatch)
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    monkeypatch.setattr(core, "search_cache_ttl_seconds", 0.001)
    time.sleep(0.01)
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    assert len(calls) == 2


def test_a_fixture_has_no_expiry(cache, monkeypatch):
    """`max_age=None` is what the eval passes. A recorded comparison must
    replay whatever its age, or re-running it is merely repeating it."""
    calls = _counting_http(monkeypatch)
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    path = search_cache.cache_path("http://x/search", {"q": "a"}, providers.namespace())
    import os
    os.utime(path, (0, 0))   # ancient
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general",
                             cache=CachePolicy(max_age=None))
    assert len(calls) == 1


def test_refresh_bypasses_the_read_but_still_writes(cache, monkeypatch):
    calls = _counting_http(monkeypatch)
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general",
                             cache=CachePolicy(refresh=True))
    assert len(calls) == 2
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    assert len(calls) == 2, "the refreshed response should have been stored"


def test_a_failed_response_is_not_cached(cache, monkeypatch):
    """A 429 or a CAPTCHA page frozen for a day turns one bad minute into a
    bad day."""
    class _Bad:
        def raise_for_status(self):
            raise RuntimeError("429")

        def json(self):
            return {}
    calls = []

    def get(url, params=None, timeout=None, **kw):
        calls.append(params)
        return _Bad()
    monkeypatch.setattr(providers.httpx, "get", get)
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    assert len(calls) == 2


def test_a_corrupt_entry_is_a_miss_not_an_error(cache, monkeypatch):
    calls = _counting_http(monkeypatch)
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    search_cache.cache_path("http://x/search", {"q": "a"}, providers.namespace()).write_text("{not json")
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    assert len(calls) == 2


def test_health_checks_are_never_served_from_cache(cache, monkeypatch):
    """`engine_health` reports which engines are refusing *now*. A cached
    health check is not a health check."""
    calls = []

    def get(url, params=None, timeout=None, **kw):
        calls.append(params)
        return _Resp({"results": [], "unresponsive_engines": [["brave", "429"]]})
    monkeypatch.setattr(providers.httpx, "get", get)
    providers.engine_health(base_url="http://x", query="a")
    providers.engine_health(base_url="http://x", query="a")
    assert len(calls) == 2


def test_stats_distinguish_a_replay_from_a_measurement(cache, monkeypatch):
    _counting_http(monkeypatch)
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    hits, misses = search_cache.cache_stats()
    assert (hits, misses) == (1, 1)
