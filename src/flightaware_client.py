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


def _fetch(
    endpoint: str,
    airport_icao: str,
    start: dt.datetime,
    end: dt.datetime,
) -> dict[str, Any]:
    api_key = _get_api_key()

    url = f"{AEROAPI_BASE_URL}/airports/{airport_icao}/flights/{endpoint}"
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


def fetch_arrivals(airport_icao: str, start: dt.datetime, end: dt.datetime) -> dict[str, Any]:
    """Fetch live-tracked arrivals (endpoint: flights/arrivals) for an airport
    between `start` and `end` (timezone-aware datetimes; either may be in the past)."""
    return _fetch("arrivals", airport_icao, start, end)


def fetch_scheduled_arrivals(
    airport_icao: str, start: dt.datetime, end: dt.datetime
) -> dict[str, Any]:
    """Fetch schedule-based arrivals (endpoint: flights/scheduled_arrivals) for an
    airport between `start` and `end` (timezone-aware datetimes; either may be in the past).
    Note: this endpoint is schedule-based and may not return data for past windows —
    verify against a live call."""
    return _fetch("scheduled_arrivals", airport_icao, start, end)
