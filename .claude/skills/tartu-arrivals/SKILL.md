---
name: tartu-arrivals
description: Use when working on the Tartu Airport (EETU) arrivals project — fetching flight arrival data from FlightAware AeroAPI, running the notebook, or extending the helper modules in src/.
---

# Tartu Arrivals Project

Proof-of-concept project that queries FlightAware's AeroAPI for flights
at Tartu Airport (ICAO `EETU`). Started as a Jupyter notebook; has since
grown two standalone HTML report generators. API and data-transform logic
live in importable helper modules under `src/`.

**Keep this file current.** When a change to this project is significant
enough to matter to a future session — a new script/module, a changed file
layout, a newly discovered API constraint or library gotcha, a new naming
or highlighting convention — update this SKILL.md in the same commit (or
the very next one). This file exists to stop those things from being
rediscovered live against the API each time; a stale skill file defeats
that purpose. Prefer editing the relevant section in place over appending;
if a "Gotcha" or layout entry is no longer true, remove or correct it
rather than leaving it to mislead.

## Project layout

- `notebooks/tartu_arrivals.ipynb` — original entry point. Sets parameters
  (airport code, explicit start/end times), calls the helpers, displays the
  arrivals table, and saves a snapshot.
- `generate_report.py` — standalone script. Writes `output/EETU_report.html`
  (fixed filename, overwritten every run — not timestamped) with actual
  arrivals/departures for the past 24h and scheduled arrivals/departures for
  the next 24h, plus a folium map and Sweden-route highlighting (see Gotchas).
- `generate_history_report.py` — standalone script. Writes
  `output/EETU_history_report.html` with actual arrivals/departures over the
  last `MAX_HISTORY_DAYS` (10, see Gotchas), aircraft type + tail number per
  flight, a folium map, and Sweden-route highlighting.
- `src/flightaware_client.py` — thin AeroAPI wrapper: `fetch_arrivals`,
  `fetch_departures`, `fetch_scheduled_arrivals`, `fetch_scheduled_departures`,
  `fetch_airport` (lat/lon + country lookup). `_fetch` transparently follows
  `links.next` pagination — callers always get the full result set for the
  window, never just one page. Reads `FLIGHTAWARE_API_KEY` from `.env` via
  `python-dotenv`.
- `src/arrivals.py` — `to_dataframe`/`to_departures_dataframe` flatten a raw
  API response into a pandas DataFrame; `save_snapshot(...)` writes the raw
  JSON and a CSV into `output/` with a UTC timestamp (used by the notebook).
- `src/mapping.py` — shared by both report scripts: the on-disk airport
  coordinate cache (`output/airport_coords_cache.json`), `is_sweden()`, and
  the folium map builder (`build_map`/`add_route_group`). Both scripts must
  keep using this one cache file rather than each maintaining their own —
  see the quota gotcha below.
- `src/store.py` — local Parquet cache of every flight record either report
  script has ever fetched (`output/flights.parquet` + covered-interval state
  in `output/flight_fetch_state.json`). `sync_source(source, fetch_fn,
  desired_start, desired_end, now)` is the entry point both scripts call
  instead of hitting `fetch_arrivals`/etc. directly — see the caching gotcha
  below before changing how either script fetches data.
- `output/` — generated reports/data (git-ignored: `*.json`, `*.csv`,
  `*.html`, `*.parquet`). Report scripts use a **fixed filename, overwritten
  in place** every run — do not add timestamps to new report filenames,
  that clutters the directory (a mistake made and corrected earlier in this
  project).
- `.env` — local secrets, git-ignored. Copy `.env.example` and fill in
  `FLIGHTAWARE_API_KEY`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m ipykernel install --user --name arrivals_to_tartu --display-name "arrivals_to_tartu"
cp .env.example .env  # then edit .env with a real AeroAPI key
```

Launch with `jupyter notebook notebooks/tartu_arrivals.ipynb` (with the
venv active) and select the `arrivals_to_tartu` kernel.

Run the report scripts directly (with the venv active):
`python generate_report.py` / `python generate_history_report.py`.

## Gotchas (hit these live — read before touching this project again)

- **AeroAPI's request quota is small and easy to exhaust.** A single dev
  session of iterating on the map feature burned through it and started
  returning `429 RATE_LIMIT_ERROR`. Consequences for how you write code here:
  - Airport coordinate/country lookups (`fetch_airport`) are cached forever
    in `output/airport_coords_cache.json` via `src/mapping.py` — coordinates
    never change, so never add a second cache or bypass this one.
    Failed lookups are *not* cached (so a future run retries them), but
    successful ones are permanent.
  - Flight data itself (arrivals/departures/scheduled_*) is cached in
    `output/flights.parquet` via `src/store.py`'s `sync_source()` — both
    report scripts call this instead of `fetch_arrivals` etc. directly, so a
    rerun only asks AeroAPI for the slice of the window not already covered.
    State is a **covered interval per source** (`{"start": ..., "end":
    ...}`), shared across both scripts, and gap-filling is two-sided: if a
    script's desired window extends earlier *or* later than what's covered,
    it issues a backward and/or forward fetch to close the gap — this is
    what lets `generate_report.py` (24h window) and
    `generate_history_report.py` (10-day window) top up the *same* cache
    correctly regardless of which one ran first or how large its window is.
    Don't "simplify" this to a single `last_fetched_at` timestamp — that
    breaks the moment two scripts with different window sizes share the
    cache (traced through carefully when building it; a naive
    forward-only-timestamp version silently skipped backfilling the older
    9 days when the 24h script had already advanced the timestamp).
    Known limitation: this still issues one API call per source per run for
    the "new since last time" sliver even when that sliver is empty — it
    does not special-case "nothing could possibly be new," so don't expect
    truly zero calls on a rerun seconds later. Also, `scheduled_*` sources
    are forward-looking and genuinely mutable (delays, schedule changes) —
    once a flight's slot has been fetched, this cache does **not** re-poll
    it for updates, only extends into newly-reachable future territory. If
    "did this scheduled flight's time change" ever matters, that needs a
    deliberate re-fetch strategy, not just wider window coverage.
  - Don't add test/debug scripts that call the live API repeatedly. Prefer
    reproducing bugs offline with synthetic data + `unittest.mock.patch` on
    `fetch_airport`/`fetch_arrivals`/`fetch_departures` (this is how the NaN
    bug below was actually root-caused, after live repro attempts made the
    quota problem worse).
  - If you must call live, do it once, read the result, stop — don't loop
    "try again" calls hoping quota comes back; report the 429 to the user
    instead. Quota did visibly reset within the same session at least once.
- **`flights/arrivals` and `flights/departures` (live-tracked/"actual" data)
  reject any `start` more than `MAX_HISTORY_DAYS` (10) in the past** —
  `400 INVALID_ARGUMENT, "time is too far in the past"`. This is a hard
  server-side cap; there is no pagination trick around it. A "2 weeks of
  history" request is only satisfiable up to 10 days — say so in the report
  output, don't silently truncate.
- **`flights/scheduled_arrivals`/`flights/scheduled_departures` reject any
  `end` more than ~2 days in the future** — the mirror-image constraint,
  documented on `fetch_scheduled_arrivals` in `flightaware_client.py`.
- **`flights/*` endpoints paginate at ~15 records/page.** `_fetch()` in
  `flightaware_client.py` already follows `links.next` until exhausted, so
  every `fetch_*` call returns the complete result set for the window — do
  not re-add manual single-page handling or a `max_pages` param.
- **pandas silently turns `None` into `float('nan')`.** An object-dtype
  DataFrame column with a `None` in it renders that cell as `None` when you
  build the DataFrame, but `.iterrows()` upgrades it to `float('nan')` on
  read. `nan` is *truthy* in Python, so `code or fallback` does **not** fall
  back — it returns the float `nan` itself. This actually broke the app: a
  `nan` leaked into a dict used as a folium marker-grouping key and as a
  JSON cache key, and `json.dumps(..., sort_keys=True)` crashed with
  `TypeError: '<' not supported between instances of 'float' and 'str'`
  once a real (string) key and the stray `nan` key coexisted. Fix pattern:
  never test a code/ident/label with a bare `if not x` or `x or fallback`
  once it has passed through a DataFrame — use `isinstance(x, str)` or
  `pd.isna(x)` explicitly first. `src/mapping.is_sweden()` and
  `src/mapping.get_coords()` show the guarded pattern.
- **GA/ad-hoc flights without a matched destination/origin airport** report
  their last known position as a synthetic code string like
  `"L 66.12936 12.70054"` (lat, lon) instead of a real ICAO code — handle via
  `src/mapping.parse_position_code()`, don't try to `fetch_airport()` it.
- **`registration` (tail number) ≠ `ident` (callsign/flight number).** For
  GA flights `ident` already *is* the tail number (e.g. `"OY-BKM"`); for
  airline flights (e.g. `FIN1047`) `registration` is the separate actual
  aircraft (e.g. `"OH-ATJ"`). Only show `registration` when it differs from
  `ident`, or every GA row gets a redundant duplicate column.
- **`aircraft_type` is already in the arrivals/departures payload** — no
  extra API call needed for equipment info, just read the field (can be
  `null`).
- **Sweden-route highlighting convention**: `src/mapping.is_sweden(code)`
  checks the ICAO prefix `"ES"` (Sweden — distinct from `"EE"` Estonia,
  `"EF"` Finland, `"EN"` Norway, `"EK"` Denmark). Highlighted rows get CSS
  class `sweden` + a `🇸🇪` label prefix; map markers/lines render `orange`
  instead of the usual blue(arrivals)/green(departures). If more countries
  ever need highlighting, generalize this, don't bolt on a second parallel
  mechanism.

## Extending

- To change the airport, edit `AIRPORT_ICAO` near the top of whichever
  script/notebook cell you're working in (AeroAPI expects ICAO codes, e.g.
  `EETU`).
- New data transforms belong in `src/arrivals.py`; new map/coordinate-caching
  logic belongs in `src/mapping.py`; flight-data caching belongs in
  `src/store.py`; API-specific logic (auth, endpoint URLs, pagination, error
  handling) belongs in `src/flightaware_client.py`. Don't inline any of this
  directly in a report script or the notebook.
- Both report scripts should keep sharing `src/mapping.py`'s coordinate
  cache, `src/store.py`'s flight-data cache, and the Sweden-highlight
  helpers rather than diverging — that's the point of having extracted them.
  A new report script should do the same: call `sync_source()` per endpoint
  rather than `fetch_arrivals`/etc. directly.

## Known future directions (not yet implemented — confirm with user before building)

- Scheduling/automating repeated runs (cron, `/schedule`, etc.)
- Publishing a report to a GitHub Pages site
- Pushing this local git repo to a remote

## Constraints

- Never commit `.env` or real API keys.
- Don't push to a remote unless explicitly told to.
- AeroAPI key is required for any live run; without it, `fetch_arrivals`
  raises `FlightAwareError` with a clear message pointing at `.env.example`.
