#!/usr/bin/env python3
"""Connectivity check: Redmine + Ollama (no Discord). Run from repo root: python scripts/smoke_check.py"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv


def main() -> int:
    load_dotenv(ROOT / ".env")
    redmine_url = os.environ.get("REDMINE_URL", "").strip().rstrip("/")
    redmine_key = os.environ.get("REDMINE_API_KEY", "").strip()
    ollama_base = os.environ.get("OLLAMA_API_BASE", "").strip().rstrip("/")
    llm_base = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")
    if not llm_base and ollama_base:
        llm_base = ollama_base if ollama_base.endswith("/v1") else f"{ollama_base}/v1"
    model = os.environ.get("LLM_MODEL", "").strip() or os.environ.get("OLLAMA_MODEL", "").strip()
    llm_key = os.environ.get("LLM_API_KEY", "").strip() or ("ollama" if ollama_base or ":11434" in llm_base else "")

    ok = True
    if not redmine_url or not redmine_key:
        print("SKIP Redmine: REDMINE_URL / REDMINE_API_KEY missing")
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

    if not llm_base or not model or not llm_key:
        print("SKIP LLM: OLLAMA_* / LLM_* not enough to test")
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
