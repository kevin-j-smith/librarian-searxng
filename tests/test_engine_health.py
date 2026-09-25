"""A refusing engine announces itself. A silently empty one does not.

`engine_health` only ever saw the loud half: an engine that reports a CAPTCHA
or a 429 lands in `unresponsive_engines` and gets named. The quiet half is an
engine that is enabled, answers 200 OK with nothing, and stays out of that
list - invisible to the health check and to the result set alike, narrowing
the pool while looking exactly like an index that lacks the material. mojeek
did this for a day: enabled 2026-08-27 as part of the fix for a *different*
narrow-pool bug, contributing zero the whole time.
"""
import json

import pytest

from librarian_searxng import client as providers


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_http(config_engines, per_query_results):
    """Serve /config once and /search from a {query: {engine: n}} mapping."""
    def get(url, params=None, timeout=None, **kw):
        if url.endswith("/config"):
            return _Resp({"engines": config_engines})
        query = (params or {}).get("q")
        spec = per_query_results.get(query, {})
        return _Resp({
            "results": [
                {"url": f"https://x/{eng}/{i}", "engines": [eng]}
                for eng, n in spec.get("engines", {}).items()
                for i in range(n)
            ],
            "unresponsive_engines": spec.get("unresponsive", []),
        })
    return get


_GENERAL = [
    {"name": "bing", "enabled": True, "categories": ["general"]},
    {"name": "mojeek", "enabled": True, "categories": ["general"]},
    {"name": "startpage", "enabled": False, "categories": ["general"]},
]


def test_an_engine_that_answers_nothing_on_every_probe_is_reported(monkeypatch):
    monkeypatch.setattr(providers.httpx, "get", _fake_http(
        _GENERAL, {q: {"engines": {"bing": 10}} for q in providers.PROBE_QUERIES}))
    assert providers.silent_engines() == ["mojeek"]


def test_an_engine_thin_on_one_subject_is_not_reported(monkeypatch):
    """Silence on a single query means nothing - plenty of engines have
    little to say about one topic. Only silence across every unrelated probe
    separates a dead engine from a merely narrow one."""
    first, *rest = providers.PROBE_QUERIES
    per_query = {first: {"engines": {"bing": 10}}}
    per_query.update({q: {"engines": {"bing": 10, "mojeek": 3}} for q in rest})
    monkeypatch.setattr(providers.httpx, "get", _fake_http(_GENERAL, per_query))
    assert providers.silent_engines() == []


def test_a_refusing_engine_is_left_to_engine_health(monkeypatch):
    """Naming it in both places reads as two separate problems. An engine
    that reported a CAPTCHA is not silent - it is loud, and already covered."""
    monkeypatch.setattr(providers.httpx, "get", _fake_http(
        _GENERAL,
        {q: {"engines": {"bing": 10}, "unresponsive": [["mojeek", "CAPTCHA"]]}
         for q in providers.PROBE_QUERIES}))
    assert providers.silent_engines() == []


def test_infobox_and_utility_engines_are_not_treated_as_dead(monkeypatch):
    """wikipedia, currency and dictzone are enabled in `general` but are not
    web indexes - they legitimately answer nothing for most queries. Without
    the exclusion they are six false positives on every probe, and the one
    real finding is lost among them."""
    engines = _GENERAL + [
        {"name": n, "enabled": True, "categories": ["general"]}
        for n in ("wikipedia", "currency", "dictzone", "lingva")
    ]
    monkeypatch.setattr(providers.httpx, "get", _fake_http(
        engines, {q: {"engines": {"bing": 10}} for q in providers.PROBE_QUERIES}))
    assert providers.silent_engines() == ["mojeek"]


def test_a_disabled_engine_is_not_reported(monkeypatch):
    """startpage is off precisely because it refuses. Reporting it as
    silently empty would be re-reporting a decision already made."""
    monkeypatch.setattr(providers.httpx, "get", _fake_http(
        _GENERAL, {q: {"engines": {"bing": 10, "mojeek": 2}}
                   for q in providers.PROBE_QUERIES}))
    assert "startpage" not in providers.silent_engines()


def test_an_instance_that_is_down_reports_nothing_rather_than_everything(
        monkeypatch):
    """If the whole instance is unreachable every engine looks silent. That
    is a different failure, and announcing it as "every engine is dead" would
    bury the real signal this check exists to give."""
    def boom(*a, **kw):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(providers.httpx, "get", boom)
    assert providers.silent_engines() == []


def test_a_contributing_engine_counted_only_from_an_infobox_still_counts(
        monkeypatch):
    """A result is not the only way an engine answers. Counting `results`
    alone would report an infobox-only engine as dead."""
    def get(url, params=None, timeout=None, **kw):
        if url.endswith("/config"):
            return _Resp({"engines": _GENERAL})
        return _Resp({
            "results": [{"url": "https://x/1", "engines": ["bing"]}],
            "infoboxes": [{"engine": "mojeek"}],
        })
    monkeypatch.setattr(providers.httpx, "get", get)
    assert providers.silent_engines() == []
