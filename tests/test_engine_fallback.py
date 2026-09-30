"""The engine-health fallback (health.py): an engine that keeps refusing is
left out of later requests for a while, reported, and asked again after."""
import pytest

from librarian_searxng import client, health
from librarian_searxng.config import settings

ENGINES = ["brave", "bing", "yep"]


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture
def searx(monkeypatch):
    """A fake SearXNG: /config lists ENGINES in general; each /search answers
    with the next scripted refusal list (then none), recording its params."""
    state = {"refusals": [], "sent": [], "config": True}

    def get(url, params=None, timeout=None, **kw):
        if url.endswith("/config"):
            if not state["config"]:
                raise ConnectionError("no /config")
            return _Resp({"engines": [{"name": n, "enabled": True, "categories": ["general"]}
                                      for n in ENGINES]})
        state["sent"].append(dict(params))
        refused = state["refusals"].pop(0) if state["refusals"] else []
        return _Resp({"results": [{"url": f"https://r/{len(state['sent'])}/{i}"} for i in range(20)],
                      "unresponsive_engines": [list(r) for r in refused]})
    monkeypatch.setattr(client.httpx, "get", get)
    clock = {"now": 1000.0}
    monkeypatch.setattr(client, "cooldowns", health.EngineCooldowns(clock=lambda: clock["now"]))
    monkeypatch.setattr(settings, "engine_cooldown_seconds", 600.0)
    state["clock"] = clock
    return state


def _search(searx):
    return client.search_json("http://x", {"q": "a", "format": "json"}, 20, None)


def test_a_rate_limited_engine_is_skipped_on_the_next_request(searx):
    searx["refusals"] = [[("brave", "too many requests")]]
    _, degraded = _search(searx)
    assert degraded == [("brave", "too many requests")]
    assert "disabled_engines" not in searx["sent"][0]

    _, degraded = _search(searx)
    assert searx["sent"][1]["disabled_engines"] == "brave__general"
    assert degraded[0][0] == "brave" and degraded[0][1].startswith("skipped for 600s")


def test_later_pages_of_a_search_skip_it_and_report_it_once(searx):
    searx["refusals"] = [[("brave", "Suspended: CAPTCHA")]]
    unresponsive = []
    list(client.paged_search("http://x", "q", 60, unresponsive=unresponsive))
    assert [p.get("disabled_engines") for p in searx["sent"]] == [None, "brave__general", "brave__general"]
    assert unresponsive == [("brave", "Suspended: CAPTCHA")]


def test_a_transient_failure_needs_two_in_a_row(searx):
    searx["refusals"] = [[("bing", "timeout")], [], [("bing", "timeout")], [("bing", "timeout")]]
    for _ in range(4):
        _search(searx)
    assert all("disabled_engines" not in p for p in searx["sent"])    # timeout, answer, timeout, timeout
    _search(searx)
    assert searx["sent"][4]["disabled_engines"] == "bing__general"


def test_the_cooldown_ends_doubles_and_clears(searx):
    clock = searx["clock"]
    searx["refusals"] = [[("brave", "too many requests")]]
    _search(searx)
    clock["now"] += 601                                   # cooled 600s; asked again
    searx["refusals"] = [[("brave", "too many requests")]]
    _search(searx)
    assert "disabled_engines" not in searx["sent"][1]
    _, degraded = _search(searx)
    assert "1200s" in dict(degraded)["brave"]             # refused again: doubled
    clock["now"] += 1201
    _search(searx)                                        # asked, and it answered
    _search(searx)
    assert "disabled_engines" not in searx["sent"][-1]


def test_it_never_skips_every_enabled_engine(searx):
    searx["refusals"] = [[(n, "too many requests") for n in ENGINES]]
    _search(searx)
    _, degraded = _search(searx)
    assert "disabled_engines" not in searx["sent"][1]
    assert degraded == []


def test_without_the_engine_list_nothing_is_skipped(searx):
    searx["config"] = False
    searx["refusals"] = [[("brave", "too many requests")]]
    _search(searx)
    _search(searx)
    assert "disabled_engines" not in searx["sent"][1]


def test_zero_turns_it_off(searx, monkeypatch):
    monkeypatch.setattr(settings, "engine_cooldown_seconds", 0.0)
    searx["refusals"] = [[("brave", "too many requests")]]
    _search(searx)
    _search(searx)
    assert "disabled_engines" not in searx["sent"][1]


def test_categories_cool_separately(searx):
    searx["refusals"] = [[("brave", "too many requests")]]
    _search(searx)
    assert client.cooldowns.cooling("science") == {}
    assert "brave" in client.cooldowns.cooling("general")


def test_a_cache_hit_neither_skips_nor_learns_and_keys_ignore_the_skip(searx, monkeypatch, tmp_path):
    from librarian.config import settings as core
    monkeypatch.setattr(core, "search_cache_enabled", True)
    monkeypatch.setattr(core, "search_cache_dir", str(tmp_path))
    searx["refusals"] = [[("brave", "too many requests")]]
    client.search_json("http://x", {"q": "first", "format": "json"}, 20, None)
    # Stored while brave was cooling, under the key without the skip list:
    client.search_json("http://x", {"q": "second", "format": "json"}, 20, None)
    assert searx["sent"][1]["disabled_engines"] == "brave__general"
    sent = len(searx["sent"])
    searx["clock"]["now"] += 10_000                      # cooldown over
    payload, degraded = client.search_json("http://x", {"q": "second", "format": "json"}, 20, None)
    assert len(searx["sent"]) == sent and payload["results"]      # replayed, not asked
    assert degraded == []
    assert "brave" not in client.cooldowns.cooling("general")
