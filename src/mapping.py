"""Shared folium map building and airport-coordinate caching for EETU reports.

Used by both generate_report.py and generate_history_report.py so they share
one on-disk coordinate cache (airport coordinates never change, and AeroAPI's
request quota is easy to exhaust otherwise) and one visual language for
highlighting Swedish routes.
"""

import html
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import folium
import pandas as pd

from src.flightaware_client import fetch_airport

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
COORD_CACHE_PATH = OUTPUT_DIR / "airport_coords_cache.json"

POSITION_RE = re.compile(r"^L\s+(-?\d+\.?\d*)\s+(-?\d+\.?\d*)$")

# All Swedish ICAO airport codes start with "ES" (e.g. ESSA, ESSL) — distinct
# from neighboring "EE" (Estonia), "EF" (Finland), "EN" (Norway), "EK" (Denmark).
SWEDEN_ICAO_PREFIX = "ES"


def parse_position_code(code: Any) -> tuple[float, float] | None:
    """Ad-hoc/GA flights without a matched airport report their last known
    position as a synthetic code like "L 66.12936 12.70054" (lat, lon)."""
    if not isinstance(code, str):
        return None
    m = POSITION_RE.match(code)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def is_sweden(code: Any) -> bool:
    # isinstance guard matters: a missing code survives as Python None at
    # extraction time but pandas silently upgrades None to float('nan') once
    # it round-trips through a DataFrame/iterrows(), and nan.upper() would raise.
    return isinstance(code, str) and code.upper().startswith(SWEDEN_ICAO_PREFIX)


def load_coord_cache() -> dict[str, tuple[float, float] | None]:
    if not COORD_CACHE_PATH.exists():
        return {}
    raw = json.loads(COORD_CACHE_PATH.read_text())
    return {code: tuple(coords) if coords else None for code, coords in raw.items()}


def save_coord_cache(cache: dict[str, tuple[float, float] | None]) -> None:
    # Drop failed lookups (None) so a future run retries them instead of
    # permanently remembering a transient rate-limit error as "no coordinates".
    resolved = {code: coords for code, coords in cache.items() if coords}
    OUTPUT_DIR.mkdir(exist_ok=True)
    COORD_CACHE_PATH.write_text(json.dumps(resolved, indent=2, sort_keys=True))


def get_coords(
    code: Any, position: tuple[float, float] | None, cache: dict
) -> tuple[float, float] | None:
    if position:
        return position
    if not isinstance(code, str):
        return None
    if code in cache:
        return cache[code]
    try:
        info = fetch_airport(code)
        coords = (info["latitude"], info["longitude"])
    except Exception as exc:  # noqa: BLE001 - keep report generation going despite a bad lookup
        print(f"warning: could not resolve coordinates for {code}: {exc}")
        coords = None
    cache[code] = coords
    return coords


def build_map(
    center_coords: tuple[float, float], center_label: str, *, zoom_start: int = 5
) -> folium.Map:
    m = folium.Map(location=center_coords, zoom_start=zoom_start, tiles="CartoDB positron")
    folium.CircleMarker(
        center_coords,
        radius=7,
        tooltip=center_label,
        color="#111111",
        fill=True,
        fill_color="#111111",
        fill_opacity=1,
    ).add_to(m)
    return m


def add_route_group(
    m: folium.Map,
    df: pd.DataFrame,
    *,
    code_col: str,
    label_fn: Callable[[pd.Series], str],
    detail_fn: Callable[[pd.Series], str],
    center_coords: tuple[float, float],
    coord_cache: dict,
    color: str,
    kind: str,
    sweden_color: str = "orange",
) -> None:
    """Add one marker+line per distinct airport code in `df`, grouped so a code
    seen on multiple flights gets a single marker listing all of them."""
    if df.empty:
        return
    grouped: dict[Any, list[pd.Series]] = defaultdict(list)
    for _, row in df.iterrows():
        code = row[code_col]
        code = None if pd.isna(code) else code
        grouped[code or label_fn(row)].append(row)

    for _, rows in grouped.items():
        sample = rows[0]
        code = sample[code_col]
        code = None if pd.isna(code) else code
        position = parse_position_code(code)
        coords = get_coords(code, position, coord_cache)
        if not coords:
            continue

        label = label_fn(sample)
        marker_color = sweden_color if is_sweden(code) else color
        detail_lines = "<br>".join(detail_fn(row) for row in rows[:10])
        popup = folium.Popup(
            f"<b>{html.escape(label)}</b><br>{kind}: {len(rows)}<br>{detail_lines}",
            max_width=260,
        )
        folium.Marker(
            coords,
            tooltip=f"{label} · {len(rows)} {kind}",
            popup=popup,
            icon=folium.Icon(color=marker_color),
        ).add_to(m)
        folium.PolyLine([coords, center_coords], color=marker_color, weight=1.5, opacity=0.5).add_to(m)


def get_center_coords(airport_icao: str, cache: dict) -> tuple[float, float]:
    coords = get_coords(airport_icao, None, cache)
    if coords is None:
        info = fetch_airport(airport_icao)
        coords = (info["latitude"], info["longitude"])
        cache[airport_icao] = coords
    return coords
