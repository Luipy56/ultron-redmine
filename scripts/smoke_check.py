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

    if not env.llm_enabled or not env.llm_base_url.strip():
        print("SKIP LLM: not configured (same rules as bot load_env)")
        return 0 if ok else 1

    llm_base = env.llm_base_url.rstrip("/")
    model = env.llm_model
    llm_key = env.llm_api_key
    if not llm_base or not model or not llm_key or model == "(none)":
        print("SKIP LLM: incomplete LLM settings")
        return 0 if ok else 1

    async def llm_ping() -> None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=llm_base, api_key=llm_key, timeout=60.0)
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": 'Reply with exactly: pong'}],
        )
        text = (resp.choices[0].message.content or "").strip()
        print(f"OK LLM: model={model!r} reply={text!r}")

    try:
        asyncio.run(llm_ping())
    except Exception as e:
        print(f"FAIL LLM: {e}")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
