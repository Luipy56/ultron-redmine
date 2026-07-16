# Task workflow (Ultron)

Tasks move through a single pipeline from creation to closure. See **`docs/agent-loop.md`** for roles, Ultron-specific rules, and optional **Redmine** / **GitHub Issues** updates (notes + status per agent role). **Before renaming or editing task files**, sync **`main`** with **`./scripts/git-sync-main.sh`** (multi-agent workflow; **`ultron-agent-loop.sh`** does this each step).

## Filename pattern

`<STATUS>-<ISSUE-ID>-<YYYYMMDD-HHMM>-<slug>.md`

- **`<ISSUE-ID>`** — Redmine issue number, GitHub issue number, or **`0`** when there is no linked tracker item.
- Examples: `NEW-0-20260716-1030-discord-gateway-reconnect.md`, `FEAT-42-20260716-1100-time-summary-bug.md`, `CLOSED-42-20260716-1200-time-summary-bug.md`

The **`<YYYYMMDD>`** segment (8 digits after the first `-`) is used to place archived tasks under **`done/YYYY/MM/DD/`** (see below). When renaming a task to **`CLOSED-…`**, use the **calendar day that work finished** (UTC or your team convention) in `YYYYMMDD`.

## Statuses

| Status       | Meaning |
|--------------|--------|
| **new**      | Task is defined and not yet started. |
| **feat**     | Feature-sized task (optional parallel queue). |
| **wip**      | Work in progress until implemented. When implemented rename **WIP*.md** to **UNTESTED*.md** |
| **untested** | Implementation done; **Testing instructions** appended; waiting for tester. |
| **testing**  | Tester is running verification. |
| **closed**   | Verified; ready for closing reviewer to archive. |

## Flow

```text
  new   ─┐
         ├─→  wip  →  untested  →  testing  →  closed  →  done/YYYY/MM/DD/
  feat  ─┘
```

Do not skip statuses. On test failure: **testing → wip** (coder fixes), then **wip → untested** again when ready.

## Archiving closed tasks (`done/` layout)

Closed tasks are **not** kept in a single flat **`done/`** directory. After the closing reviewer prepends the **Closing summary**, the file is moved to:

```text
autoagents/tasks/done/<YYYY>/<MM>/<DD>/<same-filename>.md
```

- **`<YYYY>`**, **`<MM>`**, and **`<DD>`** come from the **8-digit date in the filename** (`YYYYMMDD` right after the status prefix), not from “today” when you run the script.
- **Same basename** as in **`autoagents/tasks/`**; only the directory changes.

**Helper (recommended):** from repo root,

```bash
./scripts/move-agent-task-to-done.sh autoagents/tasks/CLOSED-42-20260716-1200-example-slug.md
```

The script creates **`done/YYYY/MM/DD`** if needed and moves the file. It only accepts **`CLOSED-`** filenames.

See **`tasks/done/README.md`** for a short index of the archive tree.

## Rules of thumb

- **new → wip** / **feat → wip** when work starts.
- **wip → untested** when implementation is complete and **Testing instructions** are at the end of the task file.
- **untested → testing** when the tester starts.
- **testing → closed** when verification passes: rename **`TESTING-…`** → **`CLOSED-…`** (keep the same **`YYYYMMDD-HHMM-slug`**; only change the status prefix). On failure, **testing → wip**.
- **closed → done/YYYY/MM/DD/** after the closing summary is added (use **`move-agent-task-to-done.sh`** or an equivalent `mkdir` + `mv`).
