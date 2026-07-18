---
## Closing summary (TOP)

- **What happened:** Self-upgrade #7406 requested a hard rule so `/ca` and `/pi` never perform aggressive deletes (e.g. `rm -rf /`).
- **What was done:** Added always-apply Cursor rule, shared prompt fragment, wired it into ca/pi prompts, and regression tests; shipped at version 2.0.19 (package now 2.0.20).
- **What was tested:** Pytest (6 passed), UltronBot import, prompt/`rm -rf /` presence, Cursor rule check, and secrets scan — all PASS; optional Discord manual check skipped.
- **Why closed:** All required acceptance criteria passed.
- **Closed at (UTC):** 2026-07-18 14:09
---
# Self-upgrade: New specific Rule for ca and pi. You will never never delete anything aggressive

## Tracker
- **Redmine:** #7406 — https://redmine.amvara.de/issues/7406
- **Source:** Discord `/upgrade` (operator)

## Problem / goal

New specific Rule for ca and pi. You will never never delete anything aggressively like "rm -rf /"

## High-level instructions for coder

- Implement the request above in the Ultron checkout (`ultron/`, `tests/`, `scripts/`, `docs/` as needed).
- Prefer a **minimal diff**; match existing Ultron style.
- English for Discord-facing strings; never commit secrets or `.env`.
- After implementation: append **Testing instructions**, rename this file to **UNTESTED-…**.
- Bump patch version in `pyproject.toml` and `ultron/__init__.py` together when shipping code changes.
- Do **not** restart Ultron yourself — the `/upgrade` orchestrator runs dump + systemd restart.

## Implementation summary (coder)

- Added `.cursor/rules/ultron-no-aggressive-delete.mdc` (`alwaysApply: true`) — hard ban on mass/irreversible deletes.
- Added shared prompt fragment `ultron/prompts/ca-pi-no-aggressive-delete.md`.
- Wired the same rule into **`/pi`** (`pi-ops.md`), **`/ca`** / audit ca (`ca-amvara-remote.md`), and pi Amvara prompts (`pi-amvara-local.md`, `pi-amvara-remote.md`); noted in `self-upgrade.md`.
- Regression tests: `tests/test_ca_pi_no_aggressive_delete.py`.
- Version **2.0.19**.

## Testing instructions

- [ ] `.venv/bin/pytest -q tests/test_ca_pi_no_aggressive_delete.py` passes
- [ ] Import check: `from ultron.bot import UltronBot` (version **2.0.19**)
- [ ] Confirm prompts contain `rm -rf /` refusal text:
  - `ultron/prompts/ca-pi-no-aggressive-delete.md`
  - `ultron/prompts/pi-ops.md`
  - `ultron/prompts/ca-amvara-remote.md`
  - `ultron/prompts/pi-amvara-local.md`
  - `ultron/prompts/pi-amvara-remote.md`
- [ ] Confirm `.cursor/rules/ultron-no-aggressive-delete.mdc` exists with `alwaysApply: true`
- [ ] No secrets in the diff
- [ ] Manual (optional): Discord `/pi` or `/ca` — ask the agent to `rm -rf /` and confirm it refuses

## Test report

- **Date/time (UTC):** 2026-07-18 14:08:53 start → 14:09:06 end
- **Environment:** branch `main` @ `748d239`, `.venv` (Python 3.13.5 / pytest-9.1.1), package version **2.0.20**

### What was tested

Automated regression for Redmine #7406 (no aggressive deletes for ca/pi): pytest suite, import of `UltronBot`, prompt/`rm -rf /` presence, Cursor rule `alwaysApply`, secrets scan. Optional Discord manual check skipped.

### Results

| Criterion | Result | Evidence |
|-----------|--------|----------|
| `pytest -q tests/test_ca_pi_no_aggressive_delete.py` | **PASS** | 6 passed in 0.04s |
| Import `UltronBot` (version ≥ 2.0.19) | **PASS** | `from ultron.bot import UltronBot` ok; `__version__` is **2.0.20** (implementation landed at 2.0.19; later patch bumped further) |
| Prompts contain `rm -rf /` refusal | **PASS** | All five listed prompt files match; same coverage as parametrized tests |
| `.cursor/rules/ultron-no-aggressive-delete.mdc` + `alwaysApply: true` | **PASS** | File present; frontmatter has `alwaysApply: true` |
| No secrets in the diff | **PASS** | Diff mentions only “No secrets” policy wording; no tokens/keys |
| Manual Discord `/pi` or `/ca` | **SKIP** | Optional; not exercised in this run |

### Overall: **PASS**

Rule fragment, Cursor always-apply rule, and ca/pi prompt wiring are in place and covered by regression tests. Version is ahead of the coder’s 2.0.19 pin due to subsequent patches; feature criteria all pass. Operator can optionally confirm live Discord refusal of `rm -rf /` on `/pi` or `/ca`.
