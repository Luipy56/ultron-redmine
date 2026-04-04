"""Time-entry aggregation for /time_summary (timezone-aware spent_on buckets)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class TimeSummaryBuckets:
    today: float
    this_week: float
    last_7_days: float
    last_24h: float
    total_fetched: float


def _safe_zoneinfo(name: str) -> ZoneInfo:
    n = (name or "").strip() or "UTC"
    try:
        return ZoneInfo(n)
    except ZoneInfoNotFoundError as e:
        raise ValueError(
            f"Invalid **timezone** in config: {n!r}. Use an IANA name (e.g. **Europe/Madrid**)."
        ) from e


def spent_on_date(entry: dict[str, Any]) -> date | None:
    raw = entry.get("spent_on")
    if raw is None:
        return None
    s = str(raw).strip()[:10]
    if len(s) < 10:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _hours_value(entry: dict[str, Any]) -> float:
    h = entry.get("hours")
    if h is None:
        return 0.0
    try:
        return float(h)
    except (TypeError, ValueError):
        return 0.0


def sum_hours_spent_on_between(entries: list[dict[str, Any]], start: date, end: date) -> float:
    """Inclusive ``spent_on`` range ``start``..``end``."""
    total = 0.0
    for e in entries:
        d = spent_on_date(e)
        if d is None:
            continue
        if start <= d <= end:
            total += _hours_value(e)
    return total


def sum_hours_created_since(entries: list[dict[str, Any]], since_utc: datetime) -> float:
    """Sum hours for entries whose ``created_on`` is at or after ``since_utc`` (UTC-aware)."""
    total = 0.0
    for e in entries:
        created = parse_entry_created_on(e)
        if created is None:
            continue
        if created >= since_utc:
            total += _hours_value(e)
    return total


def parse_entry_created_on(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("created_on")
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_time_summary_buckets(
    entries: list[dict[str, Any]],
    *,
    timezone_name: str,
    now_utc: datetime,
) -> TimeSummaryBuckets:
    """Compute bucket totals from already-fetched time entries (``spent_on`` / ``created_on``)."""
    zi = _safe_zoneinfo(timezone_name)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_local = now_utc.astimezone(zi)
    today = now_local.date()
    weekday = today.weekday()  # Monday = 0
    week_start = today - timedelta(days=weekday)
    week_end_cap = today
    last7_start = today - timedelta(days=6)

    today_h = sum_hours_spent_on_between(entries, today, today)
    week_h = sum_hours_spent_on_between(entries, week_start, week_end_cap)
    last7_h = sum_hours_spent_on_between(entries, last7_start, today)

    since_24h = now_utc - timedelta(hours=24)
    last24_h = sum_hours_created_since(entries, since_24h)

    total_all = sum(_hours_value(e) for e in entries)

    return TimeSummaryBuckets(
        today=today_h,
        this_week=week_h,
        last_7_days=last7_h,
        last_24h=last24_h,
        total_fetched=total_all,
    )


def fetch_spent_on_range_strings(
    timezone_name: str,
    now_utc: datetime,
    lookback_days: int,
) -> tuple[str, str]:
    """Return ``(from_str, to_str)`` as YYYY-MM-DD in ``timezone_name`` covering ``lookback_days`` before "today" through today."""
    zi = _safe_zoneinfo(timezone_name)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    today = now_utc.astimezone(zi).date()
    start = today - timedelta(days=max(0, lookback_days))
    return start.isoformat(), today.isoformat()
