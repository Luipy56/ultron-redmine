#!/usr/bin/env python3
"""GitHub issue → FEAT task helper for autoagents 001 (ultron-redmine repo).

Usage:
  python3 autoagents/issue_checker_github.py
  python3 autoagents/issue_checker_github.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = Path(__file__).resolve().parent / "tasks"
GH_REPO = os.environ.get("AGENT_GH_REPO", "Luipy56/ultron-redmine")


def has_task_file(issue_num: int) -> bool:
    prefix = f"{issue_num}-"
    if TASKS_DIR.is_dir():
        for name in TASKS_DIR.iterdir():
            if not name.is_file():
                continue
            bn = name.name
            if bn.startswith(f"FEAT-{prefix}") or bn.startswith(f"WIP-{prefix}"):
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


def get_open_issues() -> list[dict]:
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                GH_REPO,
                "--state",
                "open",
                "--limit",
                "40",
                "--json",
                "number,title,url,labels",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        return []


def fetch_issue_details(issue_num: int) -> dict | None:
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue_num),
                "--repo",
                GH_REPO,
                "--json",
                "body,state,title,url,labels,createdAt",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
        data["number"] = issue_num
        return data
    except Exception:
        return None


def create_task(issue: dict) -> Path:
    num = int(issue["number"])
    title = issue["title"]
    url = issue["url"]
    body = issue.get("body", "") or ""
    clean = body.replace("\n", " ").strip()
    summary = clean[:250] + ("..." if len(clean) > 250 else "")
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    filename = f"FEAT-{num}-{now}-{slugify(title)}.md"
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    path = TASKS_DIR / filename
    labels = issue.get("labels") or []
    labels_str = ", ".join(str(l.get("name", "")) for l in labels) if labels else "none"
    text = f"""# {title}

## Tracker
- **GitHub:** #{num} — {url}
- **Labels:** {labels_str}

## Problem / goal
{summary or "[No issue body]"}

## High-level instructions for coder
- Read the full issue at {url}
- Implement in `ultron/` with tests
- English for user-facing strings

## Testing instructions
_(Coder fills before UNTESTED rename)_
"""
    path.write_text(text, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    print("=" * 60)
    print(f"GitHub issue checker — {GH_REPO}")
    print("=" * 60)

    issues = get_open_issues()
    if not issues:
        print("No open GitHub issues (or gh unavailable).")
        return 0

    created = 0
    for issue in issues:
        if created >= args.limit:
            break
        num = int(issue["number"])
        labels = issue.get("labels") or []
        if any(l.get("name") == "agent:planned" for l in labels):
            print(f"  SKIP #{num}: agent:planned")
            continue
        if has_task_file(num):
            print(f"  SKIP #{num}: task file exists")
            continue
        details = fetch_issue_details(num)
        if not details:
            print(f"  SKIP #{num}: could not fetch details")
            continue
        body = (details.get("body") or "").lower()
        if "task planned" in body or "agent 001" in body:
            print(f"  SKIP #{num}: body mentions Task planned / Agent 001")
            continue
        print(f"  QUEUE #{num}: {issue['title'][:60]}")
        if args.dry_run:
            created += 1
            continue
        path = create_task({**issue, **details})
        print(f"  → Created {path.name}")
        created += 1

    print(f"\nDone ({created} task(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
