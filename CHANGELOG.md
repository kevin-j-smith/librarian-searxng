# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0]

### Added

- **Engines that keep refusing are skipped for a while.** An engine that
  refuses a search (at once for a rate limit, CAPTCHA, or suspension; after
  two in a row for a timeout or error) is left out of later pages and
  searches for `SEARXNG_ENGINE_COOLDOWN_SECONDS` (600), doubling while it
  keeps refusing, then asked again. Skipped engines are still reported as
  `degraded`, with the reason and the time left, and a category's last
  enabled engines are never all skipped. Set it to `0` to ask every engine
  every time. Cached responses are unaffected, and replay as before.

## [0.1.0]

First release on PyPI.

- `SearxngBackend`, registered under the `librarian.search_backends` entry
  point as `searxng`, answering the `web`, `video`, and `science` categories.
- Bundled Docker Compose file and SearXNG `settings.yml`. The container is
  started on demand and stopped after `SEARXNG_IDLE_GRACE_SECONDS` idle.
- Per-category pacing with jitter, result paging, librarian response-cache
  integration, and reporting of refusing upstream engines as `degraded`.

[Unreleased]: https://github.com/kevin-j-smith/librarian-searxng/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kevin-j-smith/librarian-searxng/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kevin-j-smith/librarian-searxng/releases/tag/v0.1.0
