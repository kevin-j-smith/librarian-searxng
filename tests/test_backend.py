"""SearxngBackend: librarian's search-backend interface over SearXNG.

Covers the request each category sends (unchanged from before the plugin
split, so cached responses still match), paging, degraded-engine reporting,
the session lifecycle, diagnostics, and that librarian actually discovers
this package through its entry point.
"""
import pytest

from librarian.research.backends import (
    BACKEND_API_VERSION,
    SearchBackend,
    SupportsDiagnostics,
)
from librarian_searxng import backend as backend_mod
from librarian_searxng.backend import SearxngBackend


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _capture(monkeypatch, payload=None):
    seen = []

    def fake_get(url, params=None, timeout=None, **kw):
        seen.append((url, dict(params or {})))
        return _Resp(payload if payload is not None else {"results": []})

    monkeypatch.setattr("httpx.get", fake_get)
    return seen


def _pager(pages):
    """A fake SearXNG that serves `pages[pageno - 1]`."""
    calls = []

    def fake_get(url, params=None, timeout=None, **kw):
        calls.append(params["pageno"])
        idx = params["pageno"] - 1
        return _Resp({"results": pages[idx] if idx < len(pages) else []})

    return fake_get, calls


# ─── the interface ───────────────────────────────────────────────────────

def test_it_implements_librarians_backend_interface():
    b = SearxngBackend("http://x")
    assert isinstance(b, SearchBackend)
    assert isinstance(b, SupportsDiagnostics)
    assert b.name == "searxng"
    assert b.api_version == BACKEND_API_VERSION
    assert b.categories == {"web", "video", "science"}


def test_librarian_discovers_it_through_the_entry_point(monkeypatch):
    """Needs this package installed (e.g. `uv pip install -e .`)."""
    from importlib.metadata import entry_points
    from librarian.config import settings
    from librarian.research.backends import load_backend

    names = [ep.name for ep in entry_points(group="librarian.search_backends")]
    if "searxng" not in names:
        pytest.skip("librarian-searxng is not installed in this environment")
    monkeypatch.setattr(settings, "research_search_backend", "searxng")
    assert isinstance(load_backend(), SearxngBackend)


def test_an_unknown_category_is_refused():
    with pytest.raises(ValueError):
        SearxngBackend("http://x").search("q", 10, "images")


# ─── request shapes (these are the cache keys; they must not drift) ───────

def test_web_asks_for_the_default_category_page_by_page(monkeypatch):
    seen = _capture(monkeypatch)
    SearxngBackend("http://x").search("q", 10, "web")
    assert seen == [("http://x/search", {"q": "q", "format": "json", "pageno": 1})]


def test_video_asks_for_the_videos_category_once(monkeypatch):
    seen = _capture(monkeypatch)
    SearxngBackend("http://x").search("wide shot framing", 5, "video")
    assert seen == [("http://x/search",
                     {"q": "wide shot framing", "format": "json", "categories": "videos"})]


def test_science_asks_for_the_science_category(monkeypatch):
    seen = _capture(monkeypatch)
    SearxngBackend("http://x").search("surface code thresholds", 400, "science")
    assert seen[0][1]["categories"] == "science"
    assert seen[0][1]["format"] == "json"


def test_results_are_returned_unfiltered_for_librarian_to_compile(monkeypatch):
    """The topical gate, media filter, dedup and ranking are librarian's and
    apply to every backend alike; the backend must not pre-empt them."""
    _capture(monkeypatch, {"results": [
        {"url": "https://example.com/a-blog-post", "title": "Not a video"},
        {"url": "https://www.youtube.com/watch?v=VwbSxxm6skM", "title": "Real"},
        {"title": "no url"},
    ]})
    out = SearxngBackend("http://x").search("x", 10, "video").results
    assert [r.url for r in out] == ["https://example.com/a-blog-post",
                                    "https://www.youtube.com/watch?v=VwbSxxm6skM"]


def test_a_missing_title_falls_back_to_the_url(monkeypatch):
    _capture(monkeypatch, {"results": [{"url": "https://a.example/1", "content": "snip"}]})
    r = SearxngBackend("http://x").search("q", 10).results[0]
    assert (r.title, r.snippet) == ("https://a.example/1", "snip")


def test_refusing_engines_are_reported_as_degraded(monkeypatch):
    _capture(monkeypatch, {"results": [{"url": "https://a/1"}],
                           "unresponsive_engines": [["brave", "too many requests"]]})
    resp = SearxngBackend("http://x").search("q", 1)
    assert resp.degraded == [("brave", "too many requests")]


# ─── paging ──────────────────────────────────────────────────────────────

def test_more_than_one_page_is_fetched_when_needed(monkeypatch):
    """9 of the 10 documents WebSearch returned for a research query were in
    SearXNG's own index, on pages 2 and 3. Fetching only page 1 reported an
    index gap that did not exist."""
    pages = [
        [{"url": f"https://p1-{i}.com", "title": "t"} for i in range(20)],
        [{"url": f"https://p2-{i}.com", "title": "t"} for i in range(20)],
    ]
    fake_get, calls = _pager(pages)
    monkeypatch.setattr("httpx.get", fake_get)
    out = SearxngBackend("http://x").search("q", 25).results
    assert len(out) == 25
    assert calls == [1, 2]


def test_a_single_page_is_enough_for_a_small_count(monkeypatch):
    fake_get, calls = _pager([[{"url": f"https://p1-{i}.com", "title": "t"} for i in range(20)]])
    monkeypatch.setattr("httpx.get", fake_get)
    assert len(SearxngBackend("http://x").search("q", 10).results) == 10
    assert calls == [1], "no page 2 request when page 1 already satisfies count"


def test_paging_stops_at_an_empty_page(monkeypatch):
    fake_get, calls = _pager([[{"url": "https://only.com", "title": "t"}], []])
    monkeypatch.setattr("httpx.get", fake_get)
    assert len(SearxngBackend("http://x").search("q", 50).results) == 1
    assert calls == [1, 2]


def test_a_url_repeated_across_pages_is_returned_once(monkeypatch):
    dupe = {"url": "https://same.com", "title": "t"}
    fake_get, _ = _pager([[dupe], [dupe], []])
    monkeypatch.setattr("httpx.get", fake_get)
    assert len(SearxngBackend("http://x").search("q", 50).results) == 1


def test_the_page_budget_is_bounded(monkeypatch):
    """Unbounded paging on a query with endless shallow results would turn a
    search into a crawl."""
    fake_get, calls = _pager([
        [{"url": f"https://p{p}-{i}.com", "title": "t"} for i in range(20)] for p in range(20)
    ])
    monkeypatch.setattr("httpx.get", fake_get)
    SearxngBackend("http://x").search("q", 10_000)
    assert len(calls) == 4


def test_the_science_pool_is_read_to_the_page_budget(monkeypatch):
    """librarian ranks the science pool before cutting it, and ranking can
    only promote what it has seen, so a large count must walk every page."""
    pages = [[{"url": f"https://arxiv.org/abs/p{p}{i}", "title": "q"} for i in range(20)]
             for p in range(2)] + [[]]
    fake_get, calls = _pager(pages)
    monkeypatch.setattr("httpx.get", fake_get)
    SearxngBackend("http://x").search("q", 400, "science")
    assert calls == [1, 2, 3]


# ─── lifecycle and diagnostics ───────────────────────────────────────────

def test_a_session_acquires_and_releases_the_container(monkeypatch):
    events = []
    monkeypatch.setattr(backend_mod.container, "acquire", lambda timeout=None: events.append("up"))
    monkeypatch.setattr(backend_mod.container, "release", lambda: events.append("down"))
    with SearxngBackend("http://x").session():
        events.append("search")
    assert events == ["up", "search", "down"]


def test_the_container_is_released_even_when_the_search_fails(monkeypatch):
    events = []
    monkeypatch.setattr(backend_mod.container, "acquire", lambda timeout=None: None)
    monkeypatch.setattr(backend_mod.container, "release", lambda: events.append("down"))
    with pytest.raises(RuntimeError):
        with SearxngBackend("http://x").session():
            raise RuntimeError("boom")
    assert events == ["down"]


def test_diagnose_maps_librarian_categories_to_searxng_ones(monkeypatch):
    seen = {}
    monkeypatch.setattr(backend_mod.client, "engine_health",
                        lambda base, categories=None: seen.setdefault("health", categories) and [])
    monkeypatch.setattr(backend_mod.client, "silent_engines",
                        lambda base, categories=None: seen.setdefault("silent", categories) and [])
    report = SearxngBackend("http://x").diagnose("video")
    assert seen == {"health": "videos", "silent": "videos"}
    assert set(report) == {"refusing", "silent"}
