"""cursor-agent as last-resort chat completion when ``llm_chain`` fails."""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from openai import APIConnectionError, APIStatusError, APITimeoutError

from ultron.config import AppConfig
from ultron.cursor_agent import CursorAgentProfile, call_cursor_agent_session
from ultron.llm import (
    ChainSkipCallback,
    ChainSkipNotice,
    LLMBackend,
    LLMChainClient,
    LLMChainExhaustedError,
    NoLLMConfiguredError,
    chain_skip_user_reason,
)

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_FENCE_RE = re.compile(r"^```(?:\w+)?\s*\n([\s\S]*?)\n```\s*$")


def llm_chain_client(llm: LLMBackend) -> LLMChainClient | None:
    """Return the underlying ``LLMChainClient`` if ``llm`` is or wraps one."""
    if isinstance(llm, LLMChainClient):
        return llm
    if isinstance(llm, LLMWithCursorAgentFallback):
        return llm_chain_client(llm.primary)
    return None


def _strip_outer_fence(text: str) -> str:
    t = (text or "").strip()
    m = _FENCE_RE.match(t)
    if m:
        return m.group(1).strip()
    return t


def _should_try_cursor_fallback(exc: BaseException) -> bool:
    if isinstance(exc, (LLMChainExhaustedError, NoLLMConfiguredError)):
        return True
    if isinstance(exc, (APITimeoutError, APIConnectionError, APIStatusError)):
        return True
    return False


@dataclass
class LLMWithCursorAgentFallback:
    """Try ``primary`` (usually ``LLMChainClient``); on transport/chain failure use cursor-agent."""

    primary: LLMBackend
    app_cfg: AppConfig
    state_dir: Path
    workspace: Path
    timeout_seconds: float

    @property
    def model(self) -> str:
        return self.primary.model

    def display_model_for_start(self, start_provider: str | None) -> str:
        chain = llm_chain_client(self.primary)
        if chain is not None:
            return chain.display_model_for_start(start_provider)
        return self.model

    async def complete(
        self,
        *,
        system: str,
        user: str,
        on_chain_skip: ChainSkipCallback | None = None,
        start_provider: str | None = None,
        model_override: str | None = None,
    ) -> str:
        try:
            return await self.primary.complete(
                system=system,
                user=user,
                on_chain_skip=on_chain_skip,
                start_provider=start_provider,
                model_override=model_override,
            )
        except BaseException as e:
            if not _should_try_cursor_fallback(e):
                raise
            if not self.app_cfg.cursor_agent.enabled:
                raise
            if not self.app_cfg.cursor_agent.llm_fallback_enabled:
                raise

            reason = chain_skip_user_reason(e) if not isinstance(e, NoLLMConfiguredError) else "no LLM configured"
            if isinstance(e, LLMChainExhaustedError):
                reason = chain_skip_user_reason(e.last_error)
            logger.warning(
                "llm_chain failed (%s: %s); falling back to cursor-agent for chat completion",
                type(e).__name__,
                reason,
            )
            if on_chain_skip is not None:
                from_model = self.primary.model
                try:
                    await on_chain_skip(
                        ChainSkipNotice(
                            from_entry="llm_chain",
                            from_model=from_model,
                            to_entry="cursor-agent",
                            to_model="cursor-agent",
                            reason=f"{reason}; using cursor-agent",
                        )
                    )
                except Exception:
                    logger.debug("on_chain_skip for cursor-agent fallback failed", exc_info=True)

            return await complete_via_cursor_agent(
                app_cfg=self.app_cfg,
                state_dir=self.state_dir,
                workspace=self.workspace,
                system=system,
                user=user,
                timeout_seconds=self.timeout_seconds,
            )


async def complete_via_cursor_agent(
    *,
    app_cfg: AppConfig,
    state_dir: Path,
    workspace: Path,
    system: str,
    user: str,
    timeout_seconds: float,
) -> str:
    """Run cursor-agent as a text-only completion backend (no file/shell work intended)."""
    prompt_path = _PROMPTS_DIR / "ca-llm-fallback.md"
    if not prompt_path.is_file():
        raise RuntimeError(f"cursor-agent LLM fallback prompt missing: {prompt_path}")

    workspace.mkdir(parents=True, exist_ok=True)
    # Ephemeral empty dir so --yolo has nothing useful to mutate.
    run_ws = Path(tempfile.mkdtemp(prefix="ca-llm-", dir=str(workspace)))
    profile = CursorAgentProfile(
        name="llm-fallback",
        workspace=run_ws,
        prompt_path=prompt_path,
        log_prefix="cursor-agent-llm",
    )
    user_request = (
        "Produce the assistant completion for this chat-completion request.\n\n"
        "### System\n\n"
        f"{system.strip()}\n\n"
        "### User\n\n"
        f"{user.strip()}"
    )
    try:
        result = await call_cursor_agent_session(
            app_cfg=app_cfg,
            profile=profile,
            state_dir=state_dir,
            user_request=user_request,
            timeout_seconds=timeout_seconds,
        )
    finally:
        try:
            # Best-effort cleanup of empty temp workspace.
            for child in run_ws.iterdir():
                if child.is_file():
                    child.unlink(missing_ok=True)
            run_ws.rmdir()
        except OSError:
            pass

    text = _strip_outer_fence(result.stdout or "")
    if not text and result.stderr.strip():
        text = _strip_outer_fence(result.stderr)
    if not text:
        raise RuntimeError(
            f"cursor-agent LLM fallback returned no text (exit {result.exit_code}, "
            f"session {result.session_id})"
        )
    if not result.ok:
        logger.warning(
            "cursor-agent LLM fallback exit=%s session=%s; using stdout anyway (%s chars)",
            result.exit_code,
            result.session_id,
            len(text),
        )
    logger.info(
        "LLM OK | backend=cursor-agent-fallback | session=%s | response_chars=%s",
        result.session_id,
        len(text),
    )
    return text
