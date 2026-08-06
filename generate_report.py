"""Generate an HTML report for Tartu Airport (EETU): past actual arrivals,
past actual departures, upcoming scheduled arrivals, and upcoming scheduled
departures.

Usage:
    python generate_report.py

Overwrites output/{AIRPORT_ICAO}_report.html in place on every run (same
path each time, not timestamped) and prints its path.
"""

import datetime as dt
from pathlib import Path

import pandas as pd

from src.arrivals import to_dataframe, to_departures_dataframe
from src.flightaware_client import (
    fetch_arrivals,
    fetch_departures,
    fetch_scheduled_arrivals,
    fetch_scheduled_departures,
)

AIRPORT_ICAO = "EETU"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

TIME_COLUMNS = {
    "arrivals": ("scheduled_in", "estimated_in", "actual_in"),
    "departures": ("scheduled_out", "estimated_out", "actual_out"),
}


def _format_times(df: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    df = df.copy()
    for col in columns:
        df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M UTC").fillna("")
    return df


def _table_html(df: pd.DataFrame, time_columns: tuple[str, ...]) -> str:
    if df.empty:
        return "<p class=\"empty\">No flights in this window.</p>"
    return _format_times(df, time_columns).to_html(index=False, na_rep="", border=0)


def build_report(now: dt.datetime) -> str:
    past_start, past_end = now - dt.timedelta(hours=24), now
    future_start, future_end = now, now + dt.timedelta(hours=24)

    raw_arrivals = fetch_arrivals(AIRPORT_ICAO, start=past_start, end=past_end)
    raw_departures = fetch_departures(AIRPORT_ICAO, start=past_start, end=past_end)
    raw_scheduled_arrivals = fetch_scheduled_arrivals(
        AIRPORT_ICAO, start=future_start, end=future_end
    )
    raw_scheduled_departures = fetch_scheduled_departures(
        AIRPORT_ICAO, start=future_start, end=future_end
    )

    df_arrivals = to_dataframe(raw_arrivals, key="arrivals")
    df_departures = to_departures_dataframe(raw_departures, key="departures")
    df_scheduled_arrivals = to_dataframe(raw_scheduled_arrivals, key="scheduled_arrivals")
    df_scheduled_departures = to_departures_dataframe(
        raw_scheduled_departures, key="scheduled_departures"
    )

    generated_at = now.strftime("%Y-%m-%d %H:%M UTC")
    return HTML_TEMPLATE.format(
        airport=AIRPORT_ICAO,
        generated_at=generated_at,
        past_start=past_start.strftime("%Y-%m-%d %H:%M UTC"),
        past_end=past_end.strftime("%Y-%m-%d %H:%M UTC"),
        future_start=future_start.strftime("%Y-%m-%d %H:%M UTC"),
        future_end=future_end.strftime("%Y-%m-%d %H:%M UTC"),
        arrivals_count=len(df_arrivals),
        departures_count=len(df_departures),
        scheduled_arrivals_count=len(df_scheduled_arrivals),
        scheduled_departures_count=len(df_scheduled_departures),
        arrivals_table=_table_html(df_arrivals, TIME_COLUMNS["arrivals"]),
        departures_table=_table_html(df_departures, TIME_COLUMNS["departures"]),
        scheduled_arrivals_table=_table_html(df_scheduled_arrivals, TIME_COLUMNS["arrivals"]),
        scheduled_departures_table=_table_html(
            df_scheduled_departures, TIME_COLUMNS["departures"]
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
  .empty {{ color: #888; font-style: italic; }}
</style>
</head>
<body>
<h1>{airport} Flight Report</h1>
<p class="meta">Generated {generated_at}</p>

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
    html = build_report(now)

    OUTPUT_DIR.mkdir(exist_ok=True)
    report_path = OUTPUT_DIR / f"{AIRPORT_ICAO}_report.html"
    report_path.write_text(html)
    print(f"Report written to {report_path}")
    return report_path


if __name__ == "__main__":
    main()
