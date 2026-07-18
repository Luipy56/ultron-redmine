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
