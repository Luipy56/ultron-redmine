# Ultron — user guide (Discord)

This document is for **people who use the bot in Discord**, not for server operators. Operators should read [OPERATIONS.md](OPERATIONS.md) and the repository [README.md](../README.md).

## Before you start

- You need access to a server where **Ultron** is already invited, or a **DM** with the bot (for `/token`).
- Many commands only work after an **administrator has approved** your Discord account (whitelist).
- **`/summary`**, **`/ask_issue`**, **`/note`**, and **`/ol`** need a **configured language model** on the host. If the operator runs Ultron without an LLM, those commands stay disabled; listing commands such as **`/list_new_issues`** and **`/list_unassigned_issues`** still work.

## Getting access (whitelist)

1. Open a **direct message** with the bot (not a public channel).
2. Run **`/token`**.  
   - If you are **already** on the whitelist, the bot tells you and does **not** issue a new code.
   - Otherwise you receive a **one-time code** (valid a few minutes).
3. Send that code to a **bot admin**. They run **`/approve`** with your token (or use a host command — see the operator docs).
4. After approval, you may get a DM confirming access. You can then use allowlisted commands in the server (subject to channel visibility settings).

## First commands to try

| Command | What it does |
|--------|----------------|
| **`/help`** | Lists commands and who may use them (everyone). |
| **`/ping`** | Quick connectivity check (allowlisted users). |
| **`/status`** | Placeholder status (allowlisted). |

Use **`/summary`**, **`/ask_issue`**, **`/note`**, and **`/ol`** only when you are allowlisted **and** the bot has an LLM configured; otherwise the bot replies with setup instructions for operators. **`/ol`** is for general or technical questions (Redmine, Ultron, Linux) — advisory only, not ticket-specific like **`/ask_issue`**.

**`/audit`** and **`/ca`** run server diagnostics on allowlisted Amvara hosts (when your operator has configured them). **`/audit`** tries pi first; **`/ca`** uses cursor-agent only. You can also @mention the bot with a host name (e.g. “check RAM on amvara3”) or combine an audit with a Redmine note in one message.

If you **@mention** the bot (or **reply** to one of its messages) in a channel or DM and you are allowlisted, behavior depends on host settings: with routing **on** and an LLM configured, the bot posts a short **status line** and **updates that same message** while it works (routing → running the chosen action → final answer), similar to slash “thinking” feedback. If routing is **off**, you get a brief notice instead. If nothing happens, ask your operator to confirm you are **whitelisted** and, if needed, **Message Content Intent** + **`DISCORD_MESSAGE_CONTENT_INTENT`** in the bot host configuration.

## Whitelist vs bot admins

- **Whitelist** — Users who may use Redmine-related slash commands (`/summary`, `/ask_issue`, `/note`, `/ol`, `/audit`, `/ca`, `/list_new_issues`, `/list_unassigned_issues`, etc.) as configured by your team.
- **Bot admins** — Users who may **`/approve`** and **`/remove`** whitelist entries. They are a smaller group.

If a command says you are not allowed, follow your organization’s process (often **`/token`** + admin approval). Optional **`BOT_OWNER_CONTACT`** in the bot configuration may point you to who can help.

## Privacy and channels

- Some replies may be **ephemeral** (only you see them), depending on server settings.
- If you run a restricted command in a channel without permission, the bot may hide the error from the channel and send guidance in DM instead.

## Need more detail?

- Full command matrix: [README.md — Slash commands](../README.md#slash-commands)
- Host-side setup: [OPERATIONS.md](OPERATIONS.md)
