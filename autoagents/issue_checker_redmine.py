#!/usr/bin/env python3
"""Redmine issue → FEAT task helper for autoagents 001.

Usage:
  python3 autoagents/issue_checker_redmine.py           # create tasks for qualifying issues
  python3 autoagents/issue_checker_redmine.py --dry-run # list only, no writes
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = Path(__file__).resolve().parent / "tasks"


def _load_redmine_env() -> tuple[str, str]:
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass
    url = os.environ.get("REDMINE_URL", "").strip().rstrip("/")
    key = os.environ.get("REDMINE_API_KEY", "").strip()
    if not url or not key:
        raise SystemExit("REDMINE_URL and REDMINE_API_KEY required in .env")
    return url, key


def has_task_file(issue_id: int) -> bool:
    prefix = f"{issue_id}-"
    if TASKS_DIR.is_dir():
        for name in TASKS_DIR.iterdir():
            if not name.is_file() or name.suffix != ".md":
                continue
            bn = name.name
            if bn.startswith(f"FEAT-{prefix}") or bn.startswith(f"WIP-{prefix}"):
                return True
            if bn.startswith(f"NEW-{prefix}") or bn.startswith(f"TESTING-{prefix}"):
                return True
    done_root = TASKS_DIR / "done"
    if done_root.is_dir():
        for path in done_root.rglob("*.md"):
            if path.name.startswith(f"CLOSED-{prefix}"):
                return True
    return False


def slugify(title: str, *, max_len: int = 48) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s[:max_len] or "task").strip("-")


def fetch_open_issues(url: str, key: str, *, limit: int = 40) -> list[dict]:
    issues: list[dict] = []
    offset = 0
    page = min(limit, 100)
    with httpx.Client(
        base_url=url,
        headers={"X-Redmine-API-Key": key},
        timeout=60.0,
    ) as client:
        while len(issues) < limit:
            r = client.get(
                "/issues.json",
                params={
                    "status_id": "open",
                    "limit": page,
                    "offset": offset,
                    "sort": "updated_on:desc",
                },
            )
            r.raise_for_status()
            batch = r.json().get("issues", [])
            if not batch:
                break
            issues.extend(batch)
            if len(batch) < page:
                break
            offset += page
    return issues[:limit]


def issue_skip_reason(issue: dict) -> str | None:
    iid = int(issue["id"])
    if has_task_file(iid):
        return f"task file exists for #{iid}"
    # Redmine has no agent:planned label — check description
    desc = (issue.get("description") or "").lower()
    if "task planned" in desc or "agent 001" in desc:
        return "body mentions Task planned / Agent 001"
    return None


def create_task(issue: dict, *, base_url: str) -> Path:
    iid = int(issue["id"])
    title = str(issue.get("subject", f"Issue {iid}"))
    url = f"{base_url}/issues/{iid}"
    desc = issue.get("description") or ""
    clean = desc.replace("\n", " ").strip()
    summary = clean[:250] + ("..." if len(clean) > 250 else "")
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    filename = f"FEAT-{iid}-{now}-{slugify(title)}.md"
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    path = TASKS_DIR / filename
    body = f"""# {title}

## Tracker
- **Redmine:** #{iid} — {url}

## Problem / goal
{summary or "[No description]"}

## High-level instructions for coder
- Read the full Redmine issue at {url}
- Implement in `ultron/` with tests in `tests/`
- English for Discord-facing strings
- Run `.venv/bin/pytest -q` before handoff

## Testing instructions
_(Coder fills before UNTESTED rename)_
"""
    path.write_text(body, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="List candidates only")
    parser.add_argument("--limit", type=int, default=3, help="Max tasks to create per run")
    args = parser.parse_args()

    url, key = _load_redmine_env()
    print("=" * 60)
    print("Redmine issue checker (Ultron autoagents)")
    print("=" * 60)

    try:
        issues = fetch_open_issues(url, key)
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1

    if not issues:
        print("No open Redmine issues.")
        return 0

    created = 0
    for issue in issues:
        if created >= args.limit:
            break
        iid = int(issue["id"])
        reason = issue_skip_reason(issue)
        if reason:
            print(f"  SKIP #{iid}: {reason}")
            continue
        title = str(issue.get("subject", ""))[:60]
        print(f"  QUEUE #{iid}: {title}")
        if args.dry_run:
            created += 1
            continue
        path = create_task(issue, base_url=url)
        print(f"  → Created {path.name}")
        created += 1

    print(f"\nDone ({created} task(s) {'listed' if args.dry_run else 'created'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
