"""Keep the plugin's tests fast and hermetic.

Pacing is off (a zero interval is a true bypass), engine cooldowns start
empty, and librarian's response cache is off, since a cache hit would silently satisfy requests these tests
fake and count. The tests that are *about* pacing or caching turn them back
on explicitly.
"""
import pytest


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    from librarian.config import settings as core
    from librarian_searxng import client
    from librarian_searxng.config import settings

    monkeypatch.setattr(settings, "min_interval_seconds", 0.0)
    monkeypatch.setattr(settings, "jitter_seconds", 0.0)
    monkeypatch.setattr(core, "search_cache_enabled", False)
    client._next_allowed.clear()
    client._enabled_cache.clear()
    client.cooldowns.clear()
    yield
    client._next_allowed.clear()
    client._enabled_cache.clear()
    client.cooldowns.clear()
