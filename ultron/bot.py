from __future__ import annotations

import logging
import time

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks
from openai import APITimeoutError

from ultron.config import AppConfig
from ultron.jobs import run_abandoned_report, run_stale_new_report
from ultron.llm import LLMClient
from ultron.redmine import IssueNotFound, RedmineClient, RedmineError
from ultron.settings import EnvSettings
from ultron.textutil import chunk_discord
from ultron.workflows import add_formatted_note, summarize_issue

logger = logging.getLogger(__name__)
cmd_log = logging.getLogger("ultron.commands")

_TIMEOUT_USER_MSG = (
    "The language model did not respond in time. Try a shorter ticket, a faster model, "
    "or increase **LLM_TIMEOUT_SECONDS** in the bot environment."
)
# User-facing window when slash replies may stop updating (team policy; Discord API limits vary).
_DISCORD_SESSION_MINUTES = 4
_SESSION_EXPIRED_HINT = (
    f"\n\n_Your slash-command message could not be updated (Discord session expired after ~{_DISCORD_SESSION_MINUTES} minutes). "
    "Ollama may still have been loading the model or inferring; check server logs and raise **LLM_TIMEOUT_SECONDS** if needed._"
)

_DISCORD_MSG_MAX = 2000
# Initial /note status (avoid defer "thinking…" when responses are public)
_NOTE_PROCESSING_TEMPLATE = "Processing note with model {model}…"


def _trunc(text: str) -> str:
    return text if len(text) <= _DISCORD_MSG_MAX else text[:_DISCORD_MSG_MAX]


async def _notify_if_interaction_dead(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool,
) -> None:
    """Last resort when edit_original and followup both fail (expired webhook token)."""
    base = _trunc(content)
    extra = _SESSION_EXPIRED_HINT
    if len(base) + len(extra) <= _DISCORD_MSG_MAX:
        full = base + extra
    else:
        full = _trunc(base + extra)

    async def dm_user() -> bool:
        try:
            await interaction.user.send(full)
            logger.info("Delivered message via DM after expired interaction token (user %s)", interaction.user.id)
            return True
        except discord.HTTPException as e:
            logger.warning("DM fallback after dead token failed: %s", e)
            return False

    async def channel_send() -> bool:
        ch = interaction.channel
        if ch is None or not isinstance(ch, discord.abc.Messageable):
            return False
        try:
            if interaction.guild is not None:
                await ch.send(f"{interaction.user.mention} {full}")
            else:
                await ch.send(full)
            logger.info("Delivered message via channel after expired interaction token (user %s)", interaction.user.id)
            return True
        except discord.HTTPException as e:
            logger.warning("channel fallback after dead token failed: %s", e)
            return False

    if ephemeral:
        if await dm_user():
            return
        if await channel_send():
            return
    else:
        if await channel_send():
            return
        if await dm_user():
            return
    logger.error("Could not reach user after expired token (exhausted DM and channel)")


async def _edit_or_followup(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool,
) -> None:
    """Edit the interaction's first message (defer or send_message), or follow-up if token invalid."""
    text = _trunc(content)
    try:
        await interaction.edit_original_response(content=text)
    except discord.HTTPException as e:
        code = getattr(e, "code", None)
        if e.status == 401 or code == 50027:
            logger.warning(
                "edit_original_response failed (interaction token often expires after ~%s min; slash replies may fail): %s",
                _DISCORD_SESSION_MINUTES,
                e,
            )
            try:
                await interaction.followup.send(text, ephemeral=ephemeral)
            except discord.HTTPException as e2:
                code2 = getattr(e2, "code", None)
                logger.error("followup also failed; trying channel/DM fallback: %s", e2)
                if e2.status == 401 or code2 == 50027:
                    await _notify_if_interaction_dead(interaction, content, ephemeral=ephemeral)
        else:
            raise


class UltronBot(commands.Bot):
    def __init__(
        self,
        *,
        env: EnvSettings,
        app_cfg: AppConfig,
        redmine: RedmineClient,
        llm: LLMClient,
    ) -> None:
        # Slash + scheduled posts only; no Message Content intent.
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(command_prefix=None, intents=intents, help_command=None)
        self.env = env
        self.app_cfg = app_cfg
        self.redmine = redmine
        self.llm = llm
        self._jobs_started = False

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.env.discord_guild_id) if self.env.discord_guild_id else None

        @self.tree.command(name="summary", description="Summarize a Redmine ticket")
        @app_commands.describe(issue_id="Redmine issue number")
        async def summary_cmd(interaction: discord.Interaction, issue_id: int) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            guild_label = str(interaction.guild.id) if interaction.guild else "DM"
            cmd_log.info(
                "command=start name=summary issue_id=%s user_id=%s guild_id=%s channel_id=%s",
                issue_id,
                interaction.user.id,
                guild_label,
                interaction.channel_id,
            )
            # send_message (not defer) avoids Discord showing "Ultron is thinking…" on public replies
            async def summary_error(msg: str) -> None:
                if len(msg) <= _DISCORD_MSG_MAX:
                    await _edit_or_followup(interaction, msg, ephemeral=ephemeral)
                else:
                    try:
                        await interaction.followup.send(msg, ephemeral=ephemeral)
                    except discord.HTTPException as e:
                        logger.error("followup failed for long error message: %s", e)
                        if getattr(e, "code", None) == 50027 or e.status == 401:
                            await _notify_if_interaction_dead(interaction, msg, ephemeral=ephemeral)

            await interaction.response.send_message(
                content=self.app_cfg.discord.summary_status_redmine,
                ephemeral=ephemeral,
            )
            t0 = time.monotonic()
            try:

                async def on_before_llm() -> None:
                    await _edit_or_followup(
                        interaction,
                        self.app_cfg.discord.summary_status_llm.format(model=self.llm.model),
                        ephemeral=ephemeral,
                    )

                text = await summarize_issue(
                    redmine=self.redmine,
                    llm=self.llm,
                    issue_id=issue_id,
                    log_read_messages=self.app_cfg.logging.log_read_messages,
                    on_before_llm=on_before_llm,
                )
                parts = chunk_discord(text)
                first, *rest = parts
                await _edit_or_followup(interaction, first, ephemeral=ephemeral)
                for part in rest:
                    try:
                        await interaction.followup.send(part[:_DISCORD_MSG_MAX], ephemeral=ephemeral)
                    except discord.HTTPException as e:
                        logger.error("followup failed for summary chunk (token may have expired): %s", e)
                        break
                out_chars = sum(len(p) for p in parts)
                cmd_log.info(
                    "command=done name=summary issue_id=%s output_chars=%s elapsed_s=%.3f",
                    issue_id,
                    out_chars,
                    time.monotonic() - t0,
                )
            except IssueNotFound:
                await summary_error(f"Issue **#{issue_id}** was not found in Redmine.")
            except RedmineError as e:
                logger.warning("Redmine error in summary: %s", e)
                await summary_error("Redmine request failed. Try again later.")
            except (APITimeoutError, httpx.TimeoutException) as e:
                logger.warning(
                    "LLM timeout in summary for issue_id=%s: %s | "
                    "Often Ollama is still loading the model, the prompt is very large, or CPU inference is slow; "
                    "see Ollama logs. Increase LLM_TIMEOUT_SECONDS if the model can finish within one HTTP read.",
                    issue_id,
                    str(e),
                )
                await summary_error(_TIMEOUT_USER_MSG)
            except Exception:
                logger.exception("summary command failed")
                await summary_error("Something went wrong. Check bot logs.")

        @self.tree.command(name="note", description="Add an LLM-polished note to a Redmine ticket")
        @app_commands.describe(issue_id="Redmine issue number", text="Note text to append")
        async def note_cmd(interaction: discord.Interaction, issue_id: int, text: str) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            guild_label = str(interaction.guild.id) if interaction.guild else "DM"
            cmd_log.info(
                "command=start name=note issue_id=%s user_id=%s guild_id=%s channel_id=%s raw_chars=%s",
                issue_id,
                interaction.user.id,
                guild_label,
                interaction.channel_id,
                len(text),
            )
            async def note_error(msg: str) -> None:
                if len(msg) <= _DISCORD_MSG_MAX:
                    await _edit_or_followup(interaction, msg, ephemeral=ephemeral)
                else:
                    try:
                        await interaction.followup.send(msg, ephemeral=ephemeral)
                    except discord.HTTPException as e:
                        logger.error("followup failed for long note error message: %s", e)
                        if getattr(e, "code", None) == 50027 or e.status == 401:
                            await _notify_if_interaction_dead(interaction, msg, ephemeral=ephemeral)

            await interaction.response.send_message(
                content=_NOTE_PROCESSING_TEMPLATE.format(model=self.llm.model),
                ephemeral=ephemeral,
            )
            t0 = time.monotonic()
            try:
                formatted, url = await add_formatted_note(
                    redmine=self.redmine,
                    llm=self.llm,
                    issue_id=issue_id,
                    raw_text=text,
                    log_read_messages=self.app_cfg.logging.log_read_messages,
                )
                excerpt = formatted[:500] + ("…" if len(formatted) > 500 else "")
                reply = f"Note added to [{issue_id}]({url}).\n\n**Preview:**\n{excerpt}"
                await _edit_or_followup(interaction, reply, ephemeral=ephemeral)
                cmd_log.info(
                    "command=done name=note issue_id=%s output_chars=%s elapsed_s=%.3f",
                    issue_id,
                    len(reply),
                    time.monotonic() - t0,
                )
            except IssueNotFound:
                await note_error(
                    f"Issue **#{issue_id}** was not found in Redmine. No note was added.",
                )
            except RedmineError as e:
                logger.warning("Redmine error in note: %s", e)
                await note_error("Redmine request failed. Try again later.")
            except (APITimeoutError, httpx.TimeoutException) as e:
                logger.warning(
                    "LLM timeout in note for issue_id=%s: %s | "
                    "Often Ollama is still loading the model or inference is slow; see Ollama logs. "
                    "Increase LLM_TIMEOUT_SECONDS if needed.",
                    issue_id,
                    str(e),
                )
                await note_error(_TIMEOUT_USER_MSG)
            except Exception:
                logger.exception("note command failed")
                await note_error("Something went wrong. Check bot logs.")

        if guild:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Slash commands synced to guild %s", self.env.discord_guild_id)
        else:
            await self.tree.sync()
            logger.info("Slash commands synced globally (may take up to ~1 hour to appear)")

        self.abandoned_loop.change_interval(hours=max(1, self.app_cfg.schedules.abandoned.interval_hours))
        self.stale_new_loop.change_interval(hours=max(1, self.app_cfg.schedules.stale_new.interval_hours))

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "")
        if self._jobs_started:
            return
        self._jobs_started = True
        cid = self.app_cfg.reports.channel_id
        if not cid:
            logger.info("config reports.channel_id is 0; scheduled reports disabled")
            return
        ch = self.get_channel(cid)
        if ch is None:
            logger.warning("Reports channel_id %s not visible to bot; scheduled reports disabled", cid)
            return
        if self.app_cfg.schedules.abandoned.enabled:
            self.abandoned_loop.start()
            logger.info("Started abandoned tickets loop every %sh", self.app_cfg.schedules.abandoned.interval_hours)
        if self.app_cfg.schedules.stale_new.enabled:
            self.stale_new_loop.start()
            logger.info("Started stale-new tickets loop every %sh", self.app_cfg.schedules.stale_new.interval_hours)

    async def on_error(self, event_method: str, /, *args, **kwargs) -> None:  # type: ignore[override]
        logger.exception("Error in %s", event_method)

    @tasks.loop(hours=24)
    async def abandoned_loop(self) -> None:
        cfg = self.app_cfg.schedules.abandoned
        cid = self.app_cfg.reports.channel_id
        if not cfg.enabled or not cid:
            return
        channel = self.get_channel(cid)
        if channel is None:
            logger.warning("abandoned_loop: channel %s missing", cid)
            return
        try:
            await run_abandoned_report(
                redmine=self.redmine,
                llm=self.llm,
                channel=channel,  # type: ignore[arg-type]
                cfg=cfg,
                timezone_name=self.app_cfg.timezone,
                log_read_messages=self.app_cfg.logging.log_read_messages,
            )
        except Exception:
            logger.exception("Scheduled job abandoned failed")

    @tasks.loop(hours=24)
    async def stale_new_loop(self) -> None:
        cfg = self.app_cfg.schedules.stale_new
        cid = self.app_cfg.reports.channel_id
        if not cfg.enabled or not cid:
            return
        channel = self.get_channel(cid)
        if channel is None:
            logger.warning("stale_new_loop: channel %s missing", cid)
            return
        try:
            await run_stale_new_report(
                redmine=self.redmine,
                llm=self.llm,
                channel=channel,  # type: ignore[arg-type]
                cfg=cfg,
                timezone_name=self.app_cfg.timezone,
                log_read_messages=self.app_cfg.logging.log_read_messages,
            )
        except Exception:
            logger.exception("Scheduled job stale_new failed")

    @abandoned_loop.before_loop
    async def _before_abandoned(self) -> None:
        await self.wait_until_ready()

    @stale_new_loop.before_loop
    async def _before_stale(self) -> None:
        await self.wait_until_ready()
