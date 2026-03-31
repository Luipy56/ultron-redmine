from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx


class RedmineError(Exception):
    pass


class IssueNotFound(RedmineError):
    pass


@dataclass
class RedmineClient:
    base_url: str
    api_key: str
    timeout: float = 60.0

    def _headers(self) -> dict[str, str]:
        return {
            "X-Redmine-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers(),
            timeout=self.timeout,
        )

    async def get_issue(self, issue_id: int, includes: str = "journals,attachments,relations") -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get(f"/issues/{issue_id}.json", params={"include": includes})
        if r.status_code == 404:
            raise IssueNotFound(f"Issue #{issue_id} not found")
        if r.is_error:
            raise RedmineError(f"Redmine GET issue failed: {r.status_code} {r.text[:500]}")
        return r.json()["issue"]

    async def add_note(self, issue_id: int, notes: str) -> None:
        async with self._client() as c:
            r = await c.put(f"/issues/{issue_id}.json", json={"issue": {"notes": notes}})
        if r.status_code == 404:
            raise IssueNotFound(f"Issue #{issue_id} not found")
        if r.is_error:
            raise RedmineError(f"Redmine PUT issue failed: {r.status_code} {r.text[:500]}")

    async def list_open_issues(self, *, sort: str, limit: int) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(
                "/issues.json",
                params={
                    "status_id": "open",
                    "sort": sort,
                    "limit": min(limit, 100),
                },
            )
        if r.is_error:
            raise RedmineError(f"Redmine list issues failed: {r.status_code} {r.text[:500]}")
        data = r.json()
        return list(data.get("issues", []))

    def issue_url(self, issue_id: int) -> str:
        return f"{self.base_url}/issues/{issue_id}"


def parse_redmine_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    # Redmine uses ISO8601, often with Z
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)
