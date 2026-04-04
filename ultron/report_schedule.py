"""Scheduled Redmine listing posts to the reports Discord channel."""

from __future__ import annotations

import logging

import discord

from ultron.config import AppConfig, ReportScheduleEntry
from ultron.redmine import RedmineClient
from ultron.redmine_listings import markdown_issues_by_status, markdown_unassigned_open_issues
from ultron.textutil import chunk_discord

logger = logging.getLogger("ultron.reports")


def schedule_args_dict(entry: ReportScheduleEntry) -> dict[str, str]:
    return dict(entry.args)


def humanize_report_schedule_summary(app_cfg: AppConfig) -> str:
    """English bullet list describing each scheduled job (for startup message)."""
    lines: list[str] = []
    for entry in app_cfg.report_schedule:
        every = entry.interval_hours
        if every % 24 == 0 and every >= 24:
            d = every // 24
            freq = f"every {d} days" if d != 1 else "every day"
        else:
            freq = f"every {every} hours" if every != 1 else "every hour"
        if entry.command == "list_new_issues":
            ni = app_cfg.discord.new_issues
            st = ni.status_name.strip() or "(set discord.new_issues.status_name)"
            lines.append(
                f"- **`/{entry.command}`** ({freq}): status `{st}`, min age {ni.min_age_days}d, cap {ni.list_limit}."
            )
        elif entry.command == "list_unassigned_issues":
            uo = app_cfg.discord.unassigned_open
            lines.append(
                f"- **`/{entry.command}`** ({freq}): unassigned open issues, min age {uo.min_age_days}d, cap {uo.list_limit}."
            )
        else:
            st = schedule_args_dict(entry).get("status", "")
            lines.append(
                f"- **`/{entry.command}`** ({freq}): Redmine status `{st}` (min age & cap from discord.new_issues)."
            )
    return "\n".join(lines) if lines else "_No jobs in **report_schedule**._"


def build_reports_startup_message(app_cfg: AppConfig) -> str:
    welcome = app_cfg.reports.startup_welcome.strip()
    if not welcome:
        welcome = "**Ultron** is online. Scheduled Redmine listings will be posted to this channel."
    summary = humanize_report_schedule_summary(app_cfg)
    return f"{welcome}\n\n**Configured schedule:**\n{summary}"


async def run_report_schedule_entry(
    *,
    redmine: RedmineClient,
    app_cfg: AppConfig,
    channel: discord.abc.Messageable,
    entry: ReportScheduleEntry,
) -> None:
    prefix = f"**Scheduled report** (`/{entry.command}`)\n\n"
    ni = app_cfg.discord.new_issues
    uo = app_cfg.discord.unassigned_open
    try:
        if entry.command == "list_new_issues":
            if not ni.status_name.strip():
                await channel.send(
                    prefix + "Skipped: set **discord.new_issues.status_name** in `config.yaml`."
                )
                return
            body, err, _total = await markdown_issues_by_status(
                redmine=redmine,
                status_name=ni.status_name.strip(),
                min_age_days=ni.min_age_days,
                list_limit=ni.list_limit,
            )
        elif entry.command == "list_unassigned_issues":
            body, err, _total = await markdown_unassigned_open_issues(redmine=redmine, cfg=uo)
        else:
            st = schedule_args_dict(entry)["status"].strip()
            body, err, _total = await markdown_issues_by_status(
                redmine=redmine,
                status_name=st,
                min_age_days=ni.min_age_days,
                list_limit=ni.list_limit,
            )
    except Exception as e:
        logger.exception("report_schedule %s failed", entry.command)
        try:
            await channel.send(prefix + f"Run failed: `{type(e).__name__}` (see bot logs).")
        except discord.HTTPException as send_exc:
            logger.warning("report_schedule could not send error to channel: %s", send_exc)
        return
    if err is not None:
        try:
            await channel.send(prefix + err)
        except discord.HTTPException as send_exc:
            logger.warning("report_schedule could not send error body: %s", send_exc)
        return
    assert body is not None
    full = prefix + body
    parts = chunk_discord(full, limit=1900)
    try:
        await channel.send(parts[0], suppress_embeds=True)
        for part in parts[1:]:
            await channel.send(part, suppress_embeds=True)
    except discord.HTTPException as e:
        logger.warning("report_schedule send failed: %s", e)
