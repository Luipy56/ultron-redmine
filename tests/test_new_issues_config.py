from __future__ import annotations

from pathlib import Path

from ultron.config import load_config


def test_discord_new_issues_defaults(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        """
timezone: UTC
discord: {}
reports: {}
report_schedule: []
logging: {}
llm_chain: []
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    ni = cfg.discord.new_issues
    assert ni.status_name == ""
    assert ni.list_limit == 20
    assert ni.min_age_days == 2


def test_discord_new_issues_yaml(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        """
timezone: UTC
discord:
  new_issues:
    status_name: "New/Neu"
    list_limit: 15
    min_age_days: 3
reports: {}
report_schedule: []
logging: {}
llm_chain: []
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    ni = cfg.discord.new_issues
    assert ni.status_name == "New/Neu"
    assert ni.list_limit == 15
    assert ni.min_age_days == 3
