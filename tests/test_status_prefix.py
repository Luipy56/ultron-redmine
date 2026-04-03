from __future__ import annotations

from ultron.redmine import status_matches_closed_prefix


def test_prefix_case_insensitive() -> None:
    p = ("Solved",)
    assert status_matches_closed_prefix("Solved STAGE", p) is True
    assert status_matches_closed_prefix("solved", p) is True
    assert status_matches_closed_prefix("New", p) is False


def test_empty_prefixes_never_match() -> None:
    assert status_matches_closed_prefix("Anything", ()) is False


def test_multiple_prefixes() -> None:
    p = ("Comments", "Closed")
    assert status_matches_closed_prefix("Comments open", p) is True
    assert status_matches_closed_prefix("Closed", p) is True
