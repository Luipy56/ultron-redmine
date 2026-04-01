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

4. Edit [`config.yaml`](config.yaml): set `reports.channel_id` to a Discord channel ID where the bot may post scheduled reports, or leave `0` to disable scheduled posting.

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
| `LLM_API_KEY` | Yes | API key for the LLM provider (use a dummy value for Ollama if required). |
| `LLM_BASE_URL` | No | Default `https://api.openai.com/v1`. For Ollama: `http://127.0.0.1:11434/v1`. |
| `LLM_MODEL` | No | Default `gpt-4o-mini`. |
| `LLM_TIMEOUT_SECONDS` | No | HTTP timeout for LLM calls (seconds). Default **900** (15 minutes). Increase for large tickets on slow local models; note Discord slash interactions expire sooner. |
| `LLM_MAX_RETRIES` | No | OpenAI SDK retries per request. Default **0** for Ollama/local, **2** for cloud (avoids doubling wait time on timeouts locally). |
| `LOG_LEVEL` | No | Root log level (`DEBUG`, `INFO`, …). Default `INFO`. `httpx` / `openai` loggers are capped at `WARNING` to reduce retry noise. |
| `OLLAMA_API_BASE` | No | If set (and `LLM_BASE_URL` empty), base URL without `/v1` (e.g. `http://127.0.0.1:11434`); Ultron uses `{base}/v1` and defaults `LLM_API_KEY` to `ollama` when unset. |
| `OLLAMA_MODEL` | No | Used when `LLM_MODEL` is empty. |
| `DISCORD_GUILD_ID` | No | If set, slash commands sync to this server immediately (handy for development). |
| `DISCORD_APPLICATION_ID` | No | Optional; not required for the gateway bot. |
| `CONFIG_PATH` | No | Path to `config.yaml` (default `./config.yaml`). |

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

## `config.yaml`

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

- **`/summary issue_id`** — Loads the ticket (description + recent journals), sends context to the LLM, returns a summary.
- **`/note issue_id text`** — Confirms the ticket exists, asks the LLM to polish the text, then appends it as a Redmine journal note.

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
- Avoid enabling `logging.log_read_messages` where logs are aggregated or retained; it prints full ticket and note content.

## License

Use and modify as needed for your team.
