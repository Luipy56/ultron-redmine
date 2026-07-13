from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from ultron.amvara.executor import AmvaraAuditResult, AuditAgent, run_amvara_audit
from ultron.amvara.registry import build_amvara_registry
from ultron.config import AmvaraAuditConfig, AmvaraConfig, AppConfig, DiscordConfig, LLMProviderSpec, LoggingConfig, PiConfig, ReportsConfig
from ultron.pi_agent import PiAgentResult


def test_pi_fallback_to_ca_on_failure(tmp_path) -> None:
    from pathlib import Path

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
    app_cfg = AppConfig(
        timezone="UTC",
        discord=DiscordConfig(),
        reports=ReportsConfig(),
        report_schedule=(),
        logging=LoggingConfig(),
        llm_chain=chain,
        pi=PiConfig(enabled=True),
        amvara=AmvaraConfig(
            allowed_hosts=("amvara3",),
            audit=AmvaraAuditConfig(fallback_enabled=True),
            merge_ssh_config=False,
        ),
    )
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
        mock_ca.assert_awaited_once()

    asyncio.run(_run())
