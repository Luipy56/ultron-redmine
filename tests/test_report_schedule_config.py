from __future__ import annotations

from pathlib import Path

import pytest

from ultron.config import ReportScheduleEntry, load_config

_BASE = """
timezone: UTC
discord: {}
reports: {}
logging: {}
llm_chain: []
"""


def test_report_schedule_empty(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(_BASE + "report_schedule: []\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.report_schedule == ()


def test_report_schedule_two_entries(tmp_path: Path) -> None:
    body = (
        _BASE
        + """
report_schedule:
  - enabled: true
    command: new_issues
    interval_hours: 8
    args: {}
  - enabled: true
    command: unassigned_issues
    interval_days: 1
    args: {}
"""
    )
    p = tmp_path / "c.yaml"
    p.write_text(body, encoding="utf-8")
    cfg = load_config(p)
    assert len(cfg.report_schedule) == 2
    assert cfg.report_schedule[0] == ReportScheduleEntry(
        command="list_new_issues", interval_hours=8, args=()
    )
    assert cfg.report_schedule[1] == ReportScheduleEntry(
        command="list_unassigned_issues", interval_hours=24, args=()
    )


def test_report_schedule_skips_disabled(tmp_path: Path) -> None:
    body = (
        _BASE
        + """
report_schedule:
  - enabled: false
    command: new_issues
    interval_hours: 24
    args: {}
  - enabled: true
    command: new_issues
    interval_hours: 12
    args: {}
"""
    )
    p = tmp_path / "c.yaml"
    p.write_text(body, encoding="utf-8")
    cfg = load_config(p)
    assert len(cfg.report_schedule) == 1
    assert cfg.report_schedule[0].command == "list_new_issues"
    assert cfg.report_schedule[0].interval_hours == 12


def test_report_schedule_issues_by_status_requires_status(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        _BASE
        + """
report_schedule:
  - command: issues_by_status
    interval_hours: 6
    args: {}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="args.status"):
        load_config(p)


def test_report_schedule_unknown_command(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        _BASE
        + """
report_schedule:
  - command: summary
    interval_hours: 1
    args: {}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="command"):
        load_config(p)


def test_report_schedule_list_new_issues_explicit_command(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        _BASE
        + """
report_schedule:
  - command: list_new_issues
    interval_hours: 6
    args: {}
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.report_schedule == (
        ReportScheduleEntry(command="list_new_issues", interval_hours=6, args=()),
    )


def test_report_schedule_list_unassigned_issues_explicit(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        _BASE
        + """
report_schedule:
  - command: list_unassigned_issues
    interval_hours: 8
    args: {}
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.report_schedule == (
        ReportScheduleEntry(command="list_unassigned_issues", interval_hours=8, args=()),
    )


def test_report_schedule_new_issues_no_args(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        _BASE
        + """
report_schedule:
  - command: new_issues
    interval_hours: 1
    args:
      foo: bar
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not use args"):
        load_config(p)


def test_report_schedule_unassigned_issues_legacy_no_args(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        _BASE
        + """
report_schedule:
  - command: unassigned_issues
    interval_hours: 1
    args:
      foo: bar
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not use args"):
        load_config(p)
