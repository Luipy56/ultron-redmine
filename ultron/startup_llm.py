"""Construct the LLM backend from resolved env + app config (shared by bot startup and ``ultron doctor``)."""

from __future__ import annotations

from dataclasses import dataclass

from ultron.config import AppConfig, LLMProviderResolved, resolve_llm_chain
from ultron.llm import LLMBackend, LLMChainClient, LLMClient, NullLLMBackend
from ultron.settings import EnvSettings


@dataclass(frozen=True)
class BuiltLLM:
    """LLM backend plus optional resolved chain (avoids resolving ``llm_chain`` twice at startup)."""

    backend: LLMBackend
    resolved_chain: tuple[LLMProviderResolved, ...] | None


def build_llm_backend(env: EnvSettings, app_cfg: AppConfig) -> BuiltLLM:
    """Build the same backend the bot would use. May raise ``RuntimeError`` from ``resolve_llm_chain``."""
    if app_cfg.llm_chain is not None:
        resolved = resolve_llm_chain(app_cfg.llm_chain)
        return BuiltLLM(LLMChainClient.from_resolved(resolved), resolved)
    if not env.llm_enabled:
        return BuiltLLM(NullLLMBackend(), None)
    return BuiltLLM(
        LLMClient(
            base_url=env.llm_base_url,
            api_key=env.llm_api_key,
            model=env.llm_model,
            timeout=env.llm_timeout_seconds,
            max_retries=env.llm_max_retries,
        ),
        None,
    )
