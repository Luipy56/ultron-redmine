from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_MINIMAL = """\
timezone: UTC
discord: {}
reports: {}
report_schedule: []
logging: {}
"""


def test_doctor_fails_when_config_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "missing.yaml"))
    from ultron.doctor import run_doctor

    assert run_doctor() == 1


def test_doctor_ok_without_discord_redmine_or_llm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_MINIMAL, encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    for k in (
        "DISCORD_TOKEN",
        "REDMINE_URL",
        "REDMINE_API_KEY",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "OLLAMA_API_BASE",
    ):
        monkeypatch.delenv(k, raising=False)

    from ultron.doctor import run_doctor

    assert run_doctor() == 0


def test_doctor_redmine_fail_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_MINIMAL, encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("REDMINE_URL", "https://redmine.example")
    monkeypatch.setenv("REDMINE_API_KEY", "k")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    from ultron.redmine import RedmineError

    monkeypatch.setattr(
        "ultron.doctor.RedmineClient.verify_connection",
        AsyncMock(side_effect=RedmineError("test failure")),
    )

    from ultron.doctor import run_doctor

    assert run_doctor() == 1


def test_doctor_redmine_ok_mocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_MINIMAL, encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("REDMINE_URL", "https://redmine.example")
    monkeypatch.setenv("REDMINE_API_KEY", "k")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    monkeypatch.setattr("ultron.doctor.RedmineClient.verify_connection", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "ultron.doctor.RedmineClient.fetch_current_user_label",
        AsyncMock(return_value="alice"),
    )

    from ultron.doctor import run_doctor

    assert run_doctor() == 0


def test_load_env_without_discord_or_redmine_when_optional(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_MINIMAL, encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("REDMINE_URL", raising=False)
    monkeypatch.delenv("REDMINE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    from ultron.settings import load_env

    env = load_env(require_discord=False, require_redmine=False)
    assert env.discord_token == ""
    assert env.redmine_url == ""
    assert env.redmine_api_key == ""
