"""Load, merge, and write ``config.yaml`` for the wizard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``."""
    out: dict[str, Any] = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def dump_yaml(data: dict[str, Any]) -> str:
    return yaml.dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )


def load_default_config_from_example(repo_root: Path) -> dict[str, Any]:
    """Fallback structure when ``config.yaml`` is missing."""
    example = repo_root / "config.example.yaml"
    if example.is_file():
        raw = yaml.safe_load(example.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    return {
        "timezone": "",
        "discord": {
            "ephemeral_default": None,
            "summary_status_redmine": "",
            "summary_status_llm": "",
            "llm_chain_skip_status": "",
            "llm_chain_all_failed_message": "",
            "new_issues": {"status_name": "", "list_limit": None, "min_age_days": None},
            "registration_log": {
                "enabled": None,
                "channel_id": None,
                "features": {"startup": None, "whitelist_events": None},
            },
            "unassigned_open": {
                "min_age_days": None,
                "list_limit": None,
                "closed_status_prefixes": [],
            },
        },
        "reports": {"channel_id": 0},
        "schedules": {
            "abandoned": {
                "enabled": None,
                "interval_hours": None,
                "max_days_without_update": None,
                "max_issues": None,
            },
            "stale_new": {
                "enabled": None,
                "interval_hours": None,
                "min_age_hours": None,
                "require_unassigned": None,
                "max_journal_entries": None,
                "max_issues": None,
                "issue_status_name": None,
            },
        },
        "logging": {"log_read_messages": None},
        "llm_chain": [],
    }
