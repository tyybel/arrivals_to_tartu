"""Shared local-time formatting for the EETU report scripts.

AeroAPI returns every timestamp as UTC; generate_report.py and
generate_history_report.py both display them converted to this one local
zone so the same underlying flight data reads the same way regardless of
which report you're looking at (previously generate_report.py showed UTC
while generate_history_report.py showed local time, making identical data
look inconsistent).
"""

from zoneinfo import ZoneInfo

import pandas as pd

LOCAL_TZ = ZoneInfo("Europe/Tallinn")


def fmt_local(ts, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a tz-aware timestamp (pd.Timestamp or datetime.datetime) in
    LOCAL_TZ. Returns "" for NaT/None so table cells render blank instead of
    the string "NaT"."""
    if pd.isna(ts):
        return ""
    return ts.astimezone(LOCAL_TZ).strftime(fmt)


def fmt_local_split(ts) -> tuple[str, str]:
    """Like fmt_local, but split into (date, time) strings for tables that
    show them in separate columns. Returns ("—", "—") for NaT/None."""
    if pd.isna(ts):
        return "—", "—"
    local = ts.astimezone(LOCAL_TZ)
    return local.strftime("%a %d %b"), local.strftime("%H:%M")
