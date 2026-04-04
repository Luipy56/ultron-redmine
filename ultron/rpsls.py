"""Rock–paper–scissors–lizard–Spock rules (slash game logic)."""

from __future__ import annotations

from typing import Literal

MOVES: tuple[str, ...] = ("rock", "paper", "scissors", "lizard", "spock")

BEATS: dict[str, frozenset[str]] = {
    "rock": frozenset({"scissors", "lizard"}),
    "paper": frozenset({"rock", "spock"}),
    "scissors": frozenset({"paper", "lizard"}),
    "lizard": frozenset({"spock", "paper"}),
    "spock": frozenset({"scissors", "rock"}),
}

Outcome = Literal["tie", "win", "lose"]


def judge(user: str, bot: str) -> Outcome:
    """Return whether the user ties, wins, or loses against the bot move.

    Both arguments must be keys in ``MOVES`` / ``BEATS``.
    """
    if user not in BEATS or bot not in BEATS:
        raise ValueError("invalid move")
    if user == bot:
        return "tie"
    if bot in BEATS[user]:
        return "win"
    return "lose"
