"""`/ol` — direct Ollama / llm_chain Q&A with an Ultron-specific advisor prompt."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ultron.config import (
    LLMProviderSpec,
    llm_chain_resolve_start_index,
    llm_chain_slash_model_override,
)
from ultron.llm import ChainSkipCallback, LLMBackend, LLMChainExhaustedError, NoLLMConfiguredError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT_REL = Path("prompts") / "ollama-advisor.md"
ProgressCallback = Callable[[str], Awaitable[None]]


def is_ollama_like_spec(spec: LLMProviderSpec) -> bool:
    """True when a chain entry likely points at a local Ollama server."""
    url = spec.base_url.lower()
    if ":11434" in url:
        return True
    if spec.name and "ollama" in spec.name.strip().lower():
        return True
    return False


def resolve_ol_provider_index(
    specs: tuple[LLMProviderSpec, ...],
    provider: str | None,
) -> int:
    """Pick the llm_chain index for `/ol`.

    Explicit ``provider`` (name or slot) wins; otherwise prefer the first Ollama-like entry;
    fall back to index 0.
    """
    if not specs:
        raise ValueError("No llm_chain providers configured.")
    if provider is not None and str(provider).strip():
        return llm_chain_resolve_start_index(specs, provider)
    for i, spec in enumerate(specs):
        if is_ollama_like_spec(spec):
            return i
    return 0


def provider_token_for_index(specs: tuple[LLMProviderSpec, ...], idx: int) -> str:
    """Token for ``LLMChainClient.complete(start_provider=...)``."""
    spec = specs[idx]
    if spec.name and spec.name.strip():
        return spec.name.strip()
    return str(idx)


def load_ol_system_prompt(*, prompt_path: str | None = None) -> str:
    """Load the bundled advisor prompt or an operator override path."""
    if prompt_path and str(prompt_path).strip():
        p = Path(prompt_path).expanduser()
        if not p.is_file():
            raise RuntimeError(f"Ollama advisor prompt file not found: {p}")
        return p.read_text(encoding="utf-8").strip()

    here = Path(__file__).resolve().parent
    candidate = here / _DEFAULT_PROMPT_REL
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8").strip()
    raise RuntimeError(f"Ollama advisor prompt not found: {candidate}")


def resolve_ol_llm_kwargs(
    chain: tuple[LLMProviderSpec, ...],
    llm_provider: str | None,
    llm_model: str | None,
    *,
    cmd_need_prov: bool,
    cmd_need_model: bool,
) -> tuple[str, str | None, str]:
    """Return ``(start_provider, model_override, display_model)`` for ``/ol``."""
    prov = (llm_provider or "").strip() or None
    model = (llm_model or "").strip() or None

    if cmd_need_prov and prov:
        start_idx = llm_chain_resolve_start_index(chain, prov)
    else:
        start_idx = resolve_ol_provider_index(chain, prov)

    mo, display = llm_chain_slash_model_override(
        chain,
        start_idx,
        model,
        command_includes_model_option=cmd_need_model,
    )
    start_provider = provider_token_for_index(chain, start_idx)
    return start_provider, mo, display


async def run_ol_advisor(
    *,
    llm: LLMBackend,
    chain: tuple[LLMProviderSpec, ...],
    user_text: str,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    cmd_need_prov: bool = False,
    cmd_need_model: bool = False,
    session_context: str | None = None,
    on_chain_skip: ChainSkipCallback | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[str, str]:
    """Call the configured LLM with the advisor prompt. Returns ``(display_model, body)``."""
    task = user_text.strip()
    if not task:
        raise ValueError("text must not be empty")

    start_provider, model_override, display_model = resolve_ol_llm_kwargs(
        chain,
        llm_provider,
        llm_model,
        cmd_need_prov=cmd_need_prov,
        cmd_need_model=cmd_need_model,
    )

    system = load_ol_system_prompt()
    user_parts = [task]
    if session_context and session_context.strip():
        user_parts.extend(["", "### Session context", "", session_context.strip()])
    user_msg = "\n".join(user_parts)

    if on_progress is not None:
        await on_progress(f"Asking **{display_model}**…")

    try:
        body = await llm.complete(
            system=system,
            user=user_msg,
            start_provider=start_provider,
            model_override=model_override,
            on_chain_skip=on_chain_skip,
        )
    except NoLLMConfiguredError:
        raise
    except LLMChainExhaustedError as e:
        raise RuntimeError(
            "All configured language model providers failed for `/ol`. "
            "Check **llm_chain** in `config.yaml` and bot logs."
        ) from e

    text = (body or "").strip()
    if not text:
        text = "The model returned an empty response."
    logger.info(
        "ol advisor OK | start_provider=%r | display_model=%r | response_chars=%s",
        start_provider,
        display_model,
        len(text),
    )
    return display_model, text


def format_ol_reply(*, display_model: str, body: str) -> str:
    """Discord header + model body."""
    return f"**Ollama advisor** · `{display_model}`\n\n{body}"
