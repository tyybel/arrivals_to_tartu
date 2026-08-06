"""Transform raw AeroAPI arrivals responses into tables and save snapshots."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def to_raw_dataframe(raw_response: dict[str, Any], key: str = "arrivals") -> pd.DataFrame:
    """Flatten every field of a raw AeroAPI arrivals response into a DataFrame,
    one row per flight, nested objects expanded into dot-notation columns
    (e.g. origin.code, destination.city). Meant for open-ended exploration
    (e.g. in Data Wrangler) rather than a curated view.
    """
    flights = raw_response.get(key, [])
    return pd.json_normalize(flights)


def to_dataframe(raw_response: dict[str, Any], key: str = "arrivals") -> pd.DataFrame:
    """Convert a raw AeroAPI arrivals response into a flat DataFrame.

    `key` selects which list in the response to read: "arrivals" for the
    flights/arrivals endpoint, "scheduled_arrivals" for flights/scheduled_arrivals.
    """
    flights = raw_response.get(key, [])

    rows = []
    for flight in flights:
        origin = flight.get("origin") or {}
        rows.append(
            {
                "ident": flight.get("ident"),
                "origin": origin.get("code") or origin.get("code_icao"),
                "scheduled_in": flight.get("scheduled_in"),
                "estimated_in": flight.get("estimated_in"),
                "actual_in": flight.get("actual_in"),
                "status": flight.get("status"),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        for col in ("scheduled_in", "estimated_in", "actual_in"):
            df[col] = pd.to_datetime(df[col], errors="coerce")
        df = df.sort_values("scheduled_in").reset_index(drop=True)
    return df


def to_departures_dataframe(
    raw_response: dict[str, Any], key: str = "scheduled_departures"
) -> pd.DataFrame:
    """Convert a raw AeroAPI departures response (flights/scheduled_departures or
    flights/departures) into a flat DataFrame, mirroring to_dataframe but for the
    departure side (destination airport, *_out timestamps)."""
    flights = raw_response.get(key, [])

    rows = []
    for flight in flights:
        destination = flight.get("destination") or {}
        rows.append(
            {
                "ident": flight.get("ident"),
                "destination": destination.get("code") or destination.get("code_icao"),
                "scheduled_out": flight.get("scheduled_out"),
                "estimated_out": flight.get("estimated_out"),
                "actual_out": flight.get("actual_out"),
                "status": flight.get("status"),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        for col in ("scheduled_out", "estimated_out", "actual_out"):
            df[col] = pd.to_datetime(df[col], errors="coerce")
        df = df.sort_values("scheduled_out").reset_index(drop=True)
    return df


def save_snapshot(
    raw_response: dict[str, Any], df: pd.DataFrame, airport_icao: str, label: str = "arrivals"
) -> Path:
    """Save both the raw API response and the flattened table to output/, timestamped.

    `label` distinguishes snapshots from different endpoints (e.g. "arrivals" vs
    "scheduled_arrivals") so they don't overwrite each other.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{airport_icao}_{label}_{timestamp}"

    raw_path = OUTPUT_DIR / f"{stem}_raw.json"
    raw_path.write_text(json.dumps(raw_response, indent=2))

    csv_path = OUTPUT_DIR / f"{stem}.csv"
    df.to_csv(csv_path, index=False)

    return csv_path
