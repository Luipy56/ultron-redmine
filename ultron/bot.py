from __future__ import annotations

import logging
from typing import Any, Literal
import time
import traceback
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks
from discord.utils import escape_markdown
from openai import APITimeoutError

from ultron import __version__ as _ULTRON_VERSION
from ultron.config import AppConfig, UnassignedOpenConfig
from ultron.jobs import run_abandoned_report, run_stale_new_report
from ultron.llm import (
    ChainSkipNotice,
    LLMBackend,
    LLMChainExhaustedError,
    NoLLMConfiguredError,
    safe_exc_message,
)
from ultron.nl_router import (
    NLAdminRejected,
    NLChat,
    NLInvoke,
    NLParseError,
    run_nl_router,
)
from ultron.redmine import IssueNotFound, RedmineClient, RedmineError, resolve_status_id_by_name
from ultron.settings import EnvSettings
from ultron.state_store import (
    consume_token_add_whitelist,
    is_admin,
    is_user_whitelisted,
    register_pending_token,
    remove_user_from_whitelist,
)
from ultron.textutil import chunk_discord
from ultron.workflows import add_formatted_note, ask_about_issue, summarize_issue

logger = logging.getLogger(__name__)
cmd_log = logging.getLogger("ultron.commands")
# Normal channel/DM messages (e.g. @mention replies); filter logs with: `grep ultron.chat` vs slash (`ultron.commands`).
chat_log = logging.getLogger("ultron.chat")

_TIMEOUT_USER_MSG = (
    "The language model did not respond in time. Try a shorter ticket, a faster model, "
    "or increase **LLM_TIMEOUT_SECONDS** in the bot environment."
)
_NO_LLM_SLASH_MSG = (
    "No language model is configured. Add **llm_chain** to `config.yaml` or set **LLM_API_KEY** and **LLM_MODEL** "
    "(and **LLM_BASE_URL**) in the environment. **`/summary`**, **`/ask_issue`**, and **`/note`** need a model."
)
_NL_DISABLED_MENTION_MSG = (
    "Natural-language @mention routing is **disabled**. "
    "Use slash commands, or ask an operator to enable **`discord.nl_commands`** / **`ULTRON_NL_COMMANDS`** and configure a language model."
)
# NL @mention: first reply (edited in place like slash defer → edit).
_NL_STATUS_ROUTING = "Routing your message with the language model…"
# User-facing window when slash replies may stop updating (team policy; Discord API limits vary).
_DISCORD_SESSION_MINUTES = 4
_SESSION_EXPIRED_HINT = (
    f"\n\n_Your slash-command message could not be updated (Discord session expired after ~{_DISCORD_SESSION_MINUTES} minutes). "
    "Ollama may still have been loading the model or inferring; check server logs and raise **LLM_TIMEOUT_SECONDS** if needed._"
)

_DISCORD_MSG_MAX = 2000
# Initial /note status (avoid defer "thinking…" when responses are public)
_NOTE_PROCESSING_TEMPLATE = "Processing note with model {model}…"

# Reserved slash names for development; distinct descriptions until renamed (whitelist-gated like /ping).
_DEV_PLACEHOLDER_SLOTS: frozenset[str] = frozenset(f"dev_slot_{i}" for i in range(2, 11))


def _discord_note_author_label(user: discord.User) -> str:
    """Display name for the Redmine note header (`_name_`); uses guild nickname when applicable."""
    label = user.display_name.strip()
    return label or user.name.strip()

_HELP_TEXT = (
    """**Ultron — available slash commands**

**Everyone**
• `/help` — Show this list.
• `/token` — Request a one-time approval code (**DM only**). If you are already whitelisted, the bot says so and does not issue a code.

**Whitelisted users**
• `/ping` — Quick check (whitelisted only); in servers the reply is **visible in the channel**. If not authorized, same as `/summary` / `/ask_issue` / `/note`.
• `/status` — Placeholder (reserved for future health info).
• `/new_issues` — List issues in the configured “new” Redmine status, created at least **M** days ago (see `discord.new_issues` in `config.yaml`).
• `/issues_by_status` `status` — Same listing as `/new_issues`, but **`status`** is the Redmine issue status name (min age & cap still from `discord.new_issues`).
• `/unassigned_issues` — Unassigned **open** Redmine issues created at least **N** day(s) ago (defaults and closed-status prefixes in `discord.unassigned_open` in `config.yaml`).
• `/summary` `issue_id` — Summarize a Redmine ticket (requires a configured language model).
• `/ask_issue` `issue_id` `question` — Answer a question about a ticket using the issue text as context (requires a model).
• `/note` `issue_id` `text` — Append an LLM-polished note to a ticket (requires a model).

Mention Ultron (**@…**) or **Reply** to the bot (**whitelisted users only**). With **`discord.nl_commands`** or **`ULTRON_NL_COMMANDS`**, an LLM routes your message to allowed commands (not admin). If that is off, you get a short notice that routing is disabled. **Message Content** intent may be required for some clients.

**Note:** If no language model is configured, **`/summary`**, **`/ask_issue`**, and **`/note`** are unavailable; Redmine listing, **`/ping`**, and registration still work.

**Bot admins only**
• `/approve` `token` — Approve someone who used `/token` (paste their code).
• `/remove` `user_id` — Remove a Discord user id from the whitelist.
• `/show_config` — Show important non-secret settings (**ephemeral** only)."""
    + f"\n\n*Ultron v{_ULTRON_VERSION}*"
)


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


def _message_ids(message: discord.Message) -> tuple[str, int, int, int]:
    """guild_id label, author_id, channel_id, message_id (for chat mention logs)."""
    gl = str(message.guild.id) if message.guild else "DM"
    return gl, message.author.id, message.channel.id, message.id


def _truncate_for_log(text: str, max_len: int = 200) -> str:
    t = text.replace("\n", " ").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def _nl_dispatch_status_line(command: str, args: dict[str, Any]) -> str:
    """User-visible feedback after the model chose a concrete command (second edit, before work)."""
    if command == "ping":
        return "Running **`/ping`**…"
    if command == "help":
        return "Running **`/help`**…"
    if command == "status":
        return "Running **`/status`**…"
    if command == "new_issues":
        return "Fetching **new issues** (configured Redmine status)…"
    if command == "issues_by_status":
        st = str(args.get("status", "")).strip()
        if len(st) > 120:
            st = st[:119] + "…"
        return f"Listing issues with status **{escape_markdown(st)}**…"
    if command == "unassigned_issues":
        return "Fetching **unassigned** open issues…"
    if command == "summary":
        return f"Summarizing issue **#{int(args['issue_id'])}**…"
    if command == "ask_issue":
        return f"Answering about issue **#{int(args['issue_id'])}**…"
    if command == "note":
        return f"Adding a note to issue **#{int(args['issue_id'])}**…"
    return f"Running **`/{escape_markdown(command)}`**…"


async def _nl_edit_or_reply(
    message: discord.Message,
    status_msg: discord.Message | None,
    content: str,
    *,
    mention_author: bool = False,
) -> discord.Message | None:
    """Prefer editing the processing bubble; otherwise reply."""
    text = content[:_DISCORD_MSG_MAX]
    if status_msg is not None:
        try:
            await status_msg.edit(content=text)
            return status_msg
        except discord.HTTPException:
            pass
    try:
        return await message.reply(text, mention_author=mention_author)
    except discord.HTTPException:
        return None


async def _reply_chunked_to_message(
    message: discord.Message,
    text: str,
    *,
    edit_first: discord.Message | None = None,
) -> None:
    """Reply with first chunk, then channel sends for the rest. If ``edit_first``, edit that message instead of a new reply."""
    parts = chunk_discord(text)
    if not parts:
        await _nl_edit_or_reply(message, edit_first, "(empty)", mention_author=False)
        return
    first, *rest = parts
    first = first[:_DISCORD_MSG_MAX]
    if edit_first is not None:
        try:
            await edit_first.edit(content=first)
        except discord.HTTPException:
            await message.reply(first, mention_author=False)
    else:
        await message.reply(first, mention_author=False)
    for part in rest:
        await message.channel.send(part[:_DISCORD_MSG_MAX])


def log_slash_input(command: str, interaction: discord.Interaction, *, fields: str = "") -> None:
    """Log slash **read**: Discord delivered this command to the bot."""
    gl, uid, cid = _slash_ids(interaction)
    tail = f" {fields}" if fields else ""
    logger.info(
        "source=slash | %s | slash command received from Discord | user_id=%s guild_id=%s channel_id=%s%s",
        command,
        uid,
        gl,
        cid,
        tail,
        extra={"slash_phase": "INPUT", "message_source": "slash"},
    )
    cmd_log.info(
        "source=slash | command=%s user_id=%s guild_id=%s channel_id=%s | from Discord%s",
        command,
        uid,
        gl,
        cid,
        tail,
        extra={"slash_phase": "INPUT", "message_source": "slash"},
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
        "source=slash | %s | %s | user_id=%s guild_id=%s channel_id=%s%s",
        command,
        action,
        uid,
        gl,
        cid,
        tail,
        extra={"slash_phase": "OUTPUT", "message_source": "slash"},
    )
    cmd_log.info(
        "source=slash | command=%s user_id=%s guild_id=%s channel_id=%s | %s%s",
        command,
        uid,
        gl,
        cid,
        action,
        tail,
        extra={"slash_phase": "OUTPUT", "message_source": "slash"},
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
        "source=slash | %s | %s | user_id=%s guild_id=%s channel_id=%s%s",
        command,
        action,
        uid,
        gl,
        cid,
        suffix,
        extra={"slash_phase": "ERROR", "message_source": "slash"},
    )
    cmd_log.warning(
        "source=slash | command=%s user_id=%s guild_id=%s channel_id=%s | %s%s",
        command,
        uid,
        gl,
        cid,
        action,
        suffix,
        extra={"slash_phase": "ERROR", "message_source": "slash"},
    )


def log_slash_denied(command: str, interaction: discord.Interaction, *, reason: str) -> None:
    """Log gate rejection (interaction_check); user does not run the command handler."""
    gl, uid, cid = _slash_ids(interaction)
    logger.info(
        "source=slash | %s | %s | user_id=%s guild_id=%s channel_id=%s",
        command,
        reason,
        uid,
        gl,
        cid,
        extra={"slash_phase": "DENIED", "message_source": "slash"},
    )
    cmd_log.info(
        "source=slash | command=%s user_id=%s guild_id=%s channel_id=%s | %s",
        command,
        uid,
        gl,
        cid,
        reason,
        extra={"slash_phase": "DENIED", "message_source": "slash"},
    )


def log_chat_mention_input(message: discord.Message, *, fields: str = "", feature: str = "mention") -> None:
    """Log whitelisted @mention work starting (mirrors slash ``[INPUT]``)."""
    gl, uid, cid, mid = _message_ids(message)
    tail = f" {fields}" if fields else ""
    logger.info(
        "source=chat | [INPUT] | feature=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
        feature,
        uid,
        gl,
        cid,
        mid,
        tail,
        extra={"chat_phase": "INPUT", "message_source": "chat"},
    )
    chat_log.info(
        "source=chat | [INPUT] | feature=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
        feature,
        uid,
        gl,
        cid,
        mid,
        tail,
        extra={"chat_phase": "INPUT", "message_source": "chat"},
    )


def log_chat_mention_output(message: discord.Message, *, action: str, fields: str = "", feature: str = "mention") -> None:
    """Log bot-visible result for a mention (mirrors slash ``[OUTPUT]``)."""
    gl, uid, cid, mid = _message_ids(message)
    tail = f" {fields}" if fields else ""
    logger.info(
        "source=chat | [OUTPUT] | feature=%s | %s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
        feature,
        action,
        uid,
        gl,
        cid,
        mid,
        tail,
        extra={"chat_phase": "OUTPUT", "message_source": "chat"},
    )
    chat_log.info(
        "source=chat | [OUTPUT] | feature=%s | %s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
        feature,
        action,
        uid,
        gl,
        cid,
        mid,
        tail,
        extra={"chat_phase": "OUTPUT", "message_source": "chat"},
    )


def log_chat_mention_error(message: discord.Message, *, action: str, detail: object | None = None, feature: str = "mention") -> None:
    gl, uid, cid, mid = _message_ids(message)
    suffix = f" | {detail}" if detail is not None else ""
    logger.warning(
        "source=chat | [ERROR] | feature=%s | %s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
        feature,
        action,
        uid,
        gl,
        cid,
        mid,
        suffix,
        extra={"chat_phase": "ERROR", "message_source": "chat"},
    )
    chat_log.warning(
        "source=chat | [ERROR] | feature=%s | %s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
        feature,
        action,
        uid,
        gl,
        cid,
        mid,
        suffix,
        extra={"chat_phase": "ERROR", "message_source": "chat"},
    )


def log_chat_mention_ignored(message: discord.Message, *, reason: str) -> None:
    """Log @mention not acted on (e.g. not whitelisted)."""
    gl, uid, cid, mid = _message_ids(message)
    logger.info(
        "source=chat | [IGNORE] | reason=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s",
        reason,
        uid,
        gl,
        cid,
        mid,
        extra={"chat_phase": "IGNORE", "message_source": "chat"},
    )
    chat_log.info(
        "source=chat | [IGNORE] | reason=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s",
        reason,
        uid,
        gl,
        cid,
        mid,
        extra={"chat_phase": "IGNORE", "message_source": "chat"},
    )


def log_chat_mention_received(
    message: discord.Message,
    *,
    via: str,
    whitelisted: bool,
    message_content_intent: bool,
) -> None:
    """Log every addressed @mention / reply-to-bot (before gates)."""
    gl, uid, cid, mid = _message_ids(message)
    logger.info(
        "source=chat | [RECEIVED] | via=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s "
        "whitelisted=%s message_content_intent=%s",
        via,
        uid,
        gl,
        cid,
        mid,
        whitelisted,
        message_content_intent,
        extra={"chat_phase": "RECEIVED", "message_source": "chat"},
    )
    chat_log.info(
        "source=chat | [RECEIVED] | via=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s whitelisted=%s",
        via,
        uid,
        gl,
        cid,
        mid,
        whitelisted,
        extra={"chat_phase": "RECEIVED", "message_source": "chat"},
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


async def _fetch_discord_user(client: discord.Client, uid: int) -> discord.User | None:
    """Resolve a user for display; ``None`` if the account no longer exists."""
    u = client.get_user(uid)
    if u is not None:
        return u
    try:
        return await client.fetch_user(uid)
    except discord.NotFound:
        return None


def _whitelisted_user_ack_message(user: discord.User | None, uid: int) -> str:
    """Ephemeral reply for `/approve`: show display name / handle plus numeric id."""
    if user is None:
        return (
            f"Whitelisted user **`{uid}`**. Discord profile could not be loaded "
            "(the user can still use the bot if whitelisted)."
        )
    display = (user.global_name or "").strip() or user.name
    safe = escape_markdown(display)
    return f"Whitelisted **{safe}** (`{uid}`)."


def _whitelisted_user_log_label(user: discord.User | None, uid: int) -> str:
    """Short fragment for the registration logs channel."""
    if user is None:
        return f"user **`{uid}`** (profile not loaded)"
    display = (user.global_name or "").strip() or user.name
    safe = escape_markdown(display)
    return f"**{safe}** (`{uid}`)"


def _format_show_config(app_cfg: AppConfig, env: EnvSettings) -> str:
    """Redacted summary for `/show_config` (no API keys or secrets)."""
    ru = urlparse(env.redmine_url)
    redmine_host = ru.netloc or env.redmine_url
    ni = app_cfg.discord.new_issues
    lines: list[str] = [
        "**Configuration (redacted)**",
        f"• **timezone:** {app_cfg.timezone}",
        f"• **CONFIG_PATH:** `{env.config_path}`",
        f"• **ULTRON_STATE_DIR:** `{env.state_dir}`",
        f"• **redmine host:** {redmine_host}",
        f"• **discord.ephemeral_default:** {app_cfg.discord.ephemeral_default}",
        f"• **discord.new_issues:** status_name={ni.status_name!r} list_limit={ni.list_limit} "
        f"min_age_days={ni.min_age_days}",
        f"• **discord.registration_log:** enabled={app_cfg.discord.registration_log.enabled} "
        f"channel_id={app_cfg.discord.registration_log.channel_id}",
        f"• **discord.unassigned_open:** min_age_days={app_cfg.discord.unassigned_open.min_age_days} "
        f"list_limit={app_cfg.discord.unassigned_open.list_limit} "
        f"closed_status_prefixes={list(app_cfg.discord.unassigned_open.closed_status_prefixes)!r}",
        f"• **reports.channel_id:** {app_cfg.reports.channel_id}",
        f"• **logging.log_read_messages:** {app_cfg.logging.log_read_messages}",
        f"• **DISCORD_MESSAGE_CONTENT_INTENT:** {env.discord_message_content_intent} "
        "(privileged **Message Content** intent; portal must match if true)",
        f"• **discord.nl_commands:** {app_cfg.discord.nl_commands} "
        "(natural-language @mention router; also env **ULTRON_NL_COMMANDS**)",
    ]
    ab = app_cfg.schedules.abandoned
    sn = app_cfg.schedules.stale_new
    lines.append(
        f"• **schedules.abandoned:** enabled={ab.enabled} interval_h={ab.interval_hours} "
        f"max_days={ab.max_days_without_update} max_issues={ab.max_issues}"
    )
    lines.append(
        f"• **schedules.stale_new:** enabled={sn.enabled} interval_h={sn.interval_hours} "
        f"min_age_h={sn.min_age_hours} require_unassigned={sn.require_unassigned} "
        f"max_journal_entries={sn.max_journal_entries} max_issues={sn.max_issues}"
    )
    if not env.llm_enabled:
        lines.append(
            "• **llm:** no language model configured — **`/summary`** / **`/ask_issue`** / **`/note`** and AI text in scheduled "
            "reports are skipped until you add **llm_chain** or **LLM_*** env vars"
        )
    elif app_cfg.llm_chain:
        lines.append("• **llm_chain:**")
        for i, spec in enumerate(app_cfg.llm_chain):
            label = spec.name or f"entry[{i}]"
            lines.append(f"  – {label}: `{spec.base_url}` / model `{spec.model}`")
    else:
        lines.append("• **llm:** single provider from environment (not `llm_chain`)")
        lines.append(f"  – `{env.llm_base_url}` / model `{env.llm_model}`")
    return "\n".join(lines)


async def _tree_interaction_check(env: EnvSettings, interaction: discord.Interaction) -> bool:
    """Global slash guard; must be registered on app_commands.CommandTree (not commands.Bot)."""
    if interaction.type is not discord.InteractionType.application_command:
        return True
    name = _slash_command_name(interaction)
    if name in ("token", "help"):
        return True
    if name == "ping":
        if is_user_whitelisted(env.state_dir, interaction.user.id):
            return True
        log_slash_input("ping", interaction)
        log_slash_denied("ping", interaction, reason="not whitelisted")
        try:
            await _deny_unauthorized(interaction, env.bot_owner_contact, command="ping")
        except discord.HTTPException as e:
            logger.warning("ping unauthorized reply failed: %s", e)
        return False
    if name in _DEV_PLACEHOLDER_SLOTS:
        if is_user_whitelisted(env.state_dir, interaction.user.id):
            return True
        log_slash_input(name, interaction)
        log_slash_denied(name, interaction, reason="not whitelisted")
        try:
            await _deny_unauthorized(interaction, env.bot_owner_contact, command=name)
        except discord.HTTPException as e:
            logger.warning("%s unauthorized reply failed: %s", name, e)
        return False
    if name in ("approve", "remove", "show_config"):
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


async def _markdown_unassigned_open_issues(
    *,
    redmine: RedmineClient,
    cfg: UnassignedOpenConfig,
) -> tuple[str | None, str | None, int]:
    """Build markdown body, optional error string, and issue count (-1 on fetch error)."""
    try:
        issues = await redmine.list_unassigned_open_issues_older_than_days(
            min_age_days=cfg.min_age_days,
            closed_status_prefixes=cfg.closed_status_prefixes,
        )
    except RedmineError as e:
        return None, f"Redmine error: {e}", -1
    total = len(issues)
    if total == 0:
        return (
            "No **unassigned** **open** issues created at least "
            f"**{cfg.min_age_days}** day(s) ago (within the search limit), after excluding "
            "closed-equivalent status prefixes.",
            None,
            0,
        )
    n_show = min(cfg.list_limit, total)
    header = (
        f"**Unassigned open issues** (created ≥{cfg.min_age_days} days ago; "
        "unassigned; Redmine `open`; excluding configured closed-equivalent status prefixes) · "
        f"**{total}** total"
    )
    line_strs = _discord_formatted_issue_lines(issues[:n_show], redmine)
    body = header + "\n\n" + "\n".join(line_strs)
    rest = total - n_show
    if rest > 0:
        _other = "issue" if rest == 1 else "issues"
        body += f"\n\nand **{rest}** other {_other} matching this filter."
    return body, None, total


async def _markdown_issues_by_status(
    *,
    redmine: RedmineClient,
    status_name: str,
    min_age_days: int,
    list_limit: int,
) -> tuple[str | None, str | None, int]:
    """Build markdown, optional error, total count (-1 on error)."""
    try:
        sid = await resolve_status_id_by_name(redmine, status_name)
        if sid is None:
            return (
                None,
                f"No Redmine issue status named `{status_name!r}`. "
                "Check **Administration → Issue statuses** for the exact name.",
                -1,
            )
        issues = await redmine.list_issues_older_than_days(
            status_id=sid,
            min_age_days=min_age_days,
        )
    except RedmineError as e:
        return None, f"Redmine error: {e}", -1
    total = len(issues)
    if total == 0:
        return (
            f"No issues in status `{status_name}` created at least **{min_age_days}** days ago "
            "(within the search limit).",
            None,
            0,
        )
    n_show = min(list_limit, total)
    header = (
        f"**Issues with status `{status_name}`** (created ≥{min_age_days} days ago) · "
        f"**{total}** total"
    )
    line_strs = _discord_formatted_issue_lines(issues[:n_show], redmine)
    body = header + "\n\n" + "\n".join(line_strs)
    rest = total - n_show
    if rest > 0:
        _other = "issue" if rest == 1 else "issues"
        body += f"\n\nand **{rest}** other {_other} with status `{status_name}`."
    return body, None, total


def _discord_formatted_issue_lines(issues: list[dict[str, Any]], redmine: RedmineClient) -> list[str]:
    """Markdown lines: escaped subject + linked ``[#id](url)`` per issue."""
    line_strs: list[str] = []
    for iss in issues:
        iid = int(iss["id"])
        raw_subj = str(iss.get("subject", "")).replace("\n", " ").strip()
        if len(raw_subj) > 200:
            raw_subj = raw_subj[:197] + "..."
        subj_display = escape_markdown(raw_subj)
        if len(subj_display) > 220:
            subj_display = subj_display[:217] + "..."
        url = redmine.issue_url(iid)
        link = f"[#{iid}]({url})"
        if raw_subj:
            line_strs.append(f"{subj_display} {link}")
        else:
            line_strs.append(link)
    return line_strs


async def _send_unassigned_open_issues_list(
    *,
    interaction: discord.Interaction,
    redmine: RedmineClient,
    ephemeral: bool,
    cfg: UnassignedOpenConfig,
    log_command: str,
) -> None:
    """List unassigned open issues past min age; interaction already deferred."""
    body, err, total = await _markdown_unassigned_open_issues(redmine=redmine, cfg=cfg)
    if err is not None:
        await interaction.followup.send(err, ephemeral=ephemeral)
        log_slash_error(log_command, interaction, action="redmine request failed", detail=err)
        return
    assert body is not None
    if total == 0:
        await interaction.followup.send(body, ephemeral=ephemeral)
        log_slash_output(log_command, interaction, action="empty result", fields="total=0")
        return
    n_show = min(cfg.list_limit, total)
    parts = chunk_discord(body, limit=1900)
    await interaction.followup.send(parts[0], ephemeral=ephemeral, suppress_embeds=True)
    for part in parts[1:]:
        await interaction.followup.send(part, ephemeral=ephemeral, suppress_embeds=True)
    log_slash_output(
        log_command,
        interaction,
        action="sent unassigned issue list",
        fields=f"total={total} shown={n_show}",
    )


async def _send_issues_older_than_days_list(
    *,
    interaction: discord.Interaction,
    redmine: RedmineClient,
    ephemeral: bool,
    status_name: str,
    min_age_days: int,
    list_limit: int,
    log_command: str,
) -> None:
    """Fetch issues in ``status_name`` older than ``min_age_days`` and send formatted chunks (interaction already deferred)."""
    body, err, total = await _markdown_issues_by_status(
        redmine=redmine,
        status_name=status_name,
        min_age_days=min_age_days,
        list_limit=list_limit,
    )
    if err is not None:
        if "No Redmine issue status named" in err:
            await interaction.followup.send(err, ephemeral=ephemeral)
            log_slash_output(log_command, interaction, action="status name not found in Redmine")
        else:
            await interaction.followup.send(err, ephemeral=ephemeral)
            log_slash_error(log_command, interaction, action="redmine request failed", detail=err)
        return
    assert body is not None
    if total == 0:
        await interaction.followup.send(body, ephemeral=ephemeral)
        log_slash_output(log_command, interaction, action="empty result", fields="total=0")
        return
    n_show = min(list_limit, total)
    parts = chunk_discord(body, limit=1900)
    await interaction.followup.send(parts[0], ephemeral=ephemeral, suppress_embeds=True)
    for part in parts[1:]:
        await interaction.followup.send(part, ephemeral=ephemeral, suppress_embeds=True)
    log_slash_output(
        log_command,
        interaction,
        action="sent issue list",
        fields=f"total={total} shown={n_show}",
    )


class UltronCommandTree(app_commands.CommandTree):
    """discord.py invokes interaction_check on CommandTree only, not on commands.Bot."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = self.client
        return await _tree_interaction_check(bot.env, interaction)  # type: ignore[attr-defined]


def _nl_commands_enabled(app_cfg: AppConfig, env: EnvSettings) -> bool:
    """YAML ``discord.nl_commands`` or env ``ULTRON_NL_COMMANDS``."""
    return app_cfg.discord.nl_commands or env.ultron_nl_commands


async def _message_addresses_bot(client: discord.Client, message: discord.Message) -> tuple[bool, str]:
    """True if the user pinged the bot or is replying to one of the bot's messages (Reply chains have no mention)."""
    me = client.user
    if me is None:
        return False, ""
    if me in message.mentions or me.id in message.raw_mentions:
        return True, "mention"
    ref = message.reference
    if ref is None or ref.message_id is None:
        return False, ""
    resolved = ref.resolved
    if resolved is None:
        try:
            resolved = await message.channel.fetch_message(ref.message_id)
        except (discord.NotFound, discord.HTTPException):
            return False, ""
    try:
        author = resolved.author
    except AttributeError:
        return False, ""
    if author.id == me.id:
        return True, "reply_to_bot"
    return False, ""


class UltronBot(commands.Bot):
    def __init__(
        self,
        *,
        env: EnvSettings,
        app_cfg: AppConfig,
        redmine: RedmineClient,
        llm: LLMBackend,
    ) -> None:
        # guild_messages / dm_messages are not privileged — required to receive MESSAGE_CREATE (on_message).
        # message_content is privileged: only request it when DISCORD_MESSAGE_CONTENT_INTENT=1 and the portal toggle matches.
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.dm_messages = True
        if env.discord_message_content_intent:
            intents.message_content = True
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

    async def logs_channel_send(
        self,
        content: str,
        *,
        feature: Literal["startup", "whitelist"],
    ) -> None:
        """Post to the Discord logs channel when enabled and the matching ``features.*`` flag is true."""
        rl = self.app_cfg.discord.registration_log
        if not rl.enabled:
            return
        if feature == "startup" and not rl.features.startup:
            return
        if feature == "whitelist" and not rl.features.whitelist_events:
            return
        if not rl.channel_id:
            logger.warning("discord.registration_log: enabled is true but channel_id is 0")
            return
        ch = self.get_channel(rl.channel_id)
        if ch is None:
            logger.warning("logs channel: channel_id %s not visible to bot", rl.channel_id)
            return
        try:
            await ch.send(content[:_DISCORD_MSG_MAX])
        except discord.HTTPException as e:
            logger.warning("logs channel: send failed: %s", e)

    async def _announce_startup_in_logs_channel(self) -> None:
        """First action after the bot activates: summarize online status and scheduled-report settings."""
        rl = self.app_cfg.discord.registration_log
        if not rl.enabled or not rl.features.startup:
            return
        bot = self.user
        line1 = (
            f"**Ultron** is **online** — logged in as **{bot}** (`{bot.id}`)."
            if bot
            else "**Ultron** is **online**."
        )
        rcid = self.app_cfg.reports.channel_id
        if not rcid:
            line2 = "Scheduled ticket reports: **off** (`reports.channel_id` is 0)."
        else:
            ch = self.get_channel(rcid)
            vis = "reachable" if ch else "**not visible** to the bot (scheduled jobs will not run)"
            ab = self.app_cfg.schedules.abandoned
            sn = self.app_cfg.schedules.stale_new
            st_part = f"Stale-new: **{'on' if sn.enabled else 'off'}** / {sn.interval_hours}h"
            if sn.issue_status_name:
                st_part += f" (status filter **{sn.issue_status_name}**)"
            line2 = (
                f"Scheduled reports channel `{rcid}` ({vis}). "
                f"Abandoned: **{'on' if ab.enabled else 'off'}** / {ab.interval_hours}h · "
                f"{st_part}."
            )
        line3 = ""
        if not self.env.llm_enabled:
            line3 = (
                "\n**Language model:** none assigned — **`/summary`**, **`/ask_issue`**, and **`/note`** stay disabled until you "
                "configure one. You can still use Redmine listing, **`/ping`**, **`/token`**, admin commands, and "
                "scheduled reports (plain issue lists when no model is configured)."
            )
        await self.logs_channel_send(f"{line1}\n{line2}{line3}", feature="startup")

    async def on_message(self, discord_message: discord.Message) -> None:
        """Respond to @mentions or replies to the bot's messages (whitelisted users); logs ``source=chat``."""
        if discord_message.author.bot:
            return
        addressed, via = await _message_addresses_bot(self, discord_message)
        if not addressed:
            return
        wl = is_user_whitelisted(self.env.state_dir, discord_message.author.id)
        log_chat_mention_received(
            discord_message,
            via=via,
            whitelisted=wl,
            message_content_intent=self.env.discord_message_content_intent,
        )
        if not wl:
            log_chat_mention_ignored(discord_message, reason="not whitelisted")
            return

        nl_on = _nl_commands_enabled(self.app_cfg, self.env)
        if nl_on and self.env.llm_enabled:
            await self._handle_nl_chat_message(discord_message, via)
            return
        if nl_on and not self.env.llm_enabled:
            log_chat_mention_input(discord_message, fields="path=needs_llm", feature="nl_router")
            try:
                await discord_message.reply(
                    "Natural-language routing for @mentions needs a configured **language model**. "
                    "Use slash commands, or ask an operator to configure **llm_chain** / **LLM_*** in the environment.",
                    mention_author=False,
                )
                log_chat_mention_output(
                    discord_message,
                    action="reply sent (needs LLM)",
                    feature="nl_router",
                )
            except discord.HTTPException as e:
                log_chat_mention_error(
                    discord_message,
                    action="nl router reply failed (no LLM)",
                    detail=e,
                    feature="nl_router",
                )
            return

        log_chat_mention_input(discord_message, fields="path=nl_disabled", feature="nl_disabled")
        try:
            await discord_message.reply(_NL_DISABLED_MENTION_MSG, mention_author=False)
            log_chat_mention_output(
                discord_message,
                action="reply sent (nl disabled)",
                feature="nl_disabled",
            )
        except discord.HTTPException as e:
            log_chat_mention_error(discord_message, action="reply failed", detail=e, feature="nl_disabled")

    async def _handle_nl_chat_message(self, message: discord.Message, via: str) -> None:
        """LLM router → validated dispatch (whitelist already checked)."""
        user_text = (message.content or "").strip()
        preview = _truncate_for_log(user_text)
        log_chat_mention_input(
            message,
            fields=f"via={via} text_preview={preview!r}",
            feature="nl_router",
        )

        status_msg: discord.Message | None = None
        try:
            status_msg = await message.reply(_NL_STATUS_ROUTING, mention_author=False)
        except discord.HTTPException as e:
            log_chat_mention_error(
                message,
                action="nl status bubble failed",
                detail=e,
                feature="nl_router",
            )

        t0 = time.monotonic()
        try:
            outcome = await run_nl_router(self.llm, user_text=user_text, via=via)
        except NoLLMConfiguredError:
            await _nl_edit_or_reply(message, status_msg, _NO_LLM_SLASH_MSG)
            log_chat_mention_output(message, action="routed (no LLM at runtime)", feature="nl_router")
            return
        except (APITimeoutError, httpx.TimeoutException) as e:
            logger.warning("nl_router LLM timeout: %s", e)
            await _nl_edit_or_reply(message, status_msg, _TIMEOUT_USER_MSG)
            log_chat_mention_output(message, action="routed (LLM timeout)", feature="nl_router")
            return
        except LLMChainExhaustedError as e:
            logger.error(
                "nl_router: all LLM chain providers failed | backends=%s | last=%s: %s",
                e.provider_count,
                type(e.last_error).__name__,
                safe_exc_message(e.last_error),
            )
            await _nl_edit_or_reply(message, status_msg, self.app_cfg.discord.llm_chain_all_failed_message)
            log_chat_mention_output(message, action="routed (LLM chain exhausted)", feature="nl_router")
            return
        except Exception as e:
            logger.exception("nl_router failed: %s", e)
            await _nl_edit_or_reply(
                message,
                status_msg,
                "Could not route your message. Check bot logs.",
            )
            log_chat_mention_error(message, action="nl_router exception", detail=e, feature="nl_router")
            return

        elapsed = time.monotonic() - t0
        logger.info(
            "source=chat | nl_router | classified | outcome=%s | elapsed_s=%.3f",
            type(outcome).__name__,
            elapsed,
            extra={"chat_phase": "OUTPUT", "message_source": "chat"},
        )
        chat_log.info(
            "source=chat | nl_router | classified | outcome=%s | elapsed_s=%.3f",
            type(outcome).__name__,
            elapsed,
            extra={"message_source": "chat"},
        )

        if isinstance(outcome, NLAdminRejected):
            logger.warning(
                "source=chat | nl_router | rejected_admin_command | command=%s user_id=%s",
                outcome.command,
                message.author.id,
            )
            chat_log.warning(
                "source=chat | nl_router | rejected_admin_command | command=%s",
                outcome.command,
                extra={"message_source": "chat"},
            )
            await _nl_edit_or_reply(
                message,
                status_msg,
                "I cannot run **admin** or **token** commands from chat. "
                "Use slash commands such as **`/approve`**, **`/remove`**, **`/show_config`**, **`/token`**.",
            )
            log_chat_mention_output(
                message,
                action="rejected admin command",
                fields=f"command={outcome.command!r}",
                feature="nl_router",
            )
            return
        if isinstance(outcome, NLParseError):
            await _nl_edit_or_reply(
                message,
                status_msg,
                f"I could not interpret that ({outcome.detail}). Try rephrasing or use slash commands.",
            )
            log_chat_mention_output(message, action="parse error reply", feature="nl_router")
            return
        if isinstance(outcome, NLChat):
            await _reply_chunked_to_message(message, outcome.message, edit_first=status_msg)
            log_chat_mention_output(message, action="conversational chat reply", feature="nl_router")
            return
        if isinstance(outcome, NLInvoke):
            dispatch_line = _nl_dispatch_status_line(outcome.command, outcome.args)
            try:
                if status_msg is not None:
                    await status_msg.edit(content=dispatch_line)
            except discord.HTTPException:
                pass
            logger.info(
                "source=chat | nl_router | command_accepted | command=%s | user_id=%s",
                outcome.command,
                message.author.id,
                extra={"chat_phase": "OUTPUT", "message_source": "chat"},
            )
            chat_log.info(
                "source=chat | nl_router | command_accepted | command=%s | feedback=%s",
                outcome.command,
                _truncate_for_log(dispatch_line, 160),
                extra={"chat_phase": "OUTPUT", "message_source": "chat"},
            )
            await self._run_nl_invoke(message, outcome, status_message=status_msg)
            log_chat_mention_output(
                message,
                action="invoke completed",
                fields=f"command={outcome.command!r}",
                feature="nl_router",
            )
            return

    async def _run_nl_invoke(
        self,
        message: discord.Message,
        inv: NLInvoke,
        *,
        status_message: discord.Message | None = None,
    ) -> None:
        """Execute a validated non-admin command from the NL router."""
        cmd = inv.command
        args = inv.args
        logger.info("source=chat | nl_router | dispatch | command=%s args=%r", cmd, args)
        chat_log.info("source=chat | nl_router | dispatch | command=%s", cmd, extra={"message_source": "chat"})

        async def _err(msg: str) -> None:
            out = await _nl_edit_or_reply(message, status_message, msg[:_DISCORD_MSG_MAX])
            if out is None:
                log_chat_mention_error(
                    message,
                    action="nl dispatch error reply failed",
                    feature="nl_router",
                )

        try:
            if cmd == "ping":
                await _nl_edit_or_reply(message, status_message, "Pong")
                return
            if cmd == "help":
                await _reply_chunked_to_message(message, _HELP_TEXT, edit_first=status_message)
                return
            if cmd == "status":
                await _nl_edit_or_reply(message, status_message, "Status: OK (placeholder).")
                return
            if cmd == "new_issues":
                ni = self.app_cfg.discord.new_issues
                if not ni.status_name.strip():
                    await _err(
                        "Set **`discord.new_issues.status_name`** in `config.yaml` to your Redmine issue status label."
                    )
                    return
                body, err, _total = await _markdown_issues_by_status(
                    redmine=self.redmine,
                    status_name=ni.status_name.strip(),
                    min_age_days=ni.min_age_days,
                    list_limit=ni.list_limit,
                )
                if err is not None:
                    await _err(err)
                    return
                assert body is not None
                await _reply_chunked_to_message(message, body, edit_first=status_message)
                return
            if cmd == "issues_by_status":
                st = str(args["status"])
                ni = self.app_cfg.discord.new_issues
                body, err, _total = await _markdown_issues_by_status(
                    redmine=self.redmine,
                    status_name=st,
                    min_age_days=ni.min_age_days,
                    list_limit=ni.list_limit,
                )
                if err is not None:
                    await _err(err)
                    return
                assert body is not None
                await _reply_chunked_to_message(message, body, edit_first=status_message)
                return
            if cmd == "unassigned_issues":
                uo = self.app_cfg.discord.unassigned_open
                body, err, _total = await _markdown_unassigned_open_issues(redmine=self.redmine, cfg=uo)
                if err is not None:
                    await _err(err)
                    return
                assert body is not None
                await _reply_chunked_to_message(message, body, edit_first=status_message)
                return
            if cmd == "summary":
                issue_id = int(args["issue_id"])
                text = await summarize_issue(
                    redmine=self.redmine,
                    llm=self.llm,
                    issue_id=issue_id,
                    log_read_messages=self.app_cfg.logging.log_read_messages,
                    on_before_llm=None,
                    on_llm_chain_skip=None,
                )
                await _reply_chunked_to_message(message, text, edit_first=status_message)
                return
            if cmd == "ask_issue":
                issue_id = int(args["issue_id"])
                question = str(args["question"])
                text = await ask_about_issue(
                    redmine=self.redmine,
                    llm=self.llm,
                    issue_id=issue_id,
                    question=question,
                    log_read_messages=self.app_cfg.logging.log_read_messages,
                    on_before_llm=None,
                    on_llm_chain_skip=None,
                )
                await _reply_chunked_to_message(message, text, edit_first=status_message)
                return
            if cmd == "note":
                issue_id = int(args["issue_id"])
                raw = str(args["text"])
                posted, url = await add_formatted_note(
                    redmine=self.redmine,
                    llm=self.llm,
                    issue_id=issue_id,
                    raw_text=raw,
                    log_read_messages=self.app_cfg.logging.log_read_messages,
                    on_llm_chain_skip=None,
                    note_author_label=_discord_note_author_label(message.author),
                )
                excerpt = posted[:500] + ("…" if len(posted) > 500 else "")
                reply = f"Note added to [{issue_id}]({url}).\n\n**Preview:**\n{excerpt}"
                await _reply_chunked_to_message(message, reply, edit_first=status_message)
                return
        except IssueNotFound:
            await _err("Issue not found in Redmine.")
            return
        except RedmineError as e:
            logger.warning("nl dispatch RedmineError: %s", e)
            await _err("Redmine request failed. Try again later.")
            return
        except (APITimeoutError, httpx.TimeoutException):
            await _err(_TIMEOUT_USER_MSG)
            return
        except LLMChainExhaustedError as e:
            logger.error("nl dispatch LLMChainExhaustedError: %s", e)
            await _err(self.app_cfg.discord.llm_chain_all_failed_message)
            return
        except Exception as e:
            logger.exception("nl dispatch failed: %s", e)
            await _err("Something went wrong. Check bot logs.")
            return

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
                    "You are already approved. You can use **`/summary`**, **`/ask_issue`**, and **`/note`** — you do not need a new token.",
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
            await self.logs_channel_send(
                f"**Registration** `/token` — user **{interaction.user}** (`{interaction.user.id}`) "
                "requested an approval token.",
                feature="whitelist",
            )

        @self.tree.command(name="ping", description="Simple connectivity check (replies Pong)")
        async def ping_cmd(interaction: discord.Interaction) -> None:
            _ping_reply = "Pong"
            log_slash_input("ping", interaction)
            try:
                if interaction.response.is_done():
                    # Something already acknowledged this interaction (unusual unless a race or library edge case).
                    await interaction.followup.send(_ping_reply, ephemeral=False)
                    log_action = "followup only (response already done)"
                elif interaction.guild is not None:
                    # Guild: defer + followup so the reply is a normal channel-visible webhook message.
                    await interaction.response.defer(ephemeral=False)
                    await interaction.followup.send(_ping_reply, ephemeral=False)
                    log_action = "defer + public followup posted to channel"
                else:
                    # DM: immediate response. (defer/send quirks in DM are avoided vs guild-only defer.)
                    await interaction.response.send_message(_ping_reply)
                    log_action = "send_message in DM"
            except discord.HTTPException as e:
                # 10062: token already used — common when two bot processes handle the same event (only one may ACK).
                if getattr(e, "code", None) == 10062:
                    logger.info(
                        "ping: unknown interaction (10062) — another process likely responded first; "
                        "ensure only one Ultron instance is running with this token"
                    )
                    return
                log_slash_error(
                    "ping",
                    interaction,
                    action="ping reply failed",
                    detail=e,
                )
                raise
            log_slash_output(
                "ping",
                interaction,
                action=log_action,
                fields=f"reply={_ping_reply!r}",
            )

        @self.tree.command(name="status", description="Bot status (placeholder)")
        async def status_cmd(interaction: discord.Interaction) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input("status", interaction, fields=f"ephemeral={ephemeral}")
            await interaction.response.send_message("Status: OK (placeholder).", ephemeral=ephemeral)
            log_slash_output(
                "status",
                interaction,
                action="sent status placeholder",
                fields=f"ephemeral={ephemeral}",
            )

        @self.tree.command(
            name="new_issues",
            description="Issues in configured new status, created ≥M days ago (see discord.new_issues in YAML).",
        )
        async def new_issues_cmd(interaction: discord.Interaction) -> None:
            ni = self.app_cfg.discord.new_issues
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input("new_issues", interaction, fields=f"ephemeral={ephemeral}")
            if not ni.status_name.strip():
                await interaction.response.send_message(
                    "Set **`discord.new_issues.status_name`** in `config.yaml` to your Redmine issue status "
                    "label (exact match, e.g. `New/Neu`).",
                    ephemeral=True,
                )
                log_slash_output("new_issues", interaction, action="missing discord.new_issues.status_name")
                return
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            await _send_issues_older_than_days_list(
                interaction=interaction,
                redmine=self.redmine,
                ephemeral=ephemeral,
                status_name=ni.status_name.strip(),
                min_age_days=ni.min_age_days,
                list_limit=ni.list_limit,
                log_command="new_issues",
            )

        @self.tree.command(
            name="issues_by_status",
            description="List issues by Redmine status name; min age & cap from discord.new_issues YAML.",
        )
        @app_commands.describe(
            status="Redmine issue status name (exact label; Administration → Issue statuses)."
        )
        async def issues_by_status_cmd(interaction: discord.Interaction, status: str) -> None:
            ni = self.app_cfg.discord.new_issues
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input(
                "issues_by_status",
                interaction,
                fields=f"ephemeral={ephemeral} status={status!r}",
            )
            st = status.strip()
            if not st:
                await interaction.response.send_message(
                    "Pass **`status`**: the Redmine issue status name (e.g. `New/Neu`).",
                    ephemeral=True,
                )
                log_slash_output("issues_by_status", interaction, action="missing status parameter")
                return
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            await _send_issues_older_than_days_list(
                interaction=interaction,
                redmine=self.redmine,
                ephemeral=ephemeral,
                status_name=st,
                min_age_days=ni.min_age_days,
                list_limit=ni.list_limit,
                log_command="issues_by_status",
            )

        @self.tree.command(
            name="unassigned_issues",
            description="Unassigned open issues older than min age; see discord.unassigned_open in config YAML.",
        )
        async def unassigned_issues_cmd(interaction: discord.Interaction) -> None:
            uo = self.app_cfg.discord.unassigned_open
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input("unassigned_issues", interaction, fields=f"ephemeral={ephemeral}")
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            await _send_unassigned_open_issues_list(
                interaction=interaction,
                redmine=self.redmine,
                ephemeral=ephemeral,
                cfg=uo,
                log_command="unassigned_issues",
            )

        def _dev_placeholder_handler(slot: int):
            cmd_name = f"dev_slot_{slot}"

            async def _callback(interaction: discord.Interaction) -> None:
                log_slash_input(cmd_name, interaction)
                await interaction.response.send_message(
                    f"Placeholder **`/{cmd_name}`** (development slot **{slot}/10**). Not wired yet.",
                    ephemeral=True,
                )
                log_slash_output(
                    cmd_name,
                    interaction,
                    action="dev placeholder ack",
                    fields=f"slot={slot}",
                )

            _callback.__name__ = f"ultron_dev_slot_{slot}"
            return _callback

        for _slot in range(2, 11):
            # Discord requires command descriptions ≤ 100 characters.
            _desc = f"[{_slot}/10] Reserved. Tell commands apart by description; rename later."
            self.tree.command(name=f"dev_slot_{_slot}", description=_desc)(
                _dev_placeholder_handler(_slot)
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
            target = await _fetch_discord_user(interaction.client, uid)
            if target is None:
                logger.warning("approve: user_id=%s could not be resolved for display/DM", uid)
            await interaction.response.send_message(
                _whitelisted_user_ack_message(target, uid),
                ephemeral=True,
            )
            log_slash_output(
                "approve",
                interaction,
                action="sent ephemeral (user whitelisted)",
                fields=f"approved_user_id={uid} actor_id={interaction.user.id}",
            )
            await self.logs_channel_send(
                f"**Registration** `/approve` — whitelisted {_whitelisted_user_log_label(target, uid)} "
                f"(approved by **{interaction.user}** `{interaction.user.id}`).",
                feature="whitelist",
            )
            if target is not None:
                try:
                    await target.send(
                        "Your access request has been approved. You can now use **`/summary`**, **`/ask_issue`**, and **`/note`** with this bot.",
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
                await self.logs_channel_send(
                    f"**Registration** `/remove` — removed user id **{user_id}** "
                    f"(by **{interaction.user}** `{interaction.user.id}`).",
                    feature="whitelist",
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
                await self.logs_channel_send(
                    f"**Registration** `/remove` — user id **{user_id}** was not on the whitelist "
                    f"(by **{interaction.user}** `{interaction.user.id}`).",
                    feature="whitelist",
                )

        @self.tree.command(name="show_config", description="Show important bot configuration (admins only, ephemeral)")
        async def show_config_cmd(interaction: discord.Interaction) -> None:
            log_slash_input("show_config", interaction)
            body = _format_show_config(self.app_cfg, self.env)
            chunks = chunk_discord(body, limit=1900)
            await interaction.response.send_message(chunks[0], ephemeral=True)
            for part in chunks[1:]:
                await interaction.followup.send(part, ephemeral=True)
            log_slash_output("show_config", interaction, action="sent ephemeral config summary")

        @self.tree.command(name="summary", description="Summarize a Redmine ticket")
        @app_commands.describe(issue_id="Redmine issue number")
        async def summary_cmd(interaction: discord.Interaction, issue_id: int) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input(
                "summary",
                interaction,
                fields=f"issue_id={issue_id} ephemeral={ephemeral}",
            )
            if not self.env.llm_enabled:
                await interaction.response.send_message(_NO_LLM_SLASH_MSG, ephemeral=ephemeral)
                log_slash_output(
                    "summary",
                    interaction,
                    action="rejected (no language model configured)",
                    fields=f"issue_id={issue_id}",
                )
                return
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

        @self.tree.command(name="ask_issue", description="Ask a question about a Redmine ticket (LLM)")
        @app_commands.describe(issue_id="Redmine issue number", question="What to ask about this ticket")
        async def ask_issue_cmd(
            interaction: discord.Interaction,
            issue_id: int,
            question: str,
        ) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            q_chars = len(question)
            log_slash_input(
                "ask_issue",
                interaction,
                fields=f"issue_id={issue_id} question_chars={q_chars} ephemeral={ephemeral}",
            )
            if not self.env.llm_enabled:
                await interaction.response.send_message(_NO_LLM_SLASH_MSG, ephemeral=ephemeral)
                log_slash_output(
                    "ask_issue",
                    interaction,
                    action="rejected (no language model configured)",
                    fields=f"issue_id={issue_id}",
                )
                return

            async def ask_issue_error(msg: str) -> None:
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
                    "ask_issue",
                    interaction,
                    action="user-visible error",
                    fields=f"issue_id={issue_id} detail={short!r}",
                )

            await interaction.response.send_message(
                content=self.app_cfg.discord.summary_status_redmine,
                ephemeral=ephemeral,
            )
            log_slash_output(
                "ask_issue",
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
                        "ask_issue",
                        interaction,
                        action="edited message (LLM phase)",
                        fields=f"issue_id={issue_id} model={self.llm.model!r}",
                    )

                on_skip = _llm_chain_skip_discord_cb(
                    interaction=interaction,
                    ephemeral=ephemeral,
                    template=self.app_cfg.discord.llm_chain_skip_status,
                    command="ask_issue",
                    issue_id=issue_id,
                )
                text = await ask_about_issue(
                    redmine=self.redmine,
                    llm=self.llm,
                    issue_id=issue_id,
                    question=question,
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
                        logger.error("followup failed for ask_issue chunk (token may have expired): %s", e)
                        log_slash_error(
                            "ask_issue",
                            interaction,
                            action="followup chunk send failed",
                            detail=e,
                        )
                        break
                out_chars = sum(len(p) for p in parts)
                log_slash_output(
                    "ask_issue",
                    interaction,
                    action="delivered answer to user",
                    fields=(
                        f"issue_id={issue_id} output_chars={out_chars} chunks={len(parts)} "
                        f"elapsed_s={time.monotonic() - t0:.3f}"
                    ),
                )
            except IssueNotFound:
                await ask_issue_error(f"Issue **#{issue_id}** was not found in Redmine.")
            except RedmineError as e:
                logger.warning("Redmine error in ask_issue: %s", e)
                await ask_issue_error("Redmine request failed. Try again later.")
            except (APITimeoutError, httpx.TimeoutException) as e:
                logger.warning(
                    "LLM timeout in ask_issue for issue_id=%s: %s | "
                    "Often Ollama is still loading the model, the prompt is very large, or CPU inference is slow; "
                    "see Ollama logs. Increase LLM_TIMEOUT_SECONDS if the model can finish within one HTTP read.",
                    issue_id,
                    str(e),
                )
                await ask_issue_error(_TIMEOUT_USER_MSG)
            except LLMChainExhaustedError as e:
                logger.error(
                    "ask_issue: all LLM chain providers failed | backends=%s | last=%s: %s",
                    e.provider_count,
                    type(e.last_error).__name__,
                    safe_exc_message(e.last_error),
                )
                await ask_issue_error(self.app_cfg.discord.llm_chain_all_failed_message)
            except Exception as e:
                _log_slash_command_failure("ask_issue", e)
                await ask_issue_error("Something went wrong. Check bot logs.")

        @self.tree.command(name="note", description="Add an LLM-polished note to a Redmine ticket")
        @app_commands.describe(issue_id="Redmine issue number", text="Note text to append")
        async def note_cmd(interaction: discord.Interaction, issue_id: int, text: str) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input(
                "note",
                interaction,
                fields=f"issue_id={issue_id} raw_chars={len(text)} ephemeral={ephemeral}",
            )
            if not self.env.llm_enabled:
                await interaction.response.send_message(_NO_LLM_SLASH_MSG, ephemeral=ephemeral)
                log_slash_output(
                    "note",
                    interaction,
                    action="rejected (no language model configured)",
                    fields=f"issue_id={issue_id}",
                )
                return
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
            try:
                await self.tree.sync(guild=guild)
                logger.info("Slash commands synced to guild %s", self.env.discord_guild_id)
            except discord.Forbidden as e:
                logger.warning(
                    "Guild slash sync failed (Forbidden, API code=%s): %s. "
                    "The bot must be in that server with permission to create application commands — "
                    "re-invite using a URL that includes the **applications.commands** scope, and confirm "
                    "**DISCORD_GUILD_ID** is the correct numeric server id. Falling back to **global** sync.",
                    getattr(e, "code", None),
                    e,
                )
                await self.tree.sync()
                logger.info("Slash commands synced globally (may take up to ~1 hour to appear)")
        else:
            await self.tree.sync()
            logger.info("Slash commands synced globally (may take up to ~1 hour to appear)")

        self.abandoned_loop.change_interval(hours=max(1, self.app_cfg.schedules.abandoned.interval_hours))
        self.stale_new_loop.change_interval(hours=max(1, self.app_cfg.schedules.stale_new.interval_hours))

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "")
        try:
            await self.change_presence(status=discord.Status.online)
        except discord.HTTPException as e:
            logger.warning("change_presence(online) failed: %s", e)
        if not self.env.llm_enabled:
            logger.info(
                "No language model assigned — /summary, /ask_issue, and /note are disabled; "
                "Redmine slash commands, /ping, registration, and scheduled reports (plain lists) still work."
            )
        if self._jobs_started:
            return
        self._jobs_started = True

        # First: logs channel (startup summary before scheduled report loops).
        await self._announce_startup_in_logs_channel()

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
