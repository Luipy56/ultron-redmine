from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from zoneinfo import ZoneInfo

from ultron.config import AbandonedSchedule, StaleNewSchedule
from ultron.llm import LLMClient
from ultron.redmine import RedmineClient, parse_redmine_datetime
from ultron.textutil import chunk_discord

if TYPE_CHECKING:
    import discord

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _issue_line(issue: dict) -> str:
    iid = issue.get("id")
    subj = issue.get("subject", "")
    st = (issue.get("status") or {}).get("name", "")
    return f"- #{iid} [{st}] {subj}"


async def run_abandoned_report(
    *,
    redmine: RedmineClient,
    llm: LLMClient,
    channel: discord.abc.Messageable | None,
    cfg: AbandonedSchedule,
    timezone_name: str,
) -> None:
    if not cfg.enabled or channel is None:
        return

    tz = ZoneInfo(timezone_name)
    now_local = datetime.now(tz)
    cutoff_utc = _utc_now() - timedelta(days=cfg.max_days_without_update)

    issues = await redmine.list_open_issues(sort="updated_on:asc", limit=100)
    abandoned: list[dict] = []
    for iss in issues:
        updated = parse_redmine_datetime(iss.get("updated_on"))
        if updated is None:
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated < cutoff_utc:
            abandoned.append(iss)
        if len(abandoned) >= cfg.max_issues:
            break

    if not abandoned:
        await channel.send(
            f"**Abandoned tickets report** ({now_local.date()})\nNo open tickets found "
            f"with last update older than {cfg.max_days_without_update} days (within search limit)."
        )
        return

    lines = "\n".join(_issue_line(i) for i in abandoned)
    system = (
        "You produce short operational reports for a team. "
        "Given a list of Redmine issues, output a concise summary: grouped bullets, "
        "highlight risks, no fluff. Plain text."
    )
    user = f"Report date (server TZ {timezone_name}): {now_local.isoformat()}\n\nIssues:\n{lines}"
    try:
        report = await llm.complete(system=system, user=user)
    except Exception:
        logger.exception("LLM failed for abandoned report")
        await channel.send("**Abandoned tickets report**: LLM request failed (see logs).")
        return

    header = f"**Abandoned tickets** (no update in ≥{cfg.max_days_without_update} days)\n"
    for part in chunk_discord(header + report):
        await channel.send(part)


async def run_stale_new_report(
    *,
    redmine: RedmineClient,
    llm: LLMClient,
    channel: discord.abc.Messageable | None,
    cfg: StaleNewSchedule,
    timezone_name: str,
) -> None:
    if not cfg.enabled or channel is None:
        return

    tz = ZoneInfo(timezone_name)
    now_local = datetime.now(tz)
    min_age = timedelta(hours=cfg.min_age_hours)
    cutoff_time = _utc_now() - min_age

    candidates = await redmine.list_open_issues(sort="created_on:asc", limit=100)
    stale: list[dict] = []
    for iss in candidates:
        created = parse_redmine_datetime(iss.get("created_on"))
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created > cutoff_time:
            continue
        if cfg.require_unassigned and iss.get("assigned_to") is not None:
            continue

        iid = int(iss["id"])
        full = await redmine.get_issue(iid, includes="journals")
        journals = full.get("journals") or []
        if len(journals) > cfg.max_journal_entries:
            continue
        stale.append(iss)
        if len(stale) >= cfg.max_issues:
            break

    if not stale:
        await channel.send(
            f"**Stale new tickets** ({now_local.date()})\nNo matching tickets "
            f"(open, ≥{cfg.min_age_hours}h old"
            + (", unassigned" if cfg.require_unassigned else "")
            + f", ≤{cfg.max_journal_entries} journal entries).\n"
        )
        return

    lines = "\n".join(_issue_line(i) for i in stale)
    system = (
        "You produce short operational reports for a team. "
        "These are new-ish Redmine tickets with little or no activity. "
        "Suggest triage actions. Plain text, concise."
    )
    user = f"Report date (server TZ {timezone_name}): {now_local.isoformat()}\n\nTickets:\n{lines}"
    try:
        report = await llm.complete(system=system, user=user)
    except Exception:
        logger.exception("LLM failed for stale-new report")
        await channel.send("**Stale new tickets report**: LLM request failed (see logs).")
        return

    header = (
        f"**Stale new tickets** (≥{cfg.min_age_hours}h old"
        + (", unassigned" if cfg.require_unassigned else "")
        + f", ≤{cfg.max_journal_entries} journals)\n"
    )
    for part in chunk_discord(header + report):
        await channel.send(part)
