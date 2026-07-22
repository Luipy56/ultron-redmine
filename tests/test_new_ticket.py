from __future__ import annotations

import asyncio

from ultron.nl_router import NLInvoke, NLParseError, parse_router_json_text
from ultron.redmine import RedmineClient, RedminePermissionError
from ultron.redmine_listings import create_new_ticket


def test_parse_router_invoke_new_ticket() -> None:
    raw = (
        '{"kind":"invoke","command":"new_ticket",'
        '"args":{"project":"10_AMVARA","title":"[TEST] Hello","description":"Body text"}}'
    )
    out = parse_router_json_text(raw)
    assert isinstance(out, NLInvoke)
    assert out.command == "new_ticket"
    assert out.args == {
        "project": "10_AMVARA",
        "title": "[TEST] Hello",
        "description": "Body text",
    }


def test_parse_router_new_ticket_alias_and_subject() -> None:
    raw = (
        '{"kind":"invoke","command":"create_issue",'
        '"args":{"project":"dip-re","subject":"[X] Y","description":"Desc"}}'
    )
    out = parse_router_json_text(raw)
    assert isinstance(out, NLInvoke)
    assert out.command == "new_ticket"
    assert out.args == {"project": "dip-re", "title": "[X] Y", "description": "Desc"}


def test_parse_router_new_ticket_rejects_empty_fields() -> None:
    raw = (
        '{"kind":"invoke","command":"new_ticket",'
        '"args":{"project":"10_AMVARA","title":"  ","description":"ok"}}'
    )
    out = parse_router_json_text(raw)
    assert isinstance(out, NLParseError)


def test_create_new_ticket_unknown_project(monkeypatch) -> None:
    client = RedmineClient(base_url="https://redmine.example.com", api_key="x")

    async def _projects():
        return [{"id": 1, "identifier": "dip-re", "name": "93_DIP-RE"}]

    monkeypatch.setattr(client, "list_projects", _projects)

    async def _run():
        return await create_new_ticket(
            redmine=client,
            project_query="zzzz-nope",
            title="[T] x",
            description="desc",
        )

    body, err, iid = asyncio.run(_run())
    assert body is None
    assert iid == -1
    assert err is not None
    assert "No Redmine project matching" in err


def test_create_new_ticket_success(monkeypatch) -> None:
    client = RedmineClient(base_url="https://redmine.example.com", api_key="x")

    async def _projects():
        return [{"id": 7, "identifier": "10_amvara", "name": "10_AMVARA"}]

    async def _create(*, project_id, subject, description):
        assert project_id == 7
        assert subject == "[ULTRON] smoke"
        assert description == "test body"
        return {"id": 9991, "subject": subject}

    monkeypatch.setattr(client, "list_projects", _projects)
    monkeypatch.setattr(client, "create_issue", _create)

    async def _run():
        return await create_new_ticket(
            redmine=client,
            project_query="10_AMVARA",
            title="[ULTRON] smoke",
            description="test body",
        )

    body, err, iid = asyncio.run(_run())
    assert err is None
    assert iid == 9991
    assert body is not None
    assert "[#9991](https://redmine.example.com/issues/9991)" in body
    assert "[ULTRON] smoke" in body
    assert "10_AMVARA" in body or "10\\_AMVARA" in body


def test_create_new_ticket_permission_denied(monkeypatch) -> None:
    client = RedmineClient(base_url="https://redmine.example.com", api_key="x")

    async def _projects():
        return [{"id": 1, "identifier": "dip-re", "name": "93_DIP-RE"}]

    async def _create(**_k):
        raise RedminePermissionError("denied")

    monkeypatch.setattr(client, "list_projects", _projects)
    monkeypatch.setattr(client, "create_issue", _create)

    async def _run():
        return await create_new_ticket(
            redmine=client,
            project_query="dip-re",
            title="[T] x",
            description="desc",
        )

    body, err, iid = asyncio.run(_run())
    assert body is None
    assert iid == -1
    assert err == "denied"


def test_create_issue_validates_empty_subject() -> None:
    client = RedmineClient(base_url="https://redmine.example.com", api_key="x")

    async def _run():
        try:
            await client.create_issue(project_id=1, subject="  ", description="d")
        except ValueError as e:
            return str(e)
        return None

    msg = asyncio.run(_run())
    assert msg is not None
    assert "title" in msg.casefold()
