# Refresh USER_GUIDE: /status and allowlisted commands

## Tracker
- **Redmine:** (none — enhancement reviewer)
- **GitHub:** (none)
- **0** (when no issue)

## Problem / goal

`docs/USER_GUIDE.md` still describes **`/status`** as a **placeholder** and the “First commands” table only lists `/help`, `/ping`, and `/status`. Discord users miss discoverability for **`/find_issue`**, **`/time_summary`**, **`/log_time`**, **`/rpsls`**, and **`/issues_by_status`**, which are already documented in README and `/help`.

## Evidence (008 preflight / review)

- Weekly review (`G008_WEEKLY_DUE=1`); no automated doc-drift signal, manual scan.
- `docs/USER_GUIDE.md` line ~26: “Placeholder status”.
- Contrast: `ultron/bot.py` `_HELP_TEXT` and README Command overview describe a real `/status` summary (version, uptime, latency, Redmine, LLM, NL, reports).

## High-level instructions for coder

- Update **`docs/USER_GUIDE.md` only** (English): replace the placeholder `/status` wording with a short accurate description aligned with `/help`.
- Expand the “First commands” table (or a short follow-on list) with brief one-liners for **`/find_issue`**, **`/issues_by_status`**, **`/time_summary`**, **`/log_time`**, and **`/rpsls`**; keep the guide concise — point to README for full detail.
- Do not change `ultron/` code in this task.
- Pass/fail: USER_GUIDE no longer says “placeholder” for `/status`; the five commands above appear with correct allowlist framing; English only.

## Testing instructions

1. Open **docs/USER_GUIDE.md** → **First commands to try**: confirm **`/status`** is described as a real bot summary (version, uptime, latency, Redmine, LLM, NL, reports) and that the word **placeholder** does not appear for `/status` (or anywhere in that table).
2. Confirm the same table includes one-liner rows for **`/find_issue`**, **`/issues_by_status`**, **`/time_summary`**, **`/log_time`**, and **`/rpsls`**, each marked allowlisted (or equivalent).
3. Confirm a short pointer to **README.md — Command overview** appears near the table for full detail.
4. Spot-check against **`/help`** / README: `/status` and the five commands above match allowlisted behavior (no admin-only mislabel).
5. Confirm version is **`2.0.23`** in both `pyproject.toml` and `ultron/__init__.py`.
6. From repo root: `.venv/bin/pytest -q` (expect pass) and import check with env loaded:
   ```bash
   set -a && . ./.env; set +a
   .venv/bin/python -c "from ultron.settings import load_env; from ultron.bot import UltronBot; load_env(); print('import_ok')"
   ```

