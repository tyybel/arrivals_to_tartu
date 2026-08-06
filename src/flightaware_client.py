"""Thin wrapper around FlightAware's AeroAPI for airport arrivals."""

import datetime as dt
import os
from typing import Any

import requests
from dotenv import load_dotenv

AEROAPI_BASE_URL = "https://aeroapi.flightaware.com/aeroapi"


class FlightAwareError(RuntimeError):
    pass


def _get_api_key() -> str:
    load_dotenv()
    api_key = os.environ.get("FLIGHTAWARE_API_KEY")
    if not api_key:
        raise FlightAwareError(
            "FLIGHTAWARE_API_KEY is not set. Copy .env.example to .env and add your AeroAPI key."
        )
    return api_key


def fetch_arrivals(airport_icao: str, hours_ahead: int = 24) -> dict[str, Any]:
    """Fetch scheduled arrivals for an airport within the next `hours_ahead` hours."""
    api_key = _get_api_key()

    now = dt.datetime.now(dt.timezone.utc)
    start = now
    end = now + dt.timedelta(hours=hours_ahead)

    url = f"{AEROAPI_BASE_URL}/airports/{airport_icao}/flights/arrivals"
    params = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    headers = {"x-apikey": api_key}

    response = requests.get(url, headers=headers, params=params, timeout=30)
    if response.status_code != 200:
        raise FlightAwareError(
            f"AeroAPI request failed ({response.status_code}): {response.text}"
        )
    return response.json()
