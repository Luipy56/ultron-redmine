"""Tests for Discord interaction error classification."""

from __future__ import annotations

from types import SimpleNamespace

import discord
from discord import app_commands

from ultron.discord_interaction_errors import is_unknown_interaction_error


def _fake_response(*, status: int = 404) -> SimpleNamespace:
    return SimpleNamespace(status=status, reason="Not Found")


def test_unknown_interaction_direct_notfound() -> None:
    e = discord.NotFound(
        _fake_response(),
        {"code": 10062, "message": "Unknown interaction"},
    )
    assert is_unknown_interaction_error(e) is True


def test_unknown_interaction_wrapped_in_command_invoke() -> None:
    inner = discord.NotFound(
        _fake_response(),
        {"code": 10062, "message": "Unknown interaction"},
    )
    cmd = SimpleNamespace(name="rpsls")
    outer = app_commands.CommandInvokeError(cmd, inner)
    assert is_unknown_interaction_error(outer) is True


def test_other_notfound_code() -> None:
    e = discord.NotFound(
        _fake_response(),
        {"code": 10008, "message": "Some other error"},
    )
    assert is_unknown_interaction_error(e) is False


def test_other_exception() -> None:
    assert is_unknown_interaction_error(RuntimeError("x")) is False
