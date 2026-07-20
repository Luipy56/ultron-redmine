"""Construct the LLM backend from resolved env + app config (shared by bot startup and ``ultron doctor``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ultron.config import AppConfig, LLMProviderResolved, resolve_llm_chain
from ultron.llm import LLMBackend, LLMChainClient, NullLLMBackend
from ultron.llm_cursor_fallback import LLMWithCursorAgentFallback
from ultron.settings import EnvSettings


@dataclass(frozen=True)
class BuiltLLM:
    """LLM backend plus optional resolved chain (avoids resolving ``llm_chain`` twice at startup)."""

    backend: LLMBackend
    resolved_chain: tuple[LLMProviderResolved, ...] | None


def build_llm_backend(env: EnvSettings, app_cfg: AppConfig) -> BuiltLLM:
    """Build the same backend the bot would use. May raise ``RuntimeError`` from ``resolve_llm_chain``."""
    resolved: tuple[LLMProviderResolved, ...] | None = None
    if app_cfg.llm_chain is not None:
        resolved = resolve_llm_chain(app_cfg.llm_chain)
        primary: LLMBackend = LLMChainClient.from_resolved(resolved)
    else:
        primary = NullLLMBackend()

    ca = app_cfg.cursor_agent
    if ca.enabled and ca.llm_fallback_enabled:
        repo = env.ultron_project_root.resolve()
        ws_raw = ca.workspace.strip()
        base_ws = Path(ws_raw).expanduser().resolve() if ws_raw else (env.state_dir / "cursor-agent-llm")
        backend: LLMBackend = LLMWithCursorAgentFallback(
            primary=primary,
            app_cfg=app_cfg,
            state_dir=env.state_dir.resolve(),
            workspace=base_ws if base_ws.is_absolute() else (repo / base_ws).resolve(),
            timeout_seconds=float(ca.llm_fallback_timeout_seconds),
        )
        return BuiltLLM(backend, resolved)

    return BuiltLLM(primary, resolved)
