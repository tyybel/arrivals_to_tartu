"""Transform raw AeroAPI arrivals responses into tables and save snapshots."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def to_dataframe(raw_response: dict[str, Any]) -> pd.DataFrame:
    """Convert a raw AeroAPI arrivals response into a flat DataFrame."""
    flights = raw_response.get("arrivals", [])

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


def save_snapshot(raw_response: dict[str, Any], df: pd.DataFrame, airport_icao: str) -> Path:
    """Save both the raw API response and the flattened table to output/, timestamped."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{airport_icao}_arrivals_{timestamp}"

    raw_path = OUTPUT_DIR / f"{stem}_raw.json"
    raw_path.write_text(json.dumps(raw_response, indent=2))

    csv_path = OUTPUT_DIR / f"{stem}.csv"
    df.to_csv(csv_path, index=False)

    return csv_path
