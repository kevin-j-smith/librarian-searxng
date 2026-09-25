"""Lifecycle of the local SearXNG container.

Owned by the research subsystem through `SearxngBackend.session()`, never by
librarian's server startup, so the container only runs while research is in
flight. Idempotent by design:

- acquire() no-ops if SearXNG already answers health checks, whoever
  started it (this code, the user, or a container left from a crash).
- release() doesn't stop anything itself; the *last* release arms a short
  idle-grace timer, so back-to-back research tasks don't thrash start/stop.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

import httpx

from .config import settings

logger = logging.getLogger("librarian_searxng.container")


def _compose(*args: str) -> list[str]:
    return ["docker", "compose", "-p", settings.compose_project,
            "-f", settings.compose_file, *args]


def _compose_env() -> dict:
    env = dict(os.environ)
    # The compose file mounts ${LIBRARIAN_SEARXNG_SETTINGS}; point it at the
    # configured settings file so SEARXNG_SETTINGS_PATH takes effect.
    env["LIBRARIAN_SEARXNG_SETTINGS"] = os.path.abspath(settings.settings_path)
    return env


class SearxngContainer:
    def __init__(self):
        self._lock = threading.Lock()
        self._refcount = 0
        self._stop_timer: threading.Timer | None = None

    def _healthy(self, timeout: float = 3.0) -> bool:
        try:
            return httpx.get(settings.base_url, timeout=timeout).status_code < 500
        except Exception:
            return False

    def acquire(self, timeout: float | None = None) -> None:
        """One more consumer; make sure SearXNG is healthy before returning.
        Raises RuntimeError if it can't be brought up, so callers fail just
        their own unit of work."""
        with self._lock:
            self._refcount += 1
            if self._stop_timer:
                self._stop_timer.cancel()
                self._stop_timer = None
        try:
            self._ensure_running(timeout if timeout is not None else settings.start_timeout)
        except Exception:
            with self._lock:
                self._refcount = max(0, self._refcount - 1)
            raise

    def release(self) -> None:
        """One consumer done. When none are left, schedule an idle-grace stop."""
        with self._lock:
            self._refcount = max(0, self._refcount - 1)
            if self._refcount == 0 and self._stop_timer is None:
                self._stop_timer = threading.Timer(settings.idle_grace_seconds, self._idle_stop)
                self._stop_timer.daemon = True
                self._stop_timer.start()

    def _idle_stop(self) -> None:
        with self._lock:
            if self._refcount != 0:
                return  # a new task grabbed it during the grace period
            self._stop_timer = None
        logger.info("no active research tasks — stopping SearXNG container")
        try:
            subprocess.run(_compose("stop"), capture_output=True, text=True,
                           timeout=30, check=True, env=_compose_env())
        except Exception as e:
            # Non-fatal: worst case the container idles until the next
            # acquire() or a manual stop.
            logger.warning("failed to stop SearXNG container (non-fatal): %s", e)

    def _ensure_running(self, timeout: float) -> None:
        if self._healthy():
            return
        logger.info("SearXNG not responding — starting container via docker compose")
        try:
            result = subprocess.run(_compose("up", "-d"), capture_output=True, text=True,
                                    timeout=60, env=_compose_env())
        except FileNotFoundError as e:
            raise RuntimeError("docker CLI not found on PATH — install/start Docker Desktop") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("`docker compose up -d` timed out") from e
        if result.returncode != 0:
            raise RuntimeError(
                f"docker compose up failed (exit {result.returncode}): {result.stderr.strip()}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._healthy():
                logger.info("SearXNG online")
                return
            time.sleep(1.0)
        raise RuntimeError(
            f"SearXNG did not become healthy within {timeout:.0f}s of `docker compose up -d`")


# Process-wide singleton: the refcount only makes sense shared across every
# research task in this process.
container = SearxngContainer()
