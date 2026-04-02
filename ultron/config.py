from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

_DEFAULT_SUMMARY_STATUS_REDMINE = "Fetching ticket from Redmine…"
_DEFAULT_SUMMARY_STATUS_LLM = "Passing the task to {model}…"
_DEFAULT_LLM_CHAIN_SKIP_STATUS = (
    "**{from_entry}** ({from_model}) failed: {reason}. Trying **{to_entry}** ({to_model})…"
)
_DEFAULT_LLM_CHAIN_ALL_FAILED = (
    "All configured language model providers failed (URLs, keys, quotas, or network). "
    "Check **config.yaml** `llm_chain` and the bot logs."
)

# Defaults for llm_chain entries (aligned with EnvSettings cloud defaults).
_DEFAULT_LLM_CHAIN_TIMEOUT_SECONDS = 900.0
_DEFAULT_LLM_CHAIN_MAX_RETRIES = 2


@dataclass
class NewIssuesSlashConfig:
    """`/new_issues`: list issues whose status name matches Redmine, created ≥ `min_age_days` ago."""

    status_name: str = ""
    list_limit: int = 20
    min_age_days: int = 2


@dataclass
class DiscordConfig:
    ephemeral_default: bool = True
    summary_status_redmine: str = _DEFAULT_SUMMARY_STATUS_REDMINE
    summary_status_llm: str = _DEFAULT_SUMMARY_STATUS_LLM
    llm_chain_skip_status: str = _DEFAULT_LLM_CHAIN_SKIP_STATUS
    llm_chain_all_failed_message: str = _DEFAULT_LLM_CHAIN_ALL_FAILED
    new_issues: NewIssuesSlashConfig = field(default_factory=NewIssuesSlashConfig)


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
class LoggingConfig:
    log_read_messages: bool = False


@dataclass(frozen=True)
class LLMProviderSpec:
    """One YAML `llm_chain` list item (order = priority). Keys come from `api_key_env`."""

    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float
    max_retries: int
    name: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class LLMProviderResolved:
    """Resolved list item (API key loaded); used to build SDK clients."""

    base_url: str
    model: str
    api_key: str
    timeout_seconds: float
    max_retries: int
    name: str | None = None


@dataclass
class AppConfig:
    timezone: str
    discord: DiscordConfig
    reports: ReportsConfig
    schedules: SchedulesConfig
    logging: LoggingConfig
    llm_chain: tuple[LLMProviderSpec, ...] | None = None


def _bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    return bool(v)


def _int(v: Any, default: int) -> int:
    if v is None:
        return default
    return int(v)


def _float(v: Any, default: float) -> float:
    if v is None:
        return default
    return float(v)


def _str(v: Any, default: str) -> str:
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


def _parse_llm_chain(raw: Any) -> tuple[LLMProviderSpec, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("llm_chain must be a list")
    if len(raw) == 0:
        return None
    out: list[LLMProviderSpec] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"llm_chain[{i}] must be a mapping")
        if not _bool(item.get("enabled"), True):
            continue
        base_url = _str(item.get("base_url"), "").strip().rstrip("/")
        if not base_url:
            raise ValueError(f"llm_chain[{i}].base_url is required")
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                f"llm_chain[{i}].base_url must be an http(s) URL with a host (got {base_url!r})"
            )
        model = _str(item.get("model"), "").strip()
        if not model:
            raise ValueError(f"llm_chain[{i}].model is required")
        api_key_env = _str(item.get("api_key_env"), "").strip()
        if not api_key_env:
            raise ValueError(f"llm_chain[{i}].api_key_env is required")
        timeout_seconds = _float(item.get("timeout_seconds"), _DEFAULT_LLM_CHAIN_TIMEOUT_SECONDS)
        if timeout_seconds <= 0:
            raise ValueError(f"llm_chain[{i}].timeout_seconds must be positive")
        max_retries = _int(item.get("max_retries"), _DEFAULT_LLM_CHAIN_MAX_RETRIES)
        if max_retries < 0:
            raise ValueError(f"llm_chain[{i}].max_retries must be >= 0")
        name_raw = item.get("name")
        if name_raw is None:
            entry_name: str | None = None
        else:
            ns = str(name_raw).strip()
            entry_name = ns if ns else None
        out.append(
            LLMProviderSpec(
                base_url=base_url,
                model=model,
                api_key_env=api_key_env,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                name=entry_name,
                enabled=True,
            )
        )
    if not out:
        return None
    return tuple(out)


def resolve_llm_chain(specs: tuple[LLMProviderSpec, ...]) -> tuple[LLMProviderResolved, ...]:
    """Resolve `api_key_env` for each spec. Raises RuntimeError if a variable is missing or empty."""
    resolved: list[LLMProviderResolved] = []
    for i, s in enumerate(specs):
        key = os.environ.get(s.api_key_env, "").strip()
        if not key:
            who = f" [{s.name!r}]" if s.name else ""
            raise RuntimeError(
                f"LLM chain entry [{i}]{who} requires environment variable {s.api_key_env!r} "
                "to be set and non-empty."
            )
        resolved.append(
            LLMProviderResolved(
                base_url=s.base_url,
                model=s.model,
                api_key=key,
                timeout_seconds=s.timeout_seconds,
                max_retries=s.max_retries,
                name=s.name,
            )
        )
    return tuple(resolved)


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    tz = str(raw.get("timezone") or "UTC")

    d_raw = raw.get("discord") or {}
    ni_raw = d_raw.get("new_issues") or {}
    list_limit = max(1, _int(ni_raw.get("list_limit"), 20))
    min_age_days = max(0, _int(ni_raw.get("min_age_days"), 2))
    new_issues_cfg = NewIssuesSlashConfig(
        status_name=_str(ni_raw.get("status_name"), ""),
        list_limit=list_limit,
        min_age_days=min_age_days,
    )
    discord_cfg = DiscordConfig(
        ephemeral_default=_bool(d_raw.get("ephemeral_default"), True),
        summary_status_redmine=_str(d_raw.get("summary_status_redmine"), _DEFAULT_SUMMARY_STATUS_REDMINE),
        summary_status_llm=_str(d_raw.get("summary_status_llm"), _DEFAULT_SUMMARY_STATUS_LLM),
        llm_chain_skip_status=_str(d_raw.get("llm_chain_skip_status"), _DEFAULT_LLM_CHAIN_SKIP_STATUS),
        llm_chain_all_failed_message=_str(
            d_raw.get("llm_chain_all_failed_message"), _DEFAULT_LLM_CHAIN_ALL_FAILED
        ),
        new_issues=new_issues_cfg,
    )

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

    l_raw = raw.get("logging") or {}
    logging_cfg = LoggingConfig(log_read_messages=_bool(l_raw.get("log_read_messages"), False))

    llm_chain = _parse_llm_chain(raw.get("llm_chain"))

    return AppConfig(
        timezone=tz,
        discord=discord_cfg,
        reports=reports_cfg,
        schedules=SchedulesConfig(abandoned=abandoned, stale_new=stale_new),
        logging=logging_cfg,
        llm_chain=llm_chain,
    )
