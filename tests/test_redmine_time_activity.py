from __future__ import annotations

import pytest

from ultron.redmine import resolve_time_activity_id


def test_resolve_uses_env_when_id_matches_active() -> None:
    acts = [
        {"id": 8, "name": "Dev", "active": True, "is_default": False},
        {"id": 9, "name": "Meeting", "active": True, "is_default": True},
    ]
    assert resolve_time_activity_id(acts, "9") == 9


def test_resolve_env_wins_even_if_not_default() -> None:
    acts = [
        {"id": 8, "name": "Dev", "active": True, "is_default": True},
        {"id": 9, "name": "Meeting", "active": True, "is_default": False},
    ]
    assert resolve_time_activity_id(acts, "9") == 9


def test_resolve_single_default() -> None:
    acts = [
        {"id": 1, "name": "A", "active": True, "is_default": False},
        {"id": 2, "name": "B", "active": True, "is_default": True},
    ]
    assert resolve_time_activity_id(acts, None) == 2


def test_resolve_single_active_when_no_default() -> None:
    acts = [{"id": 7, "name": "Only", "active": True, "is_default": False}]
    assert resolve_time_activity_id(acts, None) == 7


def test_resolve_skips_inactive() -> None:
    acts = [
        {"id": 1, "name": "Off", "active": False, "is_default": True},
        {"id": 2, "name": "On", "active": True, "is_default": False},
    ]
    assert resolve_time_activity_id(acts, None) == 2


def test_resolve_rejects_env_not_in_list() -> None:
    acts = [{"id": 1, "name": "A", "active": True, "is_default": True}]
    with pytest.raises(ValueError, match="REDMINE_TIME_ACTIVITY_ID"):
        resolve_time_activity_id(acts, "99")


def test_resolve_ambiguous_multiple_no_default() -> None:
    acts = [
        {"id": 1, "name": "A", "active": True, "is_default": False},
        {"id": 2, "name": "B", "active": True, "is_default": False},
    ]
    with pytest.raises(ValueError, match="REDMINE_TIME_ACTIVITY_ID"):
        resolve_time_activity_id(acts, None)


def test_resolve_multiple_defaults_error() -> None:
    acts = [
        {"id": 1, "name": "A", "active": True, "is_default": True},
        {"id": 2, "name": "B", "active": True, "is_default": True},
    ]
    with pytest.raises(ValueError, match="multiple default"):
        resolve_time_activity_id(acts, None)
