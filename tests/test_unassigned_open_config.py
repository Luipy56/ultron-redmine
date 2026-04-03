from __future__ import annotations

from pathlib import Path

from ultron.config import load_config


def test_unassigned_open_defaults(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        """
timezone: UTC
discord: {}
reports: {}
schedules:
  abandoned: {}
  stale_new: {}
logging: {}
llm_chain: []
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    uo = cfg.discord.unassigned_open
    assert uo.min_age_days == 1
    assert uo.list_limit == 20
    assert uo.closed_status_prefixes == ()


def test_unassigned_open_yaml(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        """
timezone: UTC
discord:
  unassigned_open:
    min_age_days: 2
    list_limit: 10
    closed_status_prefixes:
      - Solved
      - "Comments"
reports: {}
schedules:
  abandoned: {}
  stale_new: {}
logging: {}
llm_chain: []
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    uo = cfg.discord.unassigned_open
    assert uo.min_age_days == 2
    assert uo.list_limit == 10
    assert uo.closed_status_prefixes == ("Solved", "Comments")
