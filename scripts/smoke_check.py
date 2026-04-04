#!/usr/bin/env python3
"""Connectivity check: Redmine + Ollama (no Discord). Run from repo root: python scripts/smoke_check.py"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv


def main() -> int:
    load_dotenv(ROOT / ".env")
    try:
        from ultron.settings import load_env

        env = load_env()
    except RuntimeError as e:
        print(f"FAIL bootstrap: {e}")
        return 1

    redmine_url = env.redmine_url.rstrip("/")
    redmine_key = env.redmine_api_key

    ok = True
    if not redmine_url or not redmine_key:
        print("SKIP Redmine: missing URL or API key (via config environment_bindings)")
        ok = False
    else:
        import httpx

        try:
            r = httpx.get(
                f"{redmine_url}/issues.json",
                params={"limit": 1},
                headers={"X-Redmine-API-Key": redmine_key},
                timeout=30.0,
            )
            r.raise_for_status()
            n = len(r.json().get("issues", []))
            print(f"OK Redmine: GET /issues.json limit=1 -> {r.status_code}, issues in page: {n}")
        except Exception as e:
            print(f"FAIL Redmine: {e}")
            ok = False

    if not env.llm_enabled:
        print("SKIP LLM: not configured (no enabled llm_chain in config.yaml)")
        return 0 if ok else 1

    async def llm_ping() -> None:
        from ultron.config import load_config
        from ultron.llm import LLMChainClient, NullLLMBackend, format_llm_endpoint
        from ultron.startup_llm import build_llm_backend

        cfg_path = Path(env.config_path).expanduser()
        if not cfg_path.is_file():
            print(f"SKIP LLM: config file not found ({cfg_path})")
            return
        cfg = load_config(cfg_path)
        built = build_llm_backend(env, cfg)
        llm = built.backend
        if isinstance(llm, NullLLMBackend):
            print("SKIP LLM: not configured")
            return
        if not isinstance(llm, LLMChainClient):
            print(f"SKIP LLM: unexpected backend {type(llm).__name__}")
            return
        await llm.ping_primary()
        ep = format_llm_endpoint(llm.primary_base_url)
        print(f"OK LLM: chain primary model={llm.model!r} @ {ep}")

    try:
        asyncio.run(llm_ping())
    except Exception as e:
        print(f"FAIL LLM: {e}")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
