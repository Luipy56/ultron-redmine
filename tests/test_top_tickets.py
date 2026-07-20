from __future__ import annotations

import asyncio

from ultron.nl_router import NLInvoke, NLParseError, parse_router_json_text
from ultron.redmine import RedmineClient
from ultron.redmine_listings import (
    clamp_top_tickets_limit,
    markdown_top_tickets,
    normalize_top_tickets_kind,
    resolve_redmine_project,
)


def test_normalize_top_tickets_kind_default_and_aliases() -> None:
    assert normalize_top_tickets_kind(None) == "priority"
    assert normalize_top_tickets_kind("") == "priority"
    assert normalize_top_tickets_kind("newest") == "newests"
    assert normalize_top_tickets_kind("oldest") == "oldests"
    assert normalize_top_tickets_kind("PRIORITY") == "priority"


def test_normalize_top_tickets_kind_rejects_unknown() -> None:
    try:
        normalize_top_tickets_kind("by_assignee")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "kind_filter" in str(e)


def test_clamp_top_tickets_limit() -> None:
    assert clamp_top_tickets_limit(None) == 10
    assert clamp_top_tickets_limit(3) == 3
    assert clamp_top_tickets_limit(999) == 50


def test_resolve_redmine_project_exact_name_and_identifier() -> None:
    projects = [
        {"id": 1, "identifier": "dip-re", "name": "93_DIP-RE"},
        {"id": 2, "identifier": "10_amvara", "name": "10_AMVARA"},
    ]
    by_name = resolve_redmine_project("93_DIP-RE", projects)
    assert by_name is not None
    assert by_name.identifier == "dip-re"
    assert by_name.exact is True

    by_ident = resolve_redmine_project("dip-re", projects)
    assert by_ident is not None
    assert by_ident.identifier == "dip-re"
    assert by_ident.exact is True


def test_resolve_redmine_project_fuzzy() -> None:
    projects = [
        {"id": 1, "identifier": "dip-re", "name": "93_DIP-RE"},
        {"id": 2, "identifier": "other", "name": "Other"},
    ]
    matched = resolve_redmine_project("93 DIP RE", projects)
    assert matched is not None
    assert matched.identifier == "dip-re"

    assert resolve_redmine_project("zzzz-nope", projects) is None


def test_parse_router_invoke_top_tickets_defaults() -> None:
    raw = '{"kind":"invoke","command":"top_tickets","args":{"project":"93_DIP-RE"}}'
    out = parse_router_json_text(raw)
    assert isinstance(out, NLInvoke)
    assert out.command == "top_tickets"
    assert out.args == {"project": "93_DIP-RE", "kind_filter": "priority", "limit": 10}


def test_parse_router_top_tickets_alias_and_kind() -> None:
    raw = (
        '{"kind":"invoke","command":"top_issues",'
        '"args":{"project":"dip-re","kind_filter":"newest","limit":5}}'
    )
    out = parse_router_json_text(raw)
    assert isinstance(out, NLInvoke)
    assert out.command == "top_tickets"
    assert out.args == {"project": "dip-re", "kind_filter": "newests", "limit": 5}


def test_parse_router_top_tickets_rejects_empty_project() -> None:
    raw = '{"kind":"invoke","command":"top_tickets","args":{"project":"  "}}'
    out = parse_router_json_text(raw)
    assert isinstance(out, NLParseError)


def test_markdown_top_tickets_empty(monkeypatch) -> None:
    client = RedmineClient(base_url="https://redmine.example.com", api_key="x")

    async def _projects():
        return [{"id": 1, "identifier": "dip-re", "name": "93_DIP-RE"}]

    async def _issues(**_k):
        return []

    monkeypatch.setattr(client, "list_projects", _projects)
    monkeypatch.setattr(client, "list_issues", _issues)

    async def _run():
        return await markdown_top_tickets(
            redmine=client, project_query="93_DIP-RE", kind_filter="priority", limit=10
        )

    body, err, shown = asyncio.run(_run())
    assert err is None
    assert shown == 0
    assert body is not None
    assert "No **open** issues" in body
    assert "dip-re" in body
    assert "93\\_DIP-RE" in body or "93_DIP-RE" in body


def test_markdown_top_tickets_priority_lines(monkeypatch) -> None:
    client = RedmineClient(base_url="https://redmine.example.com", api_key="x")

    async def _projects():
        return [{"id": 1, "identifier": "dip-re", "name": "93_DIP-RE"}]

    async def _issues(**_k):
        return [
            {
                "id": 7736,
                "subject": "Urgent fix",
                "priority": {"id": 4, "name": "Urgent"},
            }
        ]

    monkeypatch.setattr(client, "list_projects", _projects)
    monkeypatch.setattr(client, "list_issues", _issues)

    async def _run():
        return await markdown_top_tickets(
            redmine=client, project_query="dip-re", kind_filter="priority", limit=10
        )

    body, err, shown = asyncio.run(_run())
    assert err is None
    assert shown == 1
    assert body is not None
    assert "**[Urgent]**" in body
    assert "[#7736](https://redmine.example.com/issues/7736)" in body
