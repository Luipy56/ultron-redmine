"""Compound NL planner: Amvara audits + Redmine steps."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from ultron.amvara.registry import AmvaraRegistry
from ultron.llm import LLMBackend
from ultron.nl_router import NL_ALLOWED_COMMANDS, NL_FORBIDDEN_COMMANDS, _validate_args, extract_json_text

logger = logging.getLogger(__name__)

NL_PLANNER_SYSTEM = """You are a task planner for the Ultron Discord bot. The user wrote a message that \
requires **multiple steps** (Amvara server work and/or Redmine).

Output EXACTLY one JSON object, no markdown fences, no other text.

Schema:
{"kind":"plan","steps":[ ... ]}

Each step is one of:

1) Amvara server audit (SSH diagnostics via pi/cursor-agent on the Ultron host):
{"kind":"amvara_audit","host":"amvara3","task":"<what to check or do on that host>"}

2) Redmine bot command (same as slash commands):
{"kind":"invoke","command":"<name>","args":{...}}

Allowed invoke commands and args:
- summary — {"issue_id": <positive int>}
- ask_issue — {"issue_id": <int>, "question": "<non-empty string>"}
- note — {"issue_id": <int>, "text": "<non-empty note body>"}
- log_time — {"issue_id": <int>, "hours": <positive number>}
- time_summary — {"user": "<Redmine login, numeric user id, or me>"}
- ol — {"text": "<non-empty string>"}
- find_issue — {"text": "<non-empty search hint>"}
- top_tickets — {"project": "<identifier or name>", "kind_filter": "<priority|newests|oldests optional>", "limit": <optional int>}
- ping, help, status, list_new_issues, list_unassigned_issues — {}
- issues_by_status — {"status": "<Redmine status name>"}

Rules:
- Order steps logically (e.g. audit first, then note with findings).
- For note after an audit, write the note text as a summary of expected audit findings (the executor passes prior output).
- Use only hosts from the allowed list provided in the user message.
- NEVER use pi, ca, audit, approve, remove, show_config, or token as invoke commands.
- If the request is impossible, output {"kind":"chat","message":"<short explanation>"} instead.
"""


@dataclass(frozen=True)
class AmvaraAuditStep:
    host: str
    task: str


@dataclass(frozen=True)
class InvokeStep:
    command: str
    args: dict[str, Any]


@dataclass(frozen=True)
class NLPlan:
    steps: tuple[AmvaraAuditStep | InvokeStep, ...]


@dataclass(frozen=True)
class NLPlanChat:
    message: str


@dataclass(frozen=True)
class NLPlanParseError:
    detail: str


NLPlanOutcome = NLPlan | NLPlanChat | NLPlanParseError


def _parse_amvara_step(obj: dict[str, Any], registry: AmvaraRegistry) -> AmvaraAuditStep:
    host_raw = obj.get("host")
    task_raw = obj.get("task")
    if not isinstance(host_raw, str) or not host_raw.strip():
        raise ValueError("amvara_audit.host must be a non-empty string")
    if not isinstance(task_raw, str) or not task_raw.strip():
        raise ValueError("amvara_audit.task must be a non-empty string")
    host = registry.validate_host(host_raw.strip())
    return AmvaraAuditStep(host=host.name, task=task_raw.strip())


def _parse_invoke_step(obj: dict[str, Any]) -> InvokeStep:
    cmd_raw = obj.get("command")
    if not isinstance(cmd_raw, str) or not cmd_raw.strip():
        raise ValueError("invoke.command missing")
    cmd = cmd_raw.strip().lower()
    if cmd in NL_FORBIDDEN_COMMANDS or cmd in ("pi", "ca", "audit"):
        raise ValueError(f"command not allowed in plan: {cmd!r}")
    if cmd not in NL_ALLOWED_COMMANDS:
        raise ValueError(f"command not allowed in plan: {cmd!r}")
    args = obj.get("args", {})
    validated = _validate_args(cmd, args)
    return InvokeStep(command=cmd, args=validated)


def parse_plan_json_text(text: str, registry: AmvaraRegistry) -> NLPlanOutcome:
    try:
        obj = json.loads(extract_json_text(text))
    except json.JSONDecodeError as e:
        return NLPlanParseError(f"invalid JSON: {e}")

    if not isinstance(obj, dict):
        return NLPlanParseError("root JSON must be an object")

    kind = obj.get("kind")
    if kind == "chat":
        msg = obj.get("message")
        if not isinstance(msg, str) or not msg.strip():
            return NLPlanParseError("chat.message missing or empty")
        return NLPlanChat(message=msg.strip())

    if kind != "plan":
        return NLPlanParseError("kind must be 'plan' or 'chat'")

    steps_raw = obj.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        return NLPlanParseError("plan.steps must be a non-empty list")

    steps: list[AmvaraAuditStep | InvokeStep] = []
    for i, item in enumerate(steps_raw):
        if not isinstance(item, dict):
            return NLPlanParseError(f"steps[{i}] must be an object")
        sk = item.get("kind")
        try:
            if sk == "amvara_audit":
                steps.append(_parse_amvara_step(item, registry))
            elif sk == "invoke":
                steps.append(_parse_invoke_step(item))
            else:
                return NLPlanParseError(f"steps[{i}].kind must be amvara_audit or invoke")
        except ValueError as e:
            return NLPlanParseError(str(e))

    return NLPlan(steps=tuple(steps))


async def run_nl_planner(
    llm: LLMBackend,
    *,
    user_text: str,
    registry: AmvaraRegistry,
    via: str,
) -> NLPlanOutcome:
    allowed = ", ".join(registry.list_allowed_hosts()) or "(none)"
    user_prompt = (
        f"Allowed Amvara hosts: {allowed}\n"
        f"User message (via={via}):\n{user_text.strip()}"
    )
    raw = await llm.complete(system=NL_PLANNER_SYSTEM, user=user_prompt)
    if not raw.strip():
        return NLPlanParseError("model returned empty response")
    return parse_plan_json_text(raw, registry)
