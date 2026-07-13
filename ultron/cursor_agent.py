from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ultron.config import AppConfig, CursorAgentConfig
from ultron.sanitize import sanitize_for_discord

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class CursorAgentProfile:
    name: str
    workspace: Path
    prompt_path: Path
    log_prefix: str = "cursor-agent"


@dataclass(frozen=True)
class CursorAgentResult:
    session_id: str
    exit_code: int
    stdout: str
    stderr: str
    prompt_path: Path
    workspace: Path
    duration_seconds: float
    profile: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def discord_text(self, *, secret_literals: list[str] | None = None) -> str:
        body = (self.stdout or "").strip()
        if not body and self.stderr.strip():
            body = self.stderr.strip()
        if not body:
            body = f"cursor-agent finished with exit code {self.exit_code} and no output."
        if not self.ok:
            err = self.stderr.strip()
            if err and err not in body:
                body = f"{body}\n\n**stderr:**\n```\n{err[:1500]}\n```"
        return sanitize_for_discord(body, secret_literals=secret_literals)


def resolve_cursor_agent_bin(cfg: CursorAgentConfig) -> str:
    env_bin = os.environ.get("ULTRON_CURSOR_AGENT_BIN", "").strip()
    if env_bin:
        p = Path(env_bin).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
        raise RuntimeError(f"ULTRON_CURSOR_AGENT_BIN is not executable: {p}")

    if cfg.bin_path.strip():
        p = Path(cfg.bin_path).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
        raise RuntimeError(f"cursor_agent.bin_path is not executable: {p}")

    found = shutil.which("cursor-agent")
    if found:
        return found
    local_bin = Path.home() / ".local" / "bin" / "cursor-agent"
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return str(local_bin.resolve())
    raise RuntimeError(
        "cursor-agent not found on PATH. Install the Cursor CLI or set ULTRON_CURSOR_AGENT_BIN."
    )


def render_prompt_template(path: Path, **replacements: str) -> str:
    text = path.read_text(encoding="utf-8")
    for key, val in replacements.items():
        text = text.replace("{" + key + "}", val)
    return text.strip()


def build_agent_prompt(
    *,
    prompt_path: Path,
    user_request: str,
    session_context: str | None = None,
    template_vars: dict[str, str] | None = None,
) -> str:
    if template_vars:
        base = render_prompt_template(prompt_path, **template_vars)
    else:
        base = prompt_path.read_text(encoding="utf-8").strip()
    parts = [base, "", "---", "", "### Operator request", "", user_request.strip()]
    if session_context and session_context.strip():
        parts.extend(["", "### Session context", "", session_context.strip()])
    return "\n".join(parts).strip()


async def call_cursor_agent_session(
    *,
    app_cfg: AppConfig,
    profile: CursorAgentProfile,
    state_dir: Path,
    user_request: str,
    session_context: str | None = None,
    timeout_seconds: float | None = None,
    template_vars: dict[str, str] | None = None,
) -> CursorAgentResult:
    if not user_request.strip():
        raise ValueError("user_request must not be empty")
    if not app_cfg.cursor_agent.enabled:
        raise RuntimeError("cursor_agent is disabled in config.yaml")

    timeout = timeout_seconds if timeout_seconds is not None else app_cfg.cursor_agent.timeout_seconds
    session_id = uuid4().hex[:12]
    started = datetime.now(timezone.utc)
    bin_path = resolve_cursor_agent_bin(app_cfg.cursor_agent)
    workspace = profile.workspace
    if not workspace.is_dir():
        raise RuntimeError(f"Workspace is not a directory: {workspace}")

    full_prompt = build_agent_prompt(
        prompt_path=profile.prompt_path,
        user_request=user_request,
        session_context=session_context,
        template_vars=template_vars,
    )

    log_dir = state_dir / profile.log_prefix
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    run_log = log_dir / f"{stamp}-{session_id}.log"
    run_log.write_text(
        "\n".join(
            [
                f"session_id={session_id}",
                f"profile={profile.name}",
                f"started_utc={started.isoformat()}",
                f"workspace={workspace}",
                f"prompt={profile.prompt_path}",
                f"binary={bin_path}",
                "",
                "=== prompt ===",
                full_prompt,
                "",
            ]
        ),
        encoding="utf-8",
    )

    cmd = [
        bin_path,
        "--yolo",
        "--print",
        "--trust",
        "--workspace",
        str(workspace),
        full_prompt,
    ]
    logger.info(
        "%s session=%s workspace=%s prompt=%s",
        profile.log_prefix,
        session_id,
        workspace,
        profile.prompt_path,
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workspace),
        env=os.environ.copy(),
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise TimeoutError(
            f"cursor-agent timed out after {timeout}s (session {session_id}, profile {profile.name})"
        ) from None

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    exit_code = proc.returncode if proc.returncode is not None else -1

    with run_log.open("a", encoding="utf-8") as fp:
        fp.write(f"exit_code={exit_code}\n")
        fp.write(f"duration_seconds={duration:.1f}\n\n")
        fp.write("=== stdout ===\n")
        fp.write(stdout)
        fp.write("\n\n=== stderr ===\n")
        fp.write(stderr)
        fp.write("\n")

    logger.info(
        "%s session=%s exit=%s duration=%.1fs",
        profile.log_prefix,
        session_id,
        exit_code,
        duration,
    )

    return CursorAgentResult(
        session_id=session_id,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        prompt_path=profile.prompt_path,
        workspace=workspace,
        duration_seconds=duration,
        profile=profile.name,
    )


def _resolve_self_upgrade_prompt(env) -> Path:
    if env.self_upgrade_prompt_path is not None and env.self_upgrade_prompt_path.is_file():
        return env.self_upgrade_prompt_path.resolve()
    default = Path(__file__).resolve().parent / "prompts" / "self-upgrade.md"
    if not default.is_file():
        raise RuntimeError(f"Self-upgrade prompt not found: {default}")
    return default


def self_upgrade_profile(env) -> CursorAgentProfile:
    return CursorAgentProfile(
        name="self-upgrade",
        workspace=env.ultron_project_root.resolve(),
        prompt_path=_resolve_self_upgrade_prompt(env),
        log_prefix="self-upgrade",
    )


async def call_self_upgrade_agent(
    *,
    app_cfg: AppConfig,
    env,
    user_request: str,
    session_context: str | None = None,
) -> CursorAgentResult:
    profile = self_upgrade_profile(env)
    return await call_cursor_agent_session(
        app_cfg=app_cfg,
        profile=profile,
        state_dir=env.state_dir,
        user_request=user_request,
        session_context=session_context,
        timeout_seconds=float(env.self_upgrade_timeout_seconds),
    )
