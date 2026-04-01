from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from ultron.llm import LLMClient
from ultron.readlog import log_read_payload
from ultron.redmine import IssueNotFound, RedmineClient
from ultron.textutil import format_issue_for_summary

SUMMARY_SYSTEM = (
    "You summarize Redmine issues for a technical team. Be concise, accurate, and actionable. "
    "Use clear sections: context, current status, blockers (if any), suggested next steps. "
    "Respond in the same language as the ticket content when obvious; otherwise use English."
)

NOTE_SYSTEM = (
    "You write the body of one Redmine journal note as plain text. "
    "Output ONLY that note text and nothing else (no preamble, no labels). "
    "Never repeat or quote the issue subject/title, issue number, project name, or tracker unless the user explicitly asked for them in their message. "
    "If the user asks a direct question—including simple arithmetic—answer it inside the note. "
    "Preserve factual claims and names; improve clarity and professional tone. "
    "Do not invent information not implied by the user's text. "
    "Use markdown only if the user already used it."
)

logger = logging.getLogger(__name__)


async def summarize_issue(
    *,
    redmine: RedmineClient,
    llm: LLMClient,
    issue_id: int,
    log_read_messages: bool = False,
    on_before_llm: Callable[[], Awaitable[None]] | None = None,
) -> str:
    issue = await redmine.get_issue(issue_id)
    body = format_issue_for_summary(issue)
    user_prompt = f"Summarize this Redmine ticket as requested by a teammate.\n\n{body}"
    if log_read_messages:
        log_read_payload(label=f"summary.issue_id={issue_id}.formatted_body", text=body)
        log_read_payload(label=f"summary.issue_id={issue_id}.llm_system", text=SUMMARY_SYSTEM)
        log_read_payload(label=f"summary.issue_id={issue_id}.llm_user", text=user_prompt)
    logger.info(
        "summarize_issue: fetched issue_id=%s prompt_chars=%s",
        issue_id,
        len(user_prompt),
    )
    logger.info("summarize_issue: calling LLM issue_id=%s", issue_id)
    if on_before_llm is not None:
        await on_before_llm()
    out = await llm.complete(system=SUMMARY_SYSTEM, user=user_prompt)
    logger.info("summarize_issue: LLM returned issue_id=%s response_chars=%s", issue_id, len(out))
    return out


async def add_formatted_note(
    *,
    redmine: RedmineClient,
    llm: LLMClient,
    issue_id: int,
    raw_text: str,
    log_read_messages: bool = False,
) -> tuple[str, str]:
    """Returns (formatted_note, issue_url). Raises IssueNotFound if missing."""
    await redmine.get_issue(issue_id, includes="journals")
    url = redmine.issue_url(issue_id)
    # Do not inject subject/title into the prompt—models often echoed it into the note body.
    user_prompt = (
        "Transform the following user text into the final journal note content only.\n\n" + raw_text
    )
    if log_read_messages:
        log_read_payload(label=f"note.issue_id={issue_id}.discord_text", text=raw_text)
        log_read_payload(label=f"note.issue_id={issue_id}.llm_system", text=NOTE_SYSTEM)
        log_read_payload(label=f"note.issue_id={issue_id}.llm_user", text=user_prompt)
    logger.info(
        "add_formatted_note: fetched issue_id=%s prompt_chars=%s",
        issue_id,
        len(user_prompt),
    )
    logger.info("add_formatted_note: calling LLM issue_id=%s", issue_id)
    formatted = await llm.complete(system=NOTE_SYSTEM, user=user_prompt)
    logger.info(
        "add_formatted_note: LLM returned issue_id=%s response_chars=%s",
        issue_id,
        len(formatted),
    )
    if not formatted:
        raise RuntimeError("LLM returned an empty note")
    await redmine.add_note(issue_id, formatted)
    return formatted, url
