from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def ollama_root_url(base_url: str) -> str:
    """Strip OpenAI-compat ``/v1`` suffix for native Ollama HTTP endpoints."""
    root = base_url.strip().rstrip("/")
    if root.endswith("/v1"):
        return root[:-3]
    return root


def ollama_openai_base_url(base_url: str) -> str:
    """Base URL for pi ``models.json`` (OpenAI-compatible completions)."""
    root = base_url.strip().rstrip("/")
    if root.endswith("/v1"):
        return root
    return f"{root}/v1"


def _ollama_tags_url(base_url: str) -> str:
    return f"{ollama_root_url(base_url).rstrip('/')}/api/tags"


def _check_ollama_sync(base_url: str, *, timeout: float) -> bool:
    req = urllib.request.Request(_ollama_tags_url(base_url), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


async def _check_ollama(base_url: str, *, timeout: float) -> bool:
    return await asyncio.to_thread(_check_ollama_sync, base_url, timeout=timeout)


async def _run_tunnel_script(script: Path) -> tuple[bool, str]:
    if not script.is_file():
        return False, f"Tunnel script not found: {script}"
    if not os.access(script, os.X_OK):
        return False, f"Tunnel script is not executable: {script}"

    proc = await asyncio.create_subprocess_exec(
        str(script),
        "start",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=os.environ.copy(),
    )
    out_b, _ = await proc.communicate()
    output = out_b.decode("utf-8", errors="replace").strip()
    return proc.returncode == 0, output


async def ensure_ollama_reachable(
    base_url: str,
    *,
    tunnel_script: Path | None = None,
    connect_timeout_seconds: float = 5.0,
    connect_retries: int = 5,
    connect_retry_delay_seconds: float = 2.0,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[bool, bool]:
    """Return ``(reachable, tunnel_script_ran)``."""
    probe_timeout = min(5.0, float(connect_timeout_seconds))
    tunnel_script_ran = False

    async def progress(msg: str) -> None:
        if on_progress is not None:
            await on_progress(msg)

    await progress("Checking Ollama connection…")
    if await _check_ollama(base_url, timeout=probe_timeout):
        return True, False

    if tunnel_script is None:
        return False, False

    await progress("Ollama not responding — starting SSH tunnel…")
    tunnel_script_ran = True
    tunnel_ok, tunnel_out = await _run_tunnel_script(tunnel_script)
    if not tunnel_ok:
        logger.warning("ollama tunnel script failed: %s", tunnel_out[:500])

    retries = max(1, int(connect_retries))
    for attempt in range(1, retries + 1):
        if attempt > 1:
            await progress(f"Retrying connection ({attempt}/{retries})…")
        if await _check_ollama(base_url, timeout=probe_timeout):
            return True, tunnel_script_ran
        if attempt < retries:
            await asyncio.sleep(float(connect_retry_delay_seconds))

    return False, tunnel_script_ran
