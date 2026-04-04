from __future__ import annotations

from pathlib import Path

import pytest


_MINIMAL = """\
timezone: UTC
environment_bindings:
  redmine_api_key_env: MY_REDMINE_KEY
discord: {}
reports: {}
report_schedule: []
logging: {}
"""


def test_load_env_uses_redmine_key_from_custom_binding_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(_MINIMAL, encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("REDMINE_URL", "https://rm.example")
    monkeypatch.setenv("MY_REDMINE_KEY", "secret-from-custom-name")
    monkeypatch.delenv("REDMINE_API_KEY", raising=False)

    from ultron.settings import load_env

    env = load_env()
    assert env.redmine_api_key == "secret-from-custom-name"
    assert env.environment_bindings.redmine_api_key_env == "MY_REDMINE_KEY"


def test_load_env_fails_when_config_file_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "nope.yaml"))

    from ultron.settings import load_env

    with pytest.raises(RuntimeError, match="Config file not found"):
        load_env()


def test_load_env_without_discord_when_not_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        """\
timezone: UTC
discord: {}
reports: {}
report_schedule: []
logging: {}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("REDMINE_URL", "https://rm.example")
    monkeypatch.setenv("REDMINE_API_KEY", "key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    from ultron.settings import load_env

    env = load_env(require_discord=False)
    assert env.discord_token == ""
    assert env.redmine_url == "https://rm.example"
