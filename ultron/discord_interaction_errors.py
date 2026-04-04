"""Helpers for Discord interaction API errors (slash commands, ACK window, etc.)."""

from __future__ import annotations

import discord
from discord import app_commands


def is_unknown_interaction_error(exc: BaseException) -> bool:
    """True if ``exc`` is (or wraps) Discord error 10062 *Unknown interaction*.

    That response means the interaction token is no longer valid: typically the
    ~3 second acknowledgement window elapsed, or another process already ACKed
    the same event (duplicate bot instance).
    """
    if isinstance(exc, discord.HTTPException) and getattr(exc, "code", None) == 10062:
        return True
    if isinstance(exc, app_commands.CommandInvokeError):
        return is_unknown_interaction_error(exc.original)
    return False
