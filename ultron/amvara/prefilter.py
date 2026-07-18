"""Code-first NL prefilter: Amvara hosts vs Redmine vs compound vs general."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_AMVARA_HOST_RE = re.compile(r"\b(amvara\d+)\b", re.IGNORECASE)
_ISSUE_ID_RE = re.compile(
    r"(?:#\s*(\d{1,9})|(?:issue|ticket|incidente|tarea)\s*#?\s*(\d{1,9}))",
    re.IGNORECASE,
)
_REDMINE_VERB_RE = re.compile(
    r"\b("
    r"summary|summarize|summarise|resumen|resumir|resume|"
    r"ask_issue|question|pregunta|"
    r"note|nota|anotar|journal|"
    r"log_time|log time|hours|horas|spent|"
    r"time_summary|time summary|"
    r"list_new_issues|new issues|issues_by_status|unassigned|"
    r"find_issue|find issue|search issue|buscar|busca|"
    r"redmine|ticket|issue"
    r")\b",
    re.IGNORECASE,
)
_CONNECT_VERB_RE = re.compile(
    r"\b(connect(?:ed|ing)?\s+to|conect(?:a|ate|ar)\s+(?:a|al|a\s+la)?|ssh\s+to|on\s+host)\b",
    re.IGNORECASE,
)


class MessageIntent(str, Enum):
    AMVARA_ONLY = "amvara_only"
    REDMINE_ONLY = "redmine_only"
    COMPOUND = "compound"
    GENERAL = "general"


@dataclass(frozen=True)
class PrefilterResult:
    intent: MessageIntent
    amvara_hosts: tuple[str, ...]
    issue_ids: tuple[int, ...]
    has_redmine_signal: bool


def extract_amvara_hosts(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _AMVARA_HOST_RE.finditer(text or ""):
        key = m.group(1).casefold()
        if key not in seen:
            seen.add(key)
            out.append(key)
    return tuple(out)


def extract_issue_ids(text: str) -> tuple[int, ...]:
    seen: set[int] = set()
    out: list[int] = []
    for m in _ISSUE_ID_RE.finditer(text or ""):
        raw = m.group(1) or m.group(2)
        if raw is None:
            continue
        iid = int(raw)
        if iid not in seen:
            seen.add(iid)
            out.append(iid)
    return tuple(out)


def has_redmine_signal(text: str, *, issue_ids: tuple[int, ...]) -> bool:
    if issue_ids:
        return True
    return _REDMINE_VERB_RE.search(text or "") is not None


def classify_message(text: str) -> PrefilterResult:
    ut = (text or "").strip()
    hosts = extract_amvara_hosts(ut)
    issue_ids = extract_issue_ids(ut)
    redmine = has_redmine_signal(ut, issue_ids=issue_ids)

    if hosts and redmine:
        intent = MessageIntent.COMPOUND
    elif hosts:
        intent = MessageIntent.AMVARA_ONLY
    elif redmine:
        intent = MessageIntent.REDMINE_ONLY
    else:
        intent = MessageIntent.GENERAL

    return PrefilterResult(
        intent=intent,
        amvara_hosts=hosts,
        issue_ids=issue_ids,
        has_redmine_signal=redmine,
    )


def extract_amvara_task(text: str, hosts: tuple[str, ...]) -> str:
    """Return user task text with Amvara host mentions and connect boilerplate stripped."""
    t = (text or "").strip()
    for host in hosts:
        t = re.sub(rf"\b{re.escape(host)}\b", " ", t, flags=re.IGNORECASE)
    t = _CONNECT_VERB_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" ,.;:")
    return t or (text or "").strip()
