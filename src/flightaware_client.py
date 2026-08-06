"""Thin wrapper around FlightAware's AeroAPI for airport arrivals."""

import datetime as dt
import os
from typing import Any

import requests
from dotenv import load_dotenv

AEROAPI_BASE_URL = "https://aeroapi.flightaware.com/aeroapi"

# Confirmed via live testing: flights/arrivals and flights/departures reject
# `start` values more than 10 days in the past (400 INVALID_ARGUMENT, "time is
# too far in the past"). flights/scheduled_arrivals and flights/scheduled_departures
# have the mirror-image limit on `end` (see fetch_scheduled_arrivals below).
MAX_HISTORY_DAYS = 10


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
    """Fetch every page of `endpoint` between `start` and `end`, following the
    `links.next` cursor. AeroAPI paginates flights/* endpoints at ~15 records
    per page, which a multi-day window regularly exceeds."""
    api_key = _get_api_key()
    headers = {"x-apikey": api_key}

    url = f"{AEROAPI_BASE_URL}/airports/{airport_icao}/flights/{endpoint}"
    params: dict[str, str] | None = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    items: list[Any] = []
    while url:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code != 200:
            raise FlightAwareError(
                f"AeroAPI request failed ({response.status_code}): {response.text}"
            )
        data = response.json()
        items.extend(data.get(endpoint, []))

        next_link = (data.get("links") or {}).get("next")
        url = f"{AEROAPI_BASE_URL}{next_link}" if next_link else None
        params = None  # the next link already carries start/end/cursor as query params

    return {endpoint: items}


def fetch_arrivals(airport_icao: str, start: dt.datetime, end: dt.datetime) -> dict[str, Any]:
    """Fetch live-tracked arrivals (endpoint: flights/arrivals) for an airport
    between `start` and `end` (timezone-aware datetimes; either may be in the past)."""
    return _fetch("arrivals", airport_icao, start, end)


def fetch_departures(airport_icao: str, start: dt.datetime, end: dt.datetime) -> dict[str, Any]:
    """Fetch live-tracked departures (endpoint: flights/departures) for an airport
    between `start` and `end` (timezone-aware datetimes; either may be in the past)."""
    return _fetch("departures", airport_icao, start, end)


def fetch_scheduled_arrivals(
    airport_icao: str, start: dt.datetime, end: dt.datetime
) -> dict[str, Any]:
    """Fetch schedule-based arrivals (endpoint: flights/scheduled_arrivals) for an
    airport between `start` and `end` (timezone-aware datetimes; either may be in the past).
    Note: confirmed via live testing that AeroAPI rejects `end` values more than
    2 days from now for this endpoint (400 INVALID_ARGUMENT, "time is too far in
    the future"). Keep `end` within 2 days of now."""
    return _fetch("scheduled_arrivals", airport_icao, start, end)


def fetch_scheduled_departures(
    airport_icao: str, start: dt.datetime, end: dt.datetime
) -> dict[str, Any]:
    """Fetch schedule-based departures (endpoint: flights/scheduled_departures) for an
    airport between `start` and `end` (timezone-aware datetimes; either may be in the past).
    Same 2-day future limit on `end` as fetch_scheduled_arrivals applies here."""
    return _fetch("scheduled_departures", airport_icao, start, end)


def fetch_airport(airport_code: str) -> dict[str, Any]:
    """Fetch airport metadata (endpoint: GET /airports/{id}), including
    latitude/longitude, for a given ICAO/IATA code."""
    api_key = _get_api_key()
    url = f"{AEROAPI_BASE_URL}/airports/{airport_code}"
    response = requests.get(url, headers={"x-apikey": api_key}, timeout=30)
    if response.status_code != 200:
        raise FlightAwareError(
            f"AeroAPI request failed ({response.status_code}): {response.text}"
        )
    return response.json()
