from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from ultron.llm import ChainSkipCallback, LLMBackend, LLMChainClient
from ultron.readlog import log_read_payload
from ultron.redmine import IssueNotFound, RedmineClient
from ultron.textutil import format_issue_for_summary
from ultron.workflow_log import wf_info

SUMMARY_SYSTEM = (
    "You summarize Redmine issues for a technical team. Be concise, accurate, and actionable. "
    "Use clear sections: context, current status, blockers (if any), suggested next steps. "
    "Respond in the same language as the ticket content when obvious; otherwise use English."
)

NOTE_SYSTEM = (
    "You write the body of one Redmine journal note as plain text. "
    "Output ONLY that note text and nothing else (no preamble, no labels). "
    "Do not add a byline or a 'Note written by … from Discord' line; the application prepends that after generation. "
    "Never repeat or quote the issue subject/title, issue number, project name, or tracker unless the user explicitly asked for them in their message. "
    "If the user asks a direct question—including simple arithmetic—answer it inside the note. "
    "Preserve factual claims and names; improve clarity and professional tone. "
    "Do not invent information not implied by the user's text. "
    "Use markdown only if the user already used it."
)

logger = logging.getLogger(__name__)

# Step tags: FETCH = Redmine read + prompt built, LLM_CALL / LLM_DONE = model boundary,
# REDMINE_WRITE = journal update. Lines are prefixed with WORKFLOW | … via wf_info.
_WF_FETCH = "FETCH"
_WF_LLM_CALL = "LLM_CALL"
_WF_LLM_DONE = "LLM_DONE"
_WF_REDMINE_WRITE = "REDMINE_WRITE"


async def summarize_issue(
    *,
    redmine: RedmineClient,
    llm: LLMBackend,
    issue_id: int,
    log_read_messages: bool = False,
    on_before_llm: Callable[[], Awaitable[None]] | None = None,
    on_llm_chain_skip: ChainSkipCallback | None = None,
) -> str:
    issue = await redmine.get_issue(issue_id)
    body = format_issue_for_summary(issue)
    user_prompt = f"Summarize this Redmine ticket as requested by a teammate.\n\n{body}"
    if log_read_messages:
        log_read_payload(label=f"summary.issue_id={issue_id}.formatted_body", text=body)
        log_read_payload(label=f"summary.issue_id={issue_id}.llm_system", text=SUMMARY_SYSTEM)
        log_read_payload(label=f"summary.issue_id={issue_id}.llm_user", text=user_prompt)
    wf_info(
        logger,
        "summarize_issue",
        _WF_FETCH,
        "issue_id=%s prompt_chars=%s",
        issue_id,
        len(user_prompt),
    )
    wf_info(logger, "summarize_issue", _WF_LLM_CALL, "issue_id=%s", issue_id)
    if on_before_llm is not None:
        await on_before_llm()
    if isinstance(llm, LLMChainClient) and on_llm_chain_skip is not None:
        out = await llm.complete(
            system=SUMMARY_SYSTEM,
            user=user_prompt,
            on_chain_skip=on_llm_chain_skip,
        )
    else:
        out = await llm.complete(system=SUMMARY_SYSTEM, user=user_prompt)
    wf_info(
        logger,
        "summarize_issue",
        _WF_LLM_DONE,
        "issue_id=%s response_chars=%s",
        issue_id,
        len(out),
    )
    return out


def _note_body_with_author(*, author_label: str | None, formatted: str) -> str:
    """Prefix italic attribution line before LLM body (Discord-style `_…_`)."""
    if not author_label or not author_label.strip():
        return formatted
    who = author_label.strip()
    header = f"_Note written by {who} from Discord_"
    return f"{header}\n\n{formatted}"


async def add_formatted_note(
    *,
    redmine: RedmineClient,
    llm: LLMBackend,
    issue_id: int,
    raw_text: str,
    log_read_messages: bool = False,
    on_llm_chain_skip: ChainSkipCallback | None = None,
    note_author_label: str | None = None,
) -> tuple[str, str]:
    """Returns (posted_note_body, issue_url). Raises IssueNotFound if missing."""
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
    wf_info(
        logger,
        "add_formatted_note",
        _WF_FETCH,
        "issue_id=%s prompt_chars=%s",
        issue_id,
        len(user_prompt),
    )
    wf_info(logger, "add_formatted_note", _WF_LLM_CALL, "issue_id=%s", issue_id)
    if isinstance(llm, LLMChainClient) and on_llm_chain_skip is not None:
        formatted = await llm.complete(
            system=NOTE_SYSTEM,
            user=user_prompt,
            on_chain_skip=on_llm_chain_skip,
        )
    else:
        formatted = await llm.complete(system=NOTE_SYSTEM, user=user_prompt)
    wf_info(
        logger,
        "add_formatted_note",
        _WF_LLM_DONE,
        "issue_id=%s response_chars=%s",
        issue_id,
        len(formatted),
    )
    if not formatted:
        raise RuntimeError("LLM returned an empty note")
    posted = _note_body_with_author(author_label=note_author_label, formatted=formatted)
    await redmine.add_note(issue_id, posted)
    wf_info(
        logger,
        "add_formatted_note",
        _WF_REDMINE_WRITE,
        "issue_id=%s note_chars=%s",
        issue_id,
        len(posted),
    )
    return posted, url
