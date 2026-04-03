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
    url = state.env_get("REDMINE_URL")
    key = state.env_get("REDMINE_API_KEY")
    print(f"REDMINE_URL: {url or '(empty)'}")
    print(f"REDMINE_API_KEY: {mask_secret('REDMINE_API_KEY', key)}\n")
    if _yn(q, "Edit Redmine URL?", default=not bool(url)):
        state.env_set("REDMINE_URL", _text(q, "REDMINE_URL (no trailing slash)", default=url).strip().rstrip("/"))
    if _yn(q, "Edit Redmine API key?", default=not bool(key)):
        state.env_set("REDMINE_API_KEY", _text(q, "REDMINE_API_KEY", default="").strip())
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
        "DISCORD_GUILD_ID: optional; if set, slash commands sync to this guild immediately.\n"
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

    print(f"\nScheduled reports channel (reports.channel_id): {_dig(y, 'reports', 'channel_id')}\n")
    if _yn(q, "Edit reports.channel_id?", default=False):
        cid = _text(q, "reports.channel_id (integer, 0 = disabled)", default=str(_dig(y, "reports", "channel_id") or "0")).strip()
        try:
            rep["channel_id"] = int(cid) if cid else 0
        except ValueError:
            print("Invalid channel_id; skipped.")


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
    print("\n--- Language model (.env + llm_chain) ---\n")
    print(
        "Ultron can run without an LLM (omit API key and llm_chain) or use a single provider from .env "
        "or an ordered llm_chain in config.yaml.\n"
    )
    dis = state.env_get("LLM_DISABLED") or state.env_get("ULTRON_NO_LLM")
    print(f"LLM_DISABLED / ULTRON_NO_LLM: {dis or '(unset)'}\n")
    if _yn(q, "Force NO language model (LLM_DISABLED=1)?", default=False):
        state.env_set("LLM_DISABLED", "1")
    elif _yn(q, "Clear LLM_DISABLED (enable LLM from env when keys are set)?", default=False):
        state.env.pop("LLM_DISABLED", None)
        state.env.pop("ULTRON_NO_LLM", None)

    print("Single-provider (.env) — leave empty to rely on llm_chain or no LLM:\n")
    print(f"  LLM_BASE_URL: {state.env_get('LLM_BASE_URL')}")
    print(f"  LLM_API_KEY: {mask_secret('LLM_API_KEY', state.env_get('LLM_API_KEY'))}")
    print(f"  LLM_MODEL: {state.env_get('LLM_MODEL')}")
    print(f"  OLLAMA_API_BASE: {state.env_get('OLLAMA_API_BASE')}\n")

    if _yn(q, "Edit single-provider LLM variables?", default=False):
        state.env_set("LLM_BASE_URL", _text(q, "LLM_BASE_URL", default=state.env_get("LLM_BASE_URL")).strip())
        state.env_set("LLM_API_KEY", _text(q, "LLM_API_KEY", default="").strip())
        state.env_set("LLM_MODEL", _text(q, "LLM_MODEL", default=state.env_get("LLM_MODEL")).strip())
        state.env_set("OLLAMA_API_BASE", _text(q, "OLLAMA_API_BASE (optional)", default=state.env_get("OLLAMA_API_BASE")).strip())
        to = _text(q, "LLM_TIMEOUT_SECONDS", default=state.env_get("LLM_TIMEOUT_SECONDS")).strip()
        if to:
            state.env_set("LLM_TIMEOUT_SECONDS", to)
        mr = _text(q, "LLM_MAX_RETRIES", default=state.env_get("LLM_MAX_RETRIES")).strip()
        if mr:
            state.env_set("LLM_MAX_RETRIES", mr)

    y = state.yaml_data
    chain = y.get("llm_chain")
    print(f"config llm_chain: {chain if chain is not None else '[]'}\n")
    llm_opts = [
        "Skip",
        "Use empty list (single client from LLM_* in .env)",
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
        entry = {
            "name": _text(q, "Optional name", default="").strip() or None,
            "enabled": True,
            "base_url": _text(q, "base_url (http(s) ... /v1)", default="").strip(),
            "model": _text(q, "model", default="").strip(),
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
    print("\n--- YAML: timezone, slash behavior, schedules ---\n")
    y = state.yaml_data
    tz = str(y.get("timezone") or "")
    if _yn(q, f"Edit timezone? (current: {tz or 'default UTC'})", default=False):
        y["timezone"] = _text(q, "timezone (e.g. UTC, Europe/Madrid)", default=tz).strip()

    d = _ensure_nested(y, "discord")
    if _yn(q, "Edit discord.ephemeral_default?", default=False):
        d["ephemeral_default"] = _yn(q, "Ephemeral slash replies by default?", default=True)

    ni = _ensure_nested(d, "new_issues")
    print(f"new_issues: {ni}\n")
    if _yn(q, "Edit /new_issues (status name, limits)?", default=False):
        ni["status_name"] = _text(q, "new_issues.status_name (exact Redmine status)", default=str(ni.get("status_name") or "")).strip()
        lim = _text(q, "new_issues.list_limit", default=str(ni.get("list_limit") or "20")).strip()
        age = _text(q, "new_issues.min_age_days", default=str(ni.get("min_age_days") or "2")).strip()
        try:
            ni["list_limit"] = int(lim)
            ni["min_age_days"] = int(age)
        except ValueError:
            print("Invalid integer; skipped.")

    uo = _ensure_nested(d, "unassigned_open")
    print(f"unassigned_open (/unassigned_issues): {uo}\n")
    if _yn(q, "Edit /unassigned_issues (unassigned_open: min age, list cap, closed status prefixes)?", default=False):
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

    ab = _ensure_nested(y, "schedules", "abandoned")
    print(f"schedules.abandoned: {ab}\n")
    if _yn(q, "Edit schedules.abandoned?", default=False):
        ab["enabled"] = _yn(q, "abandoned.enabled?", default=ab.get("enabled") is not False)
        for fld, dv in (
            ("interval_hours", "24"),
            ("max_days_without_update", "14"),
            ("max_issues", "50"),
        ):
            cur = str(ab.get(fld) if ab.get(fld) is not None else dv)
            raw = _text(q, f"abandoned.{fld}", default=cur).strip()
            if raw:
                try:
                    ab[fld] = int(raw)
                except ValueError:
                    print(f"Invalid {fld}; skipped.")

    sn = _ensure_nested(y, "schedules", "stale_new")
    print(f"schedules.stale_new: {sn}\n")
    if _yn(q, "Edit schedules.stale_new?", default=False):
        sn["enabled"] = _yn(q, "stale_new.enabled?", default=sn.get("enabled") is not False)
        for fld, dv in (
            ("interval_hours", "24"),
            ("min_age_hours", "2"),
            ("max_journal_entries", "1"),
            ("max_issues", "50"),
        ):
            cur = str(sn.get(fld) if sn.get(fld) is not None else dv)
            raw = _text(q, f"stale_new.{fld}", default=cur).strip()
            if raw:
                try:
                    sn[fld] = int(raw)
                except ValueError:
                    print(f"Invalid {fld}; skipped.")
        sn["require_unassigned"] = _yn(q, "stale_new.require_unassigned?", default=bool(sn.get("require_unassigned", True)))
        stn = _text(
            q,
            "stale_new.issue_status_name (empty = all open issues)",
            default=str(sn.get("issue_status_name") or ""),
        ).strip()
        sn["issue_status_name"] = stn or None

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
