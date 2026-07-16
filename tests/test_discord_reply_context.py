from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from ultron.discord_reply_context import (
    ReplyContext,
    build_effective_user_text,
    format_reply_context_for_prompt,
    is_deictic_reference,
    reply_context_from_message,
    resolve_note_body,
    resolve_reply_context,
    strip_discord_mentions,
)
from ultron.nl_router import run_nl_router


def test_format_reply_context_for_prompt() -> None:
    ctx = ReplyContext(
        author_display="Alice",
        content="disk full on /var",
        message_id=123456789,
    )
    out = format_reply_context_for_prompt(ctx)
    assert out is not None
    assert "Alice" in out
    assert "123456789" in out
    assert "disk full on /var" in out


def test_format_reply_context_empty_content() -> None:
    ctx = ReplyContext(author_display="Bob", content="   ", message_id=1)
    assert format_reply_context_for_prompt(ctx) is None


def test_reply_context_from_message() -> None:
    author = SimpleNamespace(display_name="Ops Bot")
    msg = SimpleNamespace(content="  error log line  ", author=author, id=99)
    ctx = reply_context_from_message(msg)  # type: ignore[arg-type]
    assert ctx is not None
    assert ctx.content == "error log line"
    assert ctx.author_display == "Ops Bot"
    assert ctx.message_id == 99


def test_reply_context_from_message_empty() -> None:
    author = SimpleNamespace(display_name="Ops Bot")
    msg = SimpleNamespace(content="", author=author, id=99)
    assert reply_context_from_message(msg) is None  # type: ignore[arg-type]


def test_strip_discord_mentions() -> None:
    assert strip_discord_mentions("<@123> Qué opinas de esto?") == "Qué opinas de esto?"
    assert strip_discord_mentions("<@!456>  hello   world") == "hello world"


def test_build_effective_user_text_without_reply() -> None:
    assert build_effective_user_text("check disk", None) == "check disk"
    assert build_effective_user_text("<@1> check disk", None) == "check disk"


def test_build_effective_user_text_with_reply() -> None:
    reply = "Replied-to Discord message (id=1, author=Bob):\nerror stack trace"
    out = build_effective_user_text("check this", reply)
    assert out.startswith(reply)
    assert out.endswith("check this")
    assert "\n\n---\n\n" in out


def test_is_deictic_reference() -> None:
    assert is_deictic_reference("all this")
    assert is_deictic_reference("Add all this to the note")
    assert is_deictic_reference("esto")
    assert not is_deictic_reference(
        "we should start with satisfecho.de as it has no real users yet."
    )


def test_resolve_note_body_deictic() -> None:
    ctx = ReplyContext(
        author_display="Ultron",
        content="Ollama advisor summary line",
        message_id=1,
    )
    assert resolve_note_body("all this", ctx) == "Ollama advisor summary line"
    out = resolve_note_body("Add all this to the ticket", ctx)
    assert "Add all this to the ticket" in out
    assert "Ollama advisor summary line" in out


def test_resolve_note_body_explicit_text() -> None:
    ctx = ReplyContext(author_display="Bob", content="cited line", message_id=2)
    explicit = "we should start with satisfecho.de as it has no real users yet."
    assert resolve_note_body(explicit, ctx) == explicit


def test_resolve_reply_context_from_resolved() -> None:
    async def _run() -> None:
        author = SimpleNamespace(display_name="Carol")
        ref_msg = SimpleNamespace(content="OOM killer triggered", author=author, id=42)
        ref = SimpleNamespace(message_id=42, resolved=ref_msg)
        channel = MagicMock()
        message = SimpleNamespace(reference=ref, channel=channel)

        ctx = await resolve_reply_context(message)  # type: ignore[arg-type]
        assert ctx is not None
        assert ctx.content == "OOM killer triggered"
        channel.fetch_message.assert_not_called()

    asyncio.run(_run())


def test_resolve_reply_context_fetches_when_unresolved() -> None:
    async def _run() -> None:
        author = SimpleNamespace(display_name="Dave")
        ref_msg = SimpleNamespace(content="nginx 502", author=author, id=77)
        ref = SimpleNamespace(message_id=77, resolved=None)
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        message = SimpleNamespace(reference=ref, channel=channel)

        ctx = await resolve_reply_context(message)  # type: ignore[arg-type]
        assert ctx is not None
        assert ctx.content == "nginx 502"
        channel.fetch_message.assert_awaited_once_with(77)

    asyncio.run(_run())


def test_resolve_reply_context_no_reference() -> None:
    async def _run() -> None:
        message = SimpleNamespace(reference=None, channel=MagicMock())
        assert await resolve_reply_context(message) is None  # type: ignore[arg-type]

    asyncio.run(_run())


def test_run_nl_router_merged_reply_in_user_text() -> None:
    async def _run() -> None:
        captured: dict[str, str] = {}

        async def fake_complete(*, system: str, user: str, **kwargs: object) -> str:
            captured["user"] = user
            return '{"kind":"chat","message":"ok"}'

        llm = SimpleNamespace(complete=fake_complete)
        reply = "Replied-to Discord message (id=1, author=Bob):\nerror stack trace"
        merged = build_effective_user_text("check this", reply)
        await run_nl_router(
            llm,  # type: ignore[arg-type]
            user_text=merged,
            via="mention",
        )
        assert "error stack trace" in captured["user"]
        assert "check this" in captured["user"]
        assert "investigation or check requests" not in captured["user"].lower()

    asyncio.run(_run())
