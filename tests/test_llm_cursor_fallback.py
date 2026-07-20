from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from openai import APITimeoutError

from ultron.config import AppConfig, CursorAgentConfig, DiscordConfig, LoggingConfig, ReportsConfig
from ultron.llm import LLMChainExhaustedError, NullLLMBackend
from ultron.llm_cursor_fallback import (
    LLMWithCursorAgentFallback,
    _strip_outer_fence,
    llm_chain_client,
)


def _app(tmp_path: Path, *, llm_fb: bool = True) -> AppConfig:
    return AppConfig(
        timezone="UTC",
        discord=DiscordConfig(),
        reports=ReportsConfig(),
        report_schedule=(),
        logging=LoggingConfig(),
        cursor_agent=CursorAgentConfig(
            enabled=True,
            llm_fallback_enabled=llm_fb,
            llm_fallback_timeout_seconds=60.0,
        ),
    )


def test_strip_outer_fence() -> None:
    assert _strip_outer_fence("hello") == "hello"
    assert _strip_outer_fence("```json\n{\"a\":1}\n```") == '{"a":1}'


def test_llm_chain_client_unwraps_fallback() -> None:
    from ultron.config import LLMProviderResolved
    from ultron.llm import LLMChainClient

    resolved = (
        LLMProviderResolved(
            base_url="http://127.0.0.1:11434/v1",
            models=("m",),
            api_key="ollama",
            timeout_seconds=10.0,
            max_retries=0,
            name="ollama",
        ),
    )
    chain = LLMChainClient.from_resolved(resolved)
    wrapped = LLMWithCursorAgentFallback(
        primary=chain,
        app_cfg=_app(Path("/tmp")),
        state_dir=Path("/tmp"),
        workspace=Path("/tmp"),
        timeout_seconds=60.0,
    )
    assert llm_chain_client(wrapped) is chain
    assert llm_chain_client(chain) is chain
    assert llm_chain_client(NullLLMBackend()) is None


def test_fallback_calls_cursor_agent_on_chain_exhausted(tmp_path: Path) -> None:
    primary = AsyncMock()
    primary.model = "gemma"
    primary.complete = AsyncMock(
        side_effect=LLMChainExhaustedError(provider_count=1, last_error=APITimeoutError("t"))
    )

    app = _app(tmp_path)
    backend = LLMWithCursorAgentFallback(
        primary=primary,
        app_cfg=app,
        state_dir=tmp_path,
        workspace=tmp_path / "ws",
        timeout_seconds=60.0,
    )

    async def _run() -> None:
        with patch(
            "ultron.llm_cursor_fallback.complete_via_cursor_agent",
            new_callable=AsyncMock,
            return_value='{"intent":"chat","message":"ok"}',
        ) as mock_ca:
            out = await backend.complete(system="sys", user="review ticket 7736")
        assert out == '{"intent":"chat","message":"ok"}'
        mock_ca.assert_awaited_once()
        primary.complete.assert_awaited_once()

    asyncio.run(_run())


def test_fallback_disabled_rethrows(tmp_path: Path) -> None:
    primary = AsyncMock()
    primary.model = "gemma"
    primary.complete = AsyncMock(
        side_effect=LLMChainExhaustedError(provider_count=1, last_error=APITimeoutError("t"))
    )
    app = _app(tmp_path, llm_fb=False)
    backend = LLMWithCursorAgentFallback(
        primary=primary,
        app_cfg=app,
        state_dir=tmp_path,
        workspace=tmp_path / "ws",
        timeout_seconds=60.0,
    )

    async def _run() -> None:
        with pytest.raises(LLMChainExhaustedError):
            await backend.complete(system="sys", user="u")

    asyncio.run(_run())


def test_fallback_skips_non_transport_errors(tmp_path: Path) -> None:
    primary = AsyncMock()
    primary.model = "gemma"
    primary.complete = AsyncMock(side_effect=ValueError("bad model"))
    backend = LLMWithCursorAgentFallback(
        primary=primary,
        app_cfg=_app(tmp_path),
        state_dir=tmp_path,
        workspace=tmp_path / "ws",
        timeout_seconds=60.0,
    )

    async def _run() -> None:
        with pytest.raises(ValueError, match="bad model"):
            await backend.complete(system="sys", user="u")

    asyncio.run(_run())
