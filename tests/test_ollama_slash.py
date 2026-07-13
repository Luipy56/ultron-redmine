from __future__ import annotations

import pytest

from ultron.config import LLMProviderSpec
from ultron.ollama_slash import (
    is_ollama_like_spec,
    load_ol_system_prompt,
    resolve_ol_llm_kwargs,
    resolve_ol_provider_index,
)


def _spec(
    *,
    base_url: str,
    name: str | None = None,
    models: tuple[str, ...] = ("llama3.2",),
) -> LLMProviderSpec:
    return LLMProviderSpec(
        base_url=base_url,
        models=models,
        api_key_env="LLM_API_KEY",
        timeout_seconds=120.0,
        max_retries=0,
        name=name,
        enabled=True,
    )


def test_is_ollama_like_spec_port_and_name() -> None:
    assert is_ollama_like_spec(_spec(base_url="http://127.0.0.1:11434/v1"))
    assert is_ollama_like_spec(_spec(base_url="https://api.openai.com/v1", name="local-ollama"))
    assert not is_ollama_like_spec(_spec(base_url="https://api.openai.com/v1", name="openai"))


def test_resolve_ol_provider_index_prefers_ollama() -> None:
    chain = (
        _spec(base_url="https://api.openai.com/v1", name="openai"),
        _spec(base_url="http://127.0.0.1:11434/v1", name="local-ollama"),
    )
    assert resolve_ol_provider_index(chain, None) == 1


def test_resolve_ol_provider_index_explicit_name() -> None:
    chain = (
        _spec(base_url="https://api.openai.com/v1", name="openai"),
        _spec(base_url="http://127.0.0.1:11434/v1", name="local-ollama"),
    )
    assert resolve_ol_provider_index(chain, "openai") == 0


def test_resolve_ol_llm_kwargs_defaults_to_ollama_slot() -> None:
    chain = (
        _spec(base_url="https://api.openai.com/v1", name="openai"),
        _spec(base_url="http://127.0.0.1:11434/v1", name="local-ollama", models=("gemma",)),
    )
    start, mo, display = resolve_ol_llm_kwargs(chain, None, None, cmd_need_prov=True, cmd_need_model=True)
    assert start == "local-ollama"
    assert mo is None
    assert display == "gemma"


def test_load_ol_system_prompt_bundled() -> None:
    text = load_ol_system_prompt()
    assert "Ultron" in text
    assert "Redmine" in text


def test_resolve_ol_provider_index_empty_chain() -> None:
    with pytest.raises(ValueError, match="No llm_chain"):
        resolve_ol_provider_index((), None)
