"""Local Parquet cache of every flight record fetched from AeroAPI, so
report reruns only ask the API for the time slice not already covered by a
previous sync instead of re-querying a whole window every time.

Each of the four endpoints (arrivals, departures, scheduled_arrivals,
scheduled_departures) gets its own tracked "covered" interval, shared across
*all* callers (both report scripts write to the same store/state files), so
whichever script ran most recently narrows what the next one needs to fetch
-- including across scripts with different window sizes; see sync_source.

Rows store the full nested flight object verbatim as JSON text (`raw`)
alongside a few extracted scalar columns used for dedup and window
filtering, so callers get back exactly {source: [flight, ...]} -- the same
shape AeroAPI itself returns -- and existing parsing code (to_dataframe,
_extract_row, etc.) needs no changes.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
STORE_PATH = OUTPUT_DIR / "flights.parquet"
STATE_PATH = OUTPUT_DIR / "flight_fetch_state.json"

# Every AeroAPI flight object carries the full round-trip lifecycle
# regardless of which endpoint returned it, so all of these fields exist
# (possibly null) on arrivals, departures, scheduled_arrivals, and
# scheduled_departures records alike.
TIME_FIELDS = (
    "scheduled_in", "estimated_in", "actual_in",
    "scheduled_on", "actual_on",
    "scheduled_out", "estimated_out", "actual_out",
    "scheduled_off", "actual_off",
)

# Which of a record's own timestamps determine whether it falls inside a
# requested display window, in priority order (first non-null wins). Scoped
# per source so an "arrivals" record is never window-filtered by its
# unrelated departure time from a prior leg, or vice versa.
SOURCE_TIME_FIELDS = {
    "arrivals": ("actual_in", "actual_on", "scheduled_in", "scheduled_on"),
    "departures": ("actual_out", "actual_off", "scheduled_out", "scheduled_off"),
    "scheduled_arrivals": ("scheduled_in", "scheduled_on"),
    "scheduled_departures": ("scheduled_out", "scheduled_off"),
}


def _load_fetch_state() -> dict[str, dict[str, str]]:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text())


def _save_fetch_state(state: dict[str, dict[str, str]]) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def load_store() -> pd.DataFrame:
    if not STORE_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(STORE_PATH)


def _save_store(df: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    df.to_parquet(STORE_PATH, index=False)


def _flight_to_row(flight: dict[str, Any], source: str, fetched_at: str) -> dict[str, Any]:
    row = {
        "fa_flight_id": flight.get("fa_flight_id"),
        "ident": flight.get("ident"),
        "_source": source,
        "_fetched_at": fetched_at,
        "raw": json.dumps(flight),
    }
    for field in TIME_FIELDS:
        row[field] = flight.get(field)
    return row


def _upsert(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([existing, new], ignore_index=True)
    has_key = combined["fa_flight_id"].notna()
    # Same fa_flight_id + source seen twice (overlapping sync windows) ->
    # keep the most recently fetched copy (freshest status/times).
    keyed = (
        combined[has_key]
        .sort_values("_fetched_at")
        .drop_duplicates(subset=["fa_flight_id", "_source"], keep="last")
    )
    unkeyed = combined[~has_key]  # never dedup on a missing key
    return pd.concat([keyed, unkeyed], ignore_index=True).reset_index(drop=True)


def sync_source(
    source: str,
    fetch_fn: Callable[[dt.datetime, dt.datetime], dict[str, Any]],
    desired_start: dt.datetime,
    desired_end: dt.datetime,
    now: dt.datetime,
    refetch_buffer: dt.timedelta = dt.timedelta(0),
) -> dict[str, list[dict[str, Any]]]:
    """Fetch only the parts of [desired_start, desired_end] not already
    covered by a previous sync of `source` (by any caller), merge them into
    the on-disk store, and return {source: [flight, ...]} for every
    cached+fresh record whose relevant timestamp falls in that window.

    `desired_start`/`desired_end` must already respect whatever window
    limits AeroAPI enforces for this endpoint (e.g. MAX_HISTORY_DAYS) --
    this function only knows how to avoid re-fetching what it already has,
    not what the API will accept.

    `refetch_buffer`: also re-query this trailing slice of already-"covered"
    time on every sync. Needed for live-tracked sources (arrivals/departures):
    AeroAPI can publish or finalize a flight's record some time after the
    event itself -- e.g. a GA flight only gets matched to an airport once its
    ADS-B track resolves -- so a window fetched exactly at the time of the
    event can miss records that show up moments later, and since `covered`
    never shrinks, that gap would otherwise never be looked at again. Leave
    at the default (0) for scheduled_* sources, whose "doesn't re-poll
    already-seen slots" behavior is a separate, intentional limitation
    (schedules can change, but re-detecting that isn't what this buffer is
    for -- see the skill file).
    """
    state = _load_fetch_state()
    covered = state.get(source)

    fetch_ranges: list[tuple[dt.datetime, dt.datetime]] = []
    if covered is None:
        fetch_ranges.append((desired_start, desired_end))
        new_start, new_end = desired_start, desired_end
    else:
        cov_start = dt.datetime.fromisoformat(covered["start"])
        cov_end = dt.datetime.fromisoformat(covered["end"])
        refetch_from = max(cov_start, cov_end - refetch_buffer)
        if desired_start < cov_start:
            fetch_ranges.append((desired_start, cov_start))
        if desired_end > refetch_from:
            fetch_ranges.append((refetch_from, desired_end))
        new_start, new_end = min(cov_start, desired_start), max(cov_end, desired_end)

    store = load_store()
    fetched_count = 0
    for range_start, range_end in fetch_ranges:
        if range_start >= range_end:
            continue
        raw = fetch_fn(range_start, range_end)
        new_flights = raw.get(source, [])
        if new_flights:
            fetched_at = now.isoformat()
            new_rows = pd.DataFrame(
                [_flight_to_row(f, source, fetched_at) for f in new_flights]
            )
            store = _upsert(store, new_rows)
            fetched_count += len(new_flights)

    if fetch_ranges:
        if fetched_count:
            _save_store(store)
        state[source] = {"start": new_start.isoformat(), "end": new_end.isoformat()}
        _save_fetch_state(state)
        span = ", ".join(
            f"{s.isoformat()} to {e.isoformat()}" for s, e in fetch_ranges if s < e
        )
        print(f"{source}: fetched {fetched_count} record(s) from AeroAPI ({span or 'nothing new'})")
    else:
        print(f"{source}: fully covered by local cache, no AeroAPI call needed")

    if store.empty or "_source" not in store.columns:
        return {source: []}

    subset = store[store["_source"] == source]
    if subset.empty:
        return {source: []}

    effective_time = pd.Series(pd.NaT, index=subset.index, dtype="datetime64[ns, UTC]")
    for field in SOURCE_TIME_FIELDS[source]:
        parsed = pd.to_datetime(subset[field], errors="coerce", utc=True)
        effective_time = effective_time.where(effective_time.notna(), parsed)

    window_start = pd.Timestamp(desired_start)
    window_end = pd.Timestamp(desired_end)
    in_window = (effective_time >= window_start) & (effective_time <= window_end)
    matched = subset[in_window]

    return {source: [json.loads(r) for r in matched["raw"]]}
