from __future__ import annotations

from datetime import date, datetime, timezone

from ultron.time_reporting import (
    compute_time_summary_buckets,
    sum_hours_created_since,
    sum_hours_spent_on_between,
)


def test_sum_hours_spent_on_between_inclusive() -> None:
    entries = [
        {"spent_on": "2024-06-10", "hours": 2},
        {"spent_on": "2024-06-10", "hours": 1},
        {"spent_on": "2024-06-09", "hours": 5},
    ]
    assert sum_hours_spent_on_between(entries, date(2024, 6, 10), date(2024, 6, 10)) == 3.0
    assert sum_hours_spent_on_between(entries, date(2024, 6, 9), date(2024, 6, 10)) == 8.0


def test_sum_hours_created_since_filters_by_created_on() -> None:
    since = datetime(2024, 6, 10, 0, 0, 0, tzinfo=timezone.utc)
    entries = [
        {"spent_on": "2024-06-10", "hours": 1.0, "created_on": "2024-06-10T12:00:00Z"},
        {"spent_on": "2024-06-10", "hours": 3.0, "created_on": "2024-06-09T12:00:00Z"},
    ]
    assert sum_hours_created_since(entries, since) == 1.0


def test_compute_buckets_today_and_week_utc() -> None:
    # 2024-06-11 is Tuesday; week starts Monday 2024-06-10.
    now = datetime(2024, 6, 11, 15, 0, 0, tzinfo=timezone.utc)
    entries = [
        {"spent_on": "2024-06-11", "hours": 2.0, "created_on": "2024-06-11T14:00:00Z"},
        {"spent_on": "2024-06-10", "hours": 1.5, "created_on": "2024-06-10T10:00:00Z"},
        {"spent_on": "2024-06-09", "hours": 99.0, "created_on": "2024-06-09T10:00:00Z"},
        {"spent_on": "2024-06-03", "hours": 10.0, "created_on": "2024-06-03T08:00:00Z"},
    ]
    b = compute_time_summary_buckets(entries, timezone_name="UTC", now_utc=now)
    assert b.today == 2.0
    assert b.this_week == 2.0 + 1.5
    assert b.last_7_days == 2.0 + 1.5 + 99.0
    assert b.total_fetched == 2.0 + 1.5 + 99.0 + 10.0
