"""Settings for the SearXNG backend, read from the environment.

librarian loads `.env` on import (librarian/config.py), so the same `.env`
that configures librarian configures this plugin. The names are the SEARXNG_*
settings librarian used before the backend split, unchanged, so an existing
`.env` keeps working.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib.resources import files

import librarian.config  # noqa: F401  (loads .env before anything reads os.environ)

_DATA = files("librarian_searxng") / "data"


def _float(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


@dataclass
class SearxngSettings:
    base_url: str = field(
        default_factory=lambda: os.environ.get("SEARXNG_BASE_URL", "http://127.0.0.1:8080"))
    # Compose file and project. The project defaults to "librarian" so the
    # container and named volume created when this code lived inside
    # librarian (compose project = its folder name) are reused.
    compose_file: str = field(
        default_factory=lambda: os.environ.get(
            "SEARXNG_COMPOSE_FILE", str(_DATA / "docker-compose.searxng.yml")))
    compose_project: str = field(
        default_factory=lambda: os.environ.get("SEARXNG_COMPOSE_PROJECT", "librarian"))
    # The engine configuration mounted into the container. Its hash also
    # namespaces the response cache, so editing it invalidates cached
    # results instead of replaying ones gathered under a different engine set.
    settings_path: str = field(
        default_factory=lambda: os.environ.get(
            "SEARXNG_SETTINGS_PATH", str(_DATA / "settings.yml")))
    start_timeout: float = field(default_factory=lambda: _float("SEARXNG_START_TIMEOUT", "60"))
    idle_grace_seconds: float = field(
        default_factory=lambda: _float("SEARXNG_IDLE_GRACE_SECONDS", "120"))
    # Pacing. SearXNG is on localhost and needs no protecting, but every query
    # fans out to every enabled upstream engine, and those rate-limit by
    # address. Volume is what gets detected, not request shape, so pacing is
    # the lever. The jitter keeps the cadence from being a metronome, itself a
    # bot signal. 0 disables pacing (the tests do).
    min_interval_seconds: float = field(
        default_factory=lambda: _float("SEARXNG_MIN_INTERVAL_SECONDS", "4.0"))
    jitter_seconds: float = field(default_factory=lambda: _float("SEARXNG_JITTER_SECONDS", "3.0"))
    # Pages per search. Every page is another full fan-out to every engine,
    # the largest single amplifier of rate-limit cost.
    max_pages: int = field(
        default_factory=lambda: int(os.environ.get("SEARXNG_MAX_PAGES", "4")))


settings = SearxngSettings()
