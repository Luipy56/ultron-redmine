from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DiscordConfig:
    ephemeral_default: bool = True


@dataclass
class ReportsConfig:
    channel_id: int = 0


@dataclass
class AbandonedSchedule:
    enabled: bool = True
    interval_hours: int = 24
    max_days_without_update: int = 14
    max_issues: int = 50


@dataclass
class StaleNewSchedule:
    enabled: bool = True
    interval_hours: int = 24
    min_age_hours: int = 2
    require_unassigned: bool = True
    max_journal_entries: int = 1
    max_issues: int = 50


@dataclass
class SchedulesConfig:
    abandoned: AbandonedSchedule
    stale_new: StaleNewSchedule


@dataclass
class AppConfig:
    timezone: str
    discord: DiscordConfig
    reports: ReportsConfig
    schedules: SchedulesConfig


def _bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    return bool(v)


def _int(v: Any, default: int) -> int:
    if v is None:
        return default
    return int(v)


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    tz = str(raw.get("timezone") or "UTC")

    d_raw = raw.get("discord") or {}
    discord_cfg = DiscordConfig(ephemeral_default=_bool(d_raw.get("ephemeral_default"), True))

    r_raw = raw.get("reports") or {}
    reports_cfg = ReportsConfig(channel_id=_int(r_raw.get("channel_id"), 0))

    s_raw = raw.get("schedules") or {}
    ab = s_raw.get("abandoned") or {}
    sn = s_raw.get("stale_new") or {}

    abandoned = AbandonedSchedule(
        enabled=_bool(ab.get("enabled"), True),
        interval_hours=_int(ab.get("interval_hours"), 24),
        max_days_without_update=_int(ab.get("max_days_without_update"), 14),
        max_issues=_int(ab.get("max_issues"), 50),
    )
    stale_new = StaleNewSchedule(
        enabled=_bool(sn.get("enabled"), True),
        interval_hours=_int(sn.get("interval_hours"), 24),
        min_age_hours=_int(sn.get("min_age_hours"), 2),
        require_unassigned=_bool(sn.get("require_unassigned"), True),
        max_journal_entries=_int(sn.get("max_journal_entries"), 1),
        max_issues=_int(sn.get("max_issues"), 50),
    )

    return AppConfig(
        timezone=tz,
        discord=discord_cfg,
        reports=reports_cfg,
        schedules=SchedulesConfig(abandoned=abandoned, stale_new=stale_new),
    )
