"""Discord reply-to message context merged into natural-language prompts."""

from __future__ import annotations

import re
from dataclasses import dataclass

import discord

_MAX_REPLY_CHARS = 4000
_MENTION_RE = re.compile(r"<@!?\d+>")
_DEICTIC_RE = re.compile(
    r"\b("
    r"all this|this|esto|the above|lo anterior|"
    r"what you (?:said|wrote)|lo que (?:dijiste|has dicho)"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReplyContext:
    author_display: str
    content: str
    message_id: int


def strip_discord_mentions(text: str) -> str:
    """Remove Discord user/bot mention tokens and collapse whitespace."""
    cleaned = _MENTION_RE.sub(" ", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def format_reply_context_for_prompt(ctx: ReplyContext | None) -> str | None:
    """Format a replied-to message for LLM / agent prompts."""
    if ctx is None:
        return None
    content = (ctx.content or "").strip()
    if not content:
        return None
    if len(content) > _MAX_REPLY_CHARS:
        content = content[:_MAX_REPLY_CHARS] + "…"
    author = (ctx.author_display or "unknown").strip() or "unknown"
    return (
        f"Replied-to Discord message (id={ctx.message_id}, author={author}):\n"
        f"{content}"
    )


def build_effective_user_text(user_text: str, reply_block: str | None) -> str:
    """Merge replied-to excerpt and user message into one NL prompt body."""
    user = strip_discord_mentions(user_text)
    block = (reply_block or "").strip()
    if not block:
        return user
    if not user:
        return block
    return f"{block}\n\n---\n\n{user}"


def is_deictic_reference(text: str) -> bool:
    """True when note/router text is a thin placeholder referring to a reply."""
    t = (text or "").strip()
    if not t:
        return True
    if len(t) > 200:
        return False
    return _DEICTIC_RE.search(t) is not None


def resolve_note_body(text: str, reply_ctx: ReplyContext | None) -> str:
    """Substitute replied-to content when the router passes a deictic note body."""
    t = (text or "").strip()
    if reply_ctx is None:
        return t
    cited = (reply_ctx.content or "").strip()
    if not cited or not is_deictic_reference(t):
        return t
    bare = t.casefold()
    if bare in {
        "all this",
        "this",
        "esto",
        "the above",
        "lo anterior",
    }:
        return cited
    return f"{t}\n\n---\n\n{cited}"


def reply_context_from_message(ref_message: discord.Message) -> ReplyContext | None:
    """Build context from an already-resolved Discord message."""
    content = (getattr(ref_message, "content", None) or "").strip()
    if not content:
        return None
    author = getattr(ref_message, "author", None)
    display = getattr(author, "display_name", None) if author is not None else None
    if not display:
        display = str(author) if author is not None else "unknown"
    message_id = int(getattr(ref_message, "id", 0) or 0)
    return ReplyContext(
        author_display=str(display),
        content=content,
        message_id=message_id,
    )


async def resolve_reply_context(message: discord.Message) -> ReplyContext | None:
    """Resolve ``message.reference`` and return prompt-ready context, if any."""
    ref = message.reference
    if ref is None or ref.message_id is None:
        return None
    resolved = ref.resolved
    if resolved is None:
        try:
            resolved = await message.channel.fetch_message(ref.message_id)
        except (discord.NotFound, discord.HTTPException):
            return None
    return reply_context_from_message(resolved)
