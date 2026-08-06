"""Generate an HTML report for Tartu Airport (EETU): past actual arrivals,
past actual departures, upcoming scheduled arrivals, and upcoming scheduled
departures, with an embedded folium map of origin/destination airports.

Usage:
    python generate_report.py

Overwrites output/{AIRPORT_ICAO}_report.html in place on every run (same
path each time, not timestamped) and prints its path.
"""

import datetime as dt
import html
from pathlib import Path

import pandas as pd

from src.arrivals import to_dataframe, to_departures_dataframe
from src.flightaware_client import (
    fetch_arrivals,
    fetch_departures,
    fetch_scheduled_arrivals,
    fetch_scheduled_departures,
)
from src.formatting import fmt_local
from src.mapping import (
    add_route_group,
    build_map,
    get_center_coords,
    is_sweden,
    load_coord_cache,
    save_coord_cache,
)
from src.store import sync_source

AIRPORT_ICAO = "EETU"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# Live-tracked arrivals/departures records can show up in AeroAPI a while
# after the actual event (e.g. GA flights pending ADS-B match) -- re-check
# this trailing slice of already-"covered" time on every run so those don't
# get permanently missed. See sync_source()'s refetch_buffer docstring.
LIVE_DATA_REFETCH_BUFFER = dt.timedelta(days=2)

TIME_COLUMNS = {
    "arrivals": ("scheduled_in", "estimated_in", "actual_in"),
    "departures": ("scheduled_out", "estimated_out", "actual_out"),
}

COLUMN_LABELS = {
    "ident": "Flight",
    "origin": "Origin",
    "destination": "Destination",
    "status": "Status",
    "scheduled_in": "Scheduled",
    "estimated_in": "Estimated",
    "actual_in": "Actual",
    "scheduled_out": "Scheduled",
    "estimated_out": "Estimated",
    "actual_out": "Actual",
}


def _code_or_unknown(value: object) -> str:
    return value if isinstance(value, str) else "Unknown"


def _table_html(df: pd.DataFrame, time_columns: tuple[str, ...], code_col: str) -> str:
    if df.empty:
        return '<p class="empty">No flights in this window.</p>'

    columns = list(df.columns)
    header = "".join(f"<th>{html.escape(COLUMN_LABELS.get(c, c))}</th>" for c in columns)

    body_rows = []
    for _, row in df.iterrows():
        row_cls = ' class="sweden"' if is_sweden(row[code_col]) else ""
        cells = []
        for col in columns:
            value = row[col]
            if col in time_columns:
                text = fmt_local(value)
            elif col == code_col and is_sweden(value):
                text = f"🇸🇪 {value}"
            else:
                text = "" if pd.isna(value) else str(value)
            cells.append(f"<td>{html.escape(text)}</td>")
        body_rows.append(f"<tr{row_cls}>{''.join(cells)}</tr>")

    return f"<table>\n<thead><tr>{header}</tr></thead>\n<tbody>{''.join(body_rows)}</tbody>\n</table>"


def _combine(*frames: pd.DataFrame) -> pd.DataFrame:
    non_empty = [f for f in frames if not f.empty]
    return pd.concat(non_empty, ignore_index=True) if non_empty else frames[0]


def _build_map(
    eetu_coords: tuple[float, float],
    df_arrivals: pd.DataFrame,
    df_scheduled_arrivals: pd.DataFrame,
    df_departures: pd.DataFrame,
    df_scheduled_departures: pd.DataFrame,
    coord_cache: dict,
) -> str:
    m = build_map(eetu_coords, "Tartu (EETU)")

    def detail(scheduled_col: str, actual_col: str):
        def fn(row: pd.Series) -> str:
            ts = row[actual_col] if pd.notna(row.get(actual_col)) else row.get(scheduled_col)
            date_str = fmt_local(ts, "%d %b %H:%M") or "?"
            return f"{html.escape(_code_or_unknown(row['ident']))} — {date_str}"

        return fn

    add_route_group(
        m,
        _combine(df_arrivals, df_scheduled_arrivals),
        code_col="origin",
        label_fn=lambda row: _code_or_unknown(row["origin"]),
        detail_fn=detail("scheduled_in", "actual_in"),
        center_coords=eetu_coords,
        coord_cache=coord_cache,
        color="blue",
        kind="arrival(s)",
    )
    add_route_group(
        m,
        _combine(df_departures, df_scheduled_departures),
        code_col="destination",
        label_fn=lambda row: _code_or_unknown(row["destination"]),
        detail_fn=detail("scheduled_out", "actual_out"),
        center_coords=eetu_coords,
        coord_cache=coord_cache,
        color="green",
        kind="departure(s)",
    )

    return m.get_root().render()


def build_report(now: dt.datetime) -> str:
    past_start, past_end = now - dt.timedelta(hours=24), now
    future_start, future_end = now, now + dt.timedelta(hours=24)

    coord_cache = load_coord_cache()
    eetu_coords = get_center_coords(AIRPORT_ICAO, coord_cache)

    raw_arrivals = sync_source(
        "arrivals",
        lambda s, e: fetch_arrivals(AIRPORT_ICAO, start=s, end=e),
        past_start,
        past_end,
        now,
        refetch_buffer=LIVE_DATA_REFETCH_BUFFER,
    )
    raw_departures = sync_source(
        "departures",
        lambda s, e: fetch_departures(AIRPORT_ICAO, start=s, end=e),
        past_start,
        past_end,
        now,
        refetch_buffer=LIVE_DATA_REFETCH_BUFFER,
    )
    raw_scheduled_arrivals = sync_source(
        "scheduled_arrivals",
        lambda s, e: fetch_scheduled_arrivals(AIRPORT_ICAO, start=s, end=e),
        future_start,
        future_end,
        now,
    )
    raw_scheduled_departures = sync_source(
        "scheduled_departures",
        lambda s, e: fetch_scheduled_departures(AIRPORT_ICAO, start=s, end=e),
        future_start,
        future_end,
        now,
    )

    df_arrivals = to_dataframe(raw_arrivals, key="arrivals")
    df_departures = to_departures_dataframe(raw_departures, key="departures")
    df_scheduled_arrivals = to_dataframe(raw_scheduled_arrivals, key="scheduled_arrivals")
    df_scheduled_departures = to_departures_dataframe(
        raw_scheduled_departures, key="scheduled_departures"
    )

    map_html = _build_map(
        eetu_coords,
        df_arrivals,
        df_scheduled_arrivals,
        df_departures,
        df_scheduled_departures,
        coord_cache,
    )
    save_coord_cache(coord_cache)

    return HTML_TEMPLATE.format(
        airport=AIRPORT_ICAO,
        generated_at=fmt_local(now),
        past_start=fmt_local(past_start),
        past_end=fmt_local(past_end),
        future_start=fmt_local(future_start),
        future_end=fmt_local(future_end),
        arrivals_count=len(df_arrivals),
        departures_count=len(df_departures),
        scheduled_arrivals_count=len(df_scheduled_arrivals),
        scheduled_departures_count=len(df_scheduled_departures),
        map_srcdoc=html.escape(map_html, quote=True),
        arrivals_table=_table_html(df_arrivals, TIME_COLUMNS["arrivals"], "origin"),
        departures_table=_table_html(df_departures, TIME_COLUMNS["departures"], "destination"),
        scheduled_arrivals_table=_table_html(
            df_scheduled_arrivals, TIME_COLUMNS["arrivals"], "origin"
        ),
        scheduled_departures_table=_table_html(
            df_scheduled_departures, TIME_COLUMNS["departures"], "destination"
        ),
    )


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{airport} Flight Report</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 2rem; color: #1a1a1a; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .meta {{ color: #666; margin-bottom: 2rem; }}
  h2 {{ margin-top: 2.5rem; border-bottom: 2px solid #ddd; padding-bottom: 0.3rem; }}
  .window {{ color: #666; font-weight: normal; font-size: 0.9rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 0.8rem; font-size: 0.92rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.7rem; border-bottom: 1px solid #eee; }}
  th {{ background: #f5f5f5; }}
  tr:hover {{ background: #fafafa; }}
  tr.sweden {{ background: #fff3b0; }}
  tr.sweden:hover {{ background: #ffec8a; }}
  .empty {{ color: #888; font-style: italic; }}
  .legend {{ color: #666; font-size: 0.85rem; margin: 0.4rem 0 0; }}
  .map-wrap {{ margin-top: 1rem; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }}
  iframe.map {{ width: 100%; height: 480px; border: 0; display: block; }}
</style>
</head>
<body>
<h1>{airport} Flight Report</h1>
<p class="meta">Generated {generated_at} &middot; local times shown in Europe/Tallinn</p>
<p class="legend">🇸🇪 highlighted rows and orange map markers are flights to/from Sweden</p>

<h2>Map</h2>
<div class="map-wrap">
<iframe class="map" srcdoc="{map_srcdoc}"></iframe>
</div>

<h2>Actual arrivals <span class="window">({past_start} &ndash; {past_end}) &middot; {arrivals_count} flights</span></h2>
{arrivals_table}

<h2>Actual departures <span class="window">({past_start} &ndash; {past_end}) &middot; {departures_count} flights</span></h2>
{departures_table}

<h2>Scheduled arrivals <span class="window">({future_start} &ndash; {future_end}) &middot; {scheduled_arrivals_count} flights</span></h2>
{scheduled_arrivals_table}

<h2>Scheduled departures <span class="window">({future_start} &ndash; {future_end}) &middot; {scheduled_departures_count} flights</span></h2>
{scheduled_departures_table}

</body>
</html>
"""


def main() -> Path:
    now = dt.datetime.now(dt.timezone.utc)
    report_html = build_report(now)

    OUTPUT_DIR.mkdir(exist_ok=True)
    report_path = OUTPUT_DIR / f"{AIRPORT_ICAO}_report.html"
    report_path.write_text(report_html)
    print(f"Report written to {report_path}")
    return report_path


if __name__ == "__main__":
    main()
