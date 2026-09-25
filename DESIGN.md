# librarian-searxng: design notes

These notes moved here from librarian's design notes (`docs/design-notes.md`) when the SearXNG
integration became this plugin (September 2026). They record what was
measured, and why the code is shaped the way it is. Paths such as
`docker/searxng/settings.yml`, `providers._throttled_get` and
`evals/searches/RESULTS-*.md` refer to where things lived in librarian at the
time. They are now `src/librarian_searxng/data/settings.yml`,
`client.throttled_get`, and librarian's `evals/searches/` respectively.

## Paging

`paged_search` walks up to `SEARXNG_MAX_PAGES` (4) until the requested count
is met, because the code previously fetched only page 1: 9 of the 10
documents WebSearch found for "surface code error correction thresholds"
were in SearXNG's own index on pages 2 and 3, so one page was reporting an
index gap that did not exist. Every page is another full fan-out to every
upstream engine, which is why depth is a setting and why every request is
paced.

## The engine set is configuration, it fails silently, and it is perishable

(`docker/searxng/settings.yml`, `providers.engine_health`,
`providers.silent_engines`). Upstream SearXNG's defaults left three of four
general web engines failing against this instance - brave "too many
requests", duckduckgo CAPTCHA, startpage "Suspended: CAPTCHA" - so every
general result came from `google cse` alone, and a meta search aggregating
one engine is not a meta search. The settings file enables `duckduckgo web`
(upstream's HTML-endpoint implementation of DDG, which answers where the
JSON one CAPTCHAs), disables the engines that consistently refuse, and
turns on `crossref` + `openalex` in science (the two largest scholarly
metadata indexes, both off by default). **Do not read the engine list here
as current** - a re-probe on 2026-08-28, one day after that set was chosen,
found a third of it had flipped: `google cse` and brave now 429 on the
first query of a cold container, while `google` (disabled the day before as
"CAPTCHA") answered 4/4 cleanly and is back in. The scraper and CSE engines
trade places. Every entry carries a comment recording what it actually did
*and when*; re-check with `librarian eval engines`, or
`curl 'http://127.0.0.1:8080/search?q=test&format=json&engines=<name>'` and
read `unresponsive_engines`. **This is why `engine_health` exists**: a
refusing engine is invisible in the results - it just narrows the pool, and
a narrow pool is indistinguishable from an index that lacks the material.
Two separate investigations chased an apparent index gap that was really
engines returning CAPTCHAs. The orchestrator logs it per iteration and
`run_searxng` records it on the run, because the eval is itself a likely
cause: a five-scenario head-to-head issues enough queries in a few minutes
to rate-limit an engine, and a comparison taken in that state understates
SearXNG with nothing in the numbers saying so. But `engine_health` only
catches engines that *announce* failure, and that is the safe half.
`silent_engines` catches the other one: enabled, 200 OK, zero results,
**absent from `unresponsive_engines`** - mojeek did exactly this for a day,
having been enabled as part of the fix for a *different* narrow-pool bug.
It requires silence across several unrelated probe queries (an engine thin
on one subject is not dead) and excludes infobox/utility engines like
`wikipedia` and `dictzone`, which are enabled in `general` but legitimately
answer nothing most of the time - without that exclusion the real finding
drowns in six false positives. It costs a request per probe, so it is an
eval/manual diagnostic, not a per-iteration check. Note what is
deliberately *not* done here: no CAPTCHA solvers, no residential-proxy
rotation. Engine diversity is the durable answer; evading one engine's bot
protection burns the address for all of them - and it would not have helped
anyway, since the narrowed 3-engine pool still returned ~31 results over
~26 domains per query. The pool was narrowed, never broken, which is the
standing argument against replacing SearXNG with hand-rolled search
compilation: the compilation layer above it (`_paged_search`,
`merge_results`, `document_key`, `is_on_topic`, `rank_by_relevance`) is the
part that already works, and owning the adapters means owning the CAPTCHAs.
See `evals/searches/RESULTS-2026-08-28.md`.
## Outgoing SearXNG queries are paced

(`providers._throttled_get`,
`SEARXNG_MIN_INTERVAL_SECONDS`/`_JITTER_SECONDS`/`_MAX_PAGES`). SearXNG is
on localhost and needs no protecting; the point is that *every* query it
receives fans out to every enabled upstream engine, and those engines
rate-limit by address. Measured 2026-08-28: a few hundred queries in ~45
minutes took `google` from answering 4/4 probes to refusing every one, with
the request fingerprint unchanged throughout - and a full Chrome header set
(`Sec-Ch-Ua`, `Sec-Fetch-*`, the lot) changed brave's 429 by nothing. For
brave, **volume is what gets detected, not the shape of the request**, so
pacing is the lever and header/TLS impersonation is not. The jitter is not
decoration: a query every N seconds on the dot is a metronome, itself a bot
signal, so the delay is drawn from `[interval, interval+jitter]`. Pacing is
keyed **per category** because general/science/videos hit disjoint engine
sets in `settings.yml` - a science page spends arXiv's goodwill, not
google's - and the wait is computed under the lock but slept *outside* it,
so concurrent callers reserve distinct slots instead of serialising on one
holder. Two things it does not fix, both worth knowing before chasing them:
`_MAX_PAGES` deep paging is the largest single amplifier (each page is
another full fan-out, and `SearxngScienceProvider` asks for `_SCIENCE_POOL`
= 400, which it can never reach, so science *always* pays the full page
budget); and `google` is a different failure from brave - on 2026-08-31,
after three quiet days, SearXNG's `google` engine was CAPTCHA'd
(`suspended_time=3600`) on the first query of a fresh container while a
plain httpx GET to google.com from the same host at the same moment
returned 200. That one *is* request-shape-dependent, and it is about
SearXNG's particular google implementation rather than this address being
in the doghouse. Related: `engine_health` is no longer called as a separate
probe by the research loop or the eval. `_paged_search` collects
`unresponsive_engines` from the searches it already runs into a sink the
providers expose as `last_unresponsive` - the probe cost an extra fan-out
per iteration, spending the very budget whose exhaustion it exists to
detect, and described a different query than the one recorded.
`engine_health`/`silent_engines` remain as standalone diagnostics behind
`librarian eval engines`. Measured effect
(`evals/searches/RESULTS-2026-08-31.md`): a full five-scenario eval now
leaves the engine pool **exactly as it found it** - contributing and
refusing sets identical before and after - where the unpaced run had
engines dropping out partway through its own burst. The eval had been
contaminating its own measurement; that is now ruled out by construction
rather than suspected. Cost is ~48s per scenario instead of ~12s, which is
the intended trade.
**And pacing changed the answer to a question already thought settled.**
The 2026-08-28 engine set was chosen from probes taken while this repo's
own unpaced eval had the address rate-limited, so it read rate-limiting as
refusal. Re-probed 08-31 under pacing, 5 queries each: `google cse` and
`yep` - both *disabled* on 08-28 - answer 5/5 at 20 results/query, the two
deepest sources available. The enabled set is now those two plus `yandex`,
`bing` and `duckduckgo web`, all 5/5; `brave` (2/5) and `qwant` (1/5) are
off despite being free, because an intermittent engine injects exactly the
run-to-run variance the pacing exists to remove. Result: 54–62 results over
26–56 domains per query with **zero unresponsive engines across an 8-query
burst**, roughly double any previous configuration. Generalise the lesson,
not the table: *measure an engine when the address is not already in the
doghouse, or you record the doghouse.* Note the rounds 1–4 scoreboards in
`evals/searches/` were all taken on 3 engines and are stale in librarian's
favour. Separately, `google` is **obsolete rather than rate-limited** and
is not worth repairing: it requests `/wml/search` with a Nokia
feature-phone User-Agent, and isolation (same host, same second) shows the
*UA* is the trigger while the endpoint is irrelevant - Nokia UA 429s on
both endpoints, Chrome UA gets 200 on both, and no combination still
returns the WML markup `response()` parses, so a UA swap yields pages the
parser cannot read. `google cse` reaches the same index at 20
results/query instead. One more reason engine flakiness is costly: a single
CAPTCHA suspends an engine for **3600s**, so one failure costs every query
in the following hour.
