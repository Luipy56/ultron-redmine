from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import discord

from ultron.config import AppConfig
from ultron.discord_interaction_errors import is_unknown_interaction_error
from ultron.sanitize import sanitize_for_discord
from ultron.textutil import chunk_discord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedbackReport:
    title: str
    body: str
    kind: Literal["self_upgrade", "self_repair", "info"] = "info"


async def send_feedback(
    bot: discord.Client,
    app_cfg: AppConfig,
    report: FeedbackReport,
    *,
    interaction: discord.Interaction | None = None,
    secret_literals: list[str] | None = None,
) -> None:
    """Post a sanitized summary to Discord (reports channel and/or slash followup)."""
    literals = list(secret_literals or [])
    safe_title = sanitize_for_discord(report.title, secret_literals=literals)
    safe_body = sanitize_for_discord(report.body, secret_literals=literals)
    message = f"**{safe_title}**\n\n{safe_body}".strip()
    chunks = chunk_discord(message)

    channel = await _resolve_feedback_channel(bot, app_cfg)
    if channel is not None:
        for part in chunks:
            try:
                await channel.send(part[:2000])
            except discord.HTTPException as e:
                logger.warning("send_feedback channel send failed: %s", e)
                break
        return

    if interaction is not None and interaction.response.is_done():
        delivered = await _send_interaction_chunks(interaction, chunks)
        if not delivered:
            await _send_channel_fallback(bot, interaction, chunks)
    elif report.kind in ("self_upgrade", "self_repair"):
        logger.warning(
            "send_feedback: reports.channel_id unset and no interaction; report logged only: %s",
            safe_title,
        )


async def _send_interaction_chunks(
    interaction: discord.Interaction,
    chunks: list[str],
) -> bool:
    for part in chunks:
        try:
            await interaction.followup.send(part[:2000], ephemeral=False)
        except discord.HTTPException as e:
            if is_unknown_interaction_error(e) or e.status == 401 or getattr(e, "code", None) == 50027:
                logger.warning("send_feedback followup failed (expired token): %s", e)
                return False
            logger.warning("send_feedback followup failed: %s", e)
            return True
    return True


async def _send_channel_fallback(
    bot: discord.Client,
    interaction: discord.Interaction,
    chunks: list[str],
) -> None:
    ch = interaction.channel
    if ch is None or not isinstance(ch, discord.abc.Messageable):
        logger.warning("send_feedback: no channel fallback for expired interaction token")
        return
    mention = interaction.user.mention if interaction.guild is not None else ""
    for i, part in enumerate(chunks):
        text = part[:2000]
        if i == 0 and mention:
            text = f"{mention} {text}"
        try:
            await ch.send(text)
        except discord.HTTPException as e:
            logger.warning("send_feedback channel fallback failed: %s", e)
            break


async def _resolve_feedback_channel(
    bot: discord.Client,
    app_cfg: AppConfig,
) -> discord.abc.Messageable | None:
    cid = app_cfg.reports.channel_id
    if not cid:
        return None
    ch = bot.get_channel(cid)
    if ch is None:
        try:
            ch = await bot.fetch_channel(cid)
        except discord.HTTPException as e:
            logger.warning("fetch feedback channel %s failed: %s", cid, e)
            return None
    if isinstance(ch, discord.abc.Messageable):
        return ch
    return None
