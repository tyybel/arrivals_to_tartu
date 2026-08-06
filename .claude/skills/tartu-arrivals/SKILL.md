---
name: tartu-arrivals
description: Use when working on the Tartu Airport (EETU) arrivals project — fetching flight arrival data from FlightAware AeroAPI, running the notebook, or extending the helper modules in src/.
---

# Tartu Arrivals Project

Proof-of-concept project that queries FlightAware's AeroAPI for flights
arriving at Tartu Airport (ICAO `EETU`) within a configurable lookahead
window (default 24 hours). The primary interface is a Jupyter notebook;
API and data-transform logic live in importable helper modules.

## Project layout

- `notebooks/tartu_arrivals.ipynb` — main entry point. Sets parameters
  (airport code, lookahead hours), calls the helpers, displays the
  arrivals table, and saves a snapshot.
- `src/flightaware_client.py` — `fetch_arrivals(airport_icao, hours_ahead)`
  wraps the AeroAPI `GET /airports/{id}/flights/arrivals` endpoint. Reads
  `FLIGHTAWARE_API_KEY` from `.env` via `python-dotenv`.
- `src/arrivals.py` — `to_dataframe(raw_response)` flattens the API
  response into a pandas DataFrame; `save_snapshot(...)` writes the raw
  JSON and a CSV into `output/` with a UTC timestamp.
- `output/` — generated snapshots (git-ignored).
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

## Extending

- To change the airport, edit `AIRPORT_ICAO` in the notebook's parameters
  cell (AeroAPI expects ICAO codes, e.g. `EETU`).
- To change the lookahead window (e.g. 24h → 8h), edit `HOURS_AHEAD` in
  the same cell.
- New data transforms or output formats belong in `src/arrivals.py`, not
  inline in the notebook, so they stay reusable (e.g. a future script
  that publishes a table to GitHub Pages).
- API-specific logic (auth, endpoint URLs, error handling) belongs in
  `src/flightaware_client.py`.

## Known future directions (not yet implemented — confirm with user before building)

- Scheduling/automating repeated runs (cron, `/schedule`, etc.)
- Publishing the arrivals table to a GitHub Pages site
- Pushing this local git repo to a remote

## Constraints

- Never commit `.env` or real API keys.
- Don't push to a remote unless explicitly told to.
- AeroAPI key is required for any live run; without it, `fetch_arrivals`
  raises `FlightAwareError` with a clear message pointing at `.env.example`.
