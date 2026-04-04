"""Individual wizard sections (questionary prompts)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ultron.wizard.masking import mask_secret
from ultron.wizard.paths import resolve_config_path
from ultron.wizard.ui import ReturnToMenu, ask, patch_question_with_return

if TYPE_CHECKING:
    import questionary

from ultron.wizard.state import WizardState


def _yn(q: Any, message: str, default: bool = True) -> bool:
    # Compact: capital letter = default on Enter; r = return to main menu.
    instruction = "(Y/n/r)" if default else "(y/N/r)"
    qu = patch_question_with_return(
        q.confirm(message, default=default, qmark=">", instruction=instruction)
    )
    try:
        return bool(ask(qu))
    except ReturnToMenu:
        raise


def _text(q: Any, message: str, default: str = "") -> str:
    qu = patch_question_with_return(
        q.text(
            message,
            default=default,
            qmark=">",
            instruction="(^R)",
        ),
        for_text=True,
    )
    try:
        r = ask(qu)
        return r if r is not None else ""
    except ReturnToMenu:
        raise


def section_paths(q: Any, state: WizardState) -> None:
    state.ensure_yaml()
    print("\n--- Paths ---\n")
    cur_cfg = state.env_get("CONFIG_PATH") or "config.yaml"
    cur_sd = state.env_get("ULTRON_STATE_DIR") or "data"
    if _yn(q, f"Edit CONFIG_PATH? (current: {cur_cfg})", default=False):
        v = _text(q, "CONFIG_PATH (relative to cwd or absolute)", default=cur_cfg)
        state.env_set("CONFIG_PATH", v.strip() or "config.yaml")
        state.config_path = resolve_config_path(state.env)
    if _yn(q, f"Edit ULTRON_STATE_DIR? (current: {cur_sd})", default=False):
        v = _text(q, "ULTRON_STATE_DIR (whitelist, admins, tokens)", default=cur_sd)
        state.env_set("ULTRON_STATE_DIR", v.strip() or "data")
    sd = Path(state.env_get("ULTRON_STATE_DIR", "data")).expanduser()
    try:
        sd.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"Warning: could not create state dir: {e}")


def section_redmine(q: Any, state: WizardState) -> None:
    print("\n--- Redmine ---\n")
    print(
        "If `config.yaml` defines `environment_bindings`, use the same variable names in `.env` "
        "as in that block (defaults match REDMINE_* here).\n"
    )
    url = state.env_get("REDMINE_URL")
    key = state.env_get("REDMINE_API_KEY")
    print(f"REDMINE_URL: {url or '(empty)'}")
    print(f"REDMINE_API_KEY: {mask_secret('REDMINE_API_KEY', key)}")
    ta = state.env_get("REDMINE_TIME_ACTIVITY_ID")
    print(
        f"REDMINE_TIME_ACTIVITY_ID: {ta or '(empty; optional — set when /log_time needs a fixed activity id)'}\n"
    )
    if _yn(q, "Edit Redmine URL?", default=not bool(url)):
        state.env_set("REDMINE_URL", _text(q, "REDMINE_URL (no trailing slash)", default=url).strip().rstrip("/"))
    if _yn(q, "Edit Redmine API key?", default=not bool(key)):
        state.env_set("REDMINE_API_KEY", _text(q, "REDMINE_API_KEY", default="").strip())
    if _yn(q, "Edit REDMINE_TIME_ACTIVITY_ID (optional, for /log_time)?", default=False):
        state.env_set(
            "REDMINE_TIME_ACTIVITY_ID",
            _text(
                q,
                "REDMINE_TIME_ACTIVITY_ID (numeric id or empty)",
                default=state.env_get("REDMINE_TIME_ACTIVITY_ID") or "",
            ).strip(),
        )
    if _yn(q, "Test connection to Redmine now?", default=bool(state.env_get("REDMINE_URL") and state.env_get("REDMINE_API_KEY"))):
        from ultron.redmine import RedmineClient, RedmineError
        import httpx

        async def _check() -> None:
            client = RedmineClient(
                base_url=state.env_get("REDMINE_URL"),
                api_key=state.env_get("REDMINE_API_KEY"),
            )
            try:
                await client.verify_connection()
                print("Redmine: OK (users/current.json)\n")
            except RedmineError as e:
                print(f"Redmine error: {e}\n")
            except httpx.RequestError as e:
                print(f"Network error: {e}\n")

        try:
            asyncio.run(_check())
        except RuntimeError:
            # nested event loop in rare environments
            print("Could not run async check in this context; skip.\n")


def section_discord_bot(q: Any, state: WizardState) -> None:
    print("\n--- Discord bot (token) ---\n")
    tok = state.env_get("DISCORD_TOKEN")
    print(f"DISCORD_TOKEN: {mask_secret('DISCORD_TOKEN', tok)}\n")
    if _yn(q, "Edit bot token?", default=not bool(tok)):
        state.env_set("DISCORD_TOKEN", _text(q, "DISCORD_TOKEN", default="").strip())
    aid = state.env_get("DISCORD_APPLICATION_ID")
    print(f"DISCORD_APPLICATION_ID: {aid or '(empty)'}\n")
    if _yn(q, "Edit application ID (optional)?", default=False):
        raw = _text(q, "DISCORD_APPLICATION_ID (numeric or empty)", default=aid).strip()
        state.env_set("DISCORD_APPLICATION_ID", raw)

    mci = state.env_get("DISCORD_MESSAGE_CONTENT_INTENT")
    print(
        "DISCORD_MESSAGE_CONTENT_INTENT: set to 1 if the Developer Portal enables the privileged Message Content intent "
        "(sometimes needed for reliable @mention handling).\n"
        f"  Current: {mci or '(unset)'}\n"
    )
    if _yn(q, "Edit DISCORD_MESSAGE_CONTENT_INTENT?", default=False):
        raw = _text(q, "DISCORD_MESSAGE_CONTENT_INTENT (empty = leave unset, or 1)", default=mci).strip()
        state.env_set("DISCORD_MESSAGE_CONTENT_INTENT", raw)

    ulnl = state.env_get("ULTRON_NL_COMMANDS")
    print(
        "ULTRON_NL_COMMANDS: optional; if truthy, forces natural-language @mention routing on "
        "(same idea as discord.nl_commands: true in config.yaml).\n"
        f"  Current: {ulnl or '(unset)'}\n"
    )
    if _yn(q, "Edit ULTRON_NL_COMMANDS?", default=False):
        raw = _text(
            q,
            "ULTRON_NL_COMMANDS (e.g. 1 — optional env override; leave empty for no value in session)",
            default=ulnl,
        ).strip()
        state.env_set("ULTRON_NL_COMMANDS", raw)


def _dig(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _ensure_nested(d: dict[str, Any], *keys: str) -> dict[str, Any]:
    cur = d
    for k in keys:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    return cur


def section_discord_server(q: Any, state: WizardState) -> None:
    state.ensure_yaml()
    print("\n--- Discord server & channels ---\n")
    gid = state.env_get("DISCORD_GUILD_ID")
    print(
        "DISCORD_GUILD_ID: optional; if unset, slash commands default to guild 788074756044750891; "
        "set another id, or 0 / global for global sync.\n"
        f"Current: {gid or '(empty)'}\n"
    )
    if _yn(q, "Edit guild (server) ID?", default=False):
        raw = _text(q, "DISCORD_GUILD_ID (numeric or empty)", default=gid).strip()
        state.env_set("DISCORD_GUILD_ID", raw)

    y = state.yaml_data
    reg = _ensure_nested(y, "discord", "registration_log")
    feat = _ensure_nested(reg, "features")
    rep = _ensure_nested(y, "reports")

    print("Registration log channel (whitelist/startup events in Discord):")
    print(f"  enabled: {_dig(y, 'discord', 'registration_log', 'enabled')}")
    print(f"  channel_id: {_dig(y, 'discord', 'registration_log', 'channel_id')}\n")
    if _yn(q, "Edit registration_log settings?", default=False):
        en = _yn(q, "Enable registration_log?", default=bool(_dig(y, "discord", "registration_log", "enabled")))
        reg["enabled"] = en
        cid = _text(
            q,
            "registration_log channel_id (integer, 0 to disable posting)",
            default=str(_dig(y, "discord", "registration_log", "channel_id") or "0"),
        ).strip()
        try:
            reg["channel_id"] = int(cid) if cid else 0
        except ValueError:
            print("Invalid channel_id; skipped.")
        reg.setdefault("features", {})
        feat["startup"] = _yn(q, "Log bot startup to this channel?", default=True)
        feat["whitelist_events"] = _yn(q, "Log /token and /approve to this channel?", default=True)

    print(f"\nReports channel (reports.channel_id): {_dig(y, 'reports', 'channel_id')}\n")
    if _yn(q, "Edit reports.channel_id?", default=False):
        cid = _text(q, "reports.channel_id (integer, 0 = disabled)", default=str(_dig(y, "reports", "channel_id") or "0")).strip()
        try:
            rep["channel_id"] = int(cid) if cid else 0
        except ValueError:
            print("Invalid channel_id; skipped.")
    rep.setdefault("startup_message_enabled", True)
    print(f"reports.startup_message_enabled: {rep.get('startup_message_enabled')}\n")
    if _yn(q, "Toggle reports.startup_message_enabled (welcome post when bot connects)?", default=False):
        rep["startup_message_enabled"] = _yn(q, "Post startup summary to reports channel?", default=bool(rep.get("startup_message_enabled", True)))
    if _yn(q, "Edit reports.startup_welcome (first line; empty = default text)?", default=False):
        rep["startup_welcome"] = _text(
            q,
            "startup_welcome (optional)",
            default=str(rep.get("startup_welcome") or ""),
        ).strip()


def section_admins(q: Any, state: WizardState) -> None:
    print("\n--- Admins & owner contact ---\n")
    adm = state.env_get("DISCORD_ADMIN_IDS")
    print(f"DISCORD_ADMIN_IDS: {adm or '(empty)'} — comma or space separated Discord user IDs\n")
    if _yn(q, "Edit DISCORD_ADMIN_IDS?", default=False):
        state.env_set("DISCORD_ADMIN_IDS", _text(q, "DISCORD_ADMIN_IDS", default=adm).strip())
    bc = state.env_get("BOT_OWNER_CONTACT")
    print(f"BOT_OWNER_CONTACT: {bc or '(empty)'}\n")
    if _yn(q, "Edit BOT_OWNER_CONTACT (shown to non-whitelisted users)?", default=False):
        state.env_set("BOT_OWNER_CONTACT", _text(q, "BOT_OWNER_CONTACT", default=bc).strip())

    sd = Path(state.env_get("ULTRON_STATE_DIR", "data")).expanduser()
    admins_path = sd / "admins.json"
    existing: list[int] = []
    if admins_path.is_file():
        try:
            raw = json.loads(admins_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = [int(x) for x in raw if str(x).isdigit()]
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    print(f"admins.json ({admins_path}): {existing or '(missing or empty)'}\n")
    if _yn(q, "Replace admins.json with a single primary admin user ID?", default=False):
        print(
            "(Note: this writes **admins.json** immediately on disk. Other wizard changes apply only on **Review & save**.)"
        )
        raw = _text(q, "Primary admin Discord user ID", default=str(existing[0]) if existing else "").strip()
        if raw.isdigit():
            admins_path.parent.mkdir(parents=True, exist_ok=True)
            admins_path.write_text(json.dumps([int(raw)], indent=2) + "\n", encoding="utf-8")
            print(f"Wrote {admins_path}\n")
        else:
            print("Invalid ID; skipped.\n")


def section_llm(q: Any, state: WizardState) -> None:
    state.ensure_yaml()
    print("\n--- Language model (llm_chain + API keys in .env) ---\n")
    print(
        "Ultron enables the LLM only when config.yaml has at least one enabled top-level llm_chain entry "
        "(not under discord). Each entry sets base_url, model, and api_key_env — the literal name of an "
        "environment variable that holds that provider's API key (set it in .env; not remapped by "
        "environment_bindings). Use only one top-level llm_chain key; YAML duplicate keys keep the last value. "
        "Optional llm_provider / llm_model on slash commands list configured slots and models when the chain is "
        "non-empty. discord.slash_show_llm_option_hints enriches autocomplete labels when true.\n"
    )
    dis = state.env_get("LLM_DISABLED") or state.env_get("ULTRON_NO_LLM")
    print(f"LLM_DISABLED / ULTRON_NO_LLM: {dis or '(unset)'}\n")
    if _yn(q, "Force NO language model (LLM_DISABLED=1)?", default=False):
        state.env_set("LLM_DISABLED", "1")
    elif _yn(q, "Clear LLM_DISABLED / ULTRON_NO_LLM (allow llm_chain when configured)?", default=False):
        state.env.pop("LLM_DISABLED", None)
        state.env.pop("ULTRON_NO_LLM", None)

    if _yn(q, "Edit a common API key variable in .env (by name)?", default=False):
        var = _text(
            q,
            "Environment variable name (e.g. LLM_API_KEY, OPENAI_API_KEY)",
            default="LLM_API_KEY",
        ).strip()
        if var:
            cur = state.env_get(var) or ""
            state.env_set(var, _text(q, f"Value for {var}", default=cur).strip())
            print(f"{var}: {mask_secret(var, state.env_get(var) or '')}\n")

    y = state.yaml_data
    chain = y.get("llm_chain")
    print(f"config llm_chain: {chain if chain is not None else '[]'}\n")
    llm_opts = [
        "Skip",
        "Clear llm_chain (set [] — no LLM until you add entries again)",
        "Append one provider (advanced)",
    ]
    qu = patch_question_with_return(
        q.select(
            "llm_chain editing — press 1–3 or arrows, Enter to confirm",
            choices=llm_opts,
            use_shortcuts=True,
            use_arrow_keys=True,
            qmark=">",
            instruction="(r)",
        )
    )
    try:
        choice = ask(qu)
    except ReturnToMenu:
        raise
    if choice is None or choice == llm_opts[0]:
        return
    if choice == llm_opts[1]:
        y["llm_chain"] = []
    elif choice == llm_opts[2]:
        model_raw = _text(
            q,
            "model(s) — one id, or comma-separated (first = primary / default)",
            default="",
        ).strip()
        model_parts = [p.strip() for p in model_raw.split(",") if p.strip()]
        if len(model_parts) == 1:
            model_yaml: str | list[str] = model_parts[0]
        elif len(model_parts) > 1:
            model_yaml = model_parts
        else:
            model_yaml = ""
        entry = {
            "name": _text(q, "Optional name", default="").strip() or None,
            "enabled": True,
            "base_url": _text(q, "base_url (http(s) ... /v1)", default="").strip(),
            "model": model_yaml,
            "api_key_env": _text(q, "api_key_env (env var name holding the API key)", default="OPENAI_API_KEY").strip(),
            "timeout_seconds": None,
            "max_retries": None,
        }
        if entry["base_url"] and entry["model"] and entry["api_key_env"]:
            lst = y.get("llm_chain")
            if not isinstance(lst, list):
                lst = []
            # strip None name for yaml cleanliness
            if entry["name"] is None:
                del entry["name"]
            lst.append(entry)
            y["llm_chain"] = lst
        else:
            print("Incomplete entry; skipped.\n")


def section_yaml_behavior(q: Any, state: WizardState) -> None:
    state.ensure_yaml()
    print("\n--- YAML: timezone, Discord behavior, schedules ---\n")
    y = state.yaml_data
    tz = str(y.get("timezone") or "")
    if _yn(q, f"Edit timezone? (current: {tz or 'default UTC'})", default=False):
        y["timezone"] = _text(q, "timezone (e.g. UTC, Europe/Madrid)", default=tz).strip()

    d = _ensure_nested(y, "discord")
    if _yn(q, "Edit discord.ephemeral_default?", default=False):
        d["ephemeral_default"] = _yn(q, "Ephemeral slash replies by default?", default=True)

    imh_cur = d.get("issue_metadata_header")
    if imh_cur is None:
        imh_desc = "null (runtime default: true)"
        imh_default_yn = True
    else:
        imh_desc = repr(imh_cur)
        imh_default_yn = bool(imh_cur)
    print(
        "discord.issue_metadata_header — Prepend journal note count, spent hours, and last update to "
        "/summary and /ask_issue (and include the same line in the LLM ticket payload).\n"
        f"  Current YAML value: {imh_desc}\n"
    )
    if _yn(q, "Edit discord.issue_metadata_header?", default=False):
        d["issue_metadata_header"] = _yn(q, "Enable issue metadata header?", default=imh_default_yn)

    ssh_cur = d.get("slash_show_llm_option_hints")
    if ssh_cur is None:
        ssh_desc = "null (runtime default: false)"
        ssh_default_yn = False
    else:
        ssh_desc = repr(ssh_cur)
        ssh_default_yn = bool(ssh_cur)
    print(
        "discord.slash_show_llm_option_hints — Richer llm_provider autocomplete (endpoint when multiple "
        "slots) and concise llm_model option text; model lists come from autocomplete, not the tooltip.\n"
        f"  Current YAML value: {ssh_desc}\n"
    )
    if _yn(q, "Edit discord.slash_show_llm_option_hints?", default=False):
        d["slash_show_llm_option_hints"] = _yn(
            q, "Show LLM provider/model hints on slash commands?", default=ssh_default_yn
        )

    nl_cur = d.get("nl_commands")
    if nl_cur is None:
        nl_desc = "null (runtime default: true)"
        nl_default_yn = True
    else:
        nl_desc = repr(nl_cur)
        nl_default_yn = bool(nl_cur)
    print(
        "discord.nl_commands — LLM routes @mention / reply-to-bot messages to allowed commands (requires a configured LLM).\n"
        f"  Current YAML value: {nl_desc}\n"
    )
    if _yn(q, "Edit discord.nl_commands?", default=False):
        d["nl_commands"] = _yn(q, "Enable natural-language @mention routing?", default=nl_default_yn)

    ni = _ensure_nested(d, "new_issues")
    print(f"new_issues: {ni}\n")
    if _yn(q, "Edit /list_new_issues (status name, limits)?", default=False):
        ni["status_name"] = _text(q, "new_issues.status_name (exact Redmine status)", default=str(ni.get("status_name") or "")).strip()
        lim = _text(q, "new_issues.list_limit", default=str(ni.get("list_limit") or "20")).strip()
        age = _text(q, "new_issues.min_age_days", default=str(ni.get("min_age_days") or "2")).strip()
        try:
            ni["list_limit"] = int(lim)
            ni["min_age_days"] = int(age)
        except ValueError:
            print("Invalid integer; skipped.")

    uo = _ensure_nested(d, "unassigned_open")
    print(f"unassigned_open (/list_unassigned_issues): {uo}\n")
    if _yn(q, "Edit /list_unassigned_issues (unassigned_open: min age, list cap, closed status prefixes)?", default=False):
        lim = _text(
            q,
            "unassigned_open.list_limit",
            default=str(uo.get("list_limit") if uo.get("list_limit") is not None else "20"),
        ).strip()
        age = _text(
            q,
            "unassigned_open.min_age_days",
            default=str(uo.get("min_age_days") if uo.get("min_age_days") is not None else "1"),
        ).strip()
        try:
            uo["list_limit"] = int(lim)
            uo["min_age_days"] = int(age)
        except ValueError:
            print("Invalid integer; skipped.")
        existing_prefs = uo.get("closed_status_prefixes")
        if isinstance(existing_prefs, list):
            pref_default = ", ".join(str(x) for x in existing_prefs if str(x).strip())
        else:
            pref_default = ""
        pref_raw = _text(
            q,
            "closed_status_prefixes (comma-separated; status names starting with these are excluded, case-insensitive)",
            default=pref_default,
        ).strip()
        uo["closed_status_prefixes"] = [p.strip() for p in pref_raw.split(",") if p.strip()]

    rs = y.get("report_schedule")
    if not isinstance(rs, list):
        rs = []
        y["report_schedule"] = rs
    print(f"report_schedule (scheduled channel commands): {rs}\n")
    print(
        "Each entry: enabled, command (list_new_issues | list_unassigned_issues | issues_by_status), "
        "interval_hours or interval_days, args (issues_by_status needs args.status).\n"
    )
    if _yn(q, "Set report_schedule template (list_new_issues + list_unassigned_issues, each every 8h)?", default=False):
        y["report_schedule"] = [
            {"enabled": True, "command": "list_new_issues", "interval_hours": 8, "args": {}},
            {"enabled": True, "command": "list_unassigned_issues", "interval_hours": 8, "args": {}},
        ]
        print("Updated report_schedule.\n")
    if _yn(q, "Clear report_schedule (no scheduled channel jobs)?", default=False):
        y["report_schedule"] = []
        print("Cleared report_schedule.\n")

    log_cfg = _ensure_nested(y, "logging")
    if _yn(q, "Edit logging.log_read_messages (debug; may log secrets)?", default=False):
        log_cfg["log_read_messages"] = _yn(q, "log_read_messages", default=False)

    if _yn(q, "Edit custom Discord status strings (summary_status_*, llm_chain messages)?", default=False):
        d["summary_status_redmine"] = _text(
            q, "summary_status_redmine", default=str(d.get("summary_status_redmine") or "")
        ).strip()
        d["summary_status_llm"] = _text(
            q, "summary_status_llm (use {model})", default=str(d.get("summary_status_llm") or "")
        ).strip()
        d["llm_chain_skip_status"] = _text(
            q, "llm_chain_skip_status", default=str(d.get("llm_chain_skip_status") or "")
        ).strip()
        d["llm_chain_all_failed_message"] = _text(
            q, "llm_chain_all_failed_message", default=str(d.get("llm_chain_all_failed_message") or "")
        ).strip()
