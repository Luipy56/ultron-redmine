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

    async def list_time_entry_activities(self) -> list[dict[str, Any]]:
        """Time tracking activities from Redmine (id, name, is_default, active, …)."""
        async with self._client() as c:
            r = await c.get("/enumerations/time_entry_activities.json")
        if r.is_error:
            raise RedmineError(
                f"Redmine list time_entry_activities failed: {r.status_code} {r.text[:500]}"
            )
        data = r.json()
        return list(data.get("time_entry_activities", []))

    async def create_time_entry(
        self,
        issue_id: int,
        hours: float,
        *,
        activity_id: int,
        comments: str | None = None,
    ) -> dict[str, Any]:
        """POST a new time entry on ``issue_id``. Raises IssueNotFound when Redmine returns 404."""
        body: dict[str, Any] = {
            "issue_id": issue_id,
            "hours": hours,
            "activity_id": activity_id,
        }
        if comments is not None and str(comments).strip():
            body["comments"] = str(comments).strip()[:255]
        async with self._client() as c:
            r = await c.post("/time_entries.json", json={"time_entry": body})
        if r.status_code == 404:
            raise IssueNotFound(f"Issue #{issue_id} not found")
        if r.is_error:
            raise RedmineError(f"Redmine POST time_entries failed: {r.status_code} {r.text[:500]}")
        data = r.json()
        te = data.get("time_entry")
        return te if isinstance(te, dict) else data


def _active_time_entry_activities(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries Redmine marks as active; if ``active`` is absent, treat as active."""
    out: list[dict[str, Any]] = []
    for a in activities:
        if a.get("active") is False:
            continue
        if a.get("id") is None:
            continue
        out.append(a)
    return out


def _format_activity_list_for_error(activities: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for a in _active_time_entry_activities(activities):
        aid = a.get("id")
        name = str(a.get("name", "") or "").strip() or "(unnamed)"
        lines.append(f"  • id **{aid}** — {name}")
    if not lines:
        return "(no active time entry activities returned by Redmine)"
    return "\n".join(lines)


def resolve_time_activity_id(
    activities: list[dict[str, Any]],
    env_override: str | None,
) -> int:
    """Pick a time-entry ``activity_id`` for POST /time_entries.json.

    Order: non-empty ``REDMINE_TIME_ACTIVITY_ID`` env (must match an active activity),
    else single ``is_default`` activity, else sole active activity, else raises
    ``ValueError`` with a user-visible message listing choices.
    """
    active = _active_time_entry_activities(activities)
    ids_active = {int(a["id"]) for a in active}

    raw_env = (env_override or "").strip()
    if raw_env:
        try:
            want = int(raw_env)
        except ValueError as e:
            raise ValueError(
                f"**REDMINE_TIME_ACTIVITY_ID** must be a numeric id. Available activities:\n"
                f"{_format_activity_list_for_error(activities)}"
            ) from e
        if want not in ids_active:
            raise ValueError(
                f"**REDMINE_TIME_ACTIVITY_ID** ({want}) is not an active time entry activity. "
                f"Set it to one of: {', '.join(str(i) for i in sorted(ids_active))}.\n"
                f"Available activities:\n{_format_activity_list_for_error(activities)}"
            )
        return want

    defaults = [a for a in active if a.get("is_default") is True]
    if len(defaults) == 1:
        return int(defaults[0]["id"])
    if len(defaults) > 1:
        raise ValueError(
            "Redmine reports multiple default time activities; set **REDMINE_TIME_ACTIVITY_ID** "
            f"in `.env` to one of these ids:\n{_format_activity_list_for_error(activities)}"
        )

    if len(active) == 1:
        return int(active[0]["id"])

    raise ValueError(
        "Cannot pick a time entry activity automatically. Set **REDMINE_TIME_ACTIVITY_ID** "
        f"in `.env` to one of these ids:\n{_format_activity_list_for_error(activities)}"
    )


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
