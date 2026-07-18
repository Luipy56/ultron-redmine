from __future__ import annotations

import asyncio

from ultron.nl_router import NLInvoke, NLParseError, parse_router_json_text
from ultron.redmine import RedmineClient
from ultron.redmine_listings import (
    crop_issue_subject,
    format_find_issue_detail_line,
    markdown_find_issues,
    parse_search_issue_hit,
)


def test_parse_search_issue_hit_standard_title() -> None:
    hit = {
        "id": 10,
        "title": "Issue #10 (Closed): Login failure on SSO",
        "type": "issue closed",
    }
    assert parse_search_issue_hit(hit) == (10, "Login failure on SSO")


def test_parse_search_issue_hit_skips_non_issue() -> None:
    hit = {"id": 5, "title": "Wiki: Page", "type": "wiki-page"}
    assert parse_search_issue_hit(hit) is None


def test_crop_issue_subject_fifteen_chars() -> None:
    assert crop_issue_subject("0123456789ABCDEF") == "0123456789ABCDE"
    assert len(crop_issue_subject("0123456789ABCDEF")) == 15


def test_format_find_issue_detail_line() -> None:
    client = RedmineClient(base_url="https://redmine.example.com", api_key="x")
    line = format_find_issue_detail_line(42, "Very long subject here", client)
    assert line.startswith("Very long subje ")
    assert "[#42](https://redmine.example.com/issues/42)" in line


def test_parse_router_invoke_find_issue() -> None:
    raw = '{"kind":"invoke","command":"find_issue","args":{"text":"sso login"}}'
    out = parse_router_json_text(raw)
    assert isinstance(out, NLInvoke)
    assert out.command == "find_issue"
    assert out.args == {"text": "sso login"}


def test_parse_router_find_issue_alias_search_issue() -> None:
    raw = '{"kind":"invoke","command":"search_issue","args":{"text":"foo"}}'
    out = parse_router_json_text(raw)
    assert isinstance(out, NLInvoke)
    assert out.command == "find_issue"


def test_parse_router_find_issue_rejects_empty_text() -> None:
    raw = '{"kind":"invoke","command":"find_issue","args":{"text":"  "}}'
    out = parse_router_json_text(raw)
    assert isinstance(out, NLParseError)


def test_markdown_find_issues_empty(monkeypatch) -> None:
    client = RedmineClient(base_url="https://redmine.example.com", api_key="x")

    async def _collect(*_a, **_k):
        return [], 0

    monkeypatch.setattr(client, "search_issues_collect", _collect)

    async def _run():
        return await markdown_find_issues(
            redmine=client, text="xyzzy", project_id="10_AMVARA"
        )

    body, err, total = asyncio.run(_run())
    assert err is None
    assert total == 0
    assert body is not None
    assert "No issues matching" in body


def test_markdown_find_issues_overflow(monkeypatch) -> None:
    client = RedmineClient(base_url="https://redmine.example.com", api_key="x")
    hits = [
        {
            "id": i,
            "title": f"Issue #{i} (New): Subject number {i}",
            "type": "issue",
        }
        for i in range(1, 26)
    ]

    async def _collect(*_a, **_k):
        return hits, 25

    monkeypatch.setattr(client, "search_issues_collect", _collect)

    async def _run():
        return await markdown_find_issues(
            redmine=client, text="Subject", project_id="10_AMVARA"
        )

    body, err, total = asyncio.run(_run())
    assert err is None
    assert total == 25
    assert body is not None
    assert "Also matching:" in body
    assert "[#21](https://redmine.example.com/issues/21)" in body
    assert "Subject number 21" not in body
    assert body.count("\n") >= 20
