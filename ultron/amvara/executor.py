"""Run Amvara audits via pi (primary) and cursor-agent (fallback)."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ultron.amvara.registry import AmvaraHost, AmvaraRegistry
from ultron.amvara.ssh_preflight import ensure_ssh_host_ready_async
from ultron.config import AppConfig
from ultron.cursor_agent import (
    CursorAgentProfile,
    CursorAgentResult,
    call_cursor_agent_session,
    render_prompt_template,
)
from ultron.pi_agent import PiAgentResult, call_pi_agent, format_pi_reply
from ultron.pi_resolve import build_pi_run_settings, pi_availability_message

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class AuditAgent(str, Enum):
    PI = "pi"
    CURSOR_AGENT = "cursor-agent"


@dataclass(frozen=True)
class AmvaraAuditResult:
    host: str
    agent: AuditAgent
    body: str
    ok: bool
    pi_result: PiAgentResult | None = None
    ca_result: CursorAgentResult | None = None
    fallback_used: bool = False


def amvara_availability_message(app_cfg: AppConfig, *, repo_root: Path | None = None) -> str | None:
    if not app_cfg.amvara.allowed_hosts:
        return (
            "**Amvara audits** are not configured. Add **`amvara.allowed_hosts`** in `config.yaml`."
        )
    pi_ok = pi_availability_message(app_cfg, repo_root=repo_root) is None
    ca_ok = app_cfg.cursor_agent.enabled
    if not pi_ok and not ca_ok:
        return (
            "Amvara audits need **pi** (`npm install` + Ollama `llm_chain`) or **cursor-agent** on PATH."
        )
    return None


def _write_rendered_prompt(template_name: str, **variables: str) -> Path:
    template = _PROMPTS_DIR / template_name
    if not template.is_file():
        raise RuntimeError(f"Prompt template not found: {template}")
    rendered = render_prompt_template(template, **variables)
    fp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=f"-{template_name}",
        delete=False,
        encoding="utf-8",
    )
    fp.write(rendered)
    fp.close()
    return Path(fp.name)


def _pi_prompt_template(host: AmvaraHost) -> str:
    return "pi-amvara-local.md" if host.is_local else "pi-amvara-remote.md"


def _template_vars(host: AmvaraHost) -> dict[str, str]:
    return {
        "host_name": host.name,
        "ssh_target": host.ssh_target,
        "workspace": host.workspace,
    }


async def _run_pi_audit(
    *,
    app_cfg: AppConfig,
    registry: AmvaraRegistry,
    host: AmvaraHost,
    state_dir: Path,
    task: str,
    session_context: str | None,
    on_progress: ProgressCallback | None,
    secret_literals: list[str] | None,
) -> AmvaraAuditResult:
    from dataclasses import replace

    base = build_pi_run_settings(app_cfg, state_dir=state_dir)
    prompt_path = _write_rendered_prompt(_pi_prompt_template(host), **_template_vars(host))
    settings = replace(
        base,
        prompt_path=prompt_path,
        timeout_seconds=registry.timeout_seconds,
    )
    try:
        result = await call_pi_agent(
            settings,
            user_request=task,
            session_context=session_context,
            on_progress=on_progress,
        )
    finally:
        try:
            prompt_path.unlink(missing_ok=True)
        except OSError:
            pass

    body = format_pi_reply(result=result, secret_literals=secret_literals)
    header = f"**Amvara audit** · `{host.name}` · pi · `{result.model}`\n\n"
    return AmvaraAuditResult(
        host=host.name,
        agent=AuditAgent.PI,
        body=header + result.discord_text(secret_literals=secret_literals),
        ok=result.ok,
        pi_result=result,
        fallback_used=False,
    )


async def _run_ca_audit(
    *,
    app_cfg: AppConfig,
    host: AmvaraHost,
    state_dir: Path,
    task: str,
    session_context: str | None,
    registry: AmvaraRegistry,
    secret_literals: list[str] | None,
) -> AmvaraAuditResult:
    repo_root = Path(__file__).resolve().parent.parent
    ws_raw = app_cfg.cursor_agent.workspace.strip()
    workspace = Path(ws_raw).expanduser() if ws_raw else repo_root
    prompt_path = _write_rendered_prompt("ca-amvara-remote.md", **_template_vars(host))
    profile = CursorAgentProfile(
        name=f"amvara-{host.name}",
        workspace=workspace.resolve(),
        prompt_path=prompt_path,
    )
    try:
        result = await call_cursor_agent_session(
            app_cfg=app_cfg,
            profile=profile,
            state_dir=state_dir,
            user_request=task,
            session_context=session_context,
            timeout_seconds=registry.timeout_seconds,
        )
    finally:
        try:
            prompt_path.unlink(missing_ok=True)
        except OSError:
            pass

    header = (
        f"**Amvara audit** · `{host.name}` · cursor-agent · "
        f"session `{result.session_id}` · exit {result.exit_code}\n\n"
    )
    return AmvaraAuditResult(
        host=host.name,
        agent=AuditAgent.CURSOR_AGENT,
        body=header + result.discord_text(secret_literals=secret_literals),
        ok=result.ok,
        ca_result=result,
        fallback_used=False,
    )


async def run_amvara_audit(
    *,
    app_cfg: AppConfig,
    registry: AmvaraRegistry,
    host_name: str,
    task: str,
    state_dir: Path,
    session_context: str | None = None,
    on_progress: ProgressCallback | None = None,
    force_agent: AuditAgent | None = None,
    secret_literals: list[str] | None = None,
) -> AmvaraAuditResult:
    """Run audit on an allowlisted host; pi first unless ``force_agent=ca``."""
    unavailable = amvara_availability_message(app_cfg)
    if unavailable is not None:
        raise RuntimeError(unavailable.replace("**", ""))

    host = registry.validate_host(host_name)
    if not task.strip():
        raise ValueError("task must not be empty")

    if not host.is_local:
        ssh_err = await ensure_ssh_host_ready_async(host, amvara_cfg=app_cfg.amvara)
        if ssh_err is not None:
            raise RuntimeError(ssh_err.replace("**", ""))

    prefer = force_agent or (
        AuditAgent.CURSOR_AGENT if registry.prefer_agent == "ca" else AuditAgent.PI
    )

    if prefer == AuditAgent.CURSOR_AGENT:
        return await _run_ca_audit(
            app_cfg=app_cfg,
            host=host,
            state_dir=state_dir,
            task=task,
            session_context=session_context,
            registry=registry,
            secret_literals=secret_literals,
        )

    pi_unavail = pi_availability_message(app_cfg)
    if pi_unavail is not None:
        if registry.fallback_enabled and app_cfg.cursor_agent.enabled:
            logger.warning("pi unavailable for amvara audit, falling back to cursor-agent: %s", pi_unavail)
            ca = await _run_ca_audit(
                app_cfg=app_cfg,
                host=host,
                state_dir=state_dir,
                task=task,
                session_context=session_context,
                registry=registry,
                secret_literals=secret_literals,
            )
            return AmvaraAuditResult(
                host=ca.host,
                agent=ca.agent,
                body=ca.body,
                ok=ca.ok,
                ca_result=ca.ca_result,
                fallback_used=True,
            )
        raise RuntimeError(pi_unavail.replace("**", ""))

    try:
        pi = await _run_pi_audit(
            app_cfg=app_cfg,
            registry=registry,
            host=host,
            state_dir=state_dir,
            task=task,
            session_context=session_context,
            on_progress=on_progress,
            secret_literals=secret_literals,
        )
        if pi.ok or not registry.fallback_enabled or not app_cfg.cursor_agent.enabled:
            return pi
        logger.warning(
            "pi audit failed on %s (exit %s), falling back to cursor-agent",
            host.name,
            pi.pi_result.exit_code if pi.pi_result else "?",
        )
    except Exception as e:
        if not registry.fallback_enabled or not app_cfg.cursor_agent.enabled:
            raise
        logger.warning("pi audit error on %s, falling back to cursor-agent: %s", host.name, e)

    ca = await _run_ca_audit(
        app_cfg=app_cfg,
        host=host,
        state_dir=state_dir,
        task=task,
        session_context=session_context,
        registry=registry,
        secret_literals=secret_literals,
    )
    return AmvaraAuditResult(
        host=ca.host,
        agent=ca.agent,
        body=ca.body,
        ok=ca.ok,
        ca_result=ca.ca_result,
        fallback_used=True,
    )
