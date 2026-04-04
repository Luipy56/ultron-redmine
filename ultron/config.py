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
    """`/list_new_issues`: list issues whose status name matches Redmine, created ≥ `min_age_days` ago."""

    status_name: str = ""
    list_limit: int = 20
    min_age_days: int = 2


@dataclass
class LogsChannelFeatures:
    """Per-feature toggles for the Discord logs channel (only apply when ``registration_log.enabled`` is true)."""

    startup: bool = True
    whitelist_events: bool = True


@dataclass
class RegistrationLogConfig:
    """Discord logs channel: master switch ``enabled`` + ``channel_id``; feature flags under ``features``."""

    enabled: bool = False
    channel_id: int = 0
    features: LogsChannelFeatures = field(default_factory=LogsChannelFeatures)


@dataclass
class UnassignedOpenConfig:
    """`/list_unassigned_issues`: unassigned + Redmine-open issues, created ≥ ``min_age_days`` ago, excluding status prefixes."""

    min_age_days: int = 1
    list_limit: int = 20
    closed_status_prefixes: tuple[str, ...] = ()


@dataclass
class DiscordConfig:
    ephemeral_default: bool = True
    #: When True (default), prepend note count, logged hours, and last update to `/summary` and `/ask_issue` replies.
    issue_metadata_header: bool = True
    summary_status_redmine: str = _DEFAULT_SUMMARY_STATUS_REDMINE
    summary_status_llm: str = _DEFAULT_SUMMARY_STATUS_LLM
    llm_chain_skip_status: str = _DEFAULT_LLM_CHAIN_SKIP_STATUS
    llm_chain_all_failed_message: str = _DEFAULT_LLM_CHAIN_ALL_FAILED
    #: When True (default), @mention / reply uses an LLM router. Set ``false`` for a fixed short greeting only.
    nl_commands: bool = True
    #: When True, slash option descriptions and autocomplete emphasize configured LLM / model names.
    slash_show_llm_option_hints: bool = False
    new_issues: NewIssuesSlashConfig = field(default_factory=NewIssuesSlashConfig)
    registration_log: RegistrationLogConfig = field(default_factory=RegistrationLogConfig)
    unassigned_open: UnassignedOpenConfig = field(default_factory=UnassignedOpenConfig)


@dataclass
class ReportsConfig:
    channel_id: int = 0
    #: Post a welcome + schedule summary to ``channel_id`` when the bot becomes ready.
    startup_message_enabled: bool = True
    #: First line of the startup post; empty uses a built-in English greeting.
    startup_welcome: str = ""


@dataclass(frozen=True)
class ReportScheduleEntry:
    """One enabled row from YAML ``report_schedule`` (disabled rows are omitted at load time)."""

    command: str
    interval_hours: int
    args: tuple[tuple[str, str], ...]


@dataclass
class LoggingConfig:
    log_read_messages: bool = False


@dataclass(frozen=True)
class LLMProviderSpec:
    """One YAML `llm_chain` list item (order = priority). Keys come from `api_key_env`."""

    base_url: str
    models: tuple[str, ...]
    api_key_env: str
    timeout_seconds: float
    max_retries: int
    name: str | None = None
    enabled: bool = True

    @property
    def model(self) -> str:
        """Primary model (first entry in YAML ``model`` list)."""
        return self.models[0]


@dataclass(frozen=True)
class LLMProviderResolved:
    """Resolved list item (API key loaded); used to build SDK clients."""

    base_url: str
    models: tuple[str, ...]
    api_key: str
    timeout_seconds: float
    max_retries: int
    name: str | None = None

    @property
    def model(self) -> str:
        return self.models[0]


@dataclass
class AppConfig:
    timezone: str
    discord: DiscordConfig
    reports: ReportsConfig
    report_schedule: tuple[ReportScheduleEntry, ...]
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


def _parse_model_yaml(raw: Any, *, entry_index: int) -> tuple[str, ...]:
    """YAML ``model``: non-empty string or non-empty list of strings (first = primary)."""
    if raw is None:
        raise ValueError(f"llm_chain[{entry_index}].model is required")
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            raise ValueError(f"llm_chain[{entry_index}].model must be non-empty")
        return (s,)
    if isinstance(raw, list):
        out: list[str] = []
        for j, item in enumerate(raw):
            t = str(item).strip()
            if not t:
                raise ValueError(f"llm_chain[{entry_index}].model[{j}] must be non-empty")
            out.append(t)
        if not out:
            raise ValueError(f"llm_chain[{entry_index}].model list must be non-empty")
        return tuple(out)
    raise ValueError(
        f"llm_chain[{entry_index}].model must be a non-empty string or a non-empty list of strings"
    )


def llm_chain_slash_flags(specs: tuple[LLMProviderSpec, ...] | None) -> tuple[bool, bool]:
    """Whether slash commands register optional ``llm_provider`` / ``llm_model`` (at Discord sync time).

    When ``llm_chain`` is non-empty, both options are always registered so ``/summary``, ``/ask_issue``,
    and ``/note`` match the documented shape; autocomplete lists configured slots and models. Users may
    leave either option empty to use chain defaults (first slot, primary model per slot).
    """
    if not specs:
        return False, False
    return True, True


def llm_chain_resolve_start_index(specs: tuple[LLMProviderSpec, ...], provider: str | None) -> int:
    """Resolve Discord provider token to chain index (0..n-1). Empty / None → 0."""
    n = len(specs)
    if n == 0:
        return 0
    if provider is None or not str(provider).strip():
        return 0
    s = str(provider).strip()
    if s.isdigit():
        i = int(s)
        if 0 <= i < n:
            return i
        raise ValueError(f"Invalid llm_chain slot {s!r} (valid: 0..{n - 1}).")
    for i, spec in enumerate(specs):
        if spec.name and spec.name.strip() == s:
            return i
    raise ValueError(f"Unknown LLM provider {s!r}.")


def llm_chain_slash_model_override(
    specs: tuple[LLMProviderSpec, ...],
    start_idx: int,
    model: str | None,
    *,
    command_includes_model_option: bool,
) -> tuple[str | None, str]:
    """Return (API model override or None for primary, display name)."""
    allowed = specs[start_idx].models
    if not command_includes_model_option or len(allowed) == 1:
        return None, allowed[0]
    choice = (model or "").strip()
    if not choice:
        return None, allowed[0]
    if choice not in allowed:
        raise ValueError(
            f"Unknown model {choice!r} for this provider. Configured: {', '.join(allowed)}."
        )
    return (None if choice == allowed[0] else choice), choice


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
        models = _parse_model_yaml(item.get("model"), entry_index=i)
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
                models=models,
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
                models=s.models,
                api_key=key,
                timeout_seconds=s.timeout_seconds,
                max_retries=s.max_retries,
                name=s.name,
            )
        )
    return tuple(resolved)


_REPORT_SCHEDULE_COMMANDS = frozenset({"list_new_issues", "list_unassigned_issues", "issues_by_status"})


def _parse_report_schedule(raw: Any) -> tuple[ReportScheduleEntry, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("report_schedule must be a list")
    out: list[ReportScheduleEntry] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"report_schedule[{i}] must be a mapping")
        if not _bool(item.get("enabled"), True):
            continue
        cmd = _str(item.get("command"), "").strip()
        if cmd == "new_issues":
            cmd = "list_new_issues"
        if cmd == "unassigned_issues":
            cmd = "list_unassigned_issues"
        if cmd not in _REPORT_SCHEDULE_COMMANDS:
            raise ValueError(
                f"report_schedule[{i}].command must be one of {sorted(_REPORT_SCHEDULE_COMMANDS)!r}, got {cmd!r}"
            )
        interval_hours = _int(item.get("interval_hours"), 0)
        idays = item.get("interval_days")
        if idays is not None:
            interval_hours = max(1, _int(idays, 0) * 24)
        if interval_hours < 1:
            raise ValueError(
                f"report_schedule[{i}] needs interval_hours >= 1 or interval_days >= 1 (got interval_hours={interval_hours!r})"
            )
        args_raw = item.get("args")
        if args_raw is None:
            args_raw = {}
        if not isinstance(args_raw, dict):
            raise ValueError(f"report_schedule[{i}].args must be a mapping")
        args_pairs = tuple((str(k), str(v)) for k, v in args_raw.items())
        if cmd in ("list_new_issues", "list_unassigned_issues") and args_pairs:
            raise ValueError(
                f"report_schedule[{i}].command {cmd!r} does not use args; configure discord.new_issues / "
                "discord.unassigned_open instead"
            )
        if cmd == "issues_by_status":
            ad = dict(args_pairs)
            st = str(ad.get("status", "")).strip()
            if not st:
                raise ValueError(f"report_schedule[{i}].args.status is required for issues_by_status")
        out.append(ReportScheduleEntry(command=cmd, interval_hours=interval_hours, args=args_pairs))
    return tuple(out)


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
    reg_raw = d_raw.get("registration_log") or {}
    feat_raw = reg_raw.get("features") or {}
    registration_log_cfg = RegistrationLogConfig(
        enabled=_bool(reg_raw.get("enabled"), False),
        channel_id=_int(reg_raw.get("channel_id"), 0),
        features=LogsChannelFeatures(
            startup=_bool(feat_raw.get("startup"), True),
            whitelist_events=_bool(feat_raw.get("whitelist_events"), True),
        ),
    )
    uo_raw = d_raw.get("unassigned_open") or {}
    csp_raw = uo_raw.get("closed_status_prefixes")
    if csp_raw is None or csp_raw == []:
        closed_prefixes: tuple[str, ...] = ()
    elif isinstance(csp_raw, list):
        closed_prefixes = tuple(str(x).strip() for x in csp_raw if str(x).strip())
    else:
        raise ValueError("discord.unassigned_open.closed_status_prefixes must be a list of strings")
    unassigned_open_cfg = UnassignedOpenConfig(
        min_age_days=max(0, _int(uo_raw.get("min_age_days"), 1)),
        list_limit=max(1, _int(uo_raw.get("list_limit"), 20)),
        closed_status_prefixes=closed_prefixes,
    )
    discord_cfg = DiscordConfig(
        ephemeral_default=_bool(d_raw.get("ephemeral_default"), True),
        issue_metadata_header=_bool(d_raw.get("issue_metadata_header"), True),
        summary_status_redmine=_str(d_raw.get("summary_status_redmine"), _DEFAULT_SUMMARY_STATUS_REDMINE),
        summary_status_llm=_str(d_raw.get("summary_status_llm"), _DEFAULT_SUMMARY_STATUS_LLM),
        llm_chain_skip_status=_str(d_raw.get("llm_chain_skip_status"), _DEFAULT_LLM_CHAIN_SKIP_STATUS),
        llm_chain_all_failed_message=_str(
            d_raw.get("llm_chain_all_failed_message"), _DEFAULT_LLM_CHAIN_ALL_FAILED
        ),
        nl_commands=_bool(d_raw.get("nl_commands"), True),
        slash_show_llm_option_hints=_bool(d_raw.get("slash_show_llm_option_hints"), False),
        new_issues=new_issues_cfg,
        registration_log=registration_log_cfg,
        unassigned_open=unassigned_open_cfg,
    )

    r_raw = raw.get("reports") or {}
    reports_cfg = ReportsConfig(
        channel_id=_int(r_raw.get("channel_id"), 0),
        startup_message_enabled=_bool(r_raw.get("startup_message_enabled"), True),
        startup_welcome=_str(r_raw.get("startup_welcome"), ""),
    )

    report_schedule = _parse_report_schedule(raw.get("report_schedule"))

    l_raw = raw.get("logging") or {}
    logging_cfg = LoggingConfig(log_read_messages=_bool(l_raw.get("log_read_messages"), False))

    llm_chain = _parse_llm_chain(raw.get("llm_chain"))

    return AppConfig(
        timezone=tz,
        discord=discord_cfg,
        reports=reports_cfg,
        report_schedule=report_schedule,
        logging=logging_cfg,
        llm_chain=llm_chain,
    )
