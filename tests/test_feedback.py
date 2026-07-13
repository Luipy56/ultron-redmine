from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from ultron.config import AppConfig, DiscordConfig, LoggingConfig, ReportsConfig
from ultron.feedback import FeedbackReport, send_feedback


def test_send_feedback_uses_reports_channel() -> None:
    app_cfg = AppConfig(
        timezone="UTC",
        discord=DiscordConfig(),
        reports=ReportsConfig(channel_id=12345),
        report_schedule=(),
        logging=LoggingConfig(),
    )
    bot = MagicMock()
    channel = MagicMock(spec=discord.abc.Messageable)
    channel.send = AsyncMock()

    async def _run() -> None:
        with patch("ultron.feedback._resolve_feedback_channel", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = channel
            await send_feedback(
                bot,
                app_cfg,
                FeedbackReport(title="Test", body="Hello", kind="info"),
            )

    asyncio.run(_run())
    channel.send.assert_awaited()


def test_feedback_report_kinds() -> None:
    r = FeedbackReport(title="T", body="B", kind="self_upgrade")
    assert r.kind == "self_upgrade"
