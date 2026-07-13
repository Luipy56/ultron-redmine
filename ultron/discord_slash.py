from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import discord

from ultron.discord_interaction_errors import is_unknown_interaction_error

logger = logging.getLogger(__name__)

_DISCORD_DEFER_TOKEN_WARN_SECONDS = 13 * 60 + 30

_LONG_RUNNING_MSG = (
    "**Operation is taking longer than expected.** "
    "The agent is still running; the result will be posted to the reports channel when it finishes."
)


async def edit_or_followup(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool = False,
) -> bool:
    """Edit the deferred slash message, or follow up. Returns False if the token is dead."""
    text = content[:2000]
    try:
        await interaction.edit_original_response(content=text)
        return True
    except discord.HTTPException as e:
        code = getattr(e, "code", None)
        if not is_unknown_interaction_error(e) and e.status != 401 and code != 50027:
            raise
        try:
            await interaction.followup.send(text, ephemeral=ephemeral)
            return True
        except discord.HTTPException as e2:
            if is_unknown_interaction_error(e2) or e2.status == 401 or getattr(e2, "code", None) == 50027:
                return False
            raise


@dataclass
class DeferredInteractionGuard:
    """Replace the thinking message before the interaction token expires."""

    interaction: discord.Interaction
    warn_after_seconds: float = _DISCORD_DEFER_TOKEN_WARN_SECONDS
    use_feedback: bool = False
    _done: bool = field(default=False, init=False, repr=False)
    _warn_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    async def start(self) -> None:
        self._warn_task = asyncio.create_task(self._warn())

    async def _warn(self) -> None:
        try:
            await asyncio.sleep(self.warn_after_seconds)
        except asyncio.CancelledError:
            return
        if self._done:
            return
        self.use_feedback = True
        try:
            await self.interaction.edit_original_response(content=_LONG_RUNNING_MSG)
        except discord.HTTPException as e:
            if is_unknown_interaction_error(e) or e.status == 401 or getattr(e, "code", None) == 50027:
                logger.warning("deferred interaction token expired before agent finished")
            else:
                logger.warning("could not post long-running notice: %s", e)

    def stop(self) -> None:
        self._done = True
        if self._warn_task is not None:
            self._warn_task.cancel()
