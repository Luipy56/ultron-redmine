from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


class RedmineError(Exception):
    pass


class IssueNotFound(RedmineError):
    pass


class RedminePermissionError(RedmineError):
    """Redmine returned 403 (or equivalent) for an API operation."""


def _redmine_user_hint(status_code: int, body: str) -> str | None:
    """Short user-facing hint from JSON error payload (no secrets)."""
    if status_code == 403:
        return (
            "Redmine **refused** this action (**403**). The API user may lack permission to log time "
            "on this issue or use this activity."
        )
    if status_code != 422:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    errs = data.get("errors")
    if isinstance(errs, list) and errs:
        parts = [str(x) for x in errs[:5] if x is not None]
        if parts:
            return "**Redmine validation:** " + "; ".join(parts)[:900]
    return "**Redmine** rejected the request (**422**). Check hours, activity, and issue permissions."


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

    async def fetch_current_user_label(self) -> str:
        """Return login (or id) for the API user after a successful ``/users/current.json`` read."""
        async with self._client() as c:
            r = await c.get("/users/current.json")
        if r.status_code == 401:
            raise RedmineError("Redmine rejected the API key (401 Unauthorized).")
        if r.is_error:
            raise RedmineError(f"Redmine connection check failed: {r.status_code} {r.text[:500]}")
        data = r.json()
        user = data.get("user") or {}
        login = str(user.get("login", "")).strip()
        if login:
            return login
        uid = user.get("id")
        if uid is not None:
            return f"id={uid}"
        return "(unknown)"

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

    async def get_user(self, user_id: int) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get(f"/users/{user_id}.json")
        if r.status_code == 404:
            raise RedmineError(f"Redmine user id {user_id} not found")
        if r.status_code == 403:
            raise RedminePermissionError(
                f"Redmine refused GET /users/{user_id}.json (403). Use a numeric id your API user may view, "
                "or configure **redmine.user_id_by_login** in config.yaml."
            )
        if r.is_error:
            raise RedmineError(f"Redmine GET user failed: {r.status_code} {r.text[:500]}")
        data = r.json()
        u = data.get("user")
        if not isinstance(u, dict):
            raise RedmineError("Redmine GET user: unexpected JSON")
        return u

    async def get_current_user(self) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get("/users/current.json")
        if r.is_error:
            raise RedmineError(f"Redmine GET users/current failed: {r.status_code} {r.text[:500]}")
        data = r.json()
        u = data.get("user")
        if not isinstance(u, dict):
            raise RedmineError("Redmine GET users/current: unexpected JSON")
        return u

    async def list_users_page(self, *, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        lim = min(max(1, limit), 100)
        off = max(0, offset)
        async with self._client() as c:
            r = await c.get("/users.json", params={"limit": lim, "offset": off, "status": 1})
        if r.status_code == 403:
            raise RedminePermissionError(
                "Cannot list Redmine users (**403**). Use a **numeric user id**, the **`me`** keyword, "
                "or set **redmine.user_id_by_login** in `config.yaml` (login → id map)."
            )
        if r.is_error:
            raise RedmineError(f"Redmine list users failed: {r.status_code} {r.text[:500]}")
        data = r.json()
        return list(data.get("users", []))

    async def find_user_id_by_login(self, login: str) -> int | None:
        """Paginate ``/users.json`` until ``login`` matches (case-insensitive) or pages exhaust."""
        want = login.strip().casefold()
        if not want:
            return None
        offset = 0
        for _ in range(50):
            page = await self.list_users_page(offset=offset, limit=100)
            if not page:
                return None
            for u in page:
                lu = str(u.get("login", "")).strip().casefold()
                if lu == want:
                    uid = u.get("id")
                    if uid is not None:
                        return int(uid)
            if len(page) < 100:
                return None
            offset += len(page)
        return None

    async def list_time_entries(
        self,
        *,
        user_id: int,
        spent_on_from: str | None,
        spent_on_to: str | None,
        max_entries: int,
    ) -> list[dict[str, Any]]:
        """Fetch time entries for ``user_id`` with optional ``spent_on`` range (YYYY-MM-DD). Paginates up to ``max_entries``."""
        cap = max(1, min(max_entries, 5000))
        out: list[dict[str, Any]] = []
        offset = 0
        params_base: dict[str, Any] = {"user_id": user_id, "limit": 100}
        if spent_on_from:
            params_base["from"] = spent_on_from
        if spent_on_to:
            params_base["to"] = spent_on_to
        while len(out) < cap:
            params = {**params_base, "offset": offset}
            async with self._client() as c:
                r = await c.get("/time_entries.json", params=params)
            if r.status_code == 403:
                raise RedminePermissionError(
                    "Cannot read time entries for this user (**403**). The API user may need permission "
                    "to **view all time entries** or time on visible projects only."
                )
            if r.is_error:
                raise RedmineError(f"Redmine list time_entries failed: {r.status_code} {r.text[:500]}")
            data = r.json()
            batch = list(data.get("time_entries", []))
            if not batch:
                break
            for te in batch:
                out.append(te)
                if len(out) >= cap:
                    break
            if len(batch) < 100:
                break
            offset += len(batch)
        return out

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
        spent_on: str | None = None,
    ) -> dict[str, Any]:
        """POST a new time entry on ``issue_id``. Raises IssueNotFound when Redmine returns 404."""
        body: dict[str, Any] = {
            "issue_id": issue_id,
            "hours": hours,
            "activity_id": activity_id,
        }
        if comments is not None and str(comments).strip():
            body["comments"] = str(comments).strip()[:255]
        if spent_on is not None and str(spent_on).strip():
            raw = str(spent_on).strip()[:10]
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
                raise ValueError(
                    "**spent_on** must be **YYYY-MM-DD** (Redmine calendar date for the time entry)."
                )
            body["spent_on"] = raw
        async with self._client() as c:
            r = await c.post("/time_entries.json", json={"time_entry": body})
        if r.status_code == 404:
            raise IssueNotFound(f"Issue #{issue_id} not found")
        if r.is_error:
            hint = _redmine_user_hint(r.status_code, r.text)
            msg = f"Redmine POST time_entries failed: {r.status_code} {r.text[:500]}"
            err = RedmineError(msg)
            err.user_message = hint  # type: ignore[attr-defined]
            raise err
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


def format_redmine_user_display(user: dict[str, Any]) -> str:
    login = str(user.get("login", "")).strip()
    fn = str(user.get("firstname", "")).strip()
    ln = str(user.get("lastname", "")).strip()
    name = f"{fn} {ln}".strip()
    if login and name:
        return f"{login} ({name})"
    if login:
        return login
    if name:
        return name
    uid = user.get("id")
    return f"id {uid}" if uid is not None else "unknown"


async def resolve_redmine_user_for_time_summary(
    client: RedmineClient,
    raw: str,
    aliases: Mapping[str, int],
) -> tuple[int, str]:
    """Resolve ``raw`` to Redmine ``user_id`` and a short display label.

    ``raw`` may be: ``me`` (current API user), a numeric id, a login matching ``aliases`` (case-insensitive keys),
    or a login discoverable via ``/users.json`` (requires API permission).
    """
    s = raw.strip()
    if not s:
        raise ValueError("Pass **user**: Redmine login, numeric **user id**, or **`me`** for the API user.")

    if s.casefold() == "me":
        u = await client.get_current_user()
        uid = u.get("id")
        if uid is None:
            raise RedmineError("Redmine /users/current returned no id")
        return int(uid), format_redmine_user_display(u)

    if s.isdigit():
        uid = int(s)
        u = await client.get_user(uid)
        return uid, format_redmine_user_display(u)

    key = s.casefold()
    if key in aliases:
        uid = int(aliases[key])
        try:
            u = await client.get_user(uid)
            return uid, format_redmine_user_display(u)
        except RedmineError:
            return uid, f"{s} (id {uid})"

    try:
        found = await client.find_user_id_by_login(s)
    except RedminePermissionError:
        raise ValueError(
            "Cannot look up Redmine logins (**permission denied**). Use a **numeric user id**, **`me`**, "
            "or add the login under **redmine.user_id_by_login** in `config.yaml`."
        ) from None
    if found is None:
        raise ValueError(
            f"No Redmine user with login **{s}**. Try a numeric **user id**, **`me`**, or **redmine.user_id_by_login**."
        )
    u = await client.get_user(found)
    return found, format_redmine_user_display(u)
