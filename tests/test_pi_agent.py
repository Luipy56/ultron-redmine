from __future__ import annotations

from pathlib import Path

import pytest

from ultron.config import AppConfig, DiscordConfig, LLMProviderSpec, LoggingConfig, PiConfig, ReportsConfig
from ultron.ollama_reachability import ollama_openai_base_url, ollama_root_url
from ultron.pi_resolve import pi_availability_message, resolve_pi_bin


def _minimal_app(*, llm_chain: tuple[LLMProviderSpec, ...] | None = None, pi: PiConfig | None = None) -> AppConfig:
    return AppConfig(
        timezone="UTC",
        discord=DiscordConfig(),
        reports=ReportsConfig(),
        report_schedule=(),
        logging=LoggingConfig(),
        llm_chain=llm_chain,
        pi=pi or PiConfig(),
    )


def _ollama_spec() -> LLMProviderSpec:
    return LLMProviderSpec(
        base_url="http://127.0.0.1:11434/v1",
        models=("llama3.2",),
        api_key_env="LLM_API_KEY",
        timeout_seconds=120.0,
        max_retries=0,
        name="local-ollama",
        enabled=True,
    )


def test_ollama_url_helpers() -> None:
    assert ollama_root_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert ollama_openai_base_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434/v1"


def test_pi_unavailable_without_chain(tmp_path: Path) -> None:
    msg = pi_availability_message(_minimal_app(), repo_root=tmp_path)
    assert msg is not None
    assert "llm_chain" in msg


def test_pi_unavailable_without_npm(tmp_path: Path) -> None:
    app = _minimal_app(llm_chain=(_ollama_spec(),))
    msg = pi_availability_message(app, repo_root=tmp_path)
    assert msg is not None
    assert "pi binary" in msg or "npm install" in msg


def test_resolve_pi_bin_missing(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="pi binary not found"):
        resolve_pi_bin(repo_root=tmp_path, bin_path_cfg="")


def test_pi_disabled_explicitly(tmp_path: Path) -> None:
    app = _minimal_app(llm_chain=(_ollama_spec(),), pi=PiConfig(enabled=False))
    msg = pi_availability_message(app, repo_root=tmp_path)
    assert msg is not None
    assert "disabled" in msg.lower()
