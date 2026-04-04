"""Natural-language router: LLM returns JSON; code validates and dispatches (never trusts admin commands)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from ultron.llm import LLMBackend

logger = logging.getLogger(__name__)

# Commands the NL path may execute (must match dispatch table in bot). Admin slash commands are NEVER listed here.
NL_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        "ping",
        "help",
        "status",
        "list_new_issues",
        "issues_by_status",
        "list_unassigned_issues",
        "summary",
        "ask_issue",
        "note",
    }
)

# If the model names these, reject (slash-only / admin).
NL_FORBIDDEN_COMMANDS: frozenset[str] = frozenset(
    {
        "token",
        "approve",
        "remove",
        "show_config",
        "dev_slot_2",
        "dev_slot_3",
        "dev_slot_4",
        "dev_slot_5",
        "dev_slot_6",
        "dev_slot_7",
        "dev_slot_8",
        "dev_slot_9",
        "dev_slot_10",
    }
)

NL_ROUTER_SYSTEM = """You are a routing assistant for the Ultron Discord bot. The user wrote a short message in a \
channel (they @mentioned the bot or replied to it). Your job is to choose how the bot should respond.

Output EXACTLY one JSON object, no markdown fences, no other text.

Schema:
1) Run a bot command:
{"kind":"invoke","command":"<name>","args":{...}}

2) Reply conversationally (no command, small talk, unclear request):
{"kind":"chat","message":"<short helpful reply in the user's language>"}

Allowed command names and args (only these):
- ping — args {}
- help — args {}
- status — args {}
- list_new_issues — args {} (uses server config for which Redmine status)
- issues_by_status — args {"status":"<exact Redmine status name string>"}
- list_unassigned_issues — args {}
- summary — args {"issue_id": <positive integer>}
- ask_issue — args {"issue_id": <int>, "question": "<non-empty string>"}
- note — args {"issue_id": <int>, "text": "<non-empty note body>"}

Rules:
- If the user wants a ticket summary, use summary with issue_id.
- If they ask a question about a ticket, use ask_issue.
- If they want to add a note to a ticket, use note.
- If they want a list of new/old/unassigned issues, pick the matching list command.
- If you are unsure, use kind chat with a brief clarification question.
- NEVER output approve, remove, show_config, or token — those are not available here.
"""


def extract_json_text(raw: str) -> str:
    """Strip optional ```json ... ``` wrapping."""
    t = raw.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines)
    return t.strip()


@dataclass(frozen=True)
class NLInvoke:
    command: str
    args: dict[str, Any]


@dataclass(frozen=True)
class NLChat:
    message: str


@dataclass(frozen=True)
class NLParseError:
    detail: str


@dataclass(frozen=True)
class NLAdminRejected:
    """Model asked for a slash-only or admin command — must not execute."""

    command: str


NLRouterOutcome = NLInvoke | NLChat | NLParseError | NLAdminRejected


def _as_int(v: Any, field: str) -> int:
    if isinstance(v, bool) or v is None:
        raise ValueError(f"{field} must be an integer")
    if isinstance(v, int):
        if v <= 0:
            raise ValueError(f"{field} must be positive")
        return v
    if isinstance(v, float):
        i = int(v)
        if i != v or i <= 0:
            raise ValueError(f"{field} must be a positive integer")
        return i
    if isinstance(v, str) and v.strip().isdigit():
        i = int(v.strip())
        if i <= 0:
            raise ValueError(f"{field} must be positive")
        return i
    raise ValueError(f"{field} must be a positive integer")


def _as_nonempty_str(v: Any, field: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return v.strip()


def _validate_args(command: str, args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise ValueError("args must be a JSON object")
    if command in ("ping", "help", "status", "list_new_issues", "list_unassigned_issues"):
        if args:
            raise ValueError(f"{command} expects empty args {{}}")
        return {}
    if command == "issues_by_status":
        st = _as_nonempty_str(args.get("status"), "status")
        return {"status": st}
    if command == "summary":
        iid = _as_int(args.get("issue_id"), "issue_id")
        return {"issue_id": iid}
    if command == "ask_issue":
        iid = _as_int(args.get("issue_id"), "issue_id")
        q = _as_nonempty_str(args.get("question"), "question")
        return {"issue_id": iid, "question": q}
    if command == "note":
        iid = _as_int(args.get("issue_id"), "issue_id")
        txt = _as_nonempty_str(args.get("text"), "text")
        return {"issue_id": iid, "text": txt}
    raise ValueError(f"unknown command {command!r}")


def parse_router_json_text(text: str) -> NLRouterOutcome:
    """Parse and validate model output."""
    try:
        obj = json.loads(extract_json_text(text))
    except json.JSONDecodeError as e:
        return NLParseError(f"invalid JSON: {e}")

    if not isinstance(obj, dict):
        return NLParseError("root JSON must be an object")

    kind = obj.get("kind")
    if kind == "chat":
        msg = obj.get("message")
        if not isinstance(msg, str) or not msg.strip():
            return NLParseError("chat.message missing or empty")
        return NLChat(message=msg.strip())

    if kind != "invoke":
        return NLParseError("kind must be 'invoke' or 'chat'")

    cmd_raw = obj.get("command")
    if not isinstance(cmd_raw, str) or not cmd_raw.strip():
        return NLParseError("invoke.command missing")
    cmd = cmd_raw.strip().lower()
    if cmd == "new_issues":
        cmd = "list_new_issues"
    if cmd == "unassigned_issues":
        cmd = "list_unassigned_issues"

    if cmd in NL_FORBIDDEN_COMMANDS:
        return NLAdminRejected(command=cmd)
    if cmd not in NL_ALLOWED_COMMANDS:
        return NLParseError(f"command not allowed: {cmd!r}")

    args = obj.get("args", {})
    try:
        validated = _validate_args(cmd, args)
    except ValueError as e:
        return NLParseError(str(e))

    return NLInvoke(command=cmd, args=validated)


async def run_nl_router(
    llm: LLMBackend,
    *,
    user_text: str,
    via: str,
) -> NLRouterOutcome:
    """One LLM call: model returns JSON; parsed and validated in code."""
    ut = user_text.strip() if user_text else ""
    if not ut:
        ut = "(empty message)"
    user_prompt = f"How should the bot respond?\n\nUser message (via={via}):\n{ut}"
    raw = await llm.complete(system=NL_ROUTER_SYSTEM, user=user_prompt)
    if not raw.strip():
        return NLParseError("model returned empty response")
    return parse_router_json_text(raw)
