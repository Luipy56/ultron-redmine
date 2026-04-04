# 🤖 Ultron — Discord ↔ Redmine + LLM

A **Discord** bot that connects **Redmine** to an **OpenAI-compatible** LLM (OpenAI, OpenRouter, local Ollama, etc.): slash-command summaries, Q&A over tickets, and polished notes, plus **scheduled Redmine listing posts** to a channel (`report_schedule` in `config.yaml`). Access is **allowlisted**; optional **natural-language routing** turns @mentions into the same allowed actions when an LLM is configured.

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
- **🧠 Any OpenAI-compatible LLM** — Single `.env` or an `llm_chain` in YAML (see the example file). The bot can run **without** an LLM: listing commands and scheduled channel listings still work; `/summary`, `/ask_issue`, and `/note` require a model.
- **⏰ Reports** — Declarative **`report_schedule`**: run `list_new_issues`, `list_unassigned_issues`, or `issues_by_status` on an interval to **`reports.channel_id`**. On startup, the bot can post a short English welcome and schedule summary there (`reports.startup_message_enabled`).
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
2. **YAML config:** copy [`config.example.yaml`](config.example.yaml) → `config.yaml`. It documents `llm_chain`, `reports`, `report_schedule`, Discord copy, registration log, and logging.
3. **Discord:** invite the bot with `applications.commands` and permission to post in the reports channel if you use scheduled reports (and in the registration log channel if enabled).

### Configuration wizard (terminal)

Install the optional extra and run the interactive wizard (arrow keys or numbers + Enter). It walks through Redmine, Discord, LLM, YAML (`report_schedule`, listings), and paths; existing `.env` values are shown with **secrets masked**.

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
| `/status` | Allowlisted — structured summary (version, uptime, gateway latency, Redmine host, LLM, NL routing, reports; visibility follows `discord.ephemeral_default`) |
| `/rpsls` | Allowlisted — rock–paper–scissors–lizard–Spock vs the bot (visibility follows `discord.ephemeral_default`) |
| `/list_new_issues` | Allowlisted — lists issues in the configured Redmine “new” status, created at least **M** days ago (`discord.new_issues` in `config.yaml`) |
| `/issues_by_status` | Allowlisted — same listing rules as `/list_new_issues`, but you pass the Redmine **status** name as the option (min age & list cap still from `discord.new_issues`) |
| `/list_unassigned_issues` | Allowlisted — **unassigned** issues still **open** in Redmine, created at least **N** day(s) ago, excluding status names that match configured **closed-equivalent prefixes** (`discord.unassigned_open` in `config.yaml`); first chunk as an embed |
| `/time_summary` `user` | Allowlisted — Redmine **spent hours** for a user (**today**, **this week** Mon–today, **last 7 days** by `spent_on`, **last 24 h** by `created_on`). `user` = Redmine login, numeric id, or **`me`**. Optional **`redmine.user_id_by_login`** in `config.yaml` if login API lookup is denied |
| `/log_time` | Allowlisted — logs **spent hours** on an issue (as the **Redmine API key** user); optional **comments** and **spent_on** (YYYY-MM-DD). Optional **`REDMINE_TIME_ACTIVITY_ID`** in `.env` when your instance has several time-entry activities |
| `/summary`, `/ask_issue`, `/note` | Allowlisted — require a **configured LLM** |
| `@Ultron` (mention or reply) | Allowlisted — LLM router when NL routing is enabled and an LLM is configured; otherwise a short notice. **Message Content Intent** may be required in some setups. |
| `/token` (DM) | Request an onboarding code |
| `/approve`, `/remove`, `/show_config` | Admins (`DISCORD_ADMIN_IDS` or `admins.json`); `/show_config` shows non-secret settings (ephemeral) |

Typical access flow: user runs **`/token` in a DM** → an admin runs **`/approve`** with that token (or on the host: `ultron add token '<token>'`).

---

## Environment variables

The bot reads **`.env`** from the current working directory (usually the repo root). Copy **[`.env.example`](.env.example)** → `.env` and fill it in — **every variable is documented there** with examples and defaults.

**Required:** `DISCORD_TOKEN`, `REDMINE_URL`, `REDMINE_API_KEY`.

**Optional:** API keys for **`llm_chain`** (only the variable names you list as `api_key_env` in `config.yaml`), `REDMINE_TIME_ACTIVITY_ID` (for **`/log_time`** when Redmine has multiple time activities), `CONFIG_PATH`, `ULTRON_STATE_DIR`, `DISCORD_ADMIN_IDS`, guild sync (`DISCORD_GUILD_ID`), intents, logging, etc.

YAML settings (Discord copy, `report_schedule`, **`llm_chain`** base URLs and models, optional **`redmine.user_id_by_login`**, …) are **not** in `.env`; use **`config.yaml`** and **[`config.example.yaml`](config.example.yaml)**.

**Environment variable names:** Optional top-level **`environment_bindings`** in `config.yaml` defines which env var **names** the bot reads for Discord, Redmine, and global toggles. Default names match [`.env.example`](.env.example). Secret **values** still come from the process environment (`.env`, Docker, systemd); the YAML only remaps those binding names. Each **`llm_chain`** entry names its API key variable separately via **`api_key_env`** (not remapped by `environment_bindings`).

## Discord checklist

1. [Discord Developer Portal](https://discord.com/developers/applications) → your app → **Bot** → reset/copy token → `DISCORD_TOKEN`.
2. Under **Bot**, enable **Message Content Intent** if you set **`DISCORD_MESSAGE_CONTENT_INTENT=1`** in `.env` (required for that privileged intent). Ultron always subscribes to **guild + DM messages** (non-privileged) so @mentions are delivered; if mentions still do not appear in logs, enable this toggle and the env var.
3. OAuth2 URL Generator: scopes **`bot`** and **`applications.commands`**. Invite the bot with permission to use slash commands in your server and to **Send Messages** in the reports channel. If you use @mention replies, also allow **Read Message History** in those channels. Grant access to any **registration log** channel you configure.
4. Slash command registration: see `DISCORD_GUILD_ID` in [`.env.example`](.env.example). Restart the bot after changing commands; guild sync is immediate for that server, global propagation can take up to about an hour.

## Redmine checklist

1. Ensure REST API is enabled and your user can read/update issues in the relevant projects. For **`/log_time`**, the same user must be allowed to **log time** on the target issues (and time tracking must be enabled for the project/activity as usual).
2. Create an API key under **My account** → **API access key** → `REDMINE_API_KEY`.

## LLM setup

**Configuration:** define an ordered **`llm_chain`** list in **`config.yaml`** — each entry is OpenAI-compatible; order is try/fallback order. Set **`base_url`**, **`model`** (one string or a list; first = default; extras appear on LLM slash commands), and **`api_key_env`** (the name of an environment variable holding that provider’s key — define it in `.env`). Examples: **OpenAI** (`https://api.openai.com/v1`), **Ollama** (`http://127.0.0.1:11434/v1`, many setups use the literal key string **`ollama`**), **OpenRouter** (their base URL + key). Full schema and comments: **[`config.example.yaml`](config.example.yaml)**.

**No LLM:** use an empty list or omit **`llm_chain`** — listing commands and registration still work; `/summary`, `/ask_issue`, `/note`, and NL routing need at least one enabled chain entry and matching keys in the environment.

More detail: [docs/OPERATIONS.md](docs/OPERATIONS.md).

## `config.yaml`

Copy **[`config.example.yaml`](config.example.yaml)** → `config.yaml`. **Every key is explained in that file** (with `# Example:` lines). You do not need to duplicate that reference here.

**Typical first edits:** `timezone`; `reports.channel_id` and **`report_schedule`** for automated channel listings; `discord.new_issues.status_name` (and limits) for issue listings; optional **`llm_chain`** plus the API key env vars it references; `discord.registration_log` if you want an ops channel.

**Safety:** `logging.log_read_messages: true` logs full prompts and ticket text — avoid in production unless debugging in a closed environment (default is off).

## Slash commands

- **`/help`** — Lists all slash commands and who may use them (ephemeral). Available to everyone.
- **@mention** (`@Ultron`) — Not a slash command: **@mention** or **Reply** to the bot (**whitelisted users only**). With NL routing **on** and a configured LLM, an LLM maps your message to allowed commands (never admin actions); the bot **edits one status message** in place (routing → optional “running …” line → result). Otherwise a short notice that routing is disabled or that no LLM is configured. May need **`DISCORD_MESSAGE_CONTENT_INTENT=1`** + portal toggle if events are missing.
- **`/summary issue_id`** — Loads the ticket (description + recent journals), sends context to the LLM, returns a summary. **Requires a whitelisted Discord user id** and a **configured language model** (otherwise the bot replies with setup instructions). Optional **`llm_provider`** / **`llm_model`** when a model is configured; see [LLM setup](#llm-setup) and **`discord.slash_show_llm_option_hints`** in [`config.example.yaml`](config.example.yaml).
- **`/ask_issue issue_id question`** — Loads the same ticket context as **`/summary`**, sends it with **`question`** to the LLM, and returns an answer grounded in the ticket text. **Requires a whitelisted user id** and a **configured model**. Same optional LLM options as **`/summary`**.
- **`/note issue_id text`** — Confirms the ticket exists, asks the LLM to polish the text, then appends it as a Redmine journal note. **Requires a whitelisted user id** and a **configured model**. Same optional LLM options as **`/summary`**.
- **`/list_new_issues`** — Lists Redmine issues whose status matches **`discord.new_issues.status_name`**, created at least **`discord.new_issues.min_age_days`** ago (see `config.yaml`). **Requires a whitelisted user id.**
- **`/issues_by_status` `status`** — Same output rules as **`/list_new_issues`**, but **`status`** is the Redmine issue status name for this run (min age and list limit still come from **`discord.new_issues`**). **Requires a whitelisted user id.**
- **`/list_unassigned_issues`** — Lists **unassigned** Redmine issues that are still **open** (`status_id=open`), created at least **`discord.unassigned_open.min_age_days`** ago (default **1**), excluding any current status whose name **equals or starts with** a string in **`discord.unassigned_open.closed_status_prefixes`** (case-insensitive; e.g. `Solved` matches `Solved STAGE`). List length is capped by **`discord.unassigned_open.list_limit`**. **Requires a whitelisted user id.** The first chunk is sent as an **embed**; the rest as plain messages.
- **`/time_summary` `user`** — Totals **hours** from Redmine **time entries** for the given Redmine user: **today** and **this week** (Monday through today, **`timezone`** in `config.yaml`), **last 7 days** (inclusive, by **`spent_on`**), and **last 24 hours** (by each entry’s **`created_on`**, UTC). Pass Redmine **login**, a numeric **user id**, or **`me`** for the API user. If your Redmine role cannot **list users** (403), configure **`redmine.user_id_by_login`** (login → id). Fetches up to **`redmine.time_summary_max_entries`** rows over a rolling **`spent_on`** window (see [config.example.yaml](config.example.yaml)). **Requires a whitelisted user id**; **no LLM** required.
- **`/log_time` `issue_id` `hours`** [`comments`] [`spent_on`] — Creates a Redmine **time entry** (fractional hours). Optional **`comments`** (short text) and **`spent_on`** (**YYYY-MM-DD**). Booked as the **Redmine user** behind **`REDMINE_API_KEY`**. Clearer errors on **403/422** when Redmine returns JSON errors. If Redmine exposes several **time entry activities**, set **`REDMINE_TIME_ACTIVITY_ID`** in `.env` when needed (see [`.env.example`](.env.example)). **Requires a whitelisted user id**; **no LLM** required.
- **`/token`** — Only in a **DM** with the bot (not in server channels). If you are **already whitelisted**, the bot says so and does **not** issue a new code. Otherwise it issues a random token valid for **5 minutes** and writes a pending request under `ULTRON_STATE_DIR`. A **bot admin** can run **`/approve`** with that token, or an operator on the host can run `ultron add token <token>`.
- **`/approve token`** — **Admins only** (see below). Consumes a pending token and adds that user’s Discord id to `whitelist.json` (same as the CLI). When you approve **in Discord**, Ultron **DMs the approved user**; **`ultron add token` on the host does not send a DM** (no Discord client in that process).
- **`/remove user_id`** — **Admins only**. Removes that numeric Discord user id from `whitelist.json` if present; otherwise replies that they were not on the whitelist.
- **`/show_config`** — **Admins only**. Ephemeral summary of important non-secret settings (Redmine URL, `report_schedule`, feature flags, etc.).

### Access control (whitelist)

Only Discord user ids stored in `whitelist.json` (under `ULTRON_STATE_DIR`, default `./data`) may use **`/summary`**, **`/ask_issue`**, **`/note`**, **`/list_new_issues`**, **`/issues_by_status`**, **`/list_unassigned_issues`**, **`/time_summary`**, **`/log_time`**, and **@mention** replies. That directory is listed in `.gitignore`; keep it on the server only.

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

If a non-whitelisted user invokes **`/summary`**, **`/ask_issue`**, **`/note`**, **`/list_new_issues`**, **`/issues_by_status`**, **`/list_unassigned_issues`**, **`/time_summary`**, or **`/log_time`** in a **server channel**, Ultron removes the visible reply so others see nothing. In **DM**, they get a short English message about **`/token`**, asking a bot admin, and contacting the owner. Optional **`BOT_OWNER_CONTACT`** is appended when set.

### Long notes (Discord limit)

Slash command string options are limited (up to **6000** characters per option). For longer notes, use shorter text in v1 or extend the bot later (for example a modal or a follow-up message in a thread).

## Docker (optional)

Prerequisites in the repository root (bind mounts must exist on the host before `docker compose up`):

1. **`.env`** — copy from `.env.example` and fill in secrets (`cp .env.example .env`).
2. **`config.yaml`** — copy from `config.example.yaml` (`cp config.example.yaml config.yaml`) and adjust. If the file is missing, Docker may create a **directory** named `config.yaml` instead of a file; remove it and copy the example again.
3. **`data/`** — `mkdir -p data` (whitelist, pending tokens, etc. persist here).

Run the bot (builds the image and installs Python dependencies from `pyproject.toml`):

```bash
docker compose up --build
```

Detached:

```bash
docker compose up -d --build
```

**`.env`**, **`config.yaml`**, and **`data/`** are mounted from the host, not copied into image layers. Rebuilding the image does not discard them.

### Network: default bridge vs host

With the **default** Compose file, the container has its **own** network namespace. There is no transparent “bridge” that makes every URL resolve like on the host: `http://127.0.0.1:…` points at the **container** loopback, not the machine where you ran `docker compose`. Hitting an LLM on the host (Ollama), or on a port that is really an **SSH local forward** on the host, will fail unless you change URLs (for example to `host.docker.internal` on Linux with `extra_hosts`)—that only helps reach the **host**, not “all URLs behave as on the host.”

If you need **the same URLs as a bare-metal run** (OpenAI, `localhost` / `127.0.0.1`, SSH `-L` tunnels on the host, same egress/VPN as the host), use the optional **host network** override (Linux):

```bash
docker compose -f docker-compose.yml -f docker-compose.hostnet.yml up -d --build
```

See comments in [`docker-compose.hostnet.yml`](docker-compose.hostnet.yml). **Docker Desktop** (macOS/Windows) does not provide the same host-network semantics as Linux; if loopback/tunnels on the physical machine do not work, run the bot on the host or use a Linux environment.

Run **exactly one** live process per **`DISCORD_TOKEN`** (one Compose service replica, or one manual `docker run`). A second copy causes Discord **10062 Unknown interaction** on slash commands and confusing mixed replies (different bot versions in the same channel).

The image still bakes a template from `config.example.yaml` as a default `/app/config.yaml` when no mount is used (for example plain `docker build` / `docker run` without compose).

Manual run without Compose:

```bash
docker build -t ultron .
docker run --rm --env-file .env -v "$(pwd)/config.yaml:/app/config.yaml:ro" -v "$(pwd)/data:/app/data" ultron
```

For the same **host-network** URL behavior without Compose (Linux), add `--network host` to `docker run` (mount paths unchanged).

---

## License

This project is released under the [MIT License](LICENSE).
