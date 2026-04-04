from __future__ import annotations

from pathlib import Path

import pytest

from ultron.config import EnvironmentBindings, load_config


def test_load_config_omitted_bindings_use_defaults(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        "timezone: UTC\n"
        "discord: {}\n"
        "reports: {}\n"
        "report_schedule: []\n"
        "logging: {}\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.environment_bindings.discord_token_env == "DISCORD_TOKEN"
    assert cfg.environment_bindings.redmine_api_key_env == "REDMINE_API_KEY"


def test_load_config_partial_bindings_override(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        "timezone: UTC\n"
        "environment_bindings:\n"
        "  redmine_api_key_env: MY_CUSTOM_REDMINE_KEY\n"
        "discord: {}\n"
        "reports: {}\n"
        "report_schedule: []\n"
        "logging: {}\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.environment_bindings.redmine_api_key_env == "MY_CUSTOM_REDMINE_KEY"
    assert cfg.environment_bindings.discord_token_env == "DISCORD_TOKEN"


def test_parse_environment_bindings_rejects_empty_name(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        "timezone: UTC\n"
        "environment_bindings:\n"
        "  discord_token_env: \"\"\n"
        "discord: {}\n"
        "reports: {}\n"
        "report_schedule: []\n"
        "logging: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="discord_token_env"):
        load_config(p)


def test_environment_bindings_frozen_defaults() -> None:
    b = EnvironmentBindings()
    assert b.llm_disabled_env == "LLM_DISABLED"


def test_load_config_redmine_user_aliases(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        "timezone: UTC\n"
        "redmine:\n"
        "  user_id_by_login:\n"
        "    JDoe: 99\n"
        "  time_summary_max_entries: 500\n"
        "discord: {}\n"
        "reports: {}\n"
        "report_schedule: []\n"
        "logging: {}\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.redmine.user_id_by_login["jdoe"] == 99
    assert cfg.redmine.time_summary_max_entries == 500
