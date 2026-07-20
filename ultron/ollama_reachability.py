from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Reasons returned by ``ensure_ollama_ready_for_inference`` when ``ok`` is False.
REASON_UNREACHABLE = "ollama_unreachable"
REASON_BUSY_PS = "ollama_busy"
REASON_BUSY_PROBE = "ollama_busy"
REASON_PROBE_ERROR = "ollama_probe_failed"


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


def _ollama_ps_url(base_url: str) -> str:
    return f"{ollama_root_url(base_url).rstrip('/')}/api/ps"


def _ollama_generate_url(base_url: str) -> str:
    return f"{ollama_root_url(base_url).rstrip('/')}/api/generate"


def _check_ollama_sync(base_url: str, *, timeout: float) -> bool:
    req = urllib.request.Request(_ollama_tags_url(base_url), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


async def _check_ollama(base_url: str, *, timeout: float) -> bool:
    return await asyncio.to_thread(_check_ollama_sync, base_url, timeout=timeout)


def _fetch_json_sync(url: str, *, timeout: float, data: bytes | None = None) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        status = int(resp.status)
    if not body.strip():
        return status, None
    return status, json.loads(body)


def fetch_ollama_ps_sync(base_url: str, *, timeout: float = 5.0) -> dict[str, Any] | None:
    """Return parsed ``/api/ps`` JSON, or ``None`` on transport/parse failure."""
    try:
        status, payload = _fetch_json_sync(_ollama_ps_url(base_url), timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("ollama /api/ps failed: %s", e)
        return None
    if not (200 <= status < 300) or not isinstance(payload, dict):
        return None
    return payload


def ollama_ps_has_loaded_models(payload: dict[str, Any] | None) -> bool:
    """True when ``/api/ps`` lists at least one model kept in memory.

    Loaded ≠ actively generating; use the inference probe for contention.
    Enable ``busy_if_models_loaded`` only when a shared Ollama must be vacated.
    """
    if not payload:
        return False
    models = payload.get("models")
    if not isinstance(models, list):
        return False
    return len(models) > 0


def ollama_is_busy_from_ps(
    base_url: str,
    *,
    timeout: float = 5.0,
    busy_if_models_loaded: bool = False,
) -> bool:
    """Optional aggressive busy signal from ``GET /api/ps`` (loaded models)."""
    if not busy_if_models_loaded:
        return False
    return ollama_ps_has_loaded_models(fetch_ollama_ps_sync(base_url, timeout=timeout))


async def ollama_is_busy(
    base_url: str,
    *,
    timeout: float = 5.0,
    busy_if_models_loaded: bool = False,
) -> bool:
    return await asyncio.to_thread(
        ollama_is_busy_from_ps,
        base_url,
        timeout=timeout,
        busy_if_models_loaded=busy_if_models_loaded,
    )


def _inference_probe_sync(
    base_url: str,
    *,
    model: str,
    timeout: float,
) -> tuple[bool, str | None]:
    """Tiny ``/api/generate``; ``(ok, reason_or_none)``."""
    payload = {
        "model": model,
        "prompt": "ping",
        "stream": False,
        "options": {"num_predict": 1},
    }
    raw = json.dumps(payload).encode("utf-8")
    try:
        status, _body = _fetch_json_sync(_ollama_generate_url(base_url), timeout=timeout, data=raw)
    except TimeoutError:
        return False, REASON_BUSY_PROBE
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            return False, REASON_BUSY_PROBE
        return False, REASON_PROBE_ERROR
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return False, REASON_PROBE_ERROR
    if status >= 500:
        return False, REASON_BUSY_PROBE
    if not (200 <= status < 300):
        return False, REASON_PROBE_ERROR
    return True, None


async def inference_probe(
    base_url: str,
    *,
    model: str,
    timeout: float,
) -> tuple[bool, str | None]:
    return await asyncio.to_thread(
        _inference_probe_sync,
        base_url,
        model=model,
        timeout=timeout,
    )


@dataclass(frozen=True)
class OllamaReadiness:
    ok: bool
    reason: str | None = None
    tunnel_script_ran: bool = False


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


async def ensure_ollama_ready_for_inference(
    base_url: str,
    *,
    model: str = "",
    tunnel_script: Path | None = None,
    connect_timeout_seconds: float = 5.0,
    connect_retries: int = 5,
    connect_retry_delay_seconds: float = 2.0,
    busy_check: bool = True,
    busy_if_models_loaded: bool = False,
    inference_probe_seconds: float = 12.0,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> OllamaReadiness:
    """Reachability plus optional busy checks and a short inference probe.

    When ``busy_check`` is true (default):

    - Optional ``busy_if_models_loaded``: non-empty ``/api/ps`` ⇒ busy (aggressive;
      idle loaded models also trip this).
    - ``inference_probe_seconds`` > 0 and ``model`` set: tiny ``/api/generate``;
      timeout or 5xx ⇒ busy / unavailable for inference.

    Returns ``OllamaReadiness(ok=True)`` when Ultron should start a long Ollama job.
    When ``ok`` is False, ``reason`` is one of ``ollama_unreachable``, ``ollama_busy``,
    or ``ollama_probe_failed``.
    """

    async def progress(msg: str) -> None:
        if on_progress is not None:
            await on_progress(msg)

    reachable, tunnel_ran = await ensure_ollama_reachable(
        base_url,
        tunnel_script=tunnel_script,
        connect_timeout_seconds=connect_timeout_seconds,
        connect_retries=connect_retries,
        connect_retry_delay_seconds=connect_retry_delay_seconds,
        on_progress=on_progress,
    )
    if not reachable:
        return OllamaReadiness(ok=False, reason=REASON_UNREACHABLE, tunnel_script_ran=tunnel_ran)

    if not busy_check:
        return OllamaReadiness(ok=True, reason=None, tunnel_script_ran=tunnel_ran)

    ps_timeout = min(5.0, float(connect_timeout_seconds))
    if busy_if_models_loaded:
        await progress("Checking whether Ollama has loaded models…")
        if await ollama_is_busy(
            base_url,
            timeout=ps_timeout,
            busy_if_models_loaded=True,
        ):
            logger.info(
                "ollama busy: /api/ps reports loaded model(s) at %s",
                ollama_root_url(base_url),
            )
            return OllamaReadiness(ok=False, reason=REASON_BUSY_PS, tunnel_script_ran=tunnel_ran)

    probe_secs = float(inference_probe_seconds)
    if probe_secs > 0 and model.strip():
        await progress(f"Probing Ollama inference (≤{probe_secs:.0f}s)…")
        ok, reason = await inference_probe(base_url, model=model.strip(), timeout=probe_secs)
        if not ok:
            logger.info(
                "ollama inference probe failed (%s) at %s model=%s",
                reason,
                ollama_root_url(base_url),
                model.strip(),
            )
            return OllamaReadiness(
                ok=False,
                reason=reason or REASON_PROBE_ERROR,
                tunnel_script_ran=tunnel_ran,
            )

    return OllamaReadiness(ok=True, reason=None, tunnel_script_ran=tunnel_ran)
