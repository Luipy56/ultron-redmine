# 🤖 Ultron — Discord ↔ Redmine + LLM

A **Discord** bot that connects **Redmine** to an **OpenAI-compatible** LLM (OpenAI, OpenRouter, local Ollama, etc.): slash-command summaries and polished notes, plus scheduled reports for abandoned or stale new tickets.

[`.env.example`](.env.example) · [`config.example.yaml`](config.example.yaml)

---

## Demo

<!-- Replace with your video: YouTube, Loom, or a GIF. Example:
[![Demo](https://img.youtube.com/vi/YOUR_VIDEO_ID/maxresdefault.jpg)](https://www.youtube.com/watch?v=YOUR_VIDEO_ID)
-->

*Video or screenshot coming soon.*

---

## Why Ultron

- **🔗 Discord + Redmine** — Slash commands without leaving the server.
- **🧠 Any OpenAI-compatible LLM** — Single `.env` or an `llm_chain` in YAML (see the example file).
- **⏰ Reports** — Old or “new” idle tickets on a schedule from config.
- **🔐 Allowlist** — `/summary`, `/note`, `/ping`, and `/status` only for approved users; `/token` + `/approve` to onboard.

---

## Requirements

| | |
|--|--|
| Python | **3.11+** |
| Discord | Application with bot + token |
| Redmine | REST API + API key |
| LLM | `/v1/chat/completions` endpoint |

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

1. **Environment:** copy [`.env.example`](.env.example) → `.env` and fill required values (Discord, Redmine, LLM). The file documents the rest.
2. **YAML config:** copy [`config.example.yaml`](config.example.yaml) → `config.yaml`. It documents `llm_chain`, reports (`reports`, `schedules`), Discord copy, and logging.
3. **Discord:** invite the bot with `applications.commands` and permission to post in the reports channel if you use scheduled reports.

---

## Main commands

| Command | Who |
|--------|-----|
| `/help` | Everyone |
| `/ping` | Allowlisted — replies `Pong` (visible in channel when not ephemeral) |
| `/status` | Allowlisted — placeholder (visibility follows `discord.ephemeral_default`) |
| `/summary`, `/note` | Allowlisted users only |
| `/token` (DM) | Request an onboarding code |
| `/approve`, `/remove`, `/show_config` | Admins (`DISCORD_ADMIN_IDS` or `admins.json`); `/show_config` shows non-secret settings (ephemeral, only you) |

Typical access flow: user runs **`/token` in a DM** → an admin runs **`/approve`** with that token (or on the host: `ultron add token '<token>'`).

---

## Docker (optional)

```bash
docker build -t ultron .
docker run --rm --env-file .env -v "$(pwd)/config.yaml:/app/config.yaml:ro" ultron
```

The image bakes a template from `config.example.yaml` as `/app/config.yaml`. Mount your real `config.yaml` so settings persist across container restarts.

---

## License

This project is released under the [MIT License](LICENSE).
