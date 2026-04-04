"""Redmine issue list markdown for slash commands and scheduled reports."""

from __future__ import annotations

from typing import Any

from discord.utils import escape_markdown

from ultron.config import UnassignedOpenConfig
from ultron.redmine import RedmineClient, RedmineError, resolve_status_id_by_name


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
