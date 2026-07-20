"""Redmine issue list markdown for slash commands and scheduled reports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

from discord.utils import escape_markdown

from ultron.config import UnassignedOpenConfig
from ultron.redmine import RedmineClient, RedmineError, resolve_status_id_by_name

TopTicketsKind = Literal["priority", "newests", "oldests"]

TOP_TICKETS_KINDS: frozenset[str] = frozenset({"priority", "newests", "oldests"})
TOP_TICKETS_KIND_SORT: dict[str, str] = {
    "priority": "priority:desc",
    "newests": "created_on:desc",
    "oldests": "created_on:asc",
}
TOP_TICKETS_KIND_LABELS: dict[str, str] = {
    "priority": "highest priority",
    "newests": "newest",
    "oldests": "oldest",
}
_TOP_TICKETS_KIND_ALIASES: dict[str, TopTicketsKind] = {
    "priority": "priority",
    "prio": "priority",
    "priorities": "priority",
    "newests": "newests",
    "newest": "newests",
    "new": "newests",
    "recent": "newests",
    "oldests": "oldests",
    "oldest": "oldests",
    "old": "oldests",
}
_TOP_TICKETS_DEFAULT_LIMIT = 10
_TOP_TICKETS_MAX_LIMIT = 50
_PROJECT_FUZZY_CUTOFF = 0.55

# Redmine search titles look like: "Issue #10 (Closed): Subject here"
_SEARCH_ISSUE_TITLE_RE = re.compile(
    r"^Issue #(\d+)(?:\s+\([^)]*\))?:\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)

_FIND_ISSUE_DETAIL_LIMIT = 20
_FIND_ISSUE_TITLE_CROP = 15
_FIND_ISSUE_MAX_RESULTS = 200


def parse_search_issue_hit(hit: dict[str, Any]) -> tuple[int, str] | None:
    """Extract ``(issue_id, subject)`` from a Redmine search result, or ``None`` if not an issue."""
    typ = str(hit.get("type") or "").strip().casefold()
    if typ and not typ.startswith("issue"):
        return None
    raw_id = hit.get("id")
    try:
        iid = int(raw_id)
    except (TypeError, ValueError):
        return None
    if iid <= 0:
        return None
    title = str(hit.get("title") or "").replace("\n", " ").strip()
    m = _SEARCH_ISSUE_TITLE_RE.match(title)
    if m:
        try:
            parsed_id = int(m.group(1))
        except ValueError:
            parsed_id = iid
        if parsed_id > 0:
            iid = parsed_id
        subject = (m.group(2) or "").strip()
    else:
        subject = title
    return iid, subject


def crop_issue_subject(subject: str, *, max_chars: int = _FIND_ISSUE_TITLE_CROP) -> str:
    """Return subject cropped to ``max_chars`` (no ellipsis)."""
    s = (subject or "").replace("\n", " ").strip()
    if max_chars <= 0:
        return ""
    return s[:max_chars]


def format_find_issue_detail_line(
    issue_id: int,
    subject: str,
    redmine: RedmineClient,
    *,
    title_crop: int = _FIND_ISSUE_TITLE_CROP,
) -> str:
    """``<15-char title> [#id](url)`` (title escaped for Discord markdown)."""
    cropped = crop_issue_subject(subject, max_chars=title_crop)
    subj_display = escape_markdown(cropped) if cropped else ""
    link = f"[#{issue_id}]({redmine.issue_url(issue_id)})"
    if subj_display:
        return f"{subj_display} {link}"
    return link


def format_find_issue_id_link(issue_id: int, redmine: RedmineClient) -> str:
    return f"[#{issue_id}]({redmine.issue_url(issue_id)})"


async def markdown_unassigned_open_issues(
    *,
    redmine: RedmineClient,
    cfg: UnassignedOpenConfig,
) -> tuple[str | None, str | None, int]:
    """Build markdown body, optional error string, and issue count (-1 on fetch error)."""
    try:
        issues = await redmine.list_unassigned_open_issues_older_than_days(
            min_age_days=cfg.min_age_days,
            closed_status_prefixes=cfg.closed_status_prefixes,
        )
    except RedmineError as e:
        return None, f"Redmine error: {e}", -1
    total = len(issues)
    if total == 0:
        return (
            "No **unassigned** **open** issues created at least "
            f"**{cfg.min_age_days}** day(s) ago (within the search limit), after excluding "
            "closed-equivalent status prefixes.",
            None,
            0,
        )
    n_show = min(cfg.list_limit, total)
    header = (
        f"**Unassigned open issues** (created ≥{cfg.min_age_days} days ago; "
        "unassigned; Redmine `open`; excluding configured closed-equivalent status prefixes) · "
        f"**{total}** total"
    )
    line_strs = discord_formatted_issue_lines(issues[:n_show], redmine)
    body = header + "\n\n" + "\n".join(line_strs)
    rest = total - n_show
    if rest > 0:
        _other = "issue" if rest == 1 else "issues"
        body += f"\n\nand **{rest}** other {_other} matching this filter."
    return body, None, total


async def markdown_issues_by_status(
    *,
    redmine: RedmineClient,
    status_name: str,
    min_age_days: int,
    list_limit: int,
) -> tuple[str | None, str | None, int]:
    """Build markdown, optional error, total count (-1 on error)."""
    try:
        sid = await resolve_status_id_by_name(redmine, status_name)
        if sid is None:
            return (
                None,
                f"No Redmine issue status named `{status_name!r}`. "
                "Check **Administration → Issue statuses** for the exact name.",
                -1,
            )
        issues = await redmine.list_issues_older_than_days(
            status_id=sid,
            min_age_days=min_age_days,
        )
    except RedmineError as e:
        return None, f"Redmine error: {e}", -1
    total = len(issues)
    if total == 0:
        return (
            f"No issues in status `{status_name}` created at least **{min_age_days}** days ago "
            "(within the search limit).",
            None,
            0,
        )
    n_show = min(list_limit, total)
    header = (
        f"**Issues with status `{status_name}`** (created ≥{min_age_days} days ago) · "
        f"**{total}** total"
    )
    line_strs = discord_formatted_issue_lines(issues[:n_show], redmine)
    body = header + "\n\n" + "\n".join(line_strs)
    rest = total - n_show
    if rest > 0:
        _other = "issue" if rest == 1 else "issues"
        body += f"\n\nand **{rest}** other {_other} with status `{status_name}`."
    return body, None, total


def discord_formatted_issue_lines(issues: list[dict[str, Any]], redmine: RedmineClient) -> list[str]:
    """Markdown lines: escaped subject + linked ``[#id](url)`` per issue."""
    line_strs: list[str] = []
    for iss in issues:
        iid = int(iss["id"])
        raw_subj = str(iss.get("subject", "")).replace("\n", " ").strip()
        if len(raw_subj) > 200:
            raw_subj = raw_subj[:197] + "..."
        subj_display = escape_markdown(raw_subj)
        if len(subj_display) > 220:
            subj_display = subj_display[:217] + "..."
        url = redmine.issue_url(iid)
        link = f"[#{iid}]({url})"
        if raw_subj:
            line_strs.append(f"{subj_display} {link}")
        else:
            line_strs.append(link)
    return line_strs


async def markdown_find_issues(
    *,
    redmine: RedmineClient,
    text: str,
    project_id: str,
    detail_limit: int = _FIND_ISSUE_DETAIL_LIMIT,
    title_crop: int = _FIND_ISSUE_TITLE_CROP,
    max_results: int = _FIND_ISSUE_MAX_RESULTS,
) -> tuple[str | None, str | None, int]:
    """Build markdown for ``/find_issue``: up to ``detail_limit`` cropped titles + overflow id links.

    Returns ``(body, error, total)``. ``total`` is Redmine's ``total_count`` (-1 on error).
    """
    q = (text or "").strip()
    proj = (project_id or "").strip()
    if not q:
        return None, "Pass **`text`**: a short hint to search for (title, description, notes, …).", -1
    if not proj:
        return None, "Configure **`redmine.find_issue_project`** in `config.yaml` (Redmine project identifier).", -1
    try:
        hits, total = await redmine.search_issues_collect(
            q,
            project_id=proj,
            max_results=max_results,
        )
    except ValueError as e:
        return None, str(e), -1
    except RedmineError as e:
        return None, f"Redmine error: {e}", -1

    parsed: list[tuple[int, str]] = []
    seen: set[int] = set()
    for hit in hits:
        item = parse_search_issue_hit(hit)
        if item is None:
            continue
        iid, subject = item
        if iid in seen:
            continue
        seen.add(iid)
        parsed.append((iid, subject))

    if not parsed:
        safe_q = escape_markdown(q)
        safe_proj = escape_markdown(proj)
        return (
            f"No issues matching **{safe_q}** in project **{safe_proj}**.",
            None,
            0,
        )

    # Prefer Redmine total_count when larger than what we collected (capped fetch).
    reported_total = max(total, len(parsed))
    n_detail = min(detail_limit, len(parsed))
    safe_q = escape_markdown(q)
    safe_proj = escape_markdown(proj)
    header = (
        f"**Find issue** · `{safe_q}` · project `{safe_proj}` · "
        f"**{reported_total}** match{'es' if reported_total != 1 else ''}"
    )
    lines = [
        format_find_issue_detail_line(iid, subj, redmine, title_crop=title_crop)
        for iid, subj in parsed[:n_detail]
    ]
    body = header + "\n\n" + "\n".join(lines)

    overflow = parsed[n_detail:]
    if reported_total > detail_limit and overflow:
        id_links = ", ".join(format_find_issue_id_link(iid, redmine) for iid, _ in overflow)
        body += f"\n\nAlso matching: {id_links}"
        rest_unfetched = reported_total - len(parsed)
        if rest_unfetched > 0:
            body += f" (+**{rest_unfetched}** more not listed; search capped at {max_results})"
    return body, None, reported_total


def normalize_top_tickets_kind(raw: str | None) -> TopTicketsKind:
    """Map user/NL kind to ``priority`` | ``newests`` | ``oldests`` (default ``priority``)."""
    if raw is None or not str(raw).strip():
        return "priority"
    key = str(raw).strip().casefold()
    mapped = _TOP_TICKETS_KIND_ALIASES.get(key)
    if mapped is None:
        raise ValueError(
            "kind_filter must be one of: **priority**, **newests**, **oldests** "
            "(aliases: newest, oldest)."
        )
    return mapped


def clamp_top_tickets_limit(raw: int | None) -> int:
    """Default 10; clamp to 1..50."""
    if raw is None:
        return _TOP_TICKETS_DEFAULT_LIMIT
    try:
        n = int(raw)
    except (TypeError, ValueError) as e:
        raise ValueError("limit must be a positive integer") from e
    if n <= 0:
        raise ValueError("limit must be a positive integer")
    return min(n, _TOP_TICKETS_MAX_LIMIT)


def _fold_project_key(s: str) -> str:
    """Normalize for comparison: casefold, collapse spaces/underscores to hyphens."""
    t = (s or "").strip().casefold()
    t = re.sub(r"[\s_]+", "-", t)
    t = re.sub(r"-{2,}", "-", t)
    return t.strip("-")


@dataclass(frozen=True)
class ResolvedRedmineProject:
    """A Redmine project resolved from a user query."""

    identifier: str
    name: str
    numeric_id: int
    exact: bool
    score: float


def resolve_redmine_project(
    query: str,
    projects: list[dict[str, Any]],
) -> ResolvedRedmineProject | None:
    """Resolve ``query`` to a project by identifier or name (exact, substring, then fuzzy)."""
    q = (query or "").strip()
    if not q or not projects:
        return None
    q_fold = _fold_project_key(q)
    if not q_fold:
        return None

    best: ResolvedRedmineProject | None = None

    def _consider(
        proj: dict[str, Any],
        *,
        score: float,
        exact: bool,
    ) -> None:
        nonlocal best
        ident = str(proj.get("identifier") or "").strip()
        name = str(proj.get("name") or "").strip()
        raw_id = proj.get("id")
        try:
            nid = int(raw_id)
        except (TypeError, ValueError):
            return
        if not ident:
            return
        cand = ResolvedRedmineProject(
            identifier=ident,
            name=name or ident,
            numeric_id=nid,
            exact=exact,
            score=score,
        )
        if best is None:
            best = cand
            return
        if exact and not best.exact:
            best = cand
            return
        if exact == best.exact and score > best.score:
            best = cand

    for proj in projects:
        ident = str(proj.get("identifier") or "").strip()
        name = str(proj.get("name") or "").strip()
        ident_fold = _fold_project_key(ident)
        name_fold = _fold_project_key(name)
        if ident_fold and ident_fold == q_fold:
            _consider(proj, score=1.0, exact=True)
            continue
        if name_fold and name_fold == q_fold:
            _consider(proj, score=1.0, exact=True)

    if best is not None and best.exact:
        return best

    for proj in projects:
        ident = str(proj.get("identifier") or "").strip()
        name = str(proj.get("name") or "").strip()
        ident_fold = _fold_project_key(ident)
        name_fold = _fold_project_key(name)
        for hay in (ident_fold, name_fold):
            if not hay:
                continue
            if q_fold in hay or hay in q_fold:
                # Longer overlap → higher score; prefer identifier containment slightly.
                overlap = min(len(q_fold), len(hay)) / max(len(q_fold), len(hay), 1)
                score = 0.75 + 0.2 * overlap
                if hay == ident_fold:
                    score += 0.01
                _consider(proj, score=score, exact=False)

    for proj in projects:
        ident = str(proj.get("identifier") or "").strip()
        name = str(proj.get("name") or "").strip()
        for label in (ident, name):
            if not label:
                continue
            ratio = SequenceMatcher(None, q_fold, _fold_project_key(label)).ratio()
            if ratio >= _PROJECT_FUZZY_CUTOFF:
                _consider(proj, score=ratio, exact=False)

    if best is None:
        return None
    if best.exact or best.score >= _PROJECT_FUZZY_CUTOFF:
        return best
    return None


def discord_formatted_top_ticket_lines(
    issues: list[dict[str, Any]],
    redmine: RedmineClient,
    *,
    show_priority: bool,
) -> list[str]:
    """Markdown lines for ``/top_tickets`` (optional priority label)."""
    line_strs: list[str] = []
    for iss in issues:
        iid = int(iss["id"])
        raw_subj = str(iss.get("subject", "")).replace("\n", " ").strip()
        if len(raw_subj) > 200:
            raw_subj = raw_subj[:197] + "..."
        subj_display = escape_markdown(raw_subj)
        if len(subj_display) > 220:
            subj_display = subj_display[:217] + "..."
        url = redmine.issue_url(iid)
        link = f"[#{iid}]({url})"
        prio = ""
        if show_priority:
            pname = str((iss.get("priority") or {}).get("name") or "").strip()
            if pname:
                prio = f"**[{escape_markdown(pname)}]** "
        if raw_subj:
            line_strs.append(f"{prio}{subj_display} {link}")
        else:
            line_strs.append(f"{prio}{link}".strip())
    return line_strs


async def markdown_top_tickets(
    *,
    redmine: RedmineClient,
    project_query: str,
    kind_filter: str | None = None,
    limit: int | None = None,
) -> tuple[str | None, str | None, int]:
    """Build markdown for ``/top_tickets``.

    Returns ``(body, error, shown_count)``. ``shown_count`` is ``-1`` on error.
    Lists **open** issues in the resolved project, sorted by kind.
    """
    q = (project_query or "").strip()
    if not q:
        return None, "Pass **`project`**: a Redmine project identifier or name (fuzzy match ok).", -1
    try:
        kind = normalize_top_tickets_kind(kind_filter)
        n = clamp_top_tickets_limit(limit)
    except ValueError as e:
        return None, str(e), -1

    try:
        projects = await redmine.list_projects()
    except RedmineError as e:
        return None, f"Redmine error: {e}", -1

    matched = resolve_redmine_project(q, projects)
    if matched is None:
        safe_q = escape_markdown(q)
        return (
            None,
            f"No Redmine project matching **{safe_q}**. "
            "Try the project **identifier** (e.g. `dip-re`) or the display **name**.",
            -1,
        )

    sort = TOP_TICKETS_KIND_SORT[kind]
    try:
        issues = await redmine.list_issues(
            sort=sort,
            limit=n,
            status_id="open",
            project_id=matched.identifier,
        )
    except RedmineError as e:
        return None, f"Redmine error: {e}", -1

    safe_name = escape_markdown(matched.name)
    safe_ident = escape_markdown(matched.identifier)
    kind_label = TOP_TICKETS_KIND_LABELS[kind]
    match_note = ""
    if not matched.exact:
        safe_q = escape_markdown(q)
        match_note = f" (matched from `{safe_q}`)"
    header = (
        f"**Top tickets** · **{safe_name}** (`{safe_ident}`){match_note} · "
        f"by **{kind_label}** · open · up to **{n}**"
    )
    if not issues:
        return (
            f"{header}\n\nNo **open** issues in this project.",
            None,
            0,
        )
    lines = discord_formatted_top_ticket_lines(
        issues,
        redmine,
        show_priority=(kind == "priority"),
    )
    body = header + "\n\n" + "\n".join(lines)
    return body, None, len(issues)
