# librarian-searxng

A **SearXNG search backend** for [librarian](https://github.com/kevin-j-smith/librarian)'s
research loop and search eval. librarian ships no search backend. Installing
this package into librarian's environment lets `start_research` /
`librarian research start` search the web through a SearXNG metasearch
instance that runs in Docker on your machine.

```
librarian research loop ──► SearxngBackend ──► SearXNG (Docker, 127.0.0.1:8080) ──► upstream engines
                                  │
                   paged · paced · cached · health-aware
```

## Before you enable it: upstream engines and their terms

SearXNG answers queries by querying other search engines on your behalf.
The bundled `settings.yml` enables engines that include Google (via
`google cse`), Bing, Yandex, Yep, and DuckDuckGo, plus scholarly indexes
(arXiv, Crossref, OpenAlex, PubMed, Semantic Scholar). **Many commercial
search engines' terms of service prohibit automated querying.** Which
engines you enable, and whether your use complies with their terms, is your
decision and your responsibility. Edit `settings.yml` (or point
`SEARXNG_SETTINGS_PATH` at your own) to match your policy. The scholarly
indexes alone are a reasonable conservative setup.

To customize engines, copy the bundled `settings.yml`, edit the copy, and set
`SEARXNG_SETTINGS_PATH` to it. If you enable OpenAlex, add your own contact
address as its `mailto` in that copy (OpenAlex's "polite pool"). The bundled
default deliberately leaves it unset.

## Install

Install **into the same environment as librarian**:

```bash
# from PyPI
uv pip install librarian-searxng        # or: pip install librarian-searxng

# from GitHub
uv pip install git+https://github.com/kevin-j-smith/librarian-searxng

# or from a local checkout, next to your librarian checkout
cd librarian
uv pip install -e ../librarian-searxng
```

librarian itself is not a declared dependency, and pip will not install it
for you. librarian is not published on PyPI, and the PyPI project named
`librarian` is an unrelated package. Install librarian first, from its
repository, then add this plugin to that environment.

> With a `uv`-managed librarian checkout, a plain `uv sync` removes packages
> that aren't in librarian's lockfile, and this one isn't (on purpose). Run
> the install command again after `uv sync`, or use `uv sync --inexact`.

For a tool install: `uv tool install git+https://github.com/kevin-j-smith/librarian --with librarian-searxng`.

Then, in librarian's `.env`:

```ini
RESEARCH_ENABLED=true
# RESEARCH_SEARCH_BACKEND=searxng   # only needed if several backends are installed
```

Docker (Docker Desktop on Windows and macOS) must be installed. You don't
start the container yourself. The backend runs
`docker compose -p librarian -f <bundled compose file> up -d` when a research
task starts, and stops the container after `SEARXNG_IDLE_GRACE_SECONDS` of no
research. It listens on `127.0.0.1:8080` only.

Check it:

```bash
librarian eval engines                 # which upstream engines refuse, and which are silently empty
librarian eval search "surface code error correction thresholds"
```

## Configuration

All settings are read from librarian's `.env` (the names predate this
package, so existing setups keep working).

| Variable | Default | Description |
|---|---|---|
| `SEARXNG_BASE_URL` | `http://127.0.0.1:8080` | Where SearXNG answers. Point it at an existing instance to skip Docker entirely (the container is only started if nothing answers here). |
| `SEARXNG_SETTINGS_PATH` | bundled `settings.yml` | Engine configuration mounted into the container. Its hash also namespaces librarian's response cache. |
| `SEARXNG_COMPOSE_FILE` | bundled compose file | Compose file used to start and stop the container |
| `SEARXNG_COMPOSE_PROJECT` | `librarian` | Compose project name. It matches containers created before this was a plugin. |
| `SEARXNG_START_TIMEOUT` | `60` | Seconds to wait for SearXNG to become healthy |
| `SEARXNG_IDLE_GRACE_SECONDS` | `120` | Keep the container up this long after the last task |
| `SEARXNG_MIN_INTERVAL_SECONDS` | `4.0` | Minimum gap between outgoing queries per category. `0` disables pacing. |
| `SEARXNG_JITTER_SECONDS` | `3.0` | Random extra delay, from 0 to this many seconds, so queries aren't sent on a fixed beat |
| `SEARXNG_MAX_PAGES` | `4` | Result pages per search. Each page queries every engine again. |

Response caching is librarian's (`SEARCH_CACHE_*` in librarian's
configuration docs). This backend uses it, so a cache hit skips both the
request and the pacing delay.

## How it maps to librarian's interface

| librarian category | SearXNG category | Notes |
|---|---|---|
| `web` | default (`general`) | Paged until the requested count is met |
| `video` | `videos` | One page |
| `science` | `science` | Paged through the full page budget |

Results come back unfiltered, in SearXNG's order. librarian applies the
same topical gate, media filter, dedup, ranking, and caps to every backend.
Engines SearXNG reports as refusing come back as `degraded`, so librarian
can log and record them. See [DESIGN.md](https://github.com/kevin-j-smith/librarian-searxng/blob/main/DESIGN.md) for the measurements
behind pacing, paging, and the engine set.

## Development

```bash
cd librarian && uv pip install --no-deps -e ../librarian-searxng
cd ../librarian-searxng && uv run --project ../librarian --no-sync python -m pytest tests
```

## License

Apache License 2.0; see [LICENSE](https://github.com/kevin-j-smith/librarian-searxng/blob/main/LICENSE) and [NOTICE](https://github.com/kevin-j-smith/librarian-searxng/blob/main/NOTICE). This
package is licensed independently of librarian, which is source-available
under its own terms. This package contains no SearXNG code. It talks to
SearXNG (AGPL-3.0) over HTTP and starts the upstream `searxng/searxng`
image.
