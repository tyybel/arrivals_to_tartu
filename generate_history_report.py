"""Generate an HTML report of actual arrivals and departures at Tartu Airport
(EETU) over the recent past, with an embedded folium map of origin/destination
airports.

AeroAPI's flights/arrivals and flights/departures endpoints (live-tracked,
"actual" data) reject any `start` more than MAX_HISTORY_DAYS in the past, so a
14-day request is clamped to that limit — see REQUESTED_HISTORY_DAYS below.

Usage:
    python generate_history_report.py

Overwrites output/{AIRPORT_ICAO}_history_report.html in place on every run.
"""

import datetime as dt
import html
from pathlib import Path
from typing import Any

import pandas as pd

from src.flightaware_client import MAX_HISTORY_DAYS, fetch_arrivals, fetch_departures
from src.formatting import fmt_local, fmt_local_split
from src.mapping import (
    add_route_group,
    build_map,
    get_center_coords,
    is_sweden,
    load_coord_cache,
    parse_position_code,
    save_coord_cache,
)
from src.store import sync_source

AIRPORT_ICAO = "EETU"
REQUESTED_HISTORY_DAYS = 14
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# See generate_report.py's LIVE_DATA_REFETCH_BUFFER / sync_source()'s
# refetch_buffer docstring: re-check this trailing slice of already-"covered"
# time on every run so late-published arrivals/departures aren't permanently
# missed.
LIVE_DATA_REFETCH_BUFFER = dt.timedelta(days=2)

STATUS_CLASSES = {
    "arrived": "status-ok",
    "cancelled": "status-bad",
    "diverted": "status-warn",
}


def _airport_label(name: str | None, city: str | None, code: str | None) -> str:
    place = city or name
    if place and code:
        label = f"{place} ({code})"
    else:
        label = code or place or "Unknown"
    return f"🇸🇪 {label}" if is_sweden(code) else label


def _extract_row(flight: dict[str, Any], *, arrival: bool) -> dict[str, Any]:
    other = flight.get("origin" if arrival else "destination") or {}
    code = other.get("code") or other.get("code_icao")
    position = parse_position_code(code)

    if arrival:
        scheduled = flight.get("scheduled_in") or flight.get("scheduled_on")
        estimated = flight.get("estimated_in") or flight.get("estimated_on")
        actual = flight.get("actual_in") or flight.get("actual_on")
    else:
        scheduled = flight.get("scheduled_out") or flight.get("scheduled_off")
        estimated = flight.get("estimated_out") or flight.get("estimated_off")
        actual = flight.get("actual_out") or flight.get("actual_off")

    ident = flight.get("ident") or "—"
    registration = flight.get("registration")
    # For GA flights `ident` already *is* the tail number (e.g. "OY-BKM") — only
    # show a separate registration when it adds information beyond the callsign.
    registration_display = registration if registration and registration != ident else "—"

    return {
        "ident": ident,
        "registration": registration_display,
        "aircraft_type": flight.get("aircraft_type") or "—",
        "other_code": code,
        "other_label": (
            f"In flight near {position[0]:.2f}, {position[1]:.2f}"
            if position
            else _airport_label(other.get("name"), other.get("city"), code)
        ),
        "scheduled": pd.to_datetime(scheduled) if scheduled else pd.NaT,
        "actual": pd.to_datetime(actual) if actual else pd.NaT,
        "status": flight.get("status") or "Unknown",
        "cancelled": bool(flight.get("cancelled")),
        "diverted": bool(flight.get("diverted")),
    }


def _build_dataframe(raw_flights: list[dict[str, Any]], *, arrival: bool) -> pd.DataFrame:
    rows = [_extract_row(f, arrival=arrival) for f in raw_flights]
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["delay_minutes"] = (
        (df["actual"] - df["scheduled"]).dt.total_seconds() / 60
    ).round().astype("Int64")
    df.loc[df["scheduled"].isna() | df["actual"].isna(), "delay_minutes"] = pd.NA

    sort_col = df["actual"].fillna(df["scheduled"])
    df = df.assign(_sort=sort_col).sort_values("_sort", ascending=False).drop(columns="_sort")
    return df.reset_index(drop=True)


def _fmt_delay(minutes: Any) -> str:
    if pd.isna(minutes):
        return "—"
    minutes = int(minutes)
    if abs(minutes) <= 5:
        return "on time"
    sign = "+" if minutes > 0 else ""
    return f"{sign}{minutes} min"


def _status_class(status: str, cancelled: bool, diverted: bool) -> str:
    if cancelled:
        return "status-bad"
    if diverted:
        return "status-warn"
    return STATUS_CLASSES.get(status.lower(), "status-neutral")


def _rows_to_html(df: pd.DataFrame, other_column_label: str) -> str:
    if df.empty:
        return '<p class="empty">No flights in this window.</p>'

    header = (
        "<tr><th>Date</th><th>Time (local)</th><th>Flight</th><th>Reg.</th><th>Aircraft</th>"
        f"<th>{other_column_label}</th><th>Status</th><th>vs. schedule</th></tr>"
    )
    body_rows = []
    for _, row in df.iterrows():
        date_str, time_str = fmt_local_split(row["actual"] if pd.notna(row["actual"]) else row["scheduled"])
        status_cls = _status_class(row["status"], row["cancelled"], row["diverted"])
        row_cls = ' class="sweden"' if is_sweden(row["other_code"]) else ""
        body_rows.append(
            f"<tr{row_cls}>"
            f"<td>{html.escape(date_str)}</td>"
            f"<td>{html.escape(time_str)}</td>"
            f"<td>{html.escape(row['ident'])}</td>"
            f"<td>{html.escape(row['registration'])}</td>"
            f"<td>{html.escape(row['aircraft_type'])}</td>"
            f"<td>{html.escape(row['other_label'])}</td>"
            f'<td><span class="badge {status_cls}">{html.escape(row["status"])}</span></td>'
            f"<td>{_fmt_delay(row['delay_minutes'])}</td>"
            "</tr>"
        )
    return f"<table>\n<thead>{header}</thead>\n<tbody>{''.join(body_rows)}</tbody>\n</table>"


def _build_map(
    eetu_coords: tuple[float, float],
    df_arrivals: pd.DataFrame,
    df_departures: pd.DataFrame,
    coord_cache: dict[str, tuple[float, float] | None],
) -> str:
    m = build_map(eetu_coords, "Tartu (EETU)")

    def detail(row: pd.Series) -> str:
        date_str = fmt_local_split(row["actual"] if pd.notna(row["actual"]) else row["scheduled"])[0]
        return f"{html.escape(row['ident'])} — {date_str}"

    add_route_group(
        m,
        df_arrivals,
        code_col="other_code",
        label_fn=lambda row: row["other_label"],
        detail_fn=detail,
        center_coords=eetu_coords,
        coord_cache=coord_cache,
        color="blue",
        kind="arrival(s)",
    )
    add_route_group(
        m,
        df_departures,
        code_col="other_code",
        label_fn=lambda row: row["other_label"],
        detail_fn=detail,
        center_coords=eetu_coords,
        coord_cache=coord_cache,
        color="green",
        kind="departure(s)",
    )

    return m.get_root().render()


def _summary(df_arrivals: pd.DataFrame, df_departures: pd.DataFrame) -> dict[str, Any]:
    origins = df_arrivals["other_code"].nunique() if not df_arrivals.empty else 0
    destinations = df_departures["other_code"].nunique() if not df_departures.empty else 0
    return {
        "arrivals": len(df_arrivals),
        "departures": len(df_departures),
        "origins": origins,
        "destinations": destinations,
    }


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{airport} History Report</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 2rem; color: #1a1a1a; background: #fff; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .meta {{ color: #666; margin-bottom: 0.3rem; }}
  .note {{ color: #8a6d00; background: #fff8e1; border: 1px solid #ffe58a; border-radius: 6px; padding: 0.6rem 0.9rem; font-size: 0.9rem; margin: 1rem 0; max-width: 60rem; }}
  .summary {{ display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 1.5rem 0; }}
  .stat {{ background: #f5f5f5; border-radius: 8px; padding: 0.8rem 1.2rem; min-width: 8rem; }}
  .stat .n {{ font-size: 1.6rem; font-weight: 600; display: block; }}
  .stat .label {{ color: #666; font-size: 0.85rem; }}
  h2 {{ margin-top: 2.5rem; border-bottom: 2px solid #ddd; padding-bottom: 0.3rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 0.8rem; font-size: 0.92rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.7rem; border-bottom: 1px solid #eee; }}
  th {{ background: #f5f5f5; }}
  tr:hover {{ background: #fafafa; }}
  .empty {{ color: #888; font-style: italic; }}
  .badge {{ display: inline-block; padding: 0.1rem 0.55rem; border-radius: 999px; font-size: 0.82rem; font-weight: 500; }}
  .status-ok {{ background: #e3f6e8; color: #1a7a3c; }}
  .status-bad {{ background: #fde8e8; color: #b3261e; }}
  .status-warn {{ background: #fff3d6; color: #93650a; }}
  .status-neutral {{ background: #eee; color: #555; }}
  .map-wrap {{ margin-top: 1rem; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }}
  iframe.map {{ width: 100%; height: 520px; border: 0; display: block; }}
  tr.sweden {{ background: #fff3b0; }}
  tr.sweden:hover {{ background: #ffec8a; }}
  .legend {{ color: #666; font-size: 0.85rem; margin: 0.4rem 0 0; }}
</style>
</head>
<body>
<h1>{airport} History Report</h1>
<p class="meta">Generated {generated_at} &middot; local times shown in Europe/Tallinn</p>
<p class="legend">🇸🇪 highlighted rows and orange map markers are flights to/from Sweden</p>
{limit_note}

<div class="summary">
  <div class="stat"><span class="n">{arrivals_count}</span><span class="label">actual arrivals</span></div>
  <div class="stat"><span class="n">{departures_count}</span><span class="label">actual departures</span></div>
  <div class="stat"><span class="n">{origins_count}</span><span class="label">distinct origins</span></div>
  <div class="stat"><span class="n">{destinations_count}</span><span class="label">distinct destinations</span></div>
</div>

<h2>Map</h2>
<div class="map-wrap">
<iframe class="map" srcdoc="{map_srcdoc}"></iframe>
</div>

<h2>Actual arrivals <span class="meta">({window_start} &ndash; {window_end})</span></h2>
{arrivals_table}

<h2>Actual departures <span class="meta">({window_start} &ndash; {window_end})</span></h2>
{departures_table}

</body>
</html>
"""


def build_report(now: dt.datetime) -> str:
    effective_days = min(REQUESTED_HISTORY_DAYS, MAX_HISTORY_DAYS)
    start = now - dt.timedelta(days=effective_days)

    coord_cache = load_coord_cache()
    eetu_coords = get_center_coords(AIRPORT_ICAO, coord_cache)

    raw_arrivals = sync_source(
        "arrivals",
        lambda s, e: fetch_arrivals(AIRPORT_ICAO, start=s, end=e),
        start,
        now,
        now,
        refetch_buffer=LIVE_DATA_REFETCH_BUFFER,
    )["arrivals"]
    raw_departures = sync_source(
        "departures",
        lambda s, e: fetch_departures(AIRPORT_ICAO, start=s, end=e),
        start,
        now,
        now,
        refetch_buffer=LIVE_DATA_REFETCH_BUFFER,
    )["departures"]

    df_arrivals = _build_dataframe(raw_arrivals, arrival=True)
    df_departures = _build_dataframe(raw_departures, arrival=False)

    map_html = _build_map(eetu_coords, df_arrivals, df_departures, coord_cache)
    save_coord_cache(coord_cache)
    stats = _summary(df_arrivals, df_departures)

    limit_note = ""
    if REQUESTED_HISTORY_DAYS > MAX_HISTORY_DAYS:
        limit_note = (
            f'<p class="note">A {REQUESTED_HISTORY_DAYS}-day window was requested, but '
            f"FlightAware AeroAPI's live-tracked arrivals/departures endpoints only retain "
            f"{MAX_HISTORY_DAYS} days of history. Showing the last {MAX_HISTORY_DAYS} days instead.</p>"
        )

    return HTML_TEMPLATE.format(
        airport=AIRPORT_ICAO,
        generated_at=fmt_local(now),
        limit_note=limit_note,
        arrivals_count=stats["arrivals"],
        departures_count=stats["departures"],
        origins_count=stats["origins"],
        destinations_count=stats["destinations"],
        map_srcdoc=html.escape(map_html, quote=True),
        window_start=fmt_local(start, "%d %b %H:%M"),
        window_end=fmt_local(now, "%d %b %H:%M"),
        arrivals_table=_rows_to_html(df_arrivals, "Origin"),
        departures_table=_rows_to_html(df_departures, "Destination"),
    )


def main() -> Path:
    now = dt.datetime.now(dt.timezone.utc)
    report_html = build_report(now)

    OUTPUT_DIR.mkdir(exist_ok=True)
    report_path = OUTPUT_DIR / f"{AIRPORT_ICAO}_history_report.html"
    report_path.write_text(report_html)
    print(f"Report written to {report_path}")
    return report_path


if __name__ == "__main__":
    main()
