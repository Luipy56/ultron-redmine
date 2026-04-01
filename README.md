# Ultron

Small **Discord → Redmine → LLM** bot: slash commands for ticket summaries and polished notes, plus scheduled reports for abandoned and stale new tickets. LLM backends are anything **OpenAI-compatible** (OpenAI, OpenRouter, local Ollama, etc.).

## Requirements

- Python **3.11+**
- A Discord application with a bot token
- Redmine with REST API and an API key
- An OpenAI-compatible HTTP API (`/v1/chat/completions`)

## Quick start

1. Clone or copy this repository.

2. Create a virtual environment and install:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

3. Copy [`.env.example`](.env.example) to `.env` and fill in values (see below).

4. Edit [`config.yaml`](config.yaml): set `reports.channel_id` to a Discord channel ID where the bot may post scheduled reports, or leave `0` to disable scheduled posting. Adjust `timezone`, `discord.*`, and optional `llm_chain` as needed. Every key is also listed (with empty/null placeholders) in [`config.example.yaml`](config.example.yaml). The default config path is `config.yaml`; to use another file (e.g. `config.yml`), set **`CONFIG_PATH`** in `.env`.

5. Run:

   ```bash
   python -m ultron
   ```

   Or: `ultron` if the console script is on your `PATH`.

## Environment variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Bot token (Developer Portal → **Bot**). |
| `REDMINE_URL` | Yes | Base URL, no trailing slash (e.g. `https://redmine.example.com`). |
| `REDMINE_API_KEY` | Yes | Redmine API key. |
| `LLM_API_KEY` | Usually yes | API key for the LLM provider (dummy for Ollama). **Optional** if `config.yaml` has a non-empty `llm_chain` (keys then come from each entry’s `api_key_env`). |
| `LLM_BASE_URL` | No | Default `https://api.openai.com/v1`. For Ollama: `http://127.0.0.1:11434/v1`. |
| `LLM_MODEL` | No | Default `gpt-4o-mini`. |
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

## Discord checklist

1. [Discord Developer Portal](https://discord.com/developers/applications) → your app → **Bot** → reset/copy token → `DISCORD_TOKEN`.
2. Under **Bot**, enable **Privileged Gateway Intents** only if you later add features that need them; the default slash-only flow does not require Message Content Intent.
3. OAuth2 URL Generator: scopes **`bot`** and **`applications.commands`**. Invite the bot with permission to use slash commands in your server and to **Send Messages** in the reports channel.
4. For faster command updates while developing, set `DISCORD_GUILD_ID` to your server ID and restart the bot after code changes.

## Redmine checklist

1. Ensure REST API is enabled and your user can read/update issues in the relevant projects.
2. Create an API key under **My account** → **API access key** → `REDMINE_API_KEY`.

## LLM examples

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
- If `llm_chain` is **absent or empty** (`[]`), behavior is unchanged: `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` from the environment drive a single client.
- Example block: see [`config.yaml`](config.yaml) (commented) and [`config.example.yaml`](config.example.yaml).

## `config.yaml`

- **`llm_chain`**: Optional ordered list of OpenAI-compatible backends (see [LLM provider chain](#llm-provider-chain-llm_chain-in-configyaml)). List order is priority; when non-empty, it replaces single-provider `LLM_*` env vars for LLM calls.
- **`logging.log_read_messages`**: If `true`, the `ultron.read` logger records **full** text Ultron ingests: formatted Redmine ticket bodies, the `/note` slash text, and complete LLM `system`/`user` prompts (including scheduled reports). **May contain secrets and PII**; keep `false` in production unless you are debugging in a safe environment. Default `false`.
- **`timezone`**: Used when formatting report headers (e.g. `Europe/Madrid`, `UTC`).
- **`discord.ephemeral_default`**: If `true`, `/summary` and `/note` replies are only visible to the user who ran the command.
- **`discord.summary_status_redmine`**: Status text shown on the deferred `/summary` message while loading the ticket from Redmine (default: `Fetching ticket from Redmine…`).
- **`discord.summary_status_llm`**: Status text before the LLM call; use `{model}` for the configured model name (default: `Calling {model}…`).
- **`reports.channel_id`**: Discord integer channel ID for scheduled reports. `0` disables loops (no automatic posts).
- **`schedules.abandoned`**: Open tickets whose `updated_on` is older than `max_days_without_update` (within the first 100 issues returned by Redmine, sorted by oldest update first).
- **`schedules.stale_new`**: Open tickets at least `min_age_hours` old, optionally unassigned, with at most `max_journal_entries` journals (fetches each candidate to count journals; capped by `max_issues`).

Tune `max_journal_entries` for your Redmine version; some installs create more than one journal entry on creation.

## Slash commands

- **`/help`** — Lists all slash commands and who may use them (ephemeral). Available to everyone.
- **`/summary issue_id`** — Loads the ticket (description + recent journals), sends context to the LLM, returns a summary. **Requires a whitelisted Discord user id.**
- **`/note issue_id text`** — Confirms the ticket exists, asks the LLM to polish the text, then appends it as a Redmine journal note. **Requires a whitelisted user id.**
- **`/token`** — Only in a **DM** with the bot (not in server channels). If you are **already whitelisted**, the bot says so and does **not** issue a new code. Otherwise it issues a random token valid for **5 minutes** and writes a pending request under `ULTRON_STATE_DIR`. A **bot admin** can run **`/approve`** with that token, or an operator on the host can run `ultron add token <token>`.
- **`/approve token`** — **Admins only** (see below). Consumes a pending token and adds that user’s Discord id to `whitelist.json` (same as the CLI). When you approve **in Discord**, Ultron **DMs the approved user**; **`ultron add token` on the host does not send a DM** (no Discord client in that process).
- **`/remove user_id`** — **Admins only**. Removes that numeric Discord user id from `whitelist.json` if present; otherwise replies that they were not on the whitelist.

### Access control (whitelist)

Only Discord user ids stored in `whitelist.json` (under `ULTRON_STATE_DIR`, default `./data`) may use **`/summary`** and **`/note`**. That directory is listed in `.gitignore`; keep it on the server only.

### Bot admins

Admins may use **`/approve`** to whitelist users and **`/remove`** to drop a user id from the whitelist, without shell access. An admin is any Discord user id in **`DISCORD_ADMIN_IDS`** and/or **`admins.json`** (same directory as `whitelist.json`, same JSON array-of-integers format as the whitelist). Use the env var for the first admin(s), or create `admins.json` on the server by hand.

**Bootstrap for a new user**

1. The user opens a **DM** with the bot and runs **`/token`** (reply is ephemeral), then sends the token to a bot admin (or to someone with host access).
2. **Option A — Discord:** an admin runs **`/approve`**, pastes the token into the `token` option.
3. **Option B — host:** on the machine where Ultron runs (same `.env` / `ULTRON_STATE_DIR` as the bot):

   ```bash
   ultron add token '<paste-token-here>'
   ```

4. The user may still need the **bot owner** to OK access organizationally; the bot mentions this in DMs when access is denied.

If a non-whitelisted user invokes **`/summary`** or **`/note`** in a **server channel**, Ultron removes the visible reply so others see nothing. In **DM**, they get a short English message about **`/token`**, asking a bot admin, and contacting the owner. Optional **`BOT_OWNER_CONTACT`** is appended when set.

### Long notes (Discord limit)

Slash command string options are limited (up to **6000** characters per option). For longer notes, use shorter text in v1 or extend the bot later (for example a modal or a follow-up message in a thread).

## Docker (optional)

```bash
docker build -t ultron .
docker run --rm --env-file .env ultron
```

Mount or bake `config.yaml` if you do not use the default copy in the image.

## Security

- Never commit `.env` or real API keys.
- Do not commit `ULTRON_STATE_DIR` contents (`whitelist.json`, `admins.json`, `pending_tokens.json`); they identify who may use the bot, who can approve users, and hold short-lived approval tokens.
- Avoid enabling `logging.log_read_messages` where logs are aggregated or retained; it prints full ticket and note content.

## License

Use and modify as needed for your team.
