"""SearxngContainer: idempotent start, refcounted stop, and that failures
raise instead of silently continuing."""
import subprocess

import httpx
import pytest

from librarian_searxng.container import SearxngContainer


def _fake_run_success(*a, **k):
    class R:
        returncode = 0
        stderr = ""
    return R()


def test_acquire_is_a_noop_when_already_healthy(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: type("R", (), {"status_code": 200})())
    calls = {"n": 0}
    def fail_if_called(*a, **k):
        calls["n"] += 1
        raise AssertionError("docker should not be invoked when already healthy")
    monkeypatch.setattr(subprocess, "run", fail_if_called)

    c = SearxngContainer()
    c.acquire(timeout=1)
    assert calls["n"] == 0


def test_acquire_starts_container_when_unhealthy_then_polls_to_healthy(monkeypatch):
    health_sequence = iter([False, False, True])
    def fake_get(*a, **k):
        healthy = next(health_sequence, True)
        if not healthy:
            raise httpx.ConnectError("refused")
        return type("R", (), {"status_code": 200})()
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(subprocess, "run", _fake_run_success)
    monkeypatch.setattr("time.sleep", lambda s: None)

    c = SearxngContainer()
    c.acquire(timeout=5)  # should not raise


def test_acquire_raises_and_rolls_back_refcount_when_docker_missing(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    def missing(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", missing)

    c = SearxngContainer()
    with pytest.raises(RuntimeError):
        c.acquire(timeout=1)
    assert c._refcount == 0  # failed acquire must not leak a held reference


def test_acquire_raises_on_timeout_without_ever_becoming_healthy(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    monkeypatch.setattr(subprocess, "run", _fake_run_success)
    monkeypatch.setattr("time.sleep", lambda s: None)

    times = iter([0, 0.1, 100])  # jump straight past the deadline
    monkeypatch.setattr("time.monotonic", lambda: next(times, 100))

    c = SearxngContainer()
    with pytest.raises(RuntimeError, match="did not become healthy"):
        c.acquire(timeout=1)


def test_release_only_arms_stop_timer_when_refcount_reaches_zero(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: type("R", (), {"status_code": 200})())
    monkeypatch.setattr(subprocess, "run", _fake_run_success)

    c = SearxngContainer()
    c.acquire(timeout=1)
    c.acquire(timeout=1)  # refcount = 2
    c.release()
    assert c._stop_timer is None  # still one active consumer
    c.release()
    assert c._stop_timer is not None  # last release arms the idle-stop timer
    c._stop_timer.cancel()  # don't let the real timer fire during the test
