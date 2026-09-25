"""Outgoing SearXNG queries are paced, because volume is what gets detected.

Measured 2026-08-28: a few hundred queries in ~45 minutes took `google` from
answering 4/4 probes to CAPTCHA-ing every one, with the request fingerprint
unchanged throughout - and a full Chrome header set changed brave's 429 by
nothing. What upstream engines react to is the rate of requests from an
address, not the shape of them, so pacing is the lever that works and header
or TLS impersonation is not.
"""
import threading
import time

import pytest

from librarian_searxng import client as providers
from librarian_searxng.config import settings


@pytest.fixture
def paced(monkeypatch):
    """Undo the suite-wide pacing bypass for tests that are about it."""
    monkeypatch.setattr(settings, "min_interval_seconds", 0.05)
    monkeypatch.setattr(settings, "jitter_seconds", 0.0)
    providers._next_allowed.clear()
    yield
    providers._next_allowed.clear()


class _Resp:
    def __init__(self, payload=None):
        self._payload = payload or {"results": []}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _record_times(monkeypatch):
    times: list[float] = []

    def get(url, params=None, timeout=None, **kw):
        times.append(time.monotonic())
        return _Resp()
    monkeypatch.setattr(providers.httpx, "get", get)
    return times


def test_consecutive_queries_are_spaced(paced, monkeypatch):
    times = _record_times(monkeypatch)
    for _ in range(3):
        providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    assert len(times) == 3
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(g >= 0.04 for g in gaps), gaps


def test_pacing_is_off_when_the_interval_is_zero(monkeypatch):
    """The suite runs with pacing disabled; a zero interval must be a true
    bypass rather than a very short sleep, or every test pays for it."""
    monkeypatch.setattr(settings, "min_interval_seconds", 0.0)
    times = _record_times(monkeypatch)
    start = time.monotonic()
    for _ in range(5):
        providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    assert len(times) == 5
    assert time.monotonic() - start < 0.05


def test_categories_are_paced_independently(paced, monkeypatch):
    """`general` and `science` hit disjoint upstream engine sets, so a science
    page spends arXiv's goodwill, not google's. Making one wait on the other
    would cost throughput and protect nothing."""
    times = _record_times(monkeypatch)
    start = time.monotonic()
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    providers.throttled_get("http://x/search", {"q": "a"}, 20, "science")
    assert len(times) == 2
    # Two different buckets, so the second must not have waited on the first.
    assert time.monotonic() - start < 0.04


def test_jitter_keeps_the_cadence_irregular(monkeypatch):
    """A request every N seconds on the dot is a metronome, which is itself a
    bot signal. The delay is drawn from a range, so gaps should differ."""
    monkeypatch.setattr(settings, "min_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "jitter_seconds", 0.05)
    providers._next_allowed.clear()
    times = _record_times(monkeypatch)
    for _ in range(6):
        providers.throttled_get("http://x/search", {"q": "a"}, 20, "general")
    gaps = [round(b - a, 3) for a, b in zip(times, times[1:])]
    assert len(set(gaps)) > 1, gaps
    providers._next_allowed.clear()


def test_concurrent_callers_take_distinct_slots(paced, monkeypatch):
    """The wait is computed under the lock and slept outside it. If it were
    slept while holding the lock, threads would serialise on the holder; if
    the slot were not reserved under the lock, they would all read the same
    'next allowed' and fire together - defeating the pacing entirely."""
    times = _record_times(monkeypatch)
    threads = [
        threading.Thread(
            target=providers.throttled_get,
            args=("http://x/search", {"q": "a"}, 20, "general"))
        for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ordered = sorted(times)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    assert len(times) == 3
    assert all(g >= 0.04 for g in gaps), gaps


def test_paged_search_collects_engine_health_from_the_real_search(monkeypatch):
    """Health comes off the search that ran, not a probe issued beforehand.
    The probe cost an extra fan-out to every upstream engine per iteration -
    spending the very budget whose exhaustion it exists to detect."""
    def get(url, params=None, timeout=None, **kw):
        return _Resp({
            "results": [{"url": "https://a/1"}],
            "unresponsive_engines": [["brave", "too many requests"]],
        })
    monkeypatch.setattr(providers.httpx, "get", get)
    sink: list = []
    list(providers.paged_search("http://x", "q", 1, unresponsive=sink))
    assert sink == [("brave", "too many requests")]


def test_an_engine_is_reported_once_across_pages(monkeypatch):
    """The same engine refuses on every page. Reporting it four times reads
    as four problems."""
    pages = []

    def get(url, params=None, timeout=None, **kw):
        pages.append(params.get("pageno"))
        return _Resp({
            "results": [{"url": f"https://a/{params.get('pageno')}"}],
            "unresponsive_engines": [["brave", "too many requests"]],
        })
    monkeypatch.setattr(providers.httpx, "get", get)
    sink: list = []
    list(providers.paged_search("http://x", "q", 99, max_pages=3,
                                 unresponsive=sink))
    assert len(pages) == 3
    assert sink == [("brave", "too many requests")]


def test_page_depth_is_configurable(monkeypatch):
    """Every page is another full fan-out to every upstream engine, so depth
    is the largest single amplifier in the loop and has to be tunable."""
    pages = []

    def get(url, params=None, timeout=None, **kw):
        pages.append(params.get("pageno"))
        return _Resp({"results": [{"url": f"https://a/{params.get('pageno')}"}]})
    monkeypatch.setattr(providers.httpx, "get", get)
    monkeypatch.setattr(settings, "max_pages", 2)
    list(providers.paged_search("http://x", "q", 99))
    assert pages == [1, 2]
