from __future__ import annotations

from ultron.redmine import _redmine_user_hint


def test_user_hint_403() -> None:
    h = _redmine_user_hint(403, "{}")
    assert h is not None
    assert "403" in h


def test_user_hint_422_errors_array() -> None:
    h = _redmine_user_hint(422, '{"errors":["Hours must be greater than 0"]}')
    assert h is not None
    assert "Hours" in h
