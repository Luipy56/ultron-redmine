"""``ultron doctor`` / ``ultron check``: read-only health report (no Discord)."""

from __future__ import annotations

import asyncio
import os
from dataclasses import fields
from pathlib import Path

import httpx

from ultron import __version__
from ultron.config import EnvironmentBindings, load_config
from ultron.llm import LLMChainClient, LLMClient, NullLLMBackend, format_llm_endpoint, safe_exc_message
from ultron.redmine import RedmineClient, RedmineError
from ultron.settings import load_env
from ultron.startup_llm import build_llm_backend

# Env vars that must never print values in the bindings table (only set/unset + masked).
_SECRET_BINDING_FIELDS = frozenset(
    {
        "discord_token_env",
        "redmine_api_key_env",
        "llm_api_key_env",
    }
)


def _binding_value_line(b: EnvironmentBindings, field_name: str, env_var_name: str) -> str:
    raw = os.environ.get(env_var_name, "").strip()
    if not raw:
        return "unset"
    if field_name in _SECRET_BINDING_FIELDS:
        return "set (masked)"
    return f"set ({raw!r})"


def run_doctor() -> int:
    """Print a health report to stdout. Returns a process exit code."""
    cwd = Path.cwd().resolve()
    config_path_raw = os.environ.get("CONFIG_PATH", "config.yaml").strip() or "config.yaml"
    cfg_file = Path(config_path_raw).expanduser()
    cfg_resolved = cfg_file.resolve()

    print(f"Ultron doctor (version {__version__})")
    print()
    print("Paths")
    print(f"  cwd:              {cwd}")
    print(f"  CONFIG_PATH:      {config_path_raw!r}")
    print(f"  config (resolved): {cfg_resolved}")

    if not cfg_file.is_file():
        print()
        print("Config: FAIL (file not found)")
        return 1

    try:
        app_cfg = load_config(cfg_file)
    except ValueError as e:
        print()
        print(f"Config: FAIL ({e})")
        return 1

    print("Config: OK (read and parse)")

    try:
        env = load_env(require_discord=False, require_redmine=False)
    except RuntimeError as e:
        print()
        print(f"Environment: FAIL ({e})")
        return 1

    state_dir = Path(os.environ.get(env.environment_bindings.ultron_state_dir_env, "") or "data")
    state_resolved = state_dir.expanduser().resolve()
    print(f"  state_dir:         {state_resolved} (env {env.environment_bindings.ultron_state_dir_env!r})")

    print()
    print("Environment bindings (names from config; values from process environment)")
    b = env.environment_bindings
    for f in fields(EnvironmentBindings):
        env_name = getattr(b, f.name)
        line = _binding_value_line(b, f.name, env_name)
        print(f"  {f.name}: {env_name!r} -> {line}")

    print()

    async def _pings() -> bool:
        pings_ok = True
        if not env.redmine_url or not env.redmine_api_key:
            print("Redmine: SKIP (URL or API key unset)")
        else:
            client = RedmineClient(base_url=env.redmine_url, api_key=env.redmine_api_key)
            try:
                await client.verify_connection()
                label = await client.fetch_current_user_label()
                print(
                    f"Redmine: OK (api user {label!r} @ {env.redmine_url.rstrip('/')})"
                )
            except RedmineError as e:
                print(f"Redmine: FAIL ({e})")
                pings_ok = False
            except httpx.RequestError as e:
                print(f"Redmine: FAIL (network: {e})")
                pings_ok = False

        try:
            built = build_llm_backend(env, app_cfg)
        except RuntimeError as e:
            print(f"LLM: FAIL ({e})")
            return False

        llm = built.backend
        if isinstance(llm, NullLLMBackend):
            print("LLM: SKIP (not configured)")
            return pings_ok

        try:
            if isinstance(llm, LLMChainClient):
                await llm.ping_primary()
                ep = format_llm_endpoint(llm.primary_base_url)
                print(f"LLM: OK (chain primary model {llm.model!r} @ {ep})")
            elif isinstance(llm, LLMClient):
                await llm.ping_minimal()
                print(f"LLM: OK (model {llm.model!r} @ {format_llm_endpoint(llm.base_url)})")
            else:
                print(f"LLM: SKIP (unknown backend {type(llm).__name__})")
        except Exception as e:
            print(f"LLM: FAIL ({type(e).__name__}: {safe_exc_message(e)})")
            pings_ok = False

        return pings_ok

    pings_ok = asyncio.run(_pings())
    return 0 if pings_ok else 1
