from __future__ import annotations

from ultron.nl_router import (
    NLAdminRejected,
    NLInvoke,
    NLParseError,
    parse_router_json_text,
)


def test_parse_router_invoke_summary() -> None:
    raw = '{"kind":"invoke","command":"summary","args":{"issue_id":42}}'
    out = parse_router_json_text(raw)
    assert isinstance(out, NLInvoke)
    assert out.command == "summary"
    assert out.args == {"issue_id": 42}


def test_parse_router_chat() -> None:
    raw = '{"kind":"chat","message":"Hello there"}'
    out = parse_router_json_text(raw)
    assert out.message == "Hello there"


def test_parse_router_rejects_admin_approve() -> None:
    raw = '{"kind":"invoke","command":"approve","args":{"token":"abc"}}'
    out = parse_router_json_text(raw)
    assert isinstance(out, NLAdminRejected)
    assert out.command == "approve"


def test_parse_router_rejects_unknown_command() -> None:
    raw = '{"kind":"invoke","command":"hack_the_planet","args":{}}'
    out = parse_router_json_text(raw)
    assert isinstance(out, NLParseError)


def test_parse_router_invalid_json() -> None:
    out = parse_router_json_text("not json")
    assert isinstance(out, NLParseError)


def test_parse_router_invoke_log_time() -> None:
    raw = '{"kind":"invoke","command":"log_time","args":{"issue_id":5,"hours":1.5}}'
    out = parse_router_json_text(raw)
    assert isinstance(out, NLInvoke)
    assert out.command == "log_time"
    assert out.args == {"issue_id": 5, "hours": 1.5}


def test_parse_router_log_time_rejects_non_positive_hours() -> None:
    raw = '{"kind":"invoke","command":"log_time","args":{"issue_id":5,"hours":0}}'
    out = parse_router_json_text(raw)
    assert isinstance(out, NLParseError)


def test_parse_router_log_time_accepts_string_hours() -> None:
    raw = '{"kind":"invoke","command":"log_time","args":{"issue_id":3,"hours":"2.25"}}'
    out = parse_router_json_text(raw)
    assert isinstance(out, NLInvoke)
    assert out.args == {"issue_id": 3, "hours": 2.25}
