from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ultron.config import EnvironmentBindings, load_config

# When guild binding is unset or empty, slash commands sync to this guild (immediate updates).
_DEFAULT_DISCORD_GUILD_SLASH_SYNC_ID = 788074756044750891

# Bootstrap: only this name is read without going through config.yaml.
_CONFIG_PATH_ENV = "CONFIG_PATH"


def _config_file_has_llm_chain(config_path: str) -> bool:
    """True when the config file parses and has at least one enabled `llm_chain` entry."""
    p = Path(config_path.strip() or "config.yaml")
    if not p.is_file():
        return False
    try:
        return load_config(p).llm_chain is not None
    except Exception:
        return False


@dataclass(frozen=True)
class EnvSettings:
    discord_token: str
    discord_guild_id: int | None
    discord_application_id: int | None
    redmine_url: str
    redmine_api_key: str
    #: False when no LLM is configured (slash commands and Redmine still work; ``/summary`` / ``/note`` need a model).
    llm_enabled: bool
    #: First chain entry base URL when ``llm_chain`` is active (display only; secrets come from ``api_key_env``).
    llm_base_url: str
    llm_api_key: str
    #: First chain entry default model when ``llm_chain`` is active (display only).
    llm_model: str
    config_path: str
    state_dir: Path
    bot_owner_contact: str | None
    discord_admin_ids: frozenset[int]
    #: When True, request privileged Message Content + guild/DM message intents (must match Developer Portal).
    discord_message_content_intent: bool
    #: When True, env override enables natural-language routing for @mention (merged with YAML ``discord.nl_commands``).
    ultron_nl_commands: bool
    #: Which environment variable names were read (from ``config.yaml`` ``environment_bindings``).
    environment_bindings: EnvironmentBindings


def _get_env(var_name: str) -> str:
    return os.environ.get(var_name, "").strip()


def _opt_int(var_name: str) -> int | None:
    v = _get_env(var_name)
    if not v:
        return None
    return int(v)


def _discord_guild_id_for_slash_sync(var_name: str) -> int | None:
    """Unset → team default guild; ``0`` / ``global`` → None (global sync)."""
    raw = _get_env(var_name).lower()
    if not raw:
        return _DEFAULT_DISCORD_GUILD_SLASH_SYNC_ID
    if raw in ("0", "global"):
        return None
    return int(raw)


def _env_flag_enabled(var_name: str) -> bool:
    v = _get_env(var_name).lower()
    return v in ("1", "true", "yes", "on")


def _parse_discord_admin_ids(var_name: str) -> frozenset[int]:
    raw = _get_env(var_name)
    if not raw:
        return frozenset()
    out: list[int] = []
    for part in raw.replace(",", " ").split():
        p = part.strip()
        if not p:
            continue
        try:
            out.append(int(p))
        except ValueError:
            continue
    return frozenset(out)


def load_env(*, require_discord: bool = True, require_redmine: bool = True) -> EnvSettings:
    config_path = os.environ.get(_CONFIG_PATH_ENV, "config.yaml").strip() or "config.yaml"
    cfg_file = Path(config_path)
    if not cfg_file.is_file():
        raise RuntimeError(
            f"Config file not found: {cfg_file.resolve()}. Set {_CONFIG_PATH_ENV!r} or create config.yaml."
        )
    try:
        app_cfg = load_config(cfg_file)
    except ValueError as e:
        raise RuntimeError(f"Invalid config: {e}") from e

    b = app_cfg.environment_bindings

    token = _get_env(b.discord_token_env)
    if require_discord and not token:
        raise RuntimeError(
            f"Discord token is required (environment variable {b.discord_token_env!r} from config environment_bindings)."
        )

    redmine_url = _get_env(b.redmine_url_env).rstrip("/")
    if require_redmine and not redmine_url:
        raise RuntimeError(
            f"Redmine URL is required (environment variable {b.redmine_url_env!r} from config environment_bindings)."
        )

    redmine_key = _get_env(b.redmine_api_key_env)
    if require_redmine and not redmine_key:
        raise RuntimeError(
            f"Redmine API key is required (environment variable {b.redmine_api_key_env!r} from config environment_bindings)."
        )

    has_chain = app_cfg.llm_chain is not None
    llm_disabled_flag = _env_flag_enabled(b.llm_disabled_env) or _env_flag_enabled(b.ultron_no_llm_env)
    if llm_disabled_flag and has_chain:
        raise RuntimeError(
            f"{b.llm_disabled_env} or {b.ultron_no_llm_env} is set but config.yaml defines llm_chain. "
            "Remove or disable llm_chain entries, or unset those variables."
        )

    llm_enabled = bool(has_chain and not llm_disabled_flag)
    if llm_enabled:
        assert app_cfg.llm_chain is not None
        first = app_cfg.llm_chain[0]
        llm_base_url = first.base_url
        llm_model = first.model
        llm_api_key = ""
    else:
        llm_base_url = ""
        llm_api_key = ""
        llm_model = "(none)"

    state_dir_raw = _get_env(b.ultron_state_dir_env) or "data"
    state_dir = Path(state_dir_raw).expanduser().resolve()

    bot_raw = _get_env(b.bot_owner_contact_env)
    bot_owner_contact = bot_raw or None

    return EnvSettings(
        discord_token=token,
        discord_guild_id=_discord_guild_id_for_slash_sync(b.discord_guild_id_env),
        discord_application_id=_opt_int(b.discord_application_id_env),
        redmine_url=redmine_url,
        redmine_api_key=redmine_key,
        llm_enabled=llm_enabled,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        config_path=config_path,
        state_dir=state_dir,
        bot_owner_contact=bot_owner_contact,
        discord_admin_ids=_parse_discord_admin_ids(b.discord_admin_ids_env),
        discord_message_content_intent=_env_flag_enabled(b.discord_message_content_intent_env),
        ultron_nl_commands=_env_flag_enabled(b.ultron_nl_commands_env),
        environment_bindings=b,
    )
