from __future__ import annotations

import os
from dataclasses import dataclass


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
    config_path: str


def _opt_int(name: str) -> int | None:
    v = os.environ.get(name, "").strip()
    if not v:
        return None
    return int(v)


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

    return EnvSettings(
        discord_token=token,
        discord_guild_id=_opt_int("DISCORD_GUILD_ID"),
        discord_application_id=_opt_int("DISCORD_APPLICATION_ID"),
        redmine_url=redmine_url,
        redmine_api_key=redmine_key,
        llm_base_url=llm_base,
        llm_api_key=llm_key,
        llm_model=llm_model,
        config_path=config_path,
    )
