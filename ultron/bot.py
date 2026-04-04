from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timedelta, timezone
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
from openai import APIConnectionError, APIStatusError, APITimeoutError

from ultron import __version__ as _ULTRON_VERSION
from ultron.discord_format import embed_issue_list_intro, embed_time_summary
from ultron.discord_interaction_errors import is_unknown_interaction_error
from ultron.config import (
    AppConfig,
    UnassignedOpenConfig,
    llm_chain_slash_flags,
    llm_chain_resolve_start_index,
    llm_chain_slash_model_override,
)
from ultron.redmine_listings import markdown_issues_by_status, markdown_unassigned_open_issues
from ultron.report_schedule import build_reports_startup_message, run_report_schedule_entry
from ultron.llm import (
    ChainSkipNotice,
    LLMBackend,
    LLMChainClient,
    LLMChainExhaustedError,
    NoLLMConfiguredError,
    NullLLMBackend,
    format_llm_endpoint,
    safe_exc_message,
)
from ultron.nl_router import (
    NLAdminRejected,
    NLChat,
    NLInvoke,
    NLParseError,
    run_nl_router,
)
from ultron.redmine import (
    IssueNotFound,
    RedmineClient,
    RedmineError,
    RedminePermissionError,
    resolve_redmine_user_for_time_summary,
    resolve_time_activity_id,
)
from ultron.time_reporting import compute_time_summary_buckets, fetch_spent_on_range_strings
from ultron.rpsls import MOVES, judge
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

# Console startup lines in setup_hook / first on_ready (see ultron-logging-phases.mdc).
_STARTUP_LOG_EXTRA: dict[str, str] = {"startup_phase": "STARTUP", "message_source": "startup"}

_TIMEOUT_USER_MSG = (
    "The language model did not respond in time. Try a shorter ticket, a faster model, "
    "or increase **timeout_seconds** on the relevant **llm_chain** entry in `config.yaml`."
)


def _llm_openai_compat_user_message() -> str:
    """User-facing hint when the OpenAI-compatible API returns a transport or HTTP error."""
    return (
        "Language model request failed (network, API key, base URL, or provider error). "
        "Check **config.yaml** `llm_chain` (**base_url**, **model**, **api_key_env** and matching keys in `.env`) "
        "and bot logs."
    )


_NO_LLM_SLASH_MSG = (
    "No language model is configured. Add at least one enabled entry under **llm_chain** in `config.yaml` "
    "(API keys live in `.env` under the variable names set by each entry's **api_key_env**). "
    "**`/summary`**, **`/ask_issue`**, and **`/note`** need a model."
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
    "Ollama may still have been loading the model or inferring; check server logs and raise **timeout_seconds** on the **llm_chain** entry if needed._"
)

_DISCORD_MSG_MAX = 2000


def _format_uptime_brief(start_utc: datetime) -> str:
    """Short uptime like ``2d 5h`` or ``<1 min`` for status text."""
    now = datetime.now(timezone.utc)
    s = start_utc
    if s.tzinfo is None:
        s = s.replace(tzinfo=timezone.utc)
    secs = max(0, int((now - s).total_seconds()))
    if secs < 60:
        return "<1 min"
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    parts: list[str] = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


def _format_status_message(
    *,
    env: EnvSettings,
    app_cfg: AppConfig,
    llm: LLMBackend,
    bot: discord.Client,
    ready_at_utc: datetime | None,
    guild: discord.Guild | None,
) -> str:
    """User-facing `/status` body: readable overview, no secrets."""
    ru = urlparse(env.redmine_url)
    redmine_host = ru.netloc or env.redmine_url.rstrip("/")

    where = (
        "Direct message"
        if guild is None
        else f"In server: **{escape_markdown(guild.name)}**"
    )

    lat = getattr(bot, "latency", 0.0) or 0.0
    latency_s = f"{round(lat * 1000)} ms" if lat > 0 else "—"

    uptime_s = _format_uptime_brief(ready_at_utc) if ready_at_utc is not None else "—"

    tz = (app_cfg.timezone or "").strip() or "UTC"

    if isinstance(llm, NullLLMBackend) or not env.llm_enabled:
        llm_line = "• **Language model:** off — `/summary`, `/ask_issue`, `/note`, and NL routing need **llm_chain**"
    elif isinstance(llm, LLMChainClient):
        n = len(app_cfg.llm_chain) if app_cfg.llm_chain else 0
        llm_line = f"• **Language model:** provider chain (**{n}** slots) · default model **`{llm.model}`**"
    else:
        llm_line = "• **Language model:** configured"

    nl_on = _nl_commands_enabled(app_cfg, env)
    nl_line = "• **@mention routing:** on (LLM maps mentions to allowed commands)" if nl_on else "• **@mention routing:** off"

    nj = len(app_cfg.report_schedule)
    if nj == 0:
        reports_line = "• **Scheduled channel reports:** none"
    elif app_cfg.reports.channel_id:
        reports_line = f"• **Scheduled channel reports:** **{nj}** job(s) → configured channel"
    else:
        reports_line = (
            f"• **Scheduled channel reports:** **{nj}** job(s) (set **`reports.channel_id`** to post)"
        )

    bot_user = bot.user
    bot_label = f"`{bot_user.name}`" if bot_user else "Ultron"
    bot_id = f"`{bot_user.id}`" if bot_user else "—"

    lines = [
        f"### Ultron · `v{_ULTRON_VERSION}`",
        "",
        f"You are {where}.",
        "",
        "**Connection**",
        f"• **Discord gateway latency:** {latency_s}",
        f"• **Uptime:** {uptime_s}",
        "",
        "**Integrations**",
        f"• **Redmine:** `{redmine_host}`",
        llm_line,
        "",
        "**Features**",
        nl_line,
        reports_line,
        f"• **Report timezone:** `{tz}`",
        "",
    ]
    rw_stats = getattr(bot, "redmine_write_stats_status_line", None)
    if callable(rw_stats):
        rw_s = rw_stats()
        if rw_s:
            lines.append("**Observability**")
            lines.append(rw_s)
            lines.append("")
    lines.extend(
        [
            "**This bot**",
            f"• {bot_label} · id {bot_id}",
            "",
            "• **Bot admins:** use **`/show_config`** for non-secret configuration detail.",
        ]
    )
    return "\n".join(lines)

_RPSLS_DISPLAY: dict[str, str] = {
    "rock": "Rock",
    "paper": "Paper",
    "scissors": "Scissors",
    "lizard": "Lizard",
    "spock": "Spock",
}
# (winner, loser) -> one-line rule (user-visible game copy).
_RPSLS_RULE: dict[tuple[str, str], str] = {
    ("rock", "scissors"): "Rock crushes scissors.",
    ("rock", "lizard"): "Rock crushes lizard.",
    ("paper", "rock"): "Paper covers rock.",
    ("paper", "spock"): "Paper disproves Spock.",
    ("scissors", "paper"): "Scissors cut paper.",
    ("scissors", "lizard"): "Scissors decapitate lizard.",
    ("lizard", "spock"): "Lizard poisons Spock.",
    ("lizard", "paper"): "Lizard eats paper.",
    ("spock", "scissors"): "Spock smashes scissors.",
    ("spock", "rock"): "Spock vaporizes rock.",
}


def _rpsls_outcome_text(user: str, bot: str, outcome: Literal["tie", "win", "lose"]) -> str:
    """Build the user-visible message (picks + outcome + rule line)."""
    du = _RPSLS_DISPLAY[user]
    db = _RPSLS_DISPLAY[bot]
    lines = [f"You: **{du}** · Bot: **{db}**"]
    if outcome == "tie":
        lines.append("Tie.")
    elif outcome == "win":
        lines.append("You win.")
        lines.append(_RPSLS_RULE[(user, bot)])
    else:
        lines.append("You lose.")
        lines.append(_RPSLS_RULE[(bot, user)])
    return "\n".join(lines)
# Initial /note status (avoid defer "thinking…" when responses are public)
_NOTE_PROCESSING_TEMPLATE = "Processing note with model {model}…"

# Reserved slash names for development; distinct descriptions until renamed (whitelist-gated like /ping).
_DEV_PLACEHOLDER_SLOTS: frozenset[str] = frozenset(f"dev_slot_{i}" for i in range(2, 11))


def _discord_note_author_label(user: discord.User) -> str:
    """Display name for the Redmine note header (`_name_`); uses guild nickname when applicable."""
    label = user.display_name.strip()
    return label or user.name.strip()

_HELP_TEXT = (
    """**Ultron — slash commands**

**Everyone**
• `/help` — This list.
• `/token` — One-time approval code (**DM only**). No code if you are already whitelisted.

**Whitelisted users**
• `/ping` — Quick check; replies are public in servers. Non-whitelisted users are denied like for other gated commands.
• `/status` — Summary: version, uptime, latency, Redmine host, LLM, NL routing, scheduled reports.
• `/rpsls` `move` — Rock–paper–scissors–lizard–Spock vs the bot.
• `/list_new_issues` — Issues in the configured “new” status past the minimum age (see `discord.new_issues`).
• `/issues_by_status` `status` — Same style of list for a Redmine status name (limits from `discord.new_issues`).
• `/list_unassigned_issues` — Unassigned open issues past the minimum age (`discord.unassigned_open`).
• `/time_summary` `user` — Redmine **spent hours** for a user: **today**, **this week** (Mon–today), **last 7 days** (by **spent_on**), and **last 24 h** (by **created_on**). `user` = Redmine login, numeric id, or **`me`**. If login lookup fails (permissions), set **redmine.user_id_by_login** in `config.yaml`.
• `/log_time` `issue_id` `hours` [`comments`] [`spent_on`] — Log spent hours (booked as the **Redmine API key** user). Optional **comments** and **spent_on** (YYYY-MM-DD). See **REDMINE_TIME_ACTIVITY_ID** in `.env` when Redmine has several activities.
• `/summary` `issue_id` [`llm_provider`] [`llm_model`] — Ticket summary (requires LLM). Optional provider/model: autocomplete when configured; omit for defaults.
• `/ask_issue` `issue_id` `question` [`llm_provider`] [`llm_model`] — Answer from the ticket text (requires LLM).
• `/note` `issue_id` `text` [`llm_provider`] [`llm_model`] — Append an LLM-polished note (requires LLM).

**@mention** or **reply**: whitelisted only. `discord.nl_commands` / `ULTRON_NL_COMMANDS` enables LLM routing into allowed commands.

Without **llm_chain**, `/summary`, `/ask_issue`, and `/note` are unavailable; listings, `/ping`, `/rpsls`, and `/token` still work.

**Bot admins only**
• `/approve` `token` — Approve a `/token` code.
• `/remove` `user_id` — Remove from the whitelist.
• `/show_config` — Non-secret settings (**ephemeral**)."""
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
    if command == "list_new_issues":
        return "Fetching **new issues** (configured Redmine status)…"
    if command == "issues_by_status":
        st = str(args.get("status", "")).strip()
        if len(st) > 120:
            st = st[:119] + "…"
        return f"Listing issues with status **{escape_markdown(st)}**…"
    if command == "list_unassigned_issues":
        return "Fetching **unassigned** open issues…"
    if command == "summary":
        return f"Summarizing issue **#{int(args['issue_id'])}**…"
    if command == "ask_issue":
        return f"Answering about issue **#{int(args['issue_id'])}**…"
    if command == "note":
        return f"Adding a note to issue **#{int(args['issue_id'])}**…"
    if command == "log_time":
        return (
            f"Logging **{float(args['hours']):g}** h on issue **#{int(args['issue_id'])}**…"
        )
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
    log_extra: dict[str, Any] | None = None,
) -> None:
    """Log slash **write**: bot sent or edited something visible on this interaction."""
    gl, uid, cid = _slash_ids(interaction)
    tail = f" {fields}" if fields else ""
    base_extra: dict[str, Any] = {"slash_phase": "OUTPUT", "message_source": "slash"}
    if log_extra:
        base_extra = {**base_extra, **log_extra}
    logger.info(
        "source=slash | %s | %s | user_id=%s guild_id=%s channel_id=%s%s",
        command,
        action,
        uid,
        gl,
        cid,
        tail,
        extra=base_extra,
    )
    cmd_log.info(
        "source=slash | command=%s user_id=%s guild_id=%s channel_id=%s | %s%s",
        command,
        uid,
        gl,
        cid,
        action,
        tail,
        extra=base_extra,
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


def log_slash_discord_unknown_interaction(command: str, interaction: discord.Interaction) -> None:
    """Log Discord HTTP 10062 *Unknown interaction* like other slash failures (WARNING, ERROR phase)."""
    detail = (
        "Discord API 10062 (Unknown interaction): interaction token expired or already acknowledged. "
        "Common: only one process may use a bot token — stop duplicate containers/systemd units/dev runs. "
        "Otherwise: slow host/network or a blocked event loop past Discord's ~3s acknowledgement window."
    )
    log_slash_error(command, interaction, action="unknown interaction", detail=detail)


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
        "source=chat | feature=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
        feature,
        uid,
        gl,
        cid,
        mid,
        tail,
        extra={"chat_phase": "INPUT", "message_source": "chat"},
    )
    chat_log.info(
        "source=chat | feature=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
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
        "source=chat | feature=%s | %s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
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
        "source=chat | feature=%s | %s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
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
        "source=chat | feature=%s | %s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
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
        "source=chat | feature=%s | %s | user_id=%s guild_id=%s channel_id=%s message_id=%s%s",
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
        "source=chat | reason=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s",
        reason,
        uid,
        gl,
        cid,
        mid,
        extra={"chat_phase": "IGNORE", "message_source": "chat"},
    )
    chat_log.info(
        "source=chat | reason=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s",
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
        "source=chat | via=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s "
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
        "source=chat | via=%s | user_id=%s guild_id=%s channel_id=%s message_id=%s whitelisted=%s",
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
        f"• **{env.environment_bindings.redmine_time_activity_id_env}:** "
        f"{'set' if os.environ.get(env.environment_bindings.redmine_time_activity_id_env, '').strip() else 'not set'} "
        f"(optional; disambiguates time-entry activity for **`/log_time`**) ",
        f"• **redmine.user_id_by_login:** {len(app_cfg.redmine.user_id_by_login)} alias(es) "
        f"(for **`/time_summary`** when login API lookup is unavailable)",
        f"• **redmine.time_summary_max_entries:** {app_cfg.redmine.time_summary_max_entries}",
        f"• **discord.ephemeral_default:** {app_cfg.discord.ephemeral_default}",
        f"• **discord.issue_metadata_header:** {app_cfg.discord.issue_metadata_header}",
        f"• **discord.new_issues:** status_name={ni.status_name!r} list_limit={ni.list_limit} "
        f"min_age_days={ni.min_age_days}",
        f"• **discord.registration_log:** enabled={app_cfg.discord.registration_log.enabled} "
        f"channel_id={app_cfg.discord.registration_log.channel_id}",
        f"• **discord.unassigned_open:** min_age_days={app_cfg.discord.unassigned_open.min_age_days} "
        f"list_limit={app_cfg.discord.unassigned_open.list_limit} "
        f"closed_status_prefixes={list(app_cfg.discord.unassigned_open.closed_status_prefixes)!r}",
        f"• **reports.channel_id:** {app_cfg.reports.channel_id}",
        f"• **reports.startup_message_enabled:** {app_cfg.reports.startup_message_enabled}",
        f"• **report_schedule:** {len(app_cfg.report_schedule)} job(s)",
        f"• **logging.log_read_messages:** {app_cfg.logging.log_read_messages}",
        f"• **DISCORD_MESSAGE_CONTENT_INTENT:** {env.discord_message_content_intent} "
        "(privileged **Message Content** intent; portal must match if true)",
        f"• **discord.nl_commands:** {app_cfg.discord.nl_commands} "
        "(natural-language @mention router; also env **ULTRON_NL_COMMANDS**)",
        f"• **discord.slash_show_llm_option_hints:** {app_cfg.discord.slash_show_llm_option_hints} "
        "(richer slash descriptions/autocomplete for LLM / model options)",
    ]
    for i, ent in enumerate(app_cfg.report_schedule):
        lines.append(
            f"• **report_schedule[{i}]:** command={ent.command!r} interval_h={ent.interval_hours} args={dict(ent.args)!r}"
        )
    if not env.llm_enabled:
        lines.append(
            "• **llm:** no language model configured — **`/summary`** / **`/ask_issue`** / **`/note`** are disabled until you "
            "add **llm_chain** (scheduled channel listings still run)"
        )
    else:
        lines.append("• **llm_chain:**")
        assert app_cfg.llm_chain is not None
        for i, spec in enumerate(app_cfg.llm_chain):
            label = spec.name or f"entry[{i}]"
            models_s = ", ".join(spec.models) if len(spec.models) > 1 else spec.model
            lines.append(f"  – {label}: `{spec.base_url}` / models `{models_s}`")
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


async def _send_unassigned_open_issues_list(
    *,
    interaction: discord.Interaction,
    redmine: RedmineClient,
    ephemeral: bool,
    cfg: UnassignedOpenConfig,
    log_command: str,
) -> None:
    """List unassigned open issues past min age; interaction already deferred."""
    body, err, total = await markdown_unassigned_open_issues(redmine=redmine, cfg=cfg)
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
    emb = embed_issue_list_intro(
        title="Unassigned open issues",
        total=n_show,
        first_body=parts[0],
    )
    await interaction.followup.send(embed=emb, ephemeral=ephemeral)
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
    body, err, total = await markdown_issues_by_status(
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
    emb = embed_issue_list_intro(
        title=f"Issues — {status_name}",
        total=n_show,
        first_body=parts[0],
    )
    await interaction.followup.send(embed=emb, ephemeral=ephemeral)
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

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if is_unknown_interaction_error(error):
            cmd = interaction.command
            name = cmd.name if cmd is not None else "?"
            log_slash_discord_unknown_interaction(name, interaction)
            return
        await super().on_error(interaction, error)


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
        self._ready_startup_logged = False
        #: Set on first ``on_ready`` for `/status` uptime (UTC).
        self._ready_at_utc: datetime | None = None
        #: UTC timestamps: last successful tick anchor per ``report_schedule`` index (see ``report_schedule_loop``).
        self._report_schedule_last_run: dict[int, datetime] = {}
        #: Counts of successful Redmine-mutating operations in this process (for `/status`).
        self._redmine_writes: dict[str, int] = {}

    def record_redmine_write(self, operation: str) -> None:
        self._redmine_writes[operation] = self._redmine_writes.get(operation, 0) + 1

    def redmine_write_stats_status_line(self) -> str:
        if not self._redmine_writes:
            return "• **Redmine writes:** none recorded this session"
        parts = [f"`{k}` × **{v}**" for k, v in sorted(self._redmine_writes.items())]
        return "• **Redmine writes:** " + ", ".join(parts)

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
        """Post startup info to the logs channel when enabled (avoid duplicating the reports-channel welcome)."""
        rl = self.app_cfg.discord.registration_log
        if not rl.enabled or not rl.features.startup:
            return
        reports_welcome_elsewhere = bool(self.app_cfg.reports.channel_id) and bool(
            self.app_cfg.reports.startup_message_enabled
        )
        parts: list[str] = []
        if not reports_welcome_elsewhere:
            parts.append("**Ultron** is **online**.")
        if not self.env.llm_enabled:
            parts.append(
                "**Language model:** none assigned — **`/summary`**, **`/ask_issue`**, and **`/note`** stay disabled until you "
                "configure one. You can still use Redmine listing, **`/ping`**, **`/token`**, admin commands, and "
                "scheduled Redmine listings in the reports channel."
            )
        if not parts:
            return
        await self.logs_channel_send("\n".join(parts), feature="startup")

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
                    "Use slash commands, or ask an operator to configure **llm_chain** in `config.yaml` (and API keys in `.env`).",
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
        except (APIConnectionError, APIStatusError) as e:
            logger.warning(
                "nl_router LLM API error | %s: %s",
                type(e).__name__,
                safe_exc_message(e),
            )
            await _nl_edit_or_reply(
                message,
                status_msg,
                _llm_openai_compat_user_message(),
            )
            log_chat_mention_output(message, action="routed (LLM API error)", feature="nl_router")
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
            extra={"chat_phase": "ROUTER", "message_source": "chat"},
        )
        chat_log.info(
            "source=chat | nl_router | classified | outcome=%s | elapsed_s=%.3f",
            type(outcome).__name__,
            elapsed,
            extra={"chat_phase": "ROUTER", "message_source": "chat"},
        )

        if isinstance(outcome, NLAdminRejected):
            logger.warning(
                "source=chat | nl_router | rejected_admin_command | command=%s user_id=%s",
                outcome.command,
                message.author.id,
                extra={"chat_phase": "DENIED", "message_source": "chat"},
            )
            chat_log.warning(
                "source=chat | nl_router | rejected_admin_command | command=%s",
                outcome.command,
                extra={"chat_phase": "DENIED", "message_source": "chat"},
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
                extra={"chat_phase": "ROUTER", "message_source": "chat"},
            )
            chat_log.info(
                "source=chat | nl_router | command_accepted | command=%s | feedback=%s",
                outcome.command,
                _truncate_for_log(dispatch_line, 160),
                extra={"chat_phase": "ROUTER", "message_source": "chat"},
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
        logger.info(
            "source=chat | nl_router | dispatch | command=%s args=%r",
            cmd,
            args,
            extra={"chat_phase": "ROUTER", "message_source": "chat"},
        )
        chat_log.info(
            "source=chat | nl_router | dispatch | command=%s",
            cmd,
            extra={"chat_phase": "ROUTER", "message_source": "chat"},
        )

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
                st = _format_status_message(
                    env=self.env,
                    app_cfg=self.app_cfg,
                    llm=self.llm,
                    bot=self,
                    ready_at_utc=self._ready_at_utc,
                    guild=message.guild,
                )
                await _nl_edit_or_reply(message, status_message, st)
                return
            if cmd == "list_new_issues":
                ni = self.app_cfg.discord.new_issues
                if not ni.status_name.strip():
                    await _err(
                        "Set **`discord.new_issues.status_name`** in `config.yaml` to your Redmine issue status label."
                    )
                    return
                body, err, _total = await markdown_issues_by_status(
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
                body, err, _total = await markdown_issues_by_status(
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
            if cmd == "list_unassigned_issues":
                uo = self.app_cfg.discord.unassigned_open
                body, err, _total = await markdown_unassigned_open_issues(redmine=self.redmine, cfg=uo)
                if err is not None:
                    await _err(err)
                    return
                assert body is not None
                await _reply_chunked_to_message(message, body, edit_first=status_message)
                return
            if cmd == "log_time":
                issue_id = int(args["issue_id"])
                hours = float(args["hours"])
                try:
                    activities = await self.redmine.list_time_entry_activities()
                    activity_id = resolve_time_activity_id(
                        activities,
                        os.environ.get(self.env.environment_bindings.redmine_time_activity_id_env),
                    )
                    te = await self.redmine.create_time_entry(
                        issue_id, hours, activity_id=activity_id
                    )
                except ValueError as e:
                    await _err(str(e))
                    return
                except RedminePermissionError as e:
                    await _err(str(e))
                    return
                except RedmineError as e:
                    hint = getattr(e, "user_message", None)
                    await _err(hint or "Redmine request failed. Try again later.")
                    return
                self.record_redmine_write("time_entry_create")
                url = self.redmine.issue_url(issue_id)
                reply = f"Logged **{hours:g}** h on issue [{issue_id}]({url})."
                tid = te.get("id")
                if tid is not None:
                    reply += f"\n• Time entry **#{tid}**"
                await _reply_chunked_to_message(message, reply, edit_first=status_message)
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
                    issue_metadata_header=self.app_cfg.discord.issue_metadata_header,
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
                    issue_metadata_header=self.app_cfg.discord.issue_metadata_header,
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
                self.record_redmine_write("issue_note")
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
        except (APIConnectionError, APIStatusError) as e:
            logger.warning(
                "nl dispatch LLM API error | %s: %s",
                type(e).__name__,
                safe_exc_message(e),
            )
            await _err(_llm_openai_compat_user_message())
            return
        except LLMChainExhaustedError as e:
            logger.error("nl dispatch LLMChainExhaustedError: %s", e)
            await _err(self.app_cfg.discord.llm_chain_all_failed_message)
            return
        except Exception as e:
            logger.exception("nl dispatch failed: %s", e)
            await _err("Something went wrong. Check bot logs.")
            return

    def _slash_register_llm_extras(self) -> tuple[bool, bool]:
        """Register optional ``llm_provider`` / ``llm_model`` on LLM slash commands when a real LLM exists."""
        if isinstance(self.llm, NullLLMBackend):
            return False, False
        chain = self.app_cfg.llm_chain
        assert chain is not None
        return llm_chain_slash_flags(chain)

    def _slash_resolve_llm_kw_display(
        self,
        llm_provider: str | None,
        llm_model: str | None,
        *,
        cmd_need_prov: bool,
        cmd_need_model: bool,
    ) -> tuple[str | None, str | None, str]:
        def _opt_str(s: str | None) -> str | None:
            if s is None:
                return None
            t = str(s).strip()
            return t if t else None

        llm_provider = _opt_str(llm_provider)
        llm_model = _opt_str(llm_model)
        chain = self.app_cfg.llm_chain
        assert chain is not None
        start_idx = llm_chain_resolve_start_index(
            chain, llm_provider if cmd_need_prov else None
        )
        mo, display = llm_chain_slash_model_override(
            chain,
            start_idx,
            llm_model,
            command_includes_model_option=cmd_need_model,
        )
        start_kw = str(start_idx) if cmd_need_prov else None
        return start_kw, mo, display

    def _slash_desc_llm_provider(self) -> str:
        chain = self.app_cfg.llm_chain
        assert chain is not None
        if self.app_cfg.discord.slash_show_llm_option_hints:
            parts: list[str] = []
            for i, s in enumerate(chain):
                label = (s.name or f"[{i}]").strip()
                parts.append(f"{label} (slot {i})")
            base = "LLM to try first: " + "; ".join(parts)
            return base if len(base) <= 100 else base[:97] + "…"
        return "Configured LLM to try first (see llm_chain in config.yaml)."

    def _slash_desc_llm_model(self) -> str:
        chain = self.app_cfg.llm_chain
        assert chain is not None
        if self.app_cfg.discord.slash_show_llm_option_hints:
            # Discord caps option descriptions at 100 chars; list every model per slot elsewhere (autocomplete).
            return (
                "Autocomplete lists models for the selected llm_provider; see llm_chain in config.yaml."
            )[:100]
        return "Model for the selected LLM; omit for the configured default."

    async def _slash_ac_llm_provider(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        chain = self.app_cfg.llm_chain
        assert chain is not None
        cur = (current or "").strip().lower()
        out: list[app_commands.Choice[str]] = []
        multi = len(chain) > 1
        for i, spec in enumerate(chain):
            val = str(i)
            label = (spec.name.strip() if spec.name else f"slot {i}")
            if self.app_cfg.discord.slash_show_llm_option_hints and multi:
                endpoint = format_llm_endpoint(spec.base_url)
                label = f"{label} · {endpoint}"[:100]
            elif self.app_cfg.discord.slash_show_llm_option_hints:
                label = label[:100]
            if not cur or cur in val or cur in label.lower():
                out.append(app_commands.Choice(name=label[:100], value=val))
        return out[:25]

    async def _slash_ac_llm_model(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        chain = self.app_cfg.llm_chain
        assert chain is not None
        cur = (current or "").strip().lower()
        ns = interaction.namespace
        prov_raw = getattr(ns, "llm_provider", None)
        try:
            idx = llm_chain_resolve_start_index(chain, prov_raw)
        except ValueError:
            idx = 0
        models = chain[idx].models
        choices: list[app_commands.Choice[str]] = []
        for m in models:
            if not cur or cur in m.lower():
                choices.append(app_commands.Choice(name=m[:100], value=m))
        return choices[:25]

    async def _run_slash_summary(
        self,
        interaction: discord.Interaction,
        issue_id: int,
        *,
        llm_provider: str | None,
        llm_model: str | None,
        cmd_need_prov: bool,
        cmd_need_model: bool,
    ) -> None:
        ephemeral = self.app_cfg.discord.ephemeral_default
        fields = f"issue_id={issue_id} ephemeral={ephemeral}"
        if cmd_need_prov:
            fields += f" llm_provider={llm_provider!r}"
        if cmd_need_model:
            fields += f" llm_model={llm_model!r}"
        log_slash_input("summary", interaction, fields=fields)
        if not self.env.llm_enabled:
            await interaction.response.send_message(_NO_LLM_SLASH_MSG, ephemeral=ephemeral)
            log_slash_output(
                "summary",
                interaction,
                action="rejected (no language model configured)",
                fields=f"issue_id={issue_id}",
            )
            return

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

        try:
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        except discord.HTTPException as e:
            if is_unknown_interaction_error(e):
                log_slash_discord_unknown_interaction("summary", interaction)
                return
            log_slash_error("summary", interaction, action="defer failed", detail=e)
            raise

        log_slash_output(
            "summary",
            interaction,
            action="deferred (summary pipeline)",
            fields=f"issue_id={issue_id}",
        )
        t0 = time.monotonic()
        try:
            try:
                sp, mo, display = self._slash_resolve_llm_kw_display(
                    llm_provider,
                    llm_model,
                    cmd_need_prov=cmd_need_prov,
                    cmd_need_model=cmd_need_model,
                )
            except ValueError as ve:
                await summary_error(str(ve))
                return

            await interaction.edit_original_response(
                content=_trunc(self.app_cfg.discord.summary_status_redmine),
            )
            log_slash_output(
                "summary",
                interaction,
                action="updated message (fetching Redmine)",
                fields=f"issue_id={issue_id}",
            )

            async def on_before_llm(d_model: str) -> None:
                await _edit_or_followup(
                    interaction,
                    self.app_cfg.discord.summary_status_llm.format(model=d_model),
                    ephemeral=ephemeral,
                )
                log_slash_output(
                    "summary",
                    interaction,
                    action="edited message (LLM phase)",
                    fields=f"issue_id={issue_id} model={d_model!r}",
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
                issue_metadata_header=self.app_cfg.discord.issue_metadata_header,
                start_provider=sp,
                model_override=mo,
                llm_display_model=display,
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
        except ValueError as e:
            await summary_error(str(e))
        except (APITimeoutError, httpx.TimeoutException) as e:
            logger.warning(
                "LLM timeout in summary for issue_id=%s: %s | "
                "Often Ollama is still loading the model, the prompt is very large, or CPU inference is slow; "
                "see Ollama logs. Increase **timeout_seconds** on the **llm_chain** entry if the model can finish within one HTTP read.",
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
        except (APIConnectionError, APIStatusError) as e:
            logger.warning(
                "summary LLM API error | %s: %s",
                type(e).__name__,
                safe_exc_message(e),
            )
            await summary_error(_llm_openai_compat_user_message())
        except Exception as e:
            _log_slash_command_failure("summary", e)
            await summary_error("Something went wrong. Check bot logs.")

    async def _run_slash_ask_issue(
        self,
        interaction: discord.Interaction,
        issue_id: int,
        question: str,
        *,
        llm_provider: str | None,
        llm_model: str | None,
        cmd_need_prov: bool,
        cmd_need_model: bool,
    ) -> None:
        ephemeral = self.app_cfg.discord.ephemeral_default
        q_chars = len(question)
        fields = f"issue_id={issue_id} question_chars={q_chars} ephemeral={ephemeral}"
        if cmd_need_prov:
            fields += f" llm_provider={llm_provider!r}"
        if cmd_need_model:
            fields += f" llm_model={llm_model!r}"
        log_slash_input("ask_issue", interaction, fields=fields)
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

        try:
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        except discord.HTTPException as e:
            if is_unknown_interaction_error(e):
                log_slash_discord_unknown_interaction("ask_issue", interaction)
                return
            log_slash_error("ask_issue", interaction, action="defer failed", detail=e)
            raise

        log_slash_output(
            "ask_issue",
            interaction,
            action="deferred (ask_issue pipeline)",
            fields=f"issue_id={issue_id}",
        )
        t0 = time.monotonic()
        try:
            try:
                sp, mo, display = self._slash_resolve_llm_kw_display(
                    llm_provider,
                    llm_model,
                    cmd_need_prov=cmd_need_prov,
                    cmd_need_model=cmd_need_model,
                )
            except ValueError as ve:
                await ask_issue_error(str(ve))
                return

            await interaction.edit_original_response(
                content=_trunc(self.app_cfg.discord.summary_status_redmine),
            )
            log_slash_output(
                "ask_issue",
                interaction,
                action="updated message (fetching Redmine)",
                fields=f"issue_id={issue_id}",
            )

            async def on_before_llm(d_model: str) -> None:
                await _edit_or_followup(
                    interaction,
                    self.app_cfg.discord.summary_status_llm.format(model=d_model),
                    ephemeral=ephemeral,
                )
                log_slash_output(
                    "ask_issue",
                    interaction,
                    action="edited message (LLM phase)",
                    fields=f"issue_id={issue_id} model={d_model!r}",
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
                issue_metadata_header=self.app_cfg.discord.issue_metadata_header,
                start_provider=sp,
                model_override=mo,
                llm_display_model=display,
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
        except ValueError as e:
            await ask_issue_error(str(e))
        except (APITimeoutError, httpx.TimeoutException) as e:
            logger.warning(
                "LLM timeout in ask_issue for issue_id=%s: %s | "
                "Often Ollama is still loading the model, the prompt is very large, or CPU inference is slow; "
                "see Ollama logs. Increase **timeout_seconds** on the **llm_chain** entry if the model can finish within one HTTP read.",
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
        except (APIConnectionError, APIStatusError) as e:
            logger.warning(
                "ask_issue LLM API error | %s: %s",
                type(e).__name__,
                safe_exc_message(e),
            )
            await ask_issue_error(_llm_openai_compat_user_message())
        except Exception as e:
            _log_slash_command_failure("ask_issue", e)
            await ask_issue_error("Something went wrong. Check bot logs.")

    async def _run_slash_note(
        self,
        interaction: discord.Interaction,
        issue_id: int,
        text: str,
        *,
        llm_provider: str | None,
        llm_model: str | None,
        cmd_need_prov: bool,
        cmd_need_model: bool,
    ) -> None:
        ephemeral = self.app_cfg.discord.ephemeral_default
        fields = f"issue_id={issue_id} raw_chars={len(text)} ephemeral={ephemeral}"
        if cmd_need_prov:
            fields += f" llm_provider={llm_provider!r}"
        if cmd_need_model:
            fields += f" llm_model={llm_model!r}"
        log_slash_input("note", interaction, fields=fields)
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

        try:
            sp, mo, display = self._slash_resolve_llm_kw_display(
                llm_provider,
                llm_model,
                cmd_need_prov=cmd_need_prov,
                cmd_need_model=cmd_need_model,
            )
        except ValueError as ve:
            await interaction.response.send_message(str(ve), ephemeral=ephemeral)
            log_slash_output("note", interaction, action="user-visible error", fields=str(ve)[:120])
            return

        await interaction.response.send_message(
            content=_NOTE_PROCESSING_TEMPLATE.format(model=display),
            ephemeral=ephemeral,
        )
        log_slash_output(
            "note",
            interaction,
            action="sent initial status (processing note)",
            fields=f"issue_id={issue_id} model={display!r}",
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
                start_provider=sp,
                model_override=mo,
            )
            excerpt = formatted[:500] + ("…" if len(formatted) > 500 else "")
            reply = f"Note added to [{issue_id}]({url}).\n\n**Preview:**\n{excerpt}"
            await _edit_or_followup(interaction, reply, ephemeral=ephemeral)
            self.record_redmine_write("issue_note")
            log_slash_output(
                "note",
                interaction,
                action="delivered confirmation to user",
                fields=(
                    f"issue_id={issue_id} output_chars={len(reply)} "
                    f"elapsed_s={time.monotonic() - t0:.3f}"
                ),
                log_extra={"redmine_operation": "issue_note", "issue_id": issue_id},
            )
        except IssueNotFound:
            await note_error(
                f"Issue **#{issue_id}** was not found in Redmine. No note was added.",
            )
        except RedmineError as e:
            logger.warning("Redmine error in note: %s", e)
            await note_error("Redmine request failed. Try again later.")
        except ValueError as e:
            await note_error(str(e))
        except (APITimeoutError, httpx.TimeoutException) as e:
            logger.warning(
                "LLM timeout in note for issue_id=%s: %s | "
                "Often Ollama is still loading the model or inference is slow; see Ollama logs. "
                "Increase **timeout_seconds** on the **llm_chain** entry if needed.",
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
        except (APIConnectionError, APIStatusError) as e:
            logger.warning(
                "note LLM API error | %s: %s",
                type(e).__name__,
                safe_exc_message(e),
            )
            await note_error(_llm_openai_compat_user_message())
        except Exception as e:
            _log_slash_command_failure("note", e)
            await note_error("Something went wrong. Check bot logs.")

    def _register_slash_summary_ask_note(self) -> None:
        need_prov, need_model = self._slash_register_llm_extras()
        prov_desc = self._slash_desc_llm_provider()
        model_desc = self._slash_desc_llm_model()
        bot = self

        async def ac_prov(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
            return await bot._slash_ac_llm_provider(interaction, current)

        async def ac_model(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
            return await bot._slash_ac_llm_model(interaction, current)

        if not need_prov and not need_model:

            @self.tree.command(name="summary", description="Summarize a Redmine ticket")
            @app_commands.describe(issue_id="Redmine issue number")
            async def summary_cmd(interaction: discord.Interaction, issue_id: int) -> None:
                await bot._run_slash_summary(
                    interaction,
                    issue_id,
                    llm_provider=None,
                    llm_model=None,
                    cmd_need_prov=False,
                    cmd_need_model=False,
                )

            @self.tree.command(
                name="ask_issue",
                description="Ask a question about a Redmine ticket (LLM)",
            )
            @app_commands.describe(
                issue_id="Redmine issue number",
                question="What to ask about this ticket",
            )
            async def ask_issue_cmd(
                interaction: discord.Interaction,
                issue_id: int,
                question: str,
            ) -> None:
                await bot._run_slash_ask_issue(
                    interaction,
                    issue_id,
                    question,
                    llm_provider=None,
                    llm_model=None,
                    cmd_need_prov=False,
                    cmd_need_model=False,
                )

            @self.tree.command(
                name="note",
                description="Add an LLM-polished note to a Redmine ticket",
            )
            @app_commands.describe(issue_id="Redmine issue number", text="Note text to append")
            async def note_cmd(interaction: discord.Interaction, issue_id: int, text: str) -> None:
                await bot._run_slash_note(
                    interaction,
                    issue_id,
                    text,
                    llm_provider=None,
                    llm_model=None,
                    cmd_need_prov=False,
                    cmd_need_model=False,
                )

        elif not need_prov and need_model:

            @self.tree.command(name="summary", description="Summarize a Redmine ticket")
            @app_commands.describe(issue_id="Redmine issue number", llm_model=model_desc)
            @app_commands.autocomplete(llm_model=ac_model)
            async def summary_cmd(
                interaction: discord.Interaction,
                issue_id: int,
                llm_model: str | None = None,
            ) -> None:
                await bot._run_slash_summary(
                    interaction,
                    issue_id,
                    llm_provider=None,
                    llm_model=llm_model,
                    cmd_need_prov=False,
                    cmd_need_model=True,
                )

            @self.tree.command(
                name="ask_issue",
                description="Ask a question about a Redmine ticket (LLM)",
            )
            @app_commands.describe(
                issue_id="Redmine issue number",
                question="What to ask about this ticket",
                llm_model=model_desc,
            )
            @app_commands.autocomplete(llm_model=ac_model)
            async def ask_issue_cmd(
                interaction: discord.Interaction,
                issue_id: int,
                question: str,
                llm_model: str | None = None,
            ) -> None:
                await bot._run_slash_ask_issue(
                    interaction,
                    issue_id,
                    question,
                    llm_provider=None,
                    llm_model=llm_model,
                    cmd_need_prov=False,
                    cmd_need_model=True,
                )

            @self.tree.command(
                name="note",
                description="Add an LLM-polished note to a Redmine ticket",
            )
            @app_commands.describe(
                issue_id="Redmine issue number",
                text="Note text to append",
                llm_model=model_desc,
            )
            @app_commands.autocomplete(llm_model=ac_model)
            async def note_cmd(
                interaction: discord.Interaction,
                issue_id: int,
                text: str,
                llm_model: str | None = None,
            ) -> None:
                await bot._run_slash_note(
                    interaction,
                    issue_id,
                    text,
                    llm_provider=None,
                    llm_model=llm_model,
                    cmd_need_prov=False,
                    cmd_need_model=True,
                )

        elif need_prov and not need_model:

            @self.tree.command(name="summary", description="Summarize a Redmine ticket")
            @app_commands.describe(issue_id="Redmine issue number", llm_provider=prov_desc)
            @app_commands.autocomplete(llm_provider=ac_prov)
            async def summary_cmd(
                interaction: discord.Interaction,
                issue_id: int,
                llm_provider: str | None = None,
            ) -> None:
                await bot._run_slash_summary(
                    interaction,
                    issue_id,
                    llm_provider=llm_provider,
                    llm_model=None,
                    cmd_need_prov=True,
                    cmd_need_model=False,
                )

            @self.tree.command(
                name="ask_issue",
                description="Ask a question about a Redmine ticket (LLM)",
            )
            @app_commands.describe(
                issue_id="Redmine issue number",
                question="What to ask about this ticket",
                llm_provider=prov_desc,
            )
            @app_commands.autocomplete(llm_provider=ac_prov)
            async def ask_issue_cmd(
                interaction: discord.Interaction,
                issue_id: int,
                question: str,
                llm_provider: str | None = None,
            ) -> None:
                await bot._run_slash_ask_issue(
                    interaction,
                    issue_id,
                    question,
                    llm_provider=llm_provider,
                    llm_model=None,
                    cmd_need_prov=True,
                    cmd_need_model=False,
                )

            @self.tree.command(
                name="note",
                description="Add an LLM-polished note to a Redmine ticket",
            )
            @app_commands.describe(
                issue_id="Redmine issue number",
                text="Note text to append",
                llm_provider=prov_desc,
            )
            @app_commands.autocomplete(llm_provider=ac_prov)
            async def note_cmd(
                interaction: discord.Interaction,
                issue_id: int,
                text: str,
                llm_provider: str | None = None,
            ) -> None:
                await bot._run_slash_note(
                    interaction,
                    issue_id,
                    text,
                    llm_provider=llm_provider,
                    llm_model=None,
                    cmd_need_prov=True,
                    cmd_need_model=False,
                )

        else:

            @self.tree.command(name="summary", description="Summarize a Redmine ticket")
            @app_commands.describe(
                issue_id="Redmine issue number",
                llm_provider=prov_desc,
                llm_model=model_desc,
            )
            @app_commands.autocomplete(llm_provider=ac_prov)
            @app_commands.autocomplete(llm_model=ac_model)
            async def summary_cmd(
                interaction: discord.Interaction,
                issue_id: int,
                llm_provider: str | None = None,
                llm_model: str | None = None,
            ) -> None:
                await bot._run_slash_summary(
                    interaction,
                    issue_id,
                    llm_provider=llm_provider,
                    llm_model=llm_model,
                    cmd_need_prov=True,
                    cmd_need_model=True,
                )

            @self.tree.command(
                name="ask_issue",
                description="Ask a question about a Redmine ticket (LLM)",
            )
            @app_commands.describe(
                issue_id="Redmine issue number",
                question="What to ask about this ticket",
                llm_provider=prov_desc,
                llm_model=model_desc,
            )
            @app_commands.autocomplete(llm_provider=ac_prov)
            @app_commands.autocomplete(llm_model=ac_model)
            async def ask_issue_cmd(
                interaction: discord.Interaction,
                issue_id: int,
                question: str,
                llm_provider: str | None = None,
                llm_model: str | None = None,
            ) -> None:
                await bot._run_slash_ask_issue(
                    interaction,
                    issue_id,
                    question,
                    llm_provider=llm_provider,
                    llm_model=llm_model,
                    cmd_need_prov=True,
                    cmd_need_model=True,
                )

            @self.tree.command(
                name="note",
                description="Add an LLM-polished note to a Redmine ticket",
            )
            @app_commands.describe(
                issue_id="Redmine issue number",
                text="Note text to append",
                llm_provider=prov_desc,
                llm_model=model_desc,
            )
            @app_commands.autocomplete(llm_provider=ac_prov)
            @app_commands.autocomplete(llm_model=ac_model)
            async def note_cmd(
                interaction: discord.Interaction,
                issue_id: int,
                text: str,
                llm_provider: str | None = None,
                llm_model: str | None = None,
            ) -> None:
                await bot._run_slash_note(
                    interaction,
                    issue_id,
                    text,
                    llm_provider=llm_provider,
                    llm_model=llm_model,
                    cmd_need_prov=True,
                    cmd_need_model=True,
                )

        logger.info(
            "Registered LLM slash command variant | llm_backend=%s need_llm_provider_option=%s need_llm_model_option=%s",
            type(self.llm).__name__,
            need_prov,
            need_model,
            extra=_STARTUP_LOG_EXTRA,
        )

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
                if is_unknown_interaction_error(e):
                    log_slash_discord_unknown_interaction("ping", interaction)
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

        @self.tree.command(
            name="status",
            description="Bot health summary: version, uptime, Redmine, LLM, and feature flags",
        )
        async def status_cmd(interaction: discord.Interaction) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            # Ack Discord before logging or formatting: duplicate bot processes fight for the same
            # interaction token; defer first minimizes the ~3s window and avoids work before ack.
            try:
                await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            except discord.HTTPException as e:
                if is_unknown_interaction_error(e):
                    log_slash_discord_unknown_interaction("status", interaction)
                    return
                log_slash_error(
                    "status",
                    interaction,
                    action="defer failed",
                    detail=e,
                )
                raise
            log_slash_input("status", interaction, fields=f"ephemeral={ephemeral}")
            body = _format_status_message(
                env=self.env,
                app_cfg=self.app_cfg,
                llm=self.llm,
                bot=self,
                ready_at_utc=self._ready_at_utc,
                guild=interaction.guild,
            )
            try:
                await interaction.followup.send(body[:_DISCORD_MSG_MAX], ephemeral=ephemeral)
            except discord.HTTPException as e:
                log_slash_error(
                    "status",
                    interaction,
                    action="followup send failed",
                    detail=e,
                )
                raise
            log_slash_output(
                "status",
                interaction,
                action="sent status summary (defer + followup)",
                fields=f"ephemeral={ephemeral} version={_ULTRON_VERSION!r}",
            )

        @self.tree.command(
            name="rpsls",
            description="Rock–paper–scissors–lizard–Spock: play against the bot",
        )
        @app_commands.describe(move="Your move")
        @app_commands.choices(
            move=[
                app_commands.Choice(name="Rock", value="rock"),
                app_commands.Choice(name="Paper", value="paper"),
                app_commands.Choice(name="Scissors", value="scissors"),
                app_commands.Choice(name="Lizard", value="lizard"),
                app_commands.Choice(name="Spock", value="spock"),
            ]
        )
        async def rpsls_cmd(interaction: discord.Interaction, move: str) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input("rpsls", interaction, fields=f"ephemeral={ephemeral} move={move!r}")
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            await asyncio.sleep(random.uniform(0.8, 2.0))
            bot_move = random.choice(MOVES)
            outcome = judge(move, bot_move)
            await interaction.followup.send(
                _rpsls_outcome_text(move, bot_move, outcome),
                ephemeral=ephemeral,
            )
            log_slash_output(
                "rpsls",
                interaction,
                action="sent rpsls result",
                fields=(
                    f"ephemeral={ephemeral} move={move!r} bot_move={bot_move!r} outcome={outcome!r}"
                ),
            )

        @self.tree.command(
            name="list_new_issues",
            description="Issues in configured new status, created ≥M days ago (see discord.new_issues in YAML).",
        )
        async def list_new_issues_cmd(interaction: discord.Interaction) -> None:
            ni = self.app_cfg.discord.new_issues
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input("list_new_issues", interaction, fields=f"ephemeral={ephemeral}")
            if not ni.status_name.strip():
                await interaction.response.send_message(
                    "Set **`discord.new_issues.status_name`** in `config.yaml` to your Redmine issue status "
                    "label (exact match, e.g. `New/Neu`).",
                    ephemeral=True,
                )
                log_slash_output(
                    "list_new_issues", interaction, action="missing discord.new_issues.status_name"
                )
                return
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            await _send_issues_older_than_days_list(
                interaction=interaction,
                redmine=self.redmine,
                ephemeral=ephemeral,
                status_name=ni.status_name.strip(),
                min_age_days=ni.min_age_days,
                list_limit=ni.list_limit,
                log_command="list_new_issues",
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
            name="list_unassigned_issues",
            description="Unassigned open issues older than min age; see discord.unassigned_open in config YAML.",
        )
        async def list_unassigned_issues_cmd(interaction: discord.Interaction) -> None:
            uo = self.app_cfg.discord.unassigned_open
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input("list_unassigned_issues", interaction, fields=f"ephemeral={ephemeral}")
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            await _send_unassigned_open_issues_list(
                interaction=interaction,
                redmine=self.redmine,
                ephemeral=ephemeral,
                cfg=uo,
                log_command="list_unassigned_issues",
            )

        @self.tree.command(
            name="time_summary",
            description="Redmine spent hours (today, week, 7d, 24h) for a user — login, id, or me",
        )
        @app_commands.describe(user="Redmine login, numeric user id, or me (API user)")
        async def time_summary_cmd(interaction: discord.Interaction, user: str) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            log_slash_input("time_summary", interaction, fields=f"ephemeral={ephemeral} user={user!r}")
            try:
                await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            except discord.HTTPException as e:
                if is_unknown_interaction_error(e):
                    log_slash_discord_unknown_interaction("time_summary", interaction)
                    return
                log_slash_error("time_summary", interaction, action="defer failed", detail=e)
                raise
            now_utc = datetime.now(timezone.utc)
            try:
                uid, label = await resolve_redmine_user_for_time_summary(
                    self.redmine,
                    user,
                    self.app_cfg.redmine.user_id_by_login,
                )
            except ValueError as e:
                await interaction.followup.send(str(e), ephemeral=ephemeral)
                log_slash_output(
                    "time_summary",
                    interaction,
                    action="user resolution failed",
                    fields=str(e).replace("\n", " ")[:160],
                )
                return
            except RedmineError as e:
                await interaction.followup.send(
                    "Could not resolve that Redmine user. Try a numeric **user id** or **`me`**.",
                    ephemeral=ephemeral,
                )
                log_slash_error("time_summary", interaction, action="user resolve Redmine error", detail=e)
                return
            try:
                d_from, d_to = fetch_spent_on_range_strings(
                    self.app_cfg.timezone, now_utc, lookback_days=14
                )
                entries = await self.redmine.list_time_entries(
                    user_id=uid,
                    spent_on_from=d_from,
                    spent_on_to=d_to,
                    max_entries=self.app_cfg.redmine.time_summary_max_entries,
                )
            except RedminePermissionError as e:
                await interaction.followup.send(str(e), ephemeral=ephemeral)
                log_slash_error("time_summary", interaction, action="time entries forbidden", detail=e)
                return
            except RedmineError as e:
                await interaction.followup.send(
                    "Redmine request failed while loading time entries. Try again later.",
                    ephemeral=ephemeral,
                )
                log_slash_error("time_summary", interaction, action="list time entries failed", detail=e)
                return
            capped = len(entries) >= self.app_cfg.redmine.time_summary_max_entries
            buckets = compute_time_summary_buckets(
                entries,
                timezone_name=self.app_cfg.timezone,
                now_utc=now_utc,
            )
            tz_disp = (self.app_cfg.timezone or "").strip() or "UTC"
            emb = embed_time_summary(
                user_label=label,
                today_h=buckets.today,
                week_h=buckets.this_week,
                last7_h=buckets.last_7_days,
                last24_h=buckets.last_24h,
                timezone_name=tz_disp,
            )
            foot = (
                f"Capped at {len(entries)} entries ({d_from}–{d_to} spent_on); totals may be incomplete."
                if capped
                else f"From {len(entries)} entries in range {d_from}–{d_to} (spent_on)."
            )
            emb.set_footer(text=foot[:2048])
            await interaction.followup.send(embed=emb, ephemeral=ephemeral)
            log_slash_output(
                "time_summary",
                interaction,
                action="sent time summary embed",
                fields=f"user_id={uid} entries={len(entries)} capped={capped}",
            )

        @self.tree.command(
            name="log_time",
            description="Log spent hours on a Redmine issue (booked as the Redmine API key user).",
        )
        @app_commands.describe(
            issue_id="Redmine issue id",
            hours="Hours spent (fractional allowed, e.g. 1.5)",
            comments="Optional comment on the time entry (max 255 chars in Redmine)",
            spent_on="Optional calendar date YYYY-MM-DD for the entry",
        )
        async def log_time_cmd(
            interaction: discord.Interaction,
            issue_id: app_commands.Range[int, 1, 2147483647],
            hours: app_commands.Range[float, 0.01, 999.0],
            comments: str | None = None,
            spent_on: str | None = None,
        ) -> None:
            ephemeral = self.app_cfg.discord.ephemeral_default
            cmt = (comments or "").strip() or None
            spo = (spent_on or "").strip() or None
            log_slash_input(
                "log_time",
                interaction,
                fields=(
                    f"ephemeral={ephemeral} issue_id={issue_id} hours={hours} "
                    f"has_comments={bool(cmt)} has_spent_on={bool(spo)}"
                ),
            )
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            try:
                activities = await self.redmine.list_time_entry_activities()
                activity_id = resolve_time_activity_id(
                    activities,
                    os.environ.get(self.env.environment_bindings.redmine_time_activity_id_env),
                )
                te = await self.redmine.create_time_entry(
                    issue_id,
                    float(hours),
                    activity_id=activity_id,
                    comments=cmt,
                    spent_on=spo,
                )
            except ValueError as e:
                await interaction.followup.send(str(e), ephemeral=ephemeral)
                log_slash_output("log_time", interaction, action="validation or activity resolution failed")
                return
            except IssueNotFound:
                await interaction.followup.send(
                    "Issue not found in Redmine.", ephemeral=ephemeral
                )
                log_slash_output("log_time", interaction, action="issue not found")
                return
            except RedminePermissionError as e:
                await interaction.followup.send(str(e), ephemeral=ephemeral)
                log_slash_error("log_time", interaction, action="redmine permission denied", detail=e)
                return
            except RedmineError as e:
                hint = getattr(e, "user_message", None)
                await interaction.followup.send(
                    hint or "Redmine request failed. Try again later.",
                    ephemeral=ephemeral,
                )
                log_slash_error(
                    "log_time",
                    interaction,
                    action="redmine request failed",
                    detail=e,
                )
                return
            self.record_redmine_write("time_entry_create")
            url = self.redmine.issue_url(issue_id)
            msg = f"Logged **{float(hours):g}** h on issue [{issue_id}]({url})."
            tid = te.get("id")
            if tid is not None:
                msg += f"\n• Time entry **#{tid}**"
            act_obj = te.get("activity")
            if isinstance(act_obj, dict):
                an = str(act_obj.get("name", "")).strip()
                if an:
                    msg += f"\n• Activity: **{an}**"
            spent = te.get("spent_on")
            if spent:
                msg += f"\n• Spent on: **{spent}**"
            await interaction.followup.send(msg, ephemeral=ephemeral)
            log_slash_output(
                "log_time",
                interaction,
                action="logged time entry",
                fields=f"issue_id={issue_id} hours={hours} time_entry_id={tid}",
                log_extra={
                    "redmine_operation": "time_entry_create",
                    "issue_id": issue_id,
                    "hours": float(hours),
                },
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
            parts = chunk_discord(_HELP_TEXT)
            await interaction.response.send_message(parts[0][: _DISCORD_MSG_MAX], ephemeral=True)
            for part in parts[1:]:
                await interaction.followup.send(part[:_DISCORD_MSG_MAX], ephemeral=True)
            log_slash_output(
                "help",
                interaction,
                action="sent help text (ephemeral)",
                fields=f"chunks={len(parts)}",
            )

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

        self._register_slash_summary_ask_note()

        if guild:
            self.tree.copy_global_to(guild=guild)
            try:
                await self.tree.sync(guild=guild)
                logger.info(
                    "Slash commands synced to guild %s",
                    self.env.discord_guild_id,
                    extra=_STARTUP_LOG_EXTRA,
                )
                try:
                    await self.tree.sync()
                    logger.info(
                        "Slash commands synced globally (Discord may take up to ~1 hour to update outside the configured guild)",
                        extra=_STARTUP_LOG_EXTRA,
                    )
                except Exception as e:
                    logger.warning(
                        "Global slash sync after guild sync failed (%s: %s); guild %s still has the latest commands. "
                        "Other servers and DMs may show an older command form until global sync succeeds.",
                        type(e).__name__,
                        e,
                        self.env.discord_guild_id,
                        extra=_STARTUP_LOG_EXTRA,
                    )
            except discord.Forbidden as e:
                logger.warning(
                    "Guild slash sync failed (Forbidden, API code=%s): %s. "
                    "The bot must be in that server with permission to create application commands — "
                    "re-invite using a URL that includes the **applications.commands** scope, and confirm "
                    "**DISCORD_GUILD_ID** is the correct numeric server id. Falling back to **global** sync.",
                    getattr(e, "code", None),
                    e,
                    extra=_STARTUP_LOG_EXTRA,
                )
                await self.tree.sync()
                logger.info(
                    "Slash commands synced globally (may take up to ~1 hour to appear)",
                    extra=_STARTUP_LOG_EXTRA,
                )
        else:
            await self.tree.sync()
            logger.info(
                "Slash commands synced globally (may take up to ~1 hour to appear)",
                extra=_STARTUP_LOG_EXTRA,
            )

    async def on_ready(self) -> None:
        first_ready = not self._ready_startup_logged
        if first_ready:
            self._ready_startup_logged = True
            self._ready_at_utc = datetime.now(timezone.utc)
        on_ready_ex = _STARTUP_LOG_EXTRA if first_ready else {}
        logger.info(
            "Logged in as %s (%s)",
            self.user,
            self.user.id if self.user else "",
            extra=on_ready_ex,
        )
        try:
            await self.change_presence(status=discord.Status.online)
        except discord.HTTPException as e:
            logger.warning("change_presence(online) failed: %s", e, extra=on_ready_ex)
        if not self.env.llm_enabled:
            logger.info(
                "No language model assigned — /summary, /ask_issue, and /note are disabled; "
                "Redmine slash commands, /ping, registration, and scheduled channel listings still work.",
                extra=on_ready_ex,
            )
        if self._jobs_started:
            return
        self._jobs_started = True

        # First: logs channel (startup summary before report_schedule loop).
        await self._announce_startup_in_logs_channel()

        cid = self.app_cfg.reports.channel_id
        nj = len(self.app_cfg.report_schedule)
        if nj and not cid:
            logger.warning(
                "report_schedule defines %s job(s) but reports.channel_id is 0 — no posts to a reports channel "
                "(set reports.channel_id to the Discord channel id, e.g. in config.yaml).",
                nj,
                extra=_STARTUP_LOG_EXTRA,
            )
        ch = self.get_channel(cid) if cid else None
        if cid and ch is None:
            logger.warning(
                "Reports channel_id %s not visible to bot; report channel posts disabled",
                cid,
                extra=_STARTUP_LOG_EXTRA,
            )
        if cid and ch is not None:
            if self.app_cfg.reports.startup_message_enabled:
                try:
                    msg = build_reports_startup_message(self.app_cfg)
                    for part in chunk_discord(msg, limit=1900):
                        await ch.send(part, suppress_embeds=True)
                except discord.HTTPException as e:
                    logger.warning(
                        "Reports channel startup message failed: %s",
                        e,
                        extra=_STARTUP_LOG_EXTRA,
                    )
            now = datetime.now(timezone.utc)
            for i in range(nj):
                self._report_schedule_last_run[i] = now
            if self.app_cfg.report_schedule:
                self.report_schedule_loop.start()
                logger.info(
                    "Started report_schedule loop (hourly tick, %s job(s))",
                    len(self.app_cfg.report_schedule),
                    extra=_STARTUP_LOG_EXTRA,
                )
            else:
                logger.info(
                    "report_schedule is empty; no periodic report jobs",
                    extra=_STARTUP_LOG_EXTRA,
                )
        elif not cid:
            logger.info(
                "reports.channel_id is 0; report channel posts disabled",
                extra=_STARTUP_LOG_EXTRA,
            )

    async def on_error(self, event_method: str, /, *args, **kwargs) -> None:  # type: ignore[override]
        logger.exception("Error in %s", event_method)

    @tasks.loop(hours=1)
    async def report_schedule_loop(self) -> None:
        cid = self.app_cfg.reports.channel_id
        if not cid or not self.app_cfg.report_schedule:
            return
        channel = self.get_channel(cid)
        if channel is None:
            return
        now = datetime.now(timezone.utc)
        for i, entry in enumerate(self.app_cfg.report_schedule):
            last = self._report_schedule_last_run.get(i, now)
            if now < last + timedelta(hours=entry.interval_hours):
                continue
            await run_report_schedule_entry(
                redmine=self.redmine,
                app_cfg=self.app_cfg,
                channel=channel,  # type: ignore[arg-type]
                entry=entry,
            )
            self._report_schedule_last_run[i] = now

    @report_schedule_loop.before_loop
    async def _before_report_schedule_loop(self) -> None:
        await self.wait_until_ready()
