from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

    async def verify_connection(self) -> None:
        """Lightweight REST check (current user). Raises RedmineError on API/auth failure."""
        async with self._client() as c:
            r = await c.get("/users/current.json")
        if r.status_code == 401:
            raise RedmineError("Redmine rejected the API key (401 Unauthorized).")
        if r.is_error:
            raise RedmineError(f"Redmine connection check failed: {r.status_code} {r.text[:500]}")

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

    async def list_issue_statuses(self) -> list[dict[str, Any]]:
        """All issue statuses (id, name, is_closed, …). Used to resolve status names to ids."""
        async with self._client() as c:
            r = await c.get("/issue_statuses.json")
        if r.is_error:
            raise RedmineError(f"Redmine list issue_statuses failed: {r.status_code} {r.text[:500]}")
        data = r.json()
        return list(data.get("issue_statuses", []))

    async def resolve_issue_status_id_by_name(self, name: str) -> int | None:
        """Return status id for an exact **name** from Redmine (case-insensitive, trimmed)."""
        want = name.strip().casefold()
        if not want:
            return None
        for st in await self.list_issue_statuses():
            raw = str(st.get("name", "")).strip()
            if raw.casefold() == want:
                sid = st.get("id")
                if sid is not None:
                    return int(sid)
        return None

    async def list_issues(
        self,
        *,
        sort: str,
        limit: int,
        status_id: str | int,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List issues filtered by ``status_id`` (``\"open\"``, ``\"closed\"``, or a numeric status id)."""
        lim = min(max(1, limit), 100)
        off = max(0, offset)
        async with self._client() as c:
            r = await c.get(
                "/issues.json",
                params={
                    "status_id": status_id,
                    "sort": sort,
                    "limit": lim,
                    "offset": off,
                },
            )
        if r.is_error:
            raise RedmineError(f"Redmine list issues failed: {r.status_code} {r.text[:500]}")
        data = r.json()
        return list(data.get("issues", []))

    async def list_open_issues(self, *, sort: str, limit: int) -> list[dict[str, Any]]:
        return await self.list_issues(sort=sort, limit=limit, status_id="open")

    async def list_issues_older_than_days(
        self,
        *,
        status_id: int,
        min_age_days: int,
        max_fetched: int = 1000,
    ) -> list[dict[str, Any]]:
        """Issues in `status_id` with `created_on` at least `min_age_days` in the past (UTC).

        Paginates `/issues.json` until a short page or `max_fetched` issues scanned.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)
        matched: list[dict[str, Any]] = []
        offset = 0
        scanned = 0
        while True:
            page = await self.list_issues(
                sort="created_on:asc",
                limit=100,
                status_id=status_id,
                offset=offset,
            )
            if not page:
                break
            for iss in page:
                scanned += 1
                if scanned > max_fetched:
                    break
                created = parse_redmine_datetime(iss.get("created_on"))
                if created is None:
                    continue
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created <= cutoff:
                    matched.append(iss)
            if scanned > max_fetched:
                break
            if len(page) < 100:
                break
            offset += len(page)
        return matched

    async def list_unassigned_open_issues_older_than_days(
        self,
        *,
        min_age_days: int,
        closed_status_prefixes: tuple[str, ...],
        max_fetched: int = 1000,
    ) -> list[dict[str, Any]]:
        """Open issues (``status_id=open``), unassigned, created ≥ ``min_age_days`` ago, excluding closed-equivalent prefixes."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)
        matched: list[dict[str, Any]] = []
        offset = 0
        scanned = 0
        while True:
            page = await self.list_issues(
                sort="created_on:asc",
                limit=100,
                status_id="open",
                offset=offset,
            )
            if not page:
                break
            for iss in page:
                scanned += 1
                if scanned > max_fetched:
                    break
                if iss.get("assigned_to"):
                    continue
                st_name = str((iss.get("status") or {}).get("name") or "")
                if status_matches_closed_prefix(st_name, closed_status_prefixes):
                    continue
                created = parse_redmine_datetime(iss.get("created_on"))
                if created is None:
                    continue
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created <= cutoff:
                    matched.append(iss)
            if scanned > max_fetched:
                break
            if len(page) < 100:
                break
            offset += len(page)
        return matched

    def issue_url(self, issue_id: int) -> str:
        return f"{self.base_url}/issues/{issue_id}"


def status_matches_closed_prefix(status_name: str, prefixes: tuple[str, ...]) -> bool:
    """True if ``status_name`` equals or starts with any non-empty prefix (case-insensitive, stripped)."""
    actual = status_name.strip().casefold()
    if not actual:
        return False
    for p in prefixes:
        t = p.strip().casefold()
        if not t:
            continue
        if actual == t or actual.startswith(t):
            return True
    return False


async def resolve_status_id_by_name(client: RedmineClient, name: str) -> int | None:
    """Resolve Redmine status id by name (case-insensitive, trimmed); same rules as ``resolve_issue_status_id_by_name``."""
    return await client.resolve_issue_status_id_by_name(name)


def parse_redmine_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    # Redmine uses ISO8601, often with Z
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)
