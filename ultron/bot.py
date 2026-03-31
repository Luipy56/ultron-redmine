from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ultron.config import AppConfig
from ultron.jobs import run_abandoned_report, run_stale_new_report
from ultron.llm import LLMClient
from ultron.redmine import IssueNotFound, RedmineClient, RedmineError
from ultron.settings import EnvSettings
from ultron.textutil import chunk_discord
from ultron.workflows import add_formatted_note, summarize_issue

logger = logging.getLogger(__name__)


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
            await interaction.response.defer(ephemeral=ephemeral)
            try:
                text = await summarize_issue(redmine=self.redmine, llm=self.llm, issue_id=issue_id)
                parts = chunk_discord(text)
                first, *rest = parts
                await interaction.followup.send(first[:2000], ephemeral=ephemeral)
                for part in rest:
                    await interaction.followup.send(part[:2000], ephemeral=ephemeral)
            except IssueNotFound:
                await interaction.followup.send(f"Issue **#{issue_id}** was not found in Redmine.", ephemeral=ephemeral)
            except RedmineError as e:
                logger.warning("Redmine error in summary: %s", e)
                await interaction.followup.send("Redmine request failed. Try again later.", ephemeral=ephemeral)
            except Exception:
                logger.exception("summary command failed")
                await interaction.followup.send("Something went wrong. Check bot logs.", ephemeral=ephemeral)

        @self.tree.command(name="note", description="Add an LLM-polished note to a Redmine ticket")
        @app_commands.describe(issue_id="Redmine issue number", text="Note text to append")
        async def note_cmd(interaction: discord.Interaction, issue_id: int, text: str) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            await interaction.response.defer(ephemeral=ephemeral)
            try:
                formatted, url = await add_formatted_note(
                    redmine=self.redmine,
                    llm=self.llm,
                    issue_id=issue_id,
                    raw_text=text,
                )
                excerpt = formatted[:500] + ("…" if len(formatted) > 500 else "")
                await interaction.followup.send(
                    f"Note added to [{issue_id}]({url}).\n\n**Preview:**\n{excerpt}",
                    ephemeral=ephemeral,
                )
            except IssueNotFound:
                await interaction.followup.send(
                    f"Issue **#{issue_id}** was not found in Redmine. No note was added.",
                    ephemeral=ephemeral,
                )
            except RedmineError as e:
                logger.warning("Redmine error in note: %s", e)
                await interaction.followup.send("Redmine request failed. Try again later.", ephemeral=ephemeral)
            except Exception:
                logger.exception("note command failed")
                await interaction.followup.send("Something went wrong. Check bot logs.", ephemeral=ephemeral)

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
            )
        except Exception:
            logger.exception("Scheduled job stale_new failed")

    @abandoned_loop.before_loop
    async def _before_abandoned(self) -> None:
        await self.wait_until_ready()

    @stale_new_loop.before_loop
    async def _before_stale(self) -> None:
        await self.wait_until_ready()
