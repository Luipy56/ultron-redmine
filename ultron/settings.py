from __future__ import annotations

import os
from dataclasses import dataclass

# Default HTTP read timeout for LLM calls (15 min). Override with LLM_TIMEOUT_SECONDS.
_DEFAULT_LLM_TIMEOUT_SECONDS = 900.0


@dataclass(frozen=True)
class EnvSettings:
    discord_token: str
    discord_guild_id: int | None
    discord_application_id: int | None
    redmine_url: str
    redmine_api_key: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: float
    llm_max_retries: int
    config_path: str


def _opt_int(name: str) -> int | None:
    v = os.environ.get(name, "").strip()
    if not v:
        return None
    return int(v)


def _opt_float(name: str) -> float | None:
    v = os.environ.get(name, "").strip()
    if not v:
        return None
    return float(v)


def _is_local_ollama(llm_base: str, ollama_api_base_raw: str) -> bool:
    if ollama_api_base_raw.strip():
        return True
    return ":11434" in llm_base


def _ollama_openai_base(api_base: str) -> str:
    """Ollama OpenAI-compatible API lives at {host}/v1."""
    b = api_base.strip().rstrip("/")
    return b if b.endswith("/v1") else f"{b}/v1"


def load_env() -> EnvSettings:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required")

    redmine_url = os.environ.get("REDMINE_URL", "").strip().rstrip("/")
    if not redmine_url:
        raise RuntimeError("REDMINE_URL is required")

    redmine_key = os.environ.get("REDMINE_API_KEY", "").strip()
    if not redmine_key:
        raise RuntimeError("REDMINE_API_KEY is required")

    ollama_api_base = os.environ.get("OLLAMA_API_BASE", "").strip()
    llm_base = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")
    if not llm_base and ollama_api_base:
        llm_base = _ollama_openai_base(ollama_api_base)
    if not llm_base:
        llm_base = "https://api.openai.com/v1"

    llm_key = os.environ.get("LLM_API_KEY", "").strip()
    if not llm_key and ollama_api_base:
        llm_key = "ollama"
    if not llm_key and ":11434" in llm_base:
        llm_key = "ollama"
    if not llm_key:
        raise RuntimeError("LLM_API_KEY is required (or set OLLAMA_API_BASE for local Ollama)")

    llm_model = os.environ.get("LLM_MODEL", "").strip()
    if not llm_model:
        llm_model = os.environ.get("OLLAMA_MODEL", "").strip()
    if not llm_model:
        llm_model = "gpt-4o-mini"
    config_path = os.environ.get("CONFIG_PATH", "config.yaml").strip()

    ollama_raw = os.environ.get("OLLAMA_API_BASE", "")
    local_ollama = _is_local_ollama(llm_base, ollama_raw)
    timeout_override = _opt_float("LLM_TIMEOUT_SECONDS")
    llm_timeout = (
        timeout_override if timeout_override is not None else _DEFAULT_LLM_TIMEOUT_SECONDS
    )
    retries_override = _opt_int("LLM_MAX_RETRIES")
    llm_max_retries = (
        retries_override
        if retries_override is not None
        else (0 if local_ollama else 2)
    )

    return EnvSettings(
        discord_token=token,
        discord_guild_id=_opt_int("DISCORD_GUILD_ID"),
        discord_application_id=_opt_int("DISCORD_APPLICATION_ID"),
        redmine_url=redmine_url,
        redmine_api_key=redmine_key,
        llm_base_url=llm_base,
        llm_api_key=llm_key,
        llm_model=llm_model,
        llm_timeout_seconds=llm_timeout,
        llm_max_retries=llm_max_retries,
        config_path=config_path,
    )
