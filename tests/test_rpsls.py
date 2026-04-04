from __future__ import annotations

import pytest

from ultron.rpsls import BEATS, MOVES, judge


def test_judge_ties() -> None:
    for m in MOVES:
        assert judge(m, m) == "tie"


@pytest.mark.parametrize(
    ("user", "bot", "expected"),
    [
        ("rock", "scissors", "win"),
        ("rock", "lizard", "win"),
        ("paper", "rock", "win"),
        ("paper", "spock", "win"),
        ("scissors", "paper", "win"),
        ("scissors", "lizard", "win"),
        ("lizard", "spock", "win"),
        ("lizard", "paper", "win"),
        ("spock", "scissors", "win"),
        ("spock", "rock", "win"),
        ("scissors", "rock", "lose"),
        ("lizard", "rock", "lose"),
        ("rock", "paper", "lose"),
        ("spock", "paper", "lose"),
        ("paper", "scissors", "lose"),
        ("lizard", "scissors", "lose"),
        ("spock", "lizard", "lose"),
        ("paper", "lizard", "lose"),
        ("scissors", "spock", "lose"),
        ("rock", "spock", "lose"),
    ],
)
def test_judge_win_lose(user: str, bot: str, expected: str) -> None:
    assert judge(user, bot) == expected


def test_each_move_beats_exactly_two() -> None:
    others = set(MOVES)
    for m in MOVES:
        wins = [o for o in others if o != m and judge(m, o) == "win"]
        losses = [o for o in others if o != m and judge(m, o) == "lose"]
        assert len(wins) == 2
        assert len(losses) == 2
        assert set(wins) == BEATS[m]


def test_judge_invalid_move() -> None:
    with pytest.raises(ValueError, match="invalid move"):
        judge("rock", "invalid")
    with pytest.raises(ValueError, match="invalid move"):
        judge("invalid", "rock")
