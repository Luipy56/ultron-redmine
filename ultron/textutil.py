from __future__ import annotations


def format_issue_metadata_header(issue: dict) -> str:
    """One-line Discord markdown: note count, logged time, last update (Redmine issue JSON)."""
    journals = issue.get("journals") or []
    note_count = sum(1 for j in journals if str(j.get("notes") or "").strip())
    raw_spent = issue.get("spent_hours")
    if raw_spent is None:
        spent_str = "0 h"
    else:
        try:
            h = float(raw_spent)
            spent_str = f"{h:g} h" if h == int(h) else f"{h:.2f} h"
        except (TypeError, ValueError):
            spent_str = str(raw_spent)
    updated = issue.get("updated_on") or "—"
    return (
        f"**Notes:** {note_count}  ·  **Total time logged:** {spent_str}  ·  **Last updated:** {updated}"
    )


def chunk_discord(text: str, limit: int = 1900) -> list[str]:
    """Split text into chunks under Discord's ~2000 char message limit."""
    text = text.strip()
    if not text:
        return ["(empty)"]
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    return chunks


def format_issue_for_summary(issue: dict) -> str:
    lines: list[str] = []
    lines.append(f"#{(issue.get('id'))}: {issue.get('subject', '')}")
    if issue.get("description"):
        lines.append("")
        lines.append("## Description")
        lines.append(str(issue["description"])[:12000])
    status = issue.get("status") or {}
    tracker = issue.get("tracker") or {}
    project = issue.get("project") or {}
    assignee = issue.get("assigned_to") or {}
    author = issue.get("author") or {}
    lines.append("")
    lines.append(
        f"Project: {project.get('name', '')} | Tracker: {tracker.get('name', '')} | "
        f"Status: {status.get('name', '')} | Priority: {(issue.get('priority') or {}).get('name', '')}"
    )
    lines.append(f"Author: {author.get('name', '')} | Assigned: {assignee.get('name', '—')}")
    lines.append(f"Created: {issue.get('created_on', '')} | Updated: {issue.get('updated_on', '')}")

    journals = issue.get("journals") or []
    if journals:
        lines.append("")
        lines.append("## Journal")
        for j in journals[-30:]:
            user = (j.get("user") or {}).get("name", "")
            created = j.get("created_on", "")
            notes = (j.get("notes") or "").strip()
            if not notes:
                continue
            lines.append(f"- [{created}] {user}: {notes[:2000]}")

    return "\n".join(lines)
