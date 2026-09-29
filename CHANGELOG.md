# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0]

First release on PyPI.

- `SearxngBackend`, registered under the `librarian.search_backends` entry
  point as `searxng`, answering the `web`, `video`, and `science` categories.
- Bundled Docker Compose file and SearXNG `settings.yml`. The container is
  started on demand and stopped after `SEARXNG_IDLE_GRACE_SECONDS` idle.
- Per-category pacing with jitter, result paging, librarian response-cache
  integration, and reporting of refusing upstream engines as `degraded`.

[Unreleased]: https://github.com/kevin-j-smith/librarian-searxng/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kevin-j-smith/librarian-searxng/releases/tag/v0.1.0
