"""Engine-health fallback: stop querying an engine that keeps refusing, for a
while (librarian roadmap 6.4).

A refusing engine costs on every request it's included in. The SearXNG
request waits for it (up to the engine's timeout), and a rate-limited
engine asked again stays rate-limited longer: one CAPTCHA suspends an
engine for an hour. A search walks up to four pages, each a full fan-out,
so an engine that refused page 1 was being asked three more times in the
same search. The research loop runs several searches an iteration.

So every real search reports its refusals here, and later requests leave a
cooling engine out with SearXNG's `disabled_engines` parameter (checked
live: `disabled_engines=yep__general` dropped yep's 20 results while the
other four engines still answered). The rules:

- A rate-limit refusal ("too many requests", CAPTCHA, access denied, or
  SearXNG's own "Suspended") cools an engine at once. A transient one
  (timeout, network or HTTP error, a parser failure) needs two in a row, so
  one slow answer costs nothing.
- The cooldown is SEARXNG_ENGINE_COOLDOWN_SECONDS, doubled for each
  refusal in a row after that, up to 16 times; 0 turns the fallback off. When
  it ends the engine is asked again, and one answer clears its record.
- Never every engine: a category's cooling engines are skipped only while
  at least one of its enabled engines (SearXNG's /config) is not cooling,
  so a network outage that trips them all can't empty every search after.
- Skipping is never silent: a skipped engine is reported as `degraded`, with
  why and until when, just as a refusing one is.

State is per process and per SearXNG category, since the categories query
different engines. A response served from librarian's cache says nothing
about an engine now, so it is neither recorded nor affected: the cache key
is the request without the skip list, so replayed eval fixtures still match.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .config import settings

# Refusals that mean "you are asking too often": asking again makes it worse.
_RATE_LIMITED = ("too many requests", "captcha", "access denied", "suspended")
_TRANSIENT_STRIKES = 2
_MAX_BACKOFF = 16


@dataclass
class _Record:
    strikes: int = 0              # refusals in a row
    until: float = 0.0            # monotonic time the cooldown ends
    reason: str = ""


class EngineCooldowns:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], _Record] = {}

    def cooling(self, category: str) -> dict[str, str]:
        """engine -> why it is being skipped, for engines cooling now."""
        if settings.engine_cooldown_seconds <= 0:
            return {}
        now = self._clock()
        with self._lock:
            return {engine: f"skipped for {rec.until - now:.0f}s more after refusing: {rec.reason}"
                    for (cat, engine), rec in self._records.items()
                    if cat == category and rec.until > now}

    def note(self, category: str, refused: list[tuple[str, str]], asked_skipping: set[str]) -> None:
        """Record one real search: the engines that refused, and (by
        omission) that every other tracked engine it asked answered."""
        base = settings.engine_cooldown_seconds
        if base <= 0:
            return
        now = self._clock()
        refusing = {engine: reason for engine, reason in refused}
        with self._lock:
            for engine, reason in refusing.items():
                rec = self._records.setdefault((category, engine), _Record())
                rec.strikes += 1
                rec.reason = reason
                rate_limited = any(m in reason.lower() for m in _RATE_LIMITED)
                if rate_limited or rec.strikes >= _TRANSIENT_STRIKES:
                    over = rec.strikes - (1 if rate_limited else _TRANSIENT_STRIKES)
                    rec.until = now + base * min(2 ** over, _MAX_BACKOFF)
            for (cat, engine) in list(self._records):
                if cat == category and engine not in refusing and engine not in asked_skipping:
                    del self._records[(cat, engine)]     # asked, and it answered

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


cooldowns = EngineCooldowns()
