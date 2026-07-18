"""Redmine issue list markdown for slash commands and scheduled reports."""

from __future__ import annotations

import re
from typing import Any

from discord.utils import escape_markdown

from ultron.config import UnassignedOpenConfig
from ultron.redmine import RedmineClient, RedmineError, resolve_status_id_by_name

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
