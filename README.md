# 🤖 Ultron — Discord ↔ Redmine + LLM

A **Discord** bot that connects **Redmine** to an **OpenAI-compatible** LLM (OpenAI, OpenRouter, local Ollama, etc.): slash-command summaries, Q&A over tickets, and polished notes, plus scheduled reports for abandoned or stale new tickets. Access is **allowlisted**; optional **natural-language routing** turns @mentions into the same allowed actions when an LLM is configured.

[`.env.example`](.env.example) · [`config.example.yaml`](config.example.yaml)

---

## Contents

- [Demo](#demo)
- [Why Ultron](#why-ultron)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Documentation](#documentation)
- [Command overview](#command-overview)
- [Environment variables](#environment-variables)
- [Discord checklist](#discord-checklist)
- [Redmine checklist](#redmine-checklist)
- [LLM setup](#llm-setup)
- [`config.yaml`](#configyaml)
- [Slash commands](#slash-commands)
- [Docker](#docker-optional)
- [License](#license)

---

## Demo

<!-- Replace with your video: YouTube, Loom, or a GIF. Example:
[![Demo](https://img.youtube.com/vi/YOUR_VIDEO_ID/maxresdefault.jpg)](https://www.youtube.com/watch?v=YOUR_VIDEO_ID)
-->

*Video or screenshot coming soon.*

---

## Why Ultron

- **🔗 Discord + Redmine** — Slash commands without leaving the server; fetch lists by status, unassigned open issues, summaries, Q&A, and notes.
- **🧠 Any OpenAI-compatible LLM** — Single `.env` or an `llm_chain` in YAML (see the example file). The bot can run **without** an LLM: listing commands still work; `/summary`, `/ask_issue`, `/note`, and AI text in scheduled reports require a model.
- **⏰ Reports** — Abandoned and stale-new ticket jobs on configurable intervals (`schedules.*` in `config.yaml`), posted to `reports.channel_id`.
- **🔐 Allowlist** — Whitelisted users get `/summary`, `/ask_issue`, `/note`, issue listings, `/ping`, `/status`, and @mention handling; **`/token` in DM** + **`/approve`** (or `ultron add token`) onboards users.
- **🧭 Optional NL routing** — With a configured LLM, **`discord.nl_commands`** (default **on**) lets the LLM interpret @mentions/replies into allowed commands only (never admin actions). Set **`false`** for a fixed short notice instead.
- **📣 Optional ops channel** — **`discord.registration_log`** can post startup and whitelist events to a Discord channel for operators.

---

## Requirements

| | |
|--|--|
| Python | **3.11+** |
| Discord | Application with bot + token |
| Redmine | REST API + API key |
| LLM | `/v1/chat/completions` endpoint — **optional** for Redmine-only usage |

---

## Quick start

```bash
git clone https://github.com/Luipy56/ultron-redmine.git
cd ultron-redmine
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env          # fill in credentials
cp config.example.yaml config.yaml   # set reports channel, timezone, etc.
python -m ultron              # or: ultron
```

1. **Environment:** copy [`.env.example`](.env.example) → `.env` and fill required values (Discord, Redmine, LLM if used). The file documents the rest.
2. **YAML config:** copy [`config.example.yaml`](config.example.yaml) → `config.yaml`. It documents `llm_chain`, reports (`reports`, `schedules`), Discord copy, registration log, and logging.
3. **Discord:** invite the bot with `applications.commands` and permission to post in the reports channel if you use scheduled reports (and in the registration log channel if enabled).

### Configuration wizard (terminal)

Install the optional extra and run the interactive wizard (arrow keys or numbers + Enter). It walks through Redmine, Discord, LLM, YAML schedules, and paths; existing `.env` values are shown with **secrets masked**.

```bash
pip install -e ".[wizard]"
ultron wizard
# alias:
ultron configure
```

Writes the repository `.env` (next to the checkout) and `config.yaml` (path from `CONFIG_PATH`, default `./config.yaml` relative to the current working directory). **Rewriting `config.yaml` drops inline comments** in that file; keep a backup or use `config.example.yaml` as reference.

During the wizard: **Y** / **N** / **r** (see the hint next to each prompt; **r** returns to the main menu). In text fields use **Ctrl+r** (^R) so plain **r** can be typed.

### Documentation

| Doc | Audience |
|-----|----------|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Discord users — onboarding, whitelist, first commands |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Host operators — integrations (Redmine, Discord, LLM), env/YAML pointers |
| [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) | Maintainers — tests, version bump, release criteria |

---

## Command overview

| Command | Who |
|--------|-----|
| `/help` | Everyone |
| `/ping` | Allowlisted — replies `Pong` (visible in channel when not ephemeral) |
| `/status` | Allowlisted — placeholder (visibility follows `discord.ephemeral_default`) |
| `/new_issues` | Allowlisted — lists issues in the configured Redmine “new” status, created at least **M** days ago (`discord.new_issues` in `config.yaml`) |
| `/issues_by_status` | Allowlisted — same listing rules as `/new_issues`, but you pass the Redmine **status** name as the option (min age & list cap still from `discord.new_issues`) |
| `/unassigned_issues` | Allowlisted — **unassigned** issues still **open** in Redmine, created at least **N** day(s) ago, excluding status names that match configured **closed-equivalent prefixes** (`discord.unassigned_open` in `config.yaml`) |
| `/summary`, `/ask_issue`, `/note` | Allowlisted — require a **configured LLM** |
| `@Ultron` (mention or reply) | Allowlisted — LLM router when NL routing is enabled and an LLM is configured; otherwise a short notice. **Message Content Intent** may be required in some setups. |
| `/token` (DM) | Request an onboarding code |
| `/approve`, `/remove`, `/show_config` | Admins (`DISCORD_ADMIN_IDS` or `admins.json`); `/show_config` shows non-secret settings (ephemeral) |

Typical access flow: user runs **`/token` in a DM** → an admin runs **`/approve`** with that token (or on the host: `ultron add token '<token>'`).

---

## Environment variables

Values are loaded from **`.env`** in the repository root (see [`.env.example`](.env.example)).

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Bot token (Developer Portal → **Bot**). |
| `REDMINE_URL` | Yes | Base URL, no trailing slash (e.g. `https://redmine.example.com`). |
| `REDMINE_API_KEY` | Yes | Redmine API key. |
| `LLM_API_KEY` | Usually yes | API key for the LLM provider (dummy for Ollama). **Optional** if `config.yaml` has a non-empty `llm_chain` (keys then come from each entry’s `api_key_env`). **Omit** (with no `llm_chain`) to run **without** any language model — **`/summary`**, **`/ask_issue`**, and **`/note`** stay disabled; Redmine listing and other slash commands still work. |
| `LLM_BASE_URL` | No | Default `https://api.openai.com/v1`. For Ollama: `http://127.0.0.1:11434/v1`. |
| `LLM_MODEL` | No | Default `gpt-4o-mini` when an LLM is configured. |
| `LLM_DISABLED` / `ULTRON_NO_LLM` | No | If set to `1` / `true` / `yes`, forces **no** LLM (must not define `llm_chain` in `config.yaml`). |
| `LLM_TIMEOUT_SECONDS` | No | HTTP timeout for LLM calls (seconds). Default **900** (15 minutes). Increase for large tickets on slow local models; note Discord slash interactions expire sooner. |
| `LLM_MAX_RETRIES` | No | OpenAI SDK retries per request. Default **0** for Ollama/local, **2** for cloud (avoids doubling wait time on timeouts locally). |
| `LOG_LEVEL` | No | Root log level (`DEBUG`, `INFO`, …). Default `INFO`. `httpx` / `openai` loggers are capped at `WARNING` to reduce retry noise. |
| `OLLAMA_API_BASE` | No | If set (and `LLM_BASE_URL` empty), base URL without `/v1` (e.g. `http://127.0.0.1:11434`); Ultron uses `{base}/v1` and defaults `LLM_API_KEY` to `ollama` when unset. |
| `OLLAMA_MODEL` | No | Used when `LLM_MODEL` is empty. |
| `DISCORD_GUILD_ID` | No | If set, slash commands sync to this server immediately (handy for development). |
| `DISCORD_APPLICATION_ID` | No | Optional; not required for the gateway bot. |
| `CONFIG_PATH` | No | Path to the YAML config file (default `./config.yaml`). Use e.g. `config.yml` if you prefer that extension. |
| `ULTRON_STATE_DIR` | No | Directory for **whitelist**, **admins**, and **pending `/token` data** (default `./data`). Not committed to git; use an absolute path in production if the working directory changes. |
| `BOT_OWNER_CONTACT` | No | Optional line (e.g. email or handle) appended to the English DM sent to users who are not whitelisted yet. |
| `DISCORD_ADMIN_IDS` | No | Comma- or space-separated Discord user IDs that are **bot admins** (may use **`/approve`**). Merged with `admins.json` in `ULTRON_STATE_DIR`. |
| `DISCORD_MESSAGE_CONTENT_INTENT` | No | Set to `1` / `true` / `yes` to request the privileged **Message Content** intent (must match the Developer Portal toggle). If unset, the bot still receives message events for @mentions; enable this if Discord does not populate mentions without it. |
| `ULTRON_NL_COMMANDS` | No | If `1` / `true` / `yes`, contributes to **natural-language @mention routing** (logical **OR** with **`discord.nl_commands`** in `config.yaml`). To keep routing off, set **`discord.nl_commands: false`** and leave this unset or false. Requires a **configured LLM** for the router to run. |

## Discord checklist

1. [Discord Developer Portal](https://discord.com/developers/applications) → your app → **Bot** → reset/copy token → `DISCORD_TOKEN`.
2. Under **Bot**, enable **Message Content Intent** if you set **`DISCORD_MESSAGE_CONTENT_INTENT=1`** in `.env` (required for that privileged intent). Ultron always subscribes to **guild + DM messages** (non-privileged) so @mentions are delivered; if mentions still do not appear in logs, enable this toggle and the env var.
3. OAuth2 URL Generator: scopes **`bot`** and **`applications.commands`**. Invite the bot with permission to use slash commands in your server and to **Send Messages** in the reports channel. If you use @mention replies, also allow **Read Message History** in those channels. Grant access to any **registration log** channel you configure.
4. For faster command updates while developing, set `DISCORD_GUILD_ID` to your server ID and restart the bot after code changes.

## Redmine checklist

1. Ensure REST API is enabled and your user can read/update issues in the relevant projects.
2. Create an API key under **My account** → **API access key** → `REDMINE_API_KEY`.

## LLM setup

### Quick examples

- **OpenAI**: `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_API_KEY=sk-...`, `LLM_MODEL=gpt-4o-mini`
- **Ollama**: `LLM_BASE_URL=http://127.0.0.1:11434/v1`, `LLM_API_KEY=ollama`, `LLM_MODEL=llama3.2`. Default timeout **900s**; override with `LLM_TIMEOUT_SECONDS`. **No SDK retries** by default when Ollama is detected.
- **OpenRouter**: use their OpenAI-compatible base URL and key from their dashboard.

### LLM provider chain (`llm_chain` in `config.yaml`)

`llm_chain` is an **ordered YAML list**: index **0** is the primary backend, then fallbacks. No per-entry `id` field — order is the hierarchy. Each item is OpenAI-compatible (**OpenAI**, **OpenRouter**, **Ollama** at `http://127.0.0.1:11434/v1`, etc.).

On provider failures Ultron logs the reason (policy line), the entry’s optional **`name`** (or index) and **`model`**, then tries the next list item. That includes **wrong URL or key** (**401**, **403**), **bad model/params** (**400**), **404**, **429**, **5xx**, and **connection/timeout** errors — so a misconfigured chain slot does not block the rest.

- **Optional `name`**: free-form label for readability in YAML and in logs (startup chain line and fallback warnings). Omit or leave unset to use list index only.
- **Secrets** only in the environment: each entry sets `api_key_env` (e.g. `OPENAI_API_KEY`, `OLLAMA_API_KEY`); use `ollama` as the value for local Ollama when that matches your setup.
- For **Ollama** in the chain, prefer **`max_retries: 0`** and a generous **`timeout_seconds`** (e.g. 900) on that entry.
- **Empty LLM reply** (`""`) is still success; no fallback for empty content.
- **`{model}` in `discord.summary_status_llm`**: **first** list entry’s `model`.
- If `llm_chain` is **absent or empty** (`[]`) and **`LLM_API_KEY`** is unset (and you are not using Ollama env defaults), Ultron runs **without** an LLM (`NullLLMBackend`): **`/summary`** / **`/ask_issue`** / **`/note`** and AI prose in scheduled reports are skipped; scheduled jobs still post **plain issue lists**.
- If `llm_chain` is **absent or empty** (`[]`) and you **do** set `LLM_API_KEY` (or Ollama), `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` from the environment drive a single client.
- Example block: see [`config.yaml`](config.yaml) (commented) and [`config.example.yaml`](config.example.yaml).

User-visible strings when the chain switches or exhausts are customizable under **`discord.llm_chain_skip_status`** and **`discord.llm_chain_all_failed_message`** (see [`config.yaml`](#configyaml)).

## `config.yaml`

- **`llm_chain`**: Optional ordered list of OpenAI-compatible backends (see [LLM provider chain](#llm-provider-chain-llm_chain-in-configyaml)). List order is priority; when non-empty, it replaces single-provider `LLM_*` env vars for LLM calls.
- **`logging.log_read_messages`**: If `true`, the `ultron.read` logger records **full** text Ultron ingests: formatted Redmine ticket bodies, `/note` slash text, `/ask_issue` prompts, and complete LLM `system`/`user` prompts (including scheduled reports). **May contain secrets and PII**; keep `false` in production unless you are debugging in a safe environment. Default `false`.
- **`timezone`**: Used when formatting report headers (e.g. `Europe/Madrid`, `UTC`).
- **`discord.ephemeral_default`**: If `true`, `/summary`, `/ask_issue`, and `/note` default to ephemeral replies (only the user who ran the command sees them).
- **`discord.summary_status_redmine`**: Status text while loading the ticket from Redmine for **`/summary`** and **`/ask_issue`** (built-in default: `Fetching ticket from Redmine…`).
- **`discord.summary_status_llm`**: Status text before the LLM call for **`/summary`** and **`/ask_issue`**; use `{model}` for the configured model name (built-in default: `Passing the task to {model}…`).
- **`discord.llm_chain_skip_status`**: Template when Ultron switches to the next `llm_chain` provider; placeholders `{from_entry}`, `{from_model}`, `{reason}`, `{to_entry}`, `{to_model}`.
- **`discord.llm_chain_all_failed_message`**: Shown when every provider in `llm_chain` fails.
- **`discord.nl_commands`**: Default **`true`**: whitelisted **@mention** / **Reply** messages use an **LLM router** (requires a **configured LLM**). Set **`false`** for a fixed short notice (no router). Env **`ULTRON_NL_COMMANDS`** is combined with logical **OR** (see [Environment variables](#environment-variables)).
- **`discord.registration_log`**: Optional channel for operator visibility — **`enabled`**, **`channel_id`**, and **`features`** (`startup`, `whitelist_events`) for posting bot online and whitelist-related events.
- **`discord.unassigned_open`**: For **`/unassigned_issues`**: **`min_age_days`** (default **1**), **`list_limit`**, and **`closed_status_prefixes`** (list of strings; an issue’s current status is excluded if its name equals or starts with any prefix, case-insensitive — e.g. `Solved` matches `Solved STAGE`).
- **`discord.new_issues`**: **`status_name`**, **`list_limit`**, **`min_age_days`** for **`/new_issues`** and **`/issues_by_status`** (shared limits).
- **`reports.channel_id`**: Discord integer channel ID for scheduled reports. `0` disables posting (loops do not run meaningfully without a channel).
- **`schedules.abandoned`**: **`enabled`**, **`interval_hours`**, **`max_days_without_update`**, **`max_issues`** — open tickets whose `updated_on` is older than the threshold (within the first 100 issues returned by Redmine, sorted by oldest update first).
- **`schedules.stale_new`**: **`enabled`**, **`interval_hours`**, **`min_age_hours`**, **`require_unassigned`**, **`max_journal_entries`**, **`max_issues`**, optional **`issue_status_name`** — tickets at least `min_age_hours` old, with at most `max_journal_entries` journals (fetches each candidate to count journals). If **`issue_status_name`** is unset/empty, all **open** issues are considered (Redmine `status_id=open`).

Tune `max_journal_entries` for your Redmine version; some installs create more than one journal entry on creation.

## Slash commands

- **`/help`** — Lists all slash commands and who may use them (ephemeral). Available to everyone.
- **@mention** (`@Ultron`) — Not a slash command: **@mention** or **Reply** to the bot (**whitelisted users only**). With NL routing **on** and a configured LLM, an LLM maps your message to allowed commands (never admin actions); the bot **edits one status message** in place (routing → optional “running …” line → result). Otherwise a short notice that routing is disabled or that no LLM is configured. May need **`DISCORD_MESSAGE_CONTENT_INTENT=1`** + portal toggle if events are missing.
- **`/summary issue_id`** — Loads the ticket (description + recent journals), sends context to the LLM, returns a summary. **Requires a whitelisted Discord user id** and a **configured language model** (otherwise the bot replies with setup instructions).
- **`/ask_issue issue_id question`** — Loads the same ticket context as **`/summary`**, sends it with **`question`** to the LLM, and returns an answer grounded in the ticket text. **Requires a whitelisted user id** and a **configured model**.
- **`/note issue_id text`** — Confirms the ticket exists, asks the LLM to polish the text, then appends it as a Redmine journal note. **Requires a whitelisted user id** and a **configured model**.
- **`/new_issues`** — Lists Redmine issues whose status matches **`discord.new_issues.status_name`**, created at least **`discord.new_issues.min_age_days`** ago (see `config.yaml`). **Requires a whitelisted user id.**
- **`/issues_by_status` `status`** — Same output rules as **`/new_issues`**, but **`status`** is the Redmine issue status name for this run (min age and list limit still come from **`discord.new_issues`**). **Requires a whitelisted user id.**
- **`/unassigned_issues`** — Lists **unassigned** Redmine issues that are still **open** (`status_id=open`), created at least **`discord.unassigned_open.min_age_days`** ago (default **1**), excluding any current status whose name **equals or starts with** a string in **`discord.unassigned_open.closed_status_prefixes`** (case-insensitive; e.g. `Solved` matches `Solved STAGE`). List length is capped by **`discord.unassigned_open.list_limit`**. **Requires a whitelisted user id.**
- **`/token`** — Only in a **DM** with the bot (not in server channels). If you are **already whitelisted**, the bot says so and does **not** issue a new code. Otherwise it issues a random token valid for **5 minutes** and writes a pending request under `ULTRON_STATE_DIR`. A **bot admin** can run **`/approve`** with that token, or an operator on the host can run `ultron add token <token>`.
- **`/approve token`** — **Admins only** (see below). Consumes a pending token and adds that user’s Discord id to `whitelist.json` (same as the CLI). When you approve **in Discord**, Ultron **DMs the approved user**; **`ultron add token` on the host does not send a DM** (no Discord client in that process).
- **`/remove user_id`** — **Admins only**. Removes that numeric Discord user id from `whitelist.json` if present; otherwise replies that they were not on the whitelist.
- **`/show_config`** — **Admins only**. Ephemeral summary of important non-secret settings (Redmine URL, schedules, feature flags, etc.).

### Access control (whitelist)

Only Discord user ids stored in `whitelist.json` (under `ULTRON_STATE_DIR`, default `./data`) may use **`/summary`**, **`/ask_issue`**, **`/note`**, **`/new_issues`**, **`/issues_by_status`**, **`/unassigned_issues`**, and **@mention** replies. That directory is listed in `.gitignore`; keep it on the server only.

### Bot admins

Admins may use **`/approve`** to whitelist users, **`/remove`** to drop a user id from the whitelist, and **`/show_config`** to inspect safe settings, without shell access. An admin is any Discord user id in **`DISCORD_ADMIN_IDS`** and/or **`admins.json`** (same directory as `whitelist.json`, same JSON array-of-integers format as the whitelist). Use the env var for the first admin(s), or create `admins.json` on the server by hand. **Use each user’s numeric Discord id (Developer Mode → copy id);** usernames, display names, or nicknames are not accepted—if `admins.json` contains only invalid entries, no one is an admin except ids in **`DISCORD_ADMIN_IDS`**.

**Bootstrap for a new user**

1. The user opens a **DM** with the bot and runs **`/token`** (reply is ephemeral), then sends the token to a bot admin (or to someone with host access).
2. **Option A — Discord:** an admin runs **`/approve`**, pastes the token into the `token` option.
3. **Option B — host:** on the machine where Ultron runs (same `.env` / `ULTRON_STATE_DIR` as the bot):

   ```bash
   ultron add token '<paste-token-here>'
   ```

4. The user may still need the **bot owner** to OK access organizationally; the bot mentions this in DMs when access is denied.

If a non-whitelisted user invokes **`/summary`**, **`/ask_issue`**, **`/note`**, **`/new_issues`**, **`/issues_by_status`**, or **`/unassigned_issues`** in a **server channel**, Ultron removes the visible reply so others see nothing. In **DM**, they get a short English message about **`/token`**, asking a bot admin, and contacting the owner. Optional **`BOT_OWNER_CONTACT`** is appended when set.

### Long notes (Discord limit)

Slash command string options are limited (up to **6000** characters per option). For longer notes, use shorter text in v1 or extend the bot later (for example a modal or a follow-up message in a thread).

## Docker (optional)

```bash
docker build -t ultron .
docker run --rm --env-file .env -v "$(pwd)/config.yaml:/app/config.yaml:ro" ultron
```

The image bakes a template from `config.example.yaml` as `/app/config.yaml`. Mount your real `config.yaml` so settings persist across container restarts.

---

## License

This project is released under the [MIT License](LICENSE).
