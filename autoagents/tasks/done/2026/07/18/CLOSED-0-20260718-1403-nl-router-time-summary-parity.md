---
## Closing summary (TOP)

- **What happened:** NL routing lacked `time_summary` parity with slash `/time_summary`, so @mentions about spent hours fell through to chat.
- **What was done:** Added `time_summary` to NL allowlist, validation, Amvara planner/prefilter, and `_run_nl_invoke` (mirroring slash); patch 2.0.17 → 2.0.18.
- **What was tested:** Pytest (nl_router + amvara prefilter + time_reporting: 22 passed), import/allowlist checks, admin/`rpsls` rejection — all PASS; optional Discord smoke skipped.
- **Why closed:** All required acceptance criteria passed.
- **Closed at (UTC):** 2026-07-20 11:26
---
# NL router: add time_summary parity

## Tracker
- **Redmine:** (none — enhancement reviewer)
- **GitHub:** (none)
- **0** (when no issue)

## Problem / goal

Slash **`/time_summary`** works for allowlisted users, and NL already routes **`log_time`**, but **`time_summary` is not** in `NL_ALLOWED_COMMANDS`, `_validate_args`, `NL_ROUTER_SYSTEM`, or `_run_nl_invoke`. Mentions like “how much time did alice log today?” fall through to chat instead of the spent-hours summary — friction for time reporting via @mention.

## Evidence (008 preflight / review)

- Weekly review (`G008_WEEKLY_DUE=1`).
- `ultron/nl_router.py`: allowed set includes `log_time` / `find_issue` but not `time_summary`.
- `ultron/bot.py` `_run_nl_invoke`: no `time_summary` branch; slash handler exists (`time_summary_cmd`).
- Parity rule: `.cursor/rules/ultron-config-help-nl-parity.mdc`.

## High-level instructions for coder

- Add **`time_summary`** to NL allowlist + system prompt args (`user` string: login, numeric id, or `me`), `_validate_args`, `_nl_dispatch_status_line`, and `_run_nl_invoke` (reuse the same Redmine resolution / bucket / embed path as the slash command).
- Keep **`rpsls`** out of NL unless you intentionally productize it; do not expose admin commands.
- Add/extend tests in `tests/test_nl_router.py` (and dispatch coverage if patterns exist).
- Pass/fail: parse invoke with `time_summary` + valid `user`; invalid args rejected; `.venv/bin/pytest -q` passes; `/help` unchanged unless a one-line NL mention is needed in docs (optional README/USER_GUIDE note only if you touch docs).

## Implementation notes (coder)

- **`ultron/nl_router.py`**: `time_summary` in `NL_ALLOWED_COMMANDS`, `NL_ROUTER_SYSTEM`, `_validate_args` (`user` non-empty string).
- **`ultron/bot.py`**: `_nl_dispatch_status_line`, `_nl_edit_or_reply_embed`, `_run_nl_invoke` branch mirroring slash (resolve user → list entries → buckets → embed).
- **`ultron/amvara/planner.py`** / **`prefilter.py`**: planner allowlist + Redmine verb signal for compound/prefilter parity.
- Patch version **2.0.17 → 2.0.18**.
- `/help` unchanged; `rpsls` still NL-excluded.

## Testing instructions

- [ ] `.venv/bin/pip install -q -e .`
- [ ] `.venv/bin/pytest -q tests/test_nl_router.py` — includes `time_summary` parse (valid `user`/`me`, reject empty/missing)
- [ ] `.venv/bin/pytest -q tests/test_nl_router.py tests/test_amvara_prefilter.py tests/test_time_reporting.py`
- [ ] Import check: `from ultron.bot import UltronBot` and `'time_summary' in NL_ALLOWED_COMMANDS`
- [ ] Optional smoke (needs running bot + Redmine): @mention allowlisted Ultron with “how much time did me log today?” / “time summary for alice” and confirm the time-summary embed (today / week / 7d / 24h), same as `/time_summary`
- [ ] Confirm admin commands (`approve`, `token`, …) still rejected on NL path; `rpsls` not routed
- [ ] No secrets in the diff

## Test report

- **Date/time (UTC):** 2026-07-20 11:25:25 UTC (start) → 2026-07-20 11:27 UTC (finish)
- **Environment:** branch `main`, `.venv` (editable install via `.venv/bin/pip install -q -e .`)

### What was tested

NL `time_summary` parity: allowlist / parse / validate args, Amvara prefilter + time reporting unit tests, import of `UltronBot`, rejection of admin/`rpsls` on NL path. Optional live Discord @mention smoke not run.

### Results

| Criterion | Result | Evidence |
|-----------|--------|----------|
| Editable install | PASS | `.venv/bin/pip install -q -e .` succeeded |
| `pytest` nl_router + amvara prefilter + time_reporting | PASS | 22 passed in 1.04s |
| Import + `'time_summary' in NL_ALLOWED_COMMANDS` | PASS | `import_ok True`; allowlist includes `time_summary` |
| Valid `user` / `me`; empty/missing rejected | PASS | `parse_router_json_text` → `NLInvoke` / `NLParseError` as expected |
| Admin (`approve`, `token`) rejected; `rpsls` not routed | PASS | `NLAdminRejected` / `NLParseError`; `rpsls` absent from `NL_ALLOWED_COMMANDS` |
| Optional Discord @mention smoke | SKIP | Optional; no live Discord exercise in this run |
| No secrets in related sources | PASS | No hardcoded API key / Discord token literals in touched modules |

### Overall: **PASS**

Operator feedback: NL routing for spent-hours summaries matches the slash command surface in code and unit tests. Live Discord confirmation of the embed was left optional and skipped; worth a quick @mention check after the next dump/restart if operators want end-to-end confidence.
