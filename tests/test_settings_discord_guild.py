from __future__ import annotations

from pathlib import Path

import pytest

_MINIMAL = """\
timezone: UTC
discord: {}
reports: {}
report_schedule: []
logging: {}
"""


@pytest.fixture
def minimal_config(tmp_path: Path) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(_MINIMAL, encoding="utf-8")
    return p


def _base_env(monkeypatch: pytest.MonkeyPatch, cfg: Path) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("REDMINE_URL", "https://redmine.example")
    monkeypatch.setenv("REDMINE_API_KEY", "k")
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_DISABLED", raising=False)
    monkeypatch.delenv("ULTRON_NO_LLM", raising=False)


def test_discord_guild_defaults_to_team_id_when_unset(
    monkeypatch: pytest.MonkeyPatch, minimal_config: Path
) -> None:
    _base_env(monkeypatch, minimal_config)
    monkeypatch.delenv("DISCORD_GUILD_ID", raising=False)

    from ultron.settings import _DEFAULT_DISCORD_GUILD_SLASH_SYNC_ID, load_env

    env = load_env()
    assert env.discord_guild_id == _DEFAULT_DISCORD_GUILD_SLASH_SYNC_ID


def test_discord_guild_none_when_zero(monkeypatch: pytest.MonkeyPatch, minimal_config: Path) -> None:
    _base_env(monkeypatch, minimal_config)
    monkeypatch.setenv("DISCORD_GUILD_ID", "0")

    from ultron.settings import load_env

    env = load_env()
    assert env.discord_guild_id is None


def test_discord_guild_none_when_global_keyword(
    monkeypatch: pytest.MonkeyPatch, minimal_config: Path
) -> None:
    _base_env(monkeypatch, minimal_config)
    monkeypatch.setenv("DISCORD_GUILD_ID", "global")

    from ultron.settings import load_env

    env = load_env()
    assert env.discord_guild_id is None


def test_discord_guild_explicit_id(monkeypatch: pytest.MonkeyPatch, minimal_config: Path) -> None:
    _base_env(monkeypatch, minimal_config)
    monkeypatch.setenv("DISCORD_GUILD_ID", "111222333444555666")

    from ultron.settings import load_env

    env = load_env()
    assert env.discord_guild_id == 111222333444555666
