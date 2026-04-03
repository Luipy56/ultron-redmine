from __future__ import annotations

from pathlib import Path

import pytest

_MINIMAL_NO_LLM = """\
timezone: UTC
discord: {}
reports: {}
schedules:
  abandoned: {}
  stale_new: {}
logging: {}
"""


@pytest.fixture
def minimal_config(tmp_path: Path) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(_MINIMAL_NO_LLM, encoding="utf-8")
    return p


def test_load_env_no_llm_without_api_key(monkeypatch: pytest.MonkeyPatch, minimal_config: Path) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("REDMINE_URL", "https://redmine.example")
    monkeypatch.setenv("REDMINE_API_KEY", "k")
    monkeypatch.setenv("CONFIG_PATH", str(minimal_config))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
    monkeypatch.delenv("LLM_DISABLED", raising=False)
    monkeypatch.delenv("ULTRON_NO_LLM", raising=False)

    from ultron.settings import load_env

    env = load_env()
    assert env.llm_enabled is False
    assert env.llm_model == "(none)"
    assert env.llm_api_key == ""


def test_load_env_llm_disabled_conflicts_with_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = _MINIMAL_NO_LLM + (
        "llm_chain:\n"
        "  - enabled: true\n"
        "    base_url: https://example.com/v1\n"
        "    model: m\n"
        "    api_key_env: SOME_KEY\n"
    )
    p = tmp_path / "c.yaml"
    p.write_text(body, encoding="utf-8")
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("REDMINE_URL", "https://r")
    monkeypatch.setenv("REDMINE_API_KEY", "k")
    monkeypatch.setenv("CONFIG_PATH", str(p))
    monkeypatch.setenv("LLM_DISABLED", "1")

    from ultron.settings import load_env

    with pytest.raises(RuntimeError, match="llm_chain"):
        load_env()


def test_load_env_explicit_llm_disabled(monkeypatch: pytest.MonkeyPatch, minimal_config: Path) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("REDMINE_URL", "https://redmine.example")
    monkeypatch.setenv("REDMINE_API_KEY", "k")
    monkeypatch.setenv("CONFIG_PATH", str(minimal_config))
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_DISABLED", "true")

    from ultron.settings import load_env

    env = load_env()
    assert env.llm_enabled is False
