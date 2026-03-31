from __future__ import annotations

from ultron.llm import LLMClient
from ultron.redmine import IssueNotFound, RedmineClient
from ultron.textutil import format_issue_for_summary

SUMMARY_SYSTEM = (
    "You summarize Redmine issues for a technical team. Be concise, accurate, and actionable. "
    "Use clear sections: context, current status, blockers (if any), suggested next steps. "
    "Respond in the same language as the ticket content when obvious; otherwise use English."
)

NOTE_SYSTEM = (
    "You rewrite user-provided text as a professional Redmine journal note. "
    "Preserve all factual claims and names; improve clarity and tone. "
    "Do not add information that was not implied. Plain text, no markdown unless the user used it."
)


async def summarize_issue(*, redmine: RedmineClient, llm: LLMClient, issue_id: int) -> str:
    issue = await redmine.get_issue(issue_id)
    body = format_issue_for_summary(issue)
    user_prompt = f"Summarize this Redmine ticket as requested by a teammate.\n\n{body}"
    return await llm.complete(system=SUMMARY_SYSTEM, user=user_prompt)


async def add_formatted_note(
    *,
    redmine: RedmineClient,
    llm: LLMClient,
    issue_id: int,
    raw_text: str,
) -> tuple[str, str]:
    """Returns (formatted_note, issue_url). Raises IssueNotFound if missing."""
    issue = await redmine.get_issue(issue_id, includes="journals")
    url = redmine.issue_url(issue_id)
    subject = issue.get("subject", "")
    user_prompt = (
        f"Ticket #{issue_id}: {subject}\n\n"
        f"The user wants to append this note to the ticket. Rewrite it for the journal:\n\n{raw_text}"
    )
    formatted = await llm.complete(system=NOTE_SYSTEM, user=user_prompt)
    if not formatted:
        raise RuntimeError("LLM returned an empty note")
    await redmine.add_note(issue_id, formatted)
    return formatted, url
