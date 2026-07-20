from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ultron.amvara.executor import AmvaraAuditResult, AuditAgent, run_amvara_audit
from ultron.amvara.registry import build_amvara_registry
from ultron.config import (
    AmvaraAuditConfig,
    AmvaraConfig,
    AppConfig,
    CursorAgentConfig,
    DiscordConfig,
    LLMProviderSpec,
    LoggingConfig,
    PiConfig,
    ReportsConfig,
)
from ultron.ollama_reachability import OllamaReadiness, REASON_BUSY_PROBE
from ultron.pi_agent import PiAgentResult


def _app_cfg(*, fallback: bool = True, busy_check: bool = True) -> AppConfig:
    chain = (
        LLMProviderSpec(
            base_url="http://127.0.0.1:11434/v1",
            models=("llama",),
            api_key_env="LLM_API_KEY",
            timeout_seconds=120.0,
            max_retries=0,
            name="ollama",
        ),
    )
    return AppConfig(
        timezone="UTC",
        discord=DiscordConfig(),
        reports=ReportsConfig(),
        report_schedule=(),
        logging=LoggingConfig(),
        llm_chain=chain,
        pi=PiConfig(enabled=True, ollama_busy_check=busy_check, ollama_inference_probe_seconds=12.0),
        amvara=AmvaraConfig(
            allowed_hosts=("amvara3",),
            audit=AmvaraAuditConfig(fallback_enabled=fallback),
            merge_ssh_config=False,
        ),
        cursor_agent=CursorAgentConfig(enabled=True),
    )


def test_pi_fallback_to_ca_on_failure(tmp_path) -> None:
    app_cfg = _app_cfg(busy_check=False)
    registry = build_amvara_registry(app_cfg.amvara)

    bad_pi = PiAgentResult(
        session_id="abc",
        exit_code=1,
        stdout="",
        stderr="fail",
        prompt_path=Path("/tmp/p"),
        workspace=tmp_path,
        duration_seconds=1.0,
        model="llama",
        tunnel_started=False,
    )

    good_ca = AmvaraAuditResult(
        host="amvara3",
        agent=AuditAgent.CURSOR_AGENT,
        body="ca ok",
        ok=True,
        fallback_used=True,
    )

    async def _run() -> None:
        with patch("ultron.amvara.executor.pi_availability_message", return_value=None):
            with patch("ultron.amvara.executor._run_pi_audit", new_callable=AsyncMock) as mock_pi:
                with patch("ultron.amvara.executor._run_ca_audit", new_callable=AsyncMock) as mock_ca:
                    mock_pi.return_value = AmvaraAuditResult(
                        host="amvara3",
                        agent=AuditAgent.PI,
                        body="pi fail",
                        ok=False,
                        pi_result=bad_pi,
                    )
                    mock_ca.return_value = good_ca
                    result = await run_amvara_audit(
                        app_cfg=app_cfg,
                        registry=registry,
                        host_name="amvara3",
                        task="check ram",
                        state_dir=tmp_path,
                    )
        assert result.fallback_used is True
        assert result.agent == AuditAgent.CURSOR_AGENT
        assert result.fallback_reason == "pi_failed"
        mock_ca.assert_awaited_once()
        mock_pi.assert_awaited_once()

    asyncio.run(_run())


def test_ollama_busy_skips_pi_to_ca(tmp_path) -> None:
    app_cfg = _app_cfg(busy_check=True)
    registry = build_amvara_registry(app_cfg.amvara)
    good_ca = AmvaraAuditResult(
        host="amvara3",
        agent=AuditAgent.CURSOR_AGENT,
        body="ca ok",
        ok=True,
    )

    async def _run() -> None:
        with patch("ultron.amvara.executor.pi_availability_message", return_value=None):
            with patch(
                "ultron.amvara.executor._probe_ollama_for_amvara_pi",
                new_callable=AsyncMock,
                return_value=OllamaReadiness(ok=False, reason=REASON_BUSY_PROBE),
            ):
                with patch("ultron.amvara.executor._run_pi_audit", new_callable=AsyncMock) as mock_pi:
                    with patch("ultron.amvara.executor._run_ca_audit", new_callable=AsyncMock) as mock_ca:
                        mock_ca.return_value = good_ca
                        result = await run_amvara_audit(
                            app_cfg=app_cfg,
                            registry=registry,
                            host_name="amvara3",
                            task="check ram",
                            state_dir=tmp_path,
                        )
        assert result.fallback_used is True
        assert result.fallback_reason == REASON_BUSY_PROBE
        assert result.agent == AuditAgent.CURSOR_AGENT
        mock_pi.assert_not_awaited()
        mock_ca.assert_awaited_once()

    asyncio.run(_run())


def test_ollama_busy_raises_when_fallback_disabled(tmp_path) -> None:
    app_cfg = _app_cfg(fallback=False, busy_check=True)
    registry = build_amvara_registry(app_cfg.amvara)

    async def _run() -> None:
        with patch("ultron.amvara.executor.pi_availability_message", return_value=None):
            with patch(
                "ultron.amvara.executor._probe_ollama_for_amvara_pi",
                new_callable=AsyncMock,
                return_value=OllamaReadiness(ok=False, reason=REASON_BUSY_PROBE),
            ):
                with patch("ultron.amvara.executor._run_ca_audit", new_callable=AsyncMock) as mock_ca:
                    raised = False
                    try:
                        await run_amvara_audit(
                            app_cfg=app_cfg,
                            registry=registry,
                            host_name="amvara3",
                            task="check ram",
                            state_dir=tmp_path,
                        )
                    except RuntimeError as e:
                        raised = True
                        assert "not ready" in str(e).casefold()
        assert raised is True
        mock_ca.assert_not_awaited()

    asyncio.run(_run())
