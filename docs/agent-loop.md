# Ultron autoagents loop

Orchestrated multi-agent pipeline for **ultron-redmine** (Discord ↔ Redmine bot). Inspired by the POS agent loop, adapted for Python, Redmine intake, and **`main`** branch workflow.

## Layout

```text
autoagents/
├── ultron-agent-loop.sh      # Orchestrator (loop or single step)
├── TASKS-README.md           # Task filename rules and statuses
├── 001-redmine-reviewer.md   # Intake: Redmine, optional GitHub, ultron.log
├── 008-enhancement-reviewer.md
├── 010-feature-coder.md      # FEAT-* implementation
├── 002-coder/CODER.md        # NEW-* / WIP-* implementation
├── 012-feature-coder-handoff.md
├── 020-test.md
├── 030-closing-reviewer.md
├── 040-committer.md
├── issue_checker_redmine.py
├── issue_checker_github.py
└── tasks/                    # Active task markdown files
```

Supporting scripts live under **`scripts/`**:

- `git-sync-main.sh` — fetch + pull --rebase on **main**
- `redmine-reviewer-preflight.sh` — digest for agent 001
- `enhancement-reviewer-preflight.sh` — digest for agent 008
- `move-agent-task-to-done.sh` — archive **CLOSED-** tasks
- `ultron-dump.sh` — editable reinstall + **`systemctl restart`** (loop runs this after committer when runtime paths change)

## Pipeline (one cycle)

```text
001 intake → 008 enhancement → FEAT coder (×5 max) → NEW/WIP coder → 012 handoff → 020 tester → 030 closing → 040 committer → ultron-dump
```

Task statuses: **new/feat → wip → untested → testing → closed → done/YYYY/MM/DD/** — see **`autoagents/TASKS-README.md`**.

### Deploy after code updates (`ultron-dump`)

When **`ultron/`**, **`pyproject.toml`**, or related runtime paths change on **`main`** (via committer or a pull), a version bump alone is not enough: the systemd process must be reinstalled and restarted.

Each full cycle ends with **`step_ultron_dump`**, which runs **`scripts/ultron-dump.sh`** (`pip install -e .`, optional `npm install`, `systemctl restart`) when those paths differ from the last successful dump stamp (`autoagents/.last-ultron-dump-sha`, gitignored).

| Command / flag | Purpose |
|----------------|---------|
| `./autoagents/ultron-agent-loop.sh dump` | Run the dump step alone |
| `AGENT_ULTRON_DUMP=0` | Skip dump/restart (e.g. read-only hosts) |

Discord **`/upgrade`** still runs dump + restart itself after the **shot** (the shot path does **not** call `step_ultron_dump`, to avoid a double restart).

## Discord `/upgrade` (one shot)

Admin **`/upgrade text`** creates a **`FEAT-7406-…`** task (Redmine tracker **#7406** by default; override with **`ULTRON_UPGRADE_REDMINE_ISSUE`**) and runs:

```bash
AGENT_GIT_SYNC=0 ./autoagents/ultron-agent-loop.sh shot
```

That shot runs **feat → handoff → tester → closing** (no intake/enhancement/committer). On success Ultron verifies, runs **`scripts/ultron-dump.sh`** (install only), posts a journal note to Redmine **#7406**, sends Discord feedback, then **`systemctl restart --no-block`**.

## Running

From repo root (requires **cursor-agent** on PATH):

```bash
chmod +x autoagents/ultron-agent-loop.sh scripts/*.sh
./autoagents/ultron-agent-loop.sh              # loop every 5 minutes
./autoagents/ultron-agent-loop.sh intake       # single step
./autoagents/ultron-agent-loop.sh tester
```

Environment highlights:

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENT_LOOP_SLEEP_MINUTES` | 5 | Loop interval |
| `AGENT_GIT_SYNC` | 1 | Run `git-sync-main.sh` before editing steps |
| `AGENT_GH_REPO` | `Luipy56/ultron-redmine` | GitHub repo for optional GH intake |
| `ULTRON_LOG_FILE` | `./ultron.log` | Log path for 001 heuristics |
| `AGENT_INTAKE_REVIEWER_ALWAYS` | 0 | Force 001 cursor-agent every cycle |
| `AGENT_ULTRON_DUMP` | 1 | After committer, run `ultron-dump.sh` when runtime paths changed |

## Tracker updates

- **Redmine:** agent 001 adds journal notes when queueing **FEAT-** tasks; coders/testers add notes on progress (no secrets).
- **GitHub (optional):** when `gh` is configured, 001 may comment and label **`agent:planned`** on **ultron-redmine** issues. Not required for local development.

## Differences from POS autoagents

| POS | Ultron |
|-----|--------|
| `agents2/`, `development` branch | `autoagents/`, **`main`** |
| Docker `pos-*` logs | **`ultron.log`** heuristics |
| Marketing repos reviewer (005) | **Removed** |
| `back/` / `front/` | **`ultron/`**, **`tests/`** |
| `CHANGELOG.md` + `front/package.json` | **`pyproject.toml`** + **`ultron/__init__.py`** |

## Related

- Discord **`/upgrade`** — creates **`FEAT-7406-…`**, runs **`shot`**, dump, Redmine note, restart (see above).
- **`ultron/prompts/self-upgrade.md`** — legacy / related cursor-agent prompt (autoagents feature-coder prompt is primary for FEAT work).
