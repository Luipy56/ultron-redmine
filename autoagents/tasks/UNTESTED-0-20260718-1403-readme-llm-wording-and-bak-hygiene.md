# README LLM wording, OPERATIONS admin list, bak hygiene

## Tracker
- **Redmine:** (none — enhancement reviewer)
- **GitHub:** (none)
- **0** (when no issue)

## Problem / goal

Three small operator-facing hygiene gaps: (1) README “Why Ultron” still implies LLM setup is “`.env` **or** `llm_chain`”, but models/endpoints are **only** configured via **`llm_chain`** in YAML (keys stay in `.env`); (2) OPERATIONS lists admin Discord commands incompletely next to `DISCORD_ADMIN_IDS`; (3) local `*.bak.*` debris is not ignored and can be committed by mistake.

## Evidence (008 preflight / review)

- Weekly review (`G008_WEEKLY_DUE=1`).
- README Why Ultron: “Single `.env` or an `llm_chain` in YAML” vs later sections and `feat!: configure LLM only through llm_chain`.
- `docs/OPERATIONS.md`: `DISCORD_ADMIN_IDS` bullet mentions `/approve`, `/remove`, `/show_config` only — omits **`/pi`** and **`/upgrade`** (documented elsewhere in the same file).
- Untracked backups in the tree: `docs/OPERATIONS.md.bak.*`, `pyproject.toml.bak.*`, `ultron/__init__.py.bak.*`; `.gitignore` already has `.env.bak.*` / `config.yaml.bak.*` but not a general `*.bak.*`.

## High-level instructions for coder

- Fix the README Why Ultron LLM bullet so it matches current setup: **`llm_chain` in `config.yaml`** + API key env vars; optional LLM.
- In **`docs/OPERATIONS.md`**, extend the `DISCORD_ADMIN_IDS` bullet to include **`/pi`** and **`/upgrade`** (or cross-link the self-upgrade section).
- Delete stray `*.bak.*` files under the repo (do not touch real `.env` / `config.yaml`) and add a **`.gitignore`** pattern such as `*.bak.*` (or explicit paths) so backups stay local.
- Pass/fail: no contradictory “`.env` or llm_chain” for model config; admin list accurate; no `*.bak.*` left in the working tree; English only.

## Testing instructions

1. Open **README.md** → **Why Ultron**: confirm the LLM bullet says models/endpoints are configured via **`llm_chain`** in **`config.yaml`** with API keys in `.env` (via `api_key_env`), and that it no longer says “Single `.env` or an `llm_chain`”.
2. Open **docs/OPERATIONS.md**: confirm the **`DISCORD_ADMIN_IDS`** bullet lists `/approve`, `/remove`, `/show_config`, `/pi`, and `/upgrade`, and that the self-upgrade section link resolves.
3. Confirm stray backups are gone: `docs/OPERATIONS.md.bak.*`, `pyproject.toml.bak.*`, and `ultron/__init__.py.bak.*` are not present (do not require deleting real `.env.bak.*`).
4. Confirm **`.gitignore`** contains `*.bak.*` and that `git check-ignore -v some-file.bak.123` reports a match.
5. Confirm version is **`2.0.20`** in both `pyproject.toml` and `ultron/__init__.py`.
6. From repo root: `.venv/bin/pytest -q` (expect pass) and import check with env loaded:
   ```bash
   set -a && . ./.env; set +a
   .venv/bin/python -c "from ultron.settings import load_env; from ultron.bot import UltronBot; load_env(); print('import_ok')"
   ```

