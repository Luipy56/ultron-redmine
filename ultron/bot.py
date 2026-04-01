from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Awaitable, Callable

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks
from openai import APITimeoutError

from ultron.config import AppConfig
from ultron.jobs import run_abandoned_report, run_stale_new_report
from ultron.llm import ChainSkipNotice, LLMBackend, LLMChainExhaustedError, safe_exc_message
from ultron.redmine import IssueNotFound, RedmineClient, RedmineError
from ultron.settings import EnvSettings
from ultron.state_store import (
    consume_token_add_whitelist,
    is_admin,
    is_user_whitelisted,
    register_pending_token,
    remove_user_from_whitelist,
)
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


def _discord_note_author_label(user: discord.User) -> str:
    """Display name for the Redmine note header (`_name_`); uses guild nickname when applicable."""
    label = user.display_name.strip()
    return label or user.name.strip()

_HELP_TEXT = """**Ultron — available slash commands**

**Everyone**
• `/help` — Show this list.
• `/token` — Request a one-time approval code (**DM only**). If you are already whitelisted, the bot says so and does not issue a code.

**Whitelisted users**
• `/hello` — Quick check (whitelisted only); in servers the reply is **visible in the channel**. If not authorized, same as `/summary` / `/note`.
• `/summary` `issue_id` — Summarize a Redmine ticket.
• `/note` `issue_id` `text` — Append an LLM-polished note to a ticket.

**Bot admins only**
• `/approve` `token` — Approve someone who used `/token` (paste their code).
• `/remove` `user_id` — Remove a Discord user id from the whitelist."""


def _slash_command_name(interaction: discord.Interaction) -> str | None:
    if interaction.type is not discord.InteractionType.application_command:
        return None
    cmd = interaction.command
    if cmd is not None and getattr(cmd, "name", None):
        return str(cmd.name)
    data = interaction.data
    if isinstance(data, dict):
        n = data.get("name")
        return str(n) if n else None
    n = getattr(data, "name", None)
    return str(n) if n else None


def _slash_ids(interaction: discord.Interaction) -> tuple[str, int, int | None]:
    guild_label = str(interaction.guild.id) if interaction.guild else "DM"
    return guild_label, interaction.user.id, interaction.channel_id


def log_slash_input(command: str, interaction: discord.Interaction, *, fields: str = "") -> None:
    """Log slash **read**: Discord delivered this command to the bot."""
    gl, uid, cid = _slash_ids(interaction)
    tail = f" {fields}" if fields else ""
    logger.info(
        "%s | slash command received from Discord | user_id=%s guild_id=%s channel_id=%s%s",
        command,
        uid,
        gl,
        cid,
        tail,
        extra={"slash_phase": "INPUT"},
    )
    cmd_log.info(
        "command=%s user_id=%s guild_id=%s channel_id=%s | from Discord%s",
        command,
        uid,
        gl,
        cid,
        tail,
        extra={"slash_phase": "INPUT"},
    )


def log_slash_output(
    command: str,
    interaction: discord.Interaction,
    *,
    action: str,
    fields: str = "",
) -> None:
    """Log slash **write**: bot sent or edited something visible on this interaction."""
    gl, uid, cid = _slash_ids(interaction)
    tail = f" {fields}" if fields else ""
    logger.info(
        "%s | %s | user_id=%s guild_id=%s channel_id=%s%s",
        command,
        action,
        uid,
        gl,
        cid,
        tail,
        extra={"slash_phase": "OUTPUT"},
    )
    cmd_log.info(
        "command=%s user_id=%s guild_id=%s channel_id=%s | %s%s",
        command,
        uid,
        gl,
        cid,
        action,
        tail,
        extra={"slash_phase": "OUTPUT"},
    )


def log_slash_error(
    command: str,
    interaction: discord.Interaction,
    *,
    action: str,
    detail: object | None = None,
) -> None:
    gl, uid, cid = _slash_ids(interaction)
    suffix = f" | {detail}" if detail is not None else ""
    logger.warning(
        "%s | %s | user_id=%s guild_id=%s channel_id=%s%s",
        command,
        action,
        uid,
        gl,
        cid,
        suffix,
        extra={"slash_phase": "ERROR"},
    )


def log_slash_denied(command: str, interaction: discord.Interaction, *, reason: str) -> None:
    """Log gate rejection (interaction_check); user does not run the command handler."""
    gl, uid, cid = _slash_ids(interaction)
    logger.info(
        "%s | %s | user_id=%s guild_id=%s channel_id=%s",
        command,
        reason,
        uid,
        gl,
        cid,
        extra={"slash_phase": "DENIED"},
    )
    cmd_log.info(
        "command=%s user_id=%s guild_id=%s channel_id=%s | %s",
        command,
        uid,
        gl,
        cid,
        reason,
        extra={"slash_phase": "DENIED"},
    )


def _unauthorized_dm_text(owner_contact: str | None) -> str:
    text = (
        "You are not authorized to use this bot yet.\n\n"
        "Run the **`/token`** slash command. It creates a one-time code that is valid for **5 minutes**. "
        "A bot admin can approve your access.\n\n"
        "You may need the bot owner to enable your session — contact them to get access."
    )
    if owner_contact:
        text += f"\n\n**Owner contact:** {owner_contact}"
    return text


async def _tree_interaction_check(env: EnvSettings, interaction: discord.Interaction) -> bool:
    """Global slash guard; must be registered on app_commands.CommandTree (not commands.Bot)."""
    if interaction.type is not discord.InteractionType.application_command:
        return True
    name = _slash_command_name(interaction)
    if name in ("token", "help"):
        return True
    if name == "hello":
        if is_user_whitelisted(env.state_dir, interaction.user.id):
            return True
        log_slash_input("hello", interaction)
        log_slash_denied("hello", interaction, reason="not whitelisted")
        try:
            await _deny_unauthorized(interaction, env.bot_owner_contact, command="hello")
        except discord.HTTPException as e:
            logger.warning("hello unauthorized reply failed: %s", e)
        return False
    if name in ("approve", "remove"):
        if is_admin(env.state_dir, interaction.user.id, env.discord_admin_ids):
            return True
        log_slash_input(name, interaction)
        log_slash_denied(name, interaction, reason="not a bot admin")
        try:
            if interaction.guild is not None:
                await interaction.response.defer(ephemeral=True, thinking=True)
                try:
                    await interaction.delete_original_response()
                except discord.HTTPException:
                    await interaction.followup.send("\u200b", ephemeral=True)
                log_slash_output(
                    name,
                    interaction,
                    action="sent not-admin deny (guild: silent)",
                )
            else:
                await interaction.response.send_message(
                    f"Only bot admins can use `/{name}`.",
                    ephemeral=True,
                )
                log_slash_output(
                    name,
                    interaction,
                    action="sent not-admin deny (ephemeral DM)",
                )
        except discord.HTTPException as e:
            logger.warning("%s deny reply failed: %s", name, e)
        return False
    if is_user_whitelisted(env.state_dir, interaction.user.id):
        return True
    cname = name or "unknown"
    log_slash_input(cname, interaction)
    log_slash_denied(cname, interaction, reason="not whitelisted")
    try:
        await _deny_unauthorized(interaction, env.bot_owner_contact, command=cname)
    except discord.HTTPException as e:
        logger.warning("unauthorized reply failed: %s", e)
    return False


async def _deny_unauthorized(
    interaction: discord.Interaction,
    owner_contact: str | None,
    *,
    command: str,
) -> None:
    """Respond so the user sees nothing in guild channels; explain in DMs."""
    if interaction.guild is not None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            try:
                await interaction.followup.send("\u200b", ephemeral=True)
            except discord.HTTPException as e:
                logger.warning("silent deny followup failed: %s", e)
        log_slash_output(
            command,
            interaction,
            action="deny flow finished (guild: deferred + hidden)",
        )
    else:
        await interaction.response.send_message(
            _unauthorized_dm_text(owner_contact),
            ephemeral=True,
        )
        log_slash_output(
            command,
            interaction,
            action="deny DM sent (not whitelisted)",
        )


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


def _llm_chain_skip_discord_cb(
    *,
    interaction: discord.Interaction,
    ephemeral: bool,
    template: str,
    command: str,
    issue_id: int,
) -> Callable[[ChainSkipNotice], Awaitable[None]]:
    """Edit the slash status message when the LLM chain advances to the next provider."""

    async def _on_skip(notice: ChainSkipNotice) -> None:
        try:
            text = template.format(
                from_entry=notice.from_entry,
                from_model=notice.from_model,
                to_entry=notice.to_entry,
                to_model=notice.to_model,
                reason=notice.reason,
            )
            await _edit_or_followup(interaction, text, ephemeral=ephemeral)
            log_slash_output(
                command,
                interaction,
                action="edited message (LLM chain skipped to next provider)",
                fields=(
                    f"issue_id={issue_id} from_model={notice.from_model!r} "
                    f"to_model={notice.to_model!r} reason={notice.reason!r}"
                ),
            )
        except Exception as e:
            logger.warning("LLM chain skip: Discord status update failed: %s", e)

    return _on_skip


def _log_slash_command_failure(command: str, exc: BaseException) -> None:
    """Log traceback frames without dumping multi‑KB exception strings (e.g. HTML error pages)."""
    tb = "".join(traceback.format_tb(exc.__traceback__)) if exc.__traceback__ else ""
    logger.error(
        "%s command failed | %s: %s%s",
        command,
        type(exc).__name__,
        safe_exc_message(exc),
        f"\n{tb}" if tb else "",
    )


class UltronCommandTree(app_commands.CommandTree):
    """discord.py invokes interaction_check on CommandTree only, not on commands.Bot."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = self.client
        return await _tree_interaction_check(bot.env, interaction)  # type: ignore[attr-defined]


class UltronBot(commands.Bot):
    def __init__(
        self,
        *,
        env: EnvSettings,
        app_cfg: AppConfig,
        redmine: RedmineClient,
        llm: LLMBackend,
    ) -> None:
        # Slash + scheduled posts only; no Message Content intent.
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(
            command_prefix=None,
            intents=intents,
            help_command=None,
            tree_cls=UltronCommandTree,
        )
        self.env = env
        self.app_cfg = app_cfg
        self.redmine = redmine
        self.llm = llm
        self._jobs_started = False

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.env.discord_guild_id) if self.env.discord_guild_id else None

        @self.tree.command(name="token", description="Get a one-time token so an operator can whitelist you")
        async def token_cmd(interaction: discord.Interaction) -> None:
            log_slash_input("token", interaction)
            if interaction.guild is not None:
                await interaction.response.send_message(
                    "**Error:** You cannot run `/token` in a server channel. "
                    "Open a direct message (DM) with the bot and use the command there.",
                    ephemeral=True,
                )
                log_slash_output(
                    "token",
                    interaction,
                    action="sent ephemeral error (DM-only command)",
                )
                return
            if is_user_whitelisted(self.env.state_dir, interaction.user.id):
                await interaction.response.send_message(
                    "You are already approved. You can use **`/summary`** and **`/note`** — you do not need a new token.",
                    ephemeral=True,
                )
                log_slash_output(
                    "token",
                    interaction,
                    action="sent ephemeral (user already whitelisted)",
                )
                return
            tok = register_pending_token(self.env.state_dir, interaction.user.id)
            body = (
                f"Your approval token (valid **5 minutes**):\n`{tok}`\n\n"
                "Send this code to a bot admin so they can approve you.\n\n"
                "You may still need the bot owner to OK your access for this session — contact them if needed."
            )
            await interaction.response.send_message(body, ephemeral=True)
            log_slash_output(
                "token",
                interaction,
                action="sent ephemeral (new approval token issued)",
                fields=f"token_len={len(tok)}",
            )

        @self.tree.command(name="hello", description="Simple connectivity check")
        async def hello_cmd(interaction: discord.Interaction) -> None:
            _hello_reply = "hello world"
            log_slash_input("hello", interaction)
            try:
                # Defer + followup so the reply is a normal channel-visible webhook message, not “only you” in servers.
                await interaction.response.defer(ephemeral=False)
                await interaction.followup.send(_hello_reply, ephemeral=False)
            except discord.HTTPException as e:
                log_slash_error(
                    "hello",
                    interaction,
                    action="defer or followup failed",
                    detail=e,
                )
                raise
            log_slash_output(
                "hello",
                interaction,
                action="defer + public followup posted to channel",
                fields=f"reply={_hello_reply!r}",
            )

        @self.tree.command(name="help", description="List available commands")
        async def help_cmd(interaction: discord.Interaction) -> None:
            log_slash_input("help", interaction)
            await interaction.response.send_message(_HELP_TEXT, ephemeral=True)
            log_slash_output("help", interaction, action="sent help text (ephemeral)")

        @self.tree.command(name="approve", description="Approve a pending user token (admins only)")
        @app_commands.describe(token="Token from the user's /token command")
        async def approve_cmd(interaction: discord.Interaction, token: str) -> None:
            log_slash_input("approve", interaction, fields=f"token_len={len(token)}")
            try:
                uid = consume_token_add_whitelist(self.env.state_dir, token)
            except ValueError as e:
                await interaction.response.send_message(str(e).capitalize() + ".", ephemeral=True)
                log_slash_output(
                    "approve",
                    interaction,
                    action="sent ephemeral (token validation failed)",
                    fields=f"error={e!r}",
                )
                return
            await interaction.response.send_message(
                f"Whitelisted Discord user id **{uid}**.",
                ephemeral=True,
            )
            log_slash_output(
                "approve",
                interaction,
                action="sent ephemeral (user whitelisted)",
                fields=f"approved_user_id={uid} actor_id={interaction.user.id}",
            )
            target = interaction.client.get_user(uid)
            if target is None:
                try:
                    target = await interaction.client.fetch_user(uid)
                except discord.NotFound:
                    logger.warning("approve: user_id=%s not found for DM notification", uid)
                    target = None
            if target is not None:
                try:
                    await target.send(
                        "Your access request has been approved. You can now use **`/summary`** and **`/note`** with this bot.",
                    )
                    log_slash_output(
                        "approve",
                        interaction,
                        action="DM sent to newly whitelisted user",
                        fields=f"target_user_id={uid}",
                    )
                except discord.HTTPException as e:
                    logger.warning(
                        "approve: DM to user_id=%s failed (user may have DMs closed): %s",
                        uid,
                        e,
                    )
                    log_slash_error(
                        "approve",
                        interaction,
                        action="DM to approved user failed",
                        detail=e,
                    )

        @self.tree.command(name="remove", description="Remove a user from the whitelist (admins only)")
        @app_commands.describe(user_id="Discord user id to remove from the whitelist")
        async def remove_cmd(interaction: discord.Interaction, user_id: int) -> None:
            log_slash_input("remove", interaction, fields=f"target_user_id={user_id}")
            removed = remove_user_from_whitelist(self.env.state_dir, user_id)
            if removed:
                await interaction.response.send_message(
                    f"Removed Discord user id **{user_id}** from the whitelist.",
                    ephemeral=True,
                )
                log_slash_output(
                    "remove",
                    interaction,
                    action="sent ephemeral (user removed from whitelist)",
                    fields=f"removed_user_id={user_id}",
                )
            else:
                await interaction.response.send_message(
                    f"User id **{user_id}** was not on the whitelist (unchanged).",
                    ephemeral=True,
                )
                log_slash_output(
                    "remove",
                    interaction,
                    action="sent ephemeral (target was not whitelisted)",
                    fields=f"user_id={user_id}",
                )

        @self.tree.command(name="summary", description="Summarize a Redmine ticket")
        @app_commands.describe(issue_id="Redmine issue number")
        async def summary_cmd(interaction: discord.Interaction, issue_id: int) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input(
                "summary",
                interaction,
                fields=f"issue_id={issue_id} ephemeral={ephemeral}",
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
                short = msg.replace("\n", " ")[:120]
                log_slash_output(
                    "summary",
                    interaction,
                    action="user-visible error",
                    fields=f"issue_id={issue_id} detail={short!r}",
                )

            await interaction.response.send_message(
                content=self.app_cfg.discord.summary_status_redmine,
                ephemeral=ephemeral,
            )
            log_slash_output(
                "summary",
                interaction,
                action="sent initial status (fetching Redmine)",
                fields=f"issue_id={issue_id}",
            )
            t0 = time.monotonic()
            try:

                async def on_before_llm() -> None:
                    await _edit_or_followup(
                        interaction,
                        self.app_cfg.discord.summary_status_llm.format(model=self.llm.model),
                        ephemeral=ephemeral,
                    )
                    log_slash_output(
                        "summary",
                        interaction,
                        action="edited message (LLM phase)",
                        fields=f"issue_id={issue_id} model={self.llm.model!r}",
                    )

                on_skip = _llm_chain_skip_discord_cb(
                    interaction=interaction,
                    ephemeral=ephemeral,
                    template=self.app_cfg.discord.llm_chain_skip_status,
                    command="summary",
                    issue_id=issue_id,
                )
                text = await summarize_issue(
                    redmine=self.redmine,
                    llm=self.llm,
                    issue_id=issue_id,
                    log_read_messages=self.app_cfg.logging.log_read_messages,
                    on_before_llm=on_before_llm,
                    on_llm_chain_skip=on_skip,
                )
                parts = chunk_discord(text)
                first, *rest = parts
                await _edit_or_followup(interaction, first, ephemeral=ephemeral)
                for part in rest:
                    try:
                        await interaction.followup.send(part[:_DISCORD_MSG_MAX], ephemeral=ephemeral)
                    except discord.HTTPException as e:
                        logger.error("followup failed for summary chunk (token may have expired): %s", e)
                        log_slash_error(
                            "summary",
                            interaction,
                            action="followup chunk send failed",
                            detail=e,
                        )
                        break
                out_chars = sum(len(p) for p in parts)
                log_slash_output(
                    "summary",
                    interaction,
                    action="delivered summary to user",
                    fields=(
                        f"issue_id={issue_id} output_chars={out_chars} chunks={len(parts)} "
                        f"elapsed_s={time.monotonic() - t0:.3f}"
                    ),
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
            except LLMChainExhaustedError as e:
                logger.error(
                    "summary: all LLM chain providers failed | backends=%s | last=%s: %s",
                    e.provider_count,
                    type(e.last_error).__name__,
                    safe_exc_message(e.last_error),
                )
                await summary_error(self.app_cfg.discord.llm_chain_all_failed_message)
            except Exception as e:
                _log_slash_command_failure("summary", e)
                await summary_error("Something went wrong. Check bot logs.")

        @self.tree.command(name="note", description="Add an LLM-polished note to a Redmine ticket")
        @app_commands.describe(issue_id="Redmine issue number", text="Note text to append")
        async def note_cmd(interaction: discord.Interaction, issue_id: int, text: str) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input(
                "note",
                interaction,
                fields=f"issue_id={issue_id} raw_chars={len(text)} ephemeral={ephemeral}",
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
                short = msg.replace("\n", " ")[:120]
                log_slash_output(
                    "note",
                    interaction,
                    action="user-visible error",
                    fields=f"issue_id={issue_id} detail={short!r}",
                )

            await interaction.response.send_message(
                content=_NOTE_PROCESSING_TEMPLATE.format(model=self.llm.model),
                ephemeral=ephemeral,
            )
            log_slash_output(
                "note",
                interaction,
                action="sent initial status (processing note)",
                fields=f"issue_id={issue_id} model={self.llm.model!r}",
            )
            t0 = time.monotonic()
            try:
                on_skip = _llm_chain_skip_discord_cb(
                    interaction=interaction,
                    ephemeral=ephemeral,
                    template=self.app_cfg.discord.llm_chain_skip_status,
                    command="note",
                    issue_id=issue_id,
                )
                formatted, url = await add_formatted_note(
                    redmine=self.redmine,
                    llm=self.llm,
                    issue_id=issue_id,
                    raw_text=text,
                    log_read_messages=self.app_cfg.logging.log_read_messages,
                    on_llm_chain_skip=on_skip,
                    note_author_label=_discord_note_author_label(interaction.user),
                )
                excerpt = formatted[:500] + ("…" if len(formatted) > 500 else "")
                reply = f"Note added to [{issue_id}]({url}).\n\n**Preview:**\n{excerpt}"
                await _edit_or_followup(interaction, reply, ephemeral=ephemeral)
                log_slash_output(
                    "note",
                    interaction,
                    action="delivered confirmation to user",
                    fields=(
                        f"issue_id={issue_id} output_chars={len(reply)} "
                        f"elapsed_s={time.monotonic() - t0:.3f}"
                    ),
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
            except LLMChainExhaustedError as e:
                logger.error(
                    "note: all LLM chain providers failed | backends=%s | last=%s: %s",
                    e.provider_count,
                    type(e.last_error).__name__,
                    safe_exc_message(e.last_error),
                )
                await note_error(self.app_cfg.discord.llm_chain_all_failed_message)
            except Exception as e:
                _log_slash_command_failure("note", e)
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
