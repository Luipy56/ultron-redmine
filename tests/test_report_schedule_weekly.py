from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ultron.config import (
    AppConfig,
    DiscordConfig,
    LoggingConfig,
    NewIssuesSlashConfig,
    ReportScheduleEntry,
    ReportsConfig,
    load_config,
)
from ultron.report_schedule import humanize_report_schedule_summary


def test_report_schedule_interval_days_weekly(tmp_path: Path) -> None:
    body = """
timezone: UTC
discord: {}
reports: {}
logging: {}
llm_chain: []
report_schedule:
  - command: list_new_issues
    interval_days: 7
    args: {}
  - command: list_unassigned_issues
    interval_days: 7
    args: {}
"""
    p = tmp_path / "c.yaml"
    p.write_text(body, encoding="utf-8")
    cfg = load_config(p)
    assert len(cfg.report_schedule) == 2
    assert all(e.interval_hours == 168 for e in cfg.report_schedule)


def test_humanize_report_schedule_every_week() -> None:
    cfg = AppConfig(
        timezone="UTC",
        discord=DiscordConfig(new_issues=NewIssuesSlashConfig(status_name="New")),
        reports=ReportsConfig(),
        report_schedule=(
            ReportScheduleEntry(command="list_new_issues", interval_hours=168, args=()),
        ),
        logging=LoggingConfig(),
    )
    summary = humanize_report_schedule_summary(cfg)
    assert "every week" in summary


def test_weekly_schedule_does_not_fire_before_interval() -> None:
    """Mirror report_schedule_loop gate: job waits full interval after anchor."""
    entry = ReportScheduleEntry(command="list_new_issues", interval_hours=168, args=())
    anchor = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)

    after_6_days = anchor + timedelta(days=6, hours=23)
    assert after_6_days < anchor + timedelta(hours=entry.interval_hours)

    after_7_days = anchor + timedelta(days=7, hours=1)
    assert after_7_days >= anchor + timedelta(hours=entry.interval_hours)
