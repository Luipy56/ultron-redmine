from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from zoneinfo import ZoneInfo

from ultron.config import AbandonedSchedule, StaleNewSchedule
from ultron.llm import LLMBackend, LLMChainExhaustedError, NullLLMBackend
from ultron.readlog import log_read_payload
from ultron.redmine import RedmineClient, RedmineError, parse_redmine_datetime
from ultron.textutil import chunk_discord
from ultron.workflow_log import wf_exception, wf_info

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


def _effective_stale_new_status_name(cfg: StaleNewSchedule, override: str | None) -> str | None:
    """Slash ``status`` overrides config when provided (including explicit empty → all open)."""
    if override is not None:
        s = override.strip()
        return s if s else None
    return cfg.issue_status_name


async def run_abandoned_report(
    *,
    redmine: RedmineClient,
    llm: LLMBackend,
    channel: discord.abc.Messageable | None,
    cfg: AbandonedSchedule,
    timezone_name: str,
    log_read_messages: bool = False,
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

    if isinstance(llm, NullLLMBackend):
        header = (
            f"**Abandoned tickets** (no update in ≥{cfg.max_days_without_update} days) — "
            f"{now_local.date()}\n"
            "_No language model is configured — listing issues without an AI summary._\n\n"
        )
        body = "\n".join(_issue_line(i) for i in abandoned)
        for part in chunk_discord(header + body):
            await channel.send(part)
        return

    lines = "\n".join(_issue_line(i) for i in abandoned)
    system = (
        "You produce short operational reports for a team. "
        "Given a list of Redmine issues, output a concise summary: grouped bullets, "
        "highlight risks, no fluff. Plain text."
    )
    user = f"Report date (server TZ {timezone_name}): {now_local.isoformat()}\n\nIssues:\n{lines}"
    if log_read_messages:
        log_read_payload(label="report.abandoned.llm_system", text=system)
        log_read_payload(label="report.abandoned.llm_user", text=user)
    wf_info(
        logger,
        "abandoned_report",
        "FETCH",
        "open_issues_scanned=%s abandoned_selected=%s prompt_chars=%s",
        len(issues),
        len(abandoned),
        len(user),
    )
    wf_info(logger, "abandoned_report", "LLM_CALL", "begin")
    try:
        report = await llm.complete(system=system, user=user)
    except LLMChainExhaustedError:
        wf_info(logger, "abandoned_report", "ERROR", "all llm_chain providers failed")
        await channel.send(
            "**Abandoned tickets report**: All configured language model providers failed (see logs)."
        )
        return
    except Exception as e:
        wf_exception(logger, "abandoned_report", e)
        await channel.send("**Abandoned tickets report**: LLM request failed (see logs).")
        return

    wf_info(logger, "abandoned_report", "LLM_DONE", "report_chars=%s", len(report))
    header = f"**Abandoned tickets** (no update in ≥{cfg.max_days_without_update} days)\n"
    for part in chunk_discord(header + report):
        await channel.send(part)


async def run_stale_new_report(
    *,
    redmine: RedmineClient,
    llm: LLMBackend,
    channel: discord.abc.Messageable | None,
    cfg: StaleNewSchedule,
    timezone_name: str,
    log_read_messages: bool = False,
    status_name_override: str | None = None,
    force: bool = False,
) -> None:
    if channel is None:
        return
    if not force and not cfg.enabled:
        return

    tz = ZoneInfo(timezone_name)
    now_local = datetime.now(tz)
    min_age = timedelta(hours=cfg.min_age_hours)
    cutoff_time = _utc_now() - min_age

    effective_status = _effective_stale_new_status_name(cfg, status_name_override)
    status_id_param: str | int = "open"
    status_scope: str
    if effective_status:
        try:
            rid = await redmine.resolve_issue_status_id_by_name(effective_status)
        except RedmineError as e:
            await channel.send(
                f"**Stale new tickets** ({now_local.date()})\n"
                f"Could not load issue statuses from Redmine: {e}"
            )
            return
        if rid is None:
            await channel.send(
                f"**Stale new tickets** ({now_local.date()})\n"
                f"No issue status named **{effective_status!s}** in Redmine. "
                "Use the exact label from **Administration → Issue statuses** (matching is case-insensitive)."
            )
            return
        status_id_param = rid
        status_scope = f"status **{effective_status}** (id {rid})"
    else:
        status_scope = "all **open** issues"

    try:
        candidates = await redmine.list_issues(
            sort="created_on:asc",
            limit=100,
            status_id=status_id_param,
        )
    except RedmineError as e:
        await channel.send(f"**Stale new tickets** ({now_local.date()})\nRedmine request failed: {e}")
        return

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
        if log_read_messages:
            subj = str(full.get("subject", ""))
            log_read_payload(
                label=f"report.stale_new.inspect issue_id={iid}",
                text=f"subject={subj!r} journal_count={len(journals)}",
            )
        if len(journals) > cfg.max_journal_entries:
            continue
        stale.append(iss)
        if len(stale) >= cfg.max_issues:
            break

    if not stale:
        await channel.send(
            f"**Stale new tickets** ({now_local.date()})\nNo matching tickets for {status_scope} "
            f"(≥{cfg.min_age_hours}h old"
            + (", unassigned" if cfg.require_unassigned else "")
            + f", ≤{cfg.max_journal_entries} journal entries; scanned up to {len(candidates)} in list order).\n"
        )
        return

    if isinstance(llm, NullLLMBackend):
        header = (
            f"**Stale new tickets** ({status_scope}, ≥{cfg.min_age_hours}h old"
            + (", unassigned" if cfg.require_unassigned else "")
            + f", ≤{cfg.max_journal_entries} journals) — {now_local.date()}\n"
            "_No language model is configured — listing tickets without an AI summary._\n\n"
        )
        body = "\n".join(_issue_line(i) for i in stale)
        for part in chunk_discord(header + body):
            await channel.send(part)
        return

    lines = "\n".join(_issue_line(i) for i in stale)
    system = (
        "You produce short operational reports for a team. "
        "These are new-ish Redmine tickets with little or no activity. "
        "Suggest triage actions. Plain text, concise."
    )
    user = f"Report date (server TZ {timezone_name}): {now_local.isoformat()}\n\nTickets:\n{lines}"
    if log_read_messages:
        log_read_payload(label="report.stale_new.llm_system", text=system)
        log_read_payload(label="report.stale_new.llm_user", text=user)
    wf_info(
        logger,
        "stale_new_report",
        "FETCH",
        "candidates_scanned=%s stale_selected=%s prompt_chars=%s",
        len(candidates),
        len(stale),
        len(user),
    )
    wf_info(logger, "stale_new_report", "LLM_CALL", "begin")
    try:
        report = await llm.complete(system=system, user=user)
    except LLMChainExhaustedError:
        wf_info(logger, "stale_new_report", "ERROR", "all llm_chain providers failed")
        await channel.send(
            "**Stale new tickets report**: All configured language model providers failed (see logs)."
        )
        return
    except Exception as e:
        wf_exception(logger, "stale_new_report", e)
        await channel.send("**Stale new tickets report**: LLM request failed (see logs).")
        return

    wf_info(logger, "stale_new_report", "LLM_DONE", "report_chars=%s", len(report))
    header = (
        f"**Stale new tickets** ({status_scope}, ≥{cfg.min_age_hours}h old"
        + (", unassigned" if cfg.require_unassigned else "")
        + f", ≤{cfg.max_journal_entries} journals)\n"
    )
    for part in chunk_discord(header + report):
        await channel.send(part)
