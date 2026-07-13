"""Run pi (pi.dev) against the Ultron workspace with Ollama."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ultron.ollama_reachability import ensure_ollama_reachable, ollama_openai_base_url
from ultron.pi_resolve import (
    PiRunSettings,
    build_pi_run_settings,
    pi_availability_message,
    pi_is_available,
)
from ultron.sanitize import sanitize_for_discord

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT_REL = Path("prompts") / "pi-ops.md"
ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class PiAgentResult:
    session_id: str
    exit_code: int
    stdout: str
    stderr: str
    prompt_path: Path
    workspace: Path
    duration_seconds: float
    model: str
    tunnel_started: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def discord_text(self, *, secret_literals: list[str] | None = None) -> str:
        body = (self.stdout or "").strip()
        if not body and self.stderr.strip():
            body = self.stderr.strip()
        if not body:
            body = f"pi finished with exit code {self.exit_code} and no output."
        if not self.ok:
            err = self.stderr.strip()
            if err and err not in body:
                body = f"{body}\n\n**stderr:**\n```\n{err[:1500]}\n```"
        return sanitize_for_discord(body, secret_literals=secret_literals)


def _resolve_prompt_path(settings: PiRunSettings) -> Path:
    if settings.prompt_path is not None:
        return settings.prompt_path.resolve()

    here = Path(__file__).resolve().parent
    candidate = here / _DEFAULT_PROMPT_REL
    if candidate.is_file():
        return candidate
    repo_candidate = settings.repo_root / "ultron" / "prompts" / "pi-ops.md"
    if repo_candidate.is_file():
        return repo_candidate.resolve()
    raise RuntimeError(f"Pi prompt not found: {candidate}")


def ensure_pi_config(settings: PiRunSettings) -> Path:
    """Write project-local pi ``models.json`` for Ollama."""
    config_dir = settings.config_dir
    config_dir.mkdir(parents=True, exist_ok=True)
    models_path = config_dir / "models.json"
    payload = {
        "providers": {
            settings.provider: {
                "baseUrl": ollama_openai_base_url(settings.ollama_base_url),
                "api": "openai-completions",
                "apiKey": settings.api_key,
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                },
                "models": [
                    {
                        "id": settings.model,
                        "name": f"{settings.model} (Ollama)",
                        "reasoning": False,
                        "input": ["text"],
                        "contextWindow": 128000,
                        "maxTokens": 32000,
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                    }
                ],
            }
        }
    }
    models_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return config_dir


def build_pi_user_message(
    *,
    user_request: str,
    session_context: str | None = None,
) -> str:
    parts = [user_request.strip()]
    if session_context and session_context.strip():
        parts.extend(["", "### Session context", "", session_context.strip()])
    return "\n".join(parts).strip()


def _pi_subprocess_env(settings: PiRunSettings) -> dict[str, str]:
    merged = os.environ.copy()
    merged["PI_CODING_AGENT_DIR"] = str(settings.config_dir.resolve())
    merged["PI_OFFLINE"] = "1"
    return merged


def _script_argv(inner: list[str]) -> list[str]:
    return ["script", "-q", "-c", " ".join(shlex.quote(part) for part in inner), "/dev/null"]


def format_pi_reply(
    *,
    result: PiAgentResult,
    secret_literals: list[str] | None = None,
) -> str:
    tunnel_note = " · tunnel started" if result.tunnel_started else ""
    header = (
        f"**Pi agent** · `{result.model}` · session `{result.session_id}` · "
        f"{result.duration_seconds:.0f}s · exit {result.exit_code}{tunnel_note}\n\n"
    )
    return header + result.discord_text(secret_literals=secret_literals)


async def call_pi_agent(
    settings: PiRunSettings,
    *,
    user_request: str,
    session_context: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> PiAgentResult:
    """Run pi with Ollama on the Ultron workspace."""
    if not user_request.strip():
        raise ValueError("user_request must not be empty")

    async def progress(msg: str) -> None:
        if on_progress is not None:
            await on_progress(msg)

    session_id = uuid4().hex[:12]
    started = datetime.now(timezone.utc)
    workspace = settings.workspace
    if not workspace.is_dir():
        raise RuntimeError(f"Workspace is not a directory: {workspace}")

    prompt_path = _resolve_prompt_path(settings)
    config_dir = ensure_pi_config(settings)
    user_message = build_pi_user_message(
        user_request=user_request,
        session_context=session_context,
    )

    log_dir = settings.state_dir / "pi"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    run_log = log_dir / f"{stamp}-{session_id}.log"
    user_prompt_file = log_dir / f"{stamp}-{session_id}-prompt.txt"
    user_prompt_file.write_text(user_message, encoding="utf-8")

    run_log.write_text(
        "\n".join(
            [
                f"session_id={session_id}",
                f"started_utc={started.isoformat()}",
                f"workspace={workspace}",
                f"model={settings.model}",
                f"provider={settings.provider}",
                f"prompt={prompt_path}",
                f"binary={settings.bin_path}",
                f"config_dir={config_dir}",
                "",
                "=== user message ===",
                user_message,
                "",
            ]
        ),
        encoding="utf-8",
    )

    await progress("Checking Ollama connection…")
    reachable, tunnel_started = await ensure_ollama_reachable(
        settings.ollama_base_url,
        tunnel_script=settings.tunnel_script,
        connect_timeout_seconds=settings.ollama_connect_timeout_seconds,
        connect_retries=settings.ollama_connect_retries,
        connect_retry_delay_seconds=settings.ollama_connect_retry_delay_seconds,
        on_progress=on_progress,
    )
    if not reachable:
        raise ConnectionError(
            "Could not reach Ollama after several attempts. "
            "Check that Ollama is running, `llm_chain` base_url is correct, "
            "and optionally set ULTRON_OLLAMA_TUNNEL_SCRIPT for an SSH tunnel."
        )

    await progress(f"Running **pi** with `{settings.model}`…")

    inner_cmd = [
        str(settings.bin_path),
        "-p",
        "--provider",
        settings.provider,
        "--model",
        settings.model,
        "--api-key",
        settings.api_key,
        "--approve",
        "--no-session",
        "--append-system-prompt",
        str(prompt_path),
        f"@{user_prompt_file}",
    ]
    cmd = _script_argv(inner_cmd)

    logger.info(
        "pi session=%s workspace=%s model=%s prompt=%s",
        session_id,
        workspace,
        settings.model,
        prompt_path,
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workspace),
        env=_pi_subprocess_env(settings),
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=float(settings.timeout_seconds),
        )
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise TimeoutError(
            f"pi timed out after {settings.timeout_seconds}s (session {session_id})"
        ) from None

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    exit_code = proc.returncode if proc.returncode is not None else -1

    with run_log.open("a", encoding="utf-8") as fp:
        fp.write(f"exit_code={exit_code}\n")
        fp.write(f"duration_seconds={duration:.1f}\n")
        fp.write(f"tunnel_started={tunnel_started}\n\n")
        fp.write("=== stdout ===\n")
        fp.write(stdout)
        fp.write("\n\n=== stderr ===\n")
        fp.write(stderr)
        fp.write("\n")

    logger.info("pi session=%s exit=%s duration=%.1fs", session_id, exit_code, duration)

    return PiAgentResult(
        session_id=session_id,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        prompt_path=prompt_path,
        workspace=workspace,
        duration_seconds=duration,
        model=settings.model,
        tunnel_started=tunnel_started,
    )


__all__ = [
    "PiAgentResult",
    "PiRunSettings",
    "build_pi_run_settings",
    "call_pi_agent",
    "format_pi_reply",
    "pi_availability_message",
    "pi_is_available",
]
