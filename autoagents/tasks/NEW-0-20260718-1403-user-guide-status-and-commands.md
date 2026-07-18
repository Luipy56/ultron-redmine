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
