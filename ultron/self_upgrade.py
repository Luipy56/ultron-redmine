from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands

from ultron.config import AppConfig
from ultron.cursor_agent import CursorAgentResult, call_self_upgrade_agent
from ultron.feedback import FeedbackReport, send_feedback
from ultron.sanitize import sanitize_for_discord
from ultron.settings import EnvSettings

logger = logging.getLogger(__name__)

_COMPILE_MODULES = (
    "ultron/__init__.py",
    "ultron/bot.py",
    "ultron/config.py",
    "ultron/settings.py",
    "ultron/cursor_agent.py",
    "ultron/self_upgrade.py",
    "ultron/feedback.py",
    "ultron/sanitize.py",
    "ultron/discord_slash.py",
    "ultron/amvara/executor.py",
)


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    steps: list[str]
    error: str | None = None


async def _run_step(args: list[str], *, cwd: str, label: str) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=os.environ.copy(),
    )
    out_b, _ = await proc.communicate()
    output = out_b.decode("utf-8", errors="replace").strip()
    ok = proc.returncode == 0
    line = f"{label}: {'OK' if ok else f'FAILED (exit {proc.returncode})'}"
    if output:
        line += f" — {output[:200]}"
    return ok, line


async def verify_ultron_install(env: EnvSettings) -> VerifyResult:
    """Post-upgrade checks before requesting a systemd restart."""
    root = str(env.ultron_project_root.resolve())
    venv_py = str(env.ultron_project_root / ".venv" / "bin" / "python")
    venv_pip = str(env.ultron_project_root / ".venv" / "bin" / "pip")
    steps: list[str] = []

    ok, line = await _run_step(
        [venv_pip, "install", "-q", "-e", "."],
        cwd=root,
        label="pip install -e .",
    )
    steps.append(line)
    if not ok:
        return VerifyResult(ok=False, steps=steps, error="Editable install failed")

    pkg_json = env.ultron_project_root / "package.json"
    if pkg_json.is_file() and shutil.which("npm"):
        ok, line = await _run_step(
            ["npm", "install", "--ignore-scripts", "--silent"],
            cwd=root,
            label="npm install",
        )
        steps.append(line)
        if not ok:
            return VerifyResult(ok=False, steps=steps, error="npm install failed")

    ok, line = await _run_step(
        [
            venv_py,
            "-c",
            "from ultron.settings import load_env; from ultron.bot import UltronBot; "
            "load_env(); print('import_ok')",
        ],
        cwd=root,
        label="import ultron",
    )
    steps.append(line)
    if not ok:
        return VerifyResult(ok=False, steps=steps, error="Import check failed")

    ok, line = await _run_step(
        [venv_py, "-m", "py_compile", *_COMPILE_MODULES],
        cwd=root,
        label="py_compile core modules",
    )
    steps.append(line)
    if not ok:
        return VerifyResult(ok=False, steps=steps, error="Syntax check failed")

    return VerifyResult(ok=True, steps=steps)


def request_systemd_restart(unit: str) -> None:
    """Ask systemd to restart the Ultron unit (non-blocking; avoids deadlock)."""
    subprocess.Popen(
        ["systemctl", "restart", "--no-block", unit],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    logger.info("Requested systemd restart: %s", unit)


_AUTO_REPAIR_COOLDOWN_SECONDS = 1800
_AUTO_REPAIR_STATE = "self-repair-state.txt"


class SelfUpgradeMode(str, Enum):
    OPERATOR = "operator"
    AUTO_REPAIR = "auto_repair"


@dataclass(frozen=True)
class SelfUpgradeTrigger:
    mode: SelfUpgradeMode
    request: str
    error_type: str | None = None
    error_message: str | None = None
    command: str | None = None
    traceback_snippet: str | None = None


@dataclass(frozen=True)
class SelfUpgradeOutcome:
    trigger: SelfUpgradeTrigger
    agent_result: CursorAgentResult | None
    verify_ok: bool
    verify_steps: list[str]
    verify_error: str | None
    restarted: bool
    user_action: str | None
    failure_reason: str | None = None


def is_likely_code_bug(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            ImportError,
            ModuleNotFoundError,
            AttributeError,
            NameError,
            SyntaxError,
            TypeError,
            NotImplementedError,
        ),
    ):
        return True
    if isinstance(exc, app_commands.AppCommandError):
        return is_likely_code_bug(exc.original) if exc.original else False
    cause = exc.__cause__
    if cause is not None and cause is not exc:
        return is_likely_code_bug(cause)
    return False


def build_auto_repair_request(trigger: SelfUpgradeTrigger) -> str:
    parts = [
        "Ultron hit a **code error** at runtime. Diagnose and fix the bug with a **minimal diff**.",
        "Focus on the failing code path; do not refactor unrelated modules.",
        "",
        f"**Error type:** {trigger.error_type or 'unknown'}",
        f"**Error message:** {trigger.error_message or '(none)'}",
    ]
    if trigger.command:
        parts.append(f"**Slash command:** `/{trigger.command}`")
    if trigger.traceback_snippet:
        parts.extend(["", "**Traceback (tail):**", "```", trigger.traceback_snippet[:2500], "```"])
    parts.extend(
        [
            "",
            "After fixing: run the self-upgrade checklist (pip install -e ., npm install if needed, "
            "import test, py_compile).",
            "Ultron will verify and restart automatically if checks pass.",
        ]
    )
    return "\n".join(parts)


def _read_last_auto_repair_ts(state_dir: Path) -> float:
    path = state_dir / _AUTO_REPAIR_STATE
    try:
        return float(path.read_text(encoding="utf-8").strip().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def _write_last_auto_repair_ts(state_dir: Path) -> None:
    path = state_dir / _AUTO_REPAIR_STATE
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{time.time():.0f}\n", encoding="utf-8")


def auto_repair_allowed(env: EnvSettings) -> bool:
    if not env.self_repair_enabled:
        return False
    return (time.time() - _read_last_auto_repair_ts(env.state_dir)) >= _AUTO_REPAIR_COOLDOWN_SECONDS


def make_auto_repair_trigger(
    exc: BaseException,
    *,
    command: str | None = None,
) -> SelfUpgradeTrigger:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=12))
    return SelfUpgradeTrigger(
        mode=SelfUpgradeMode.AUTO_REPAIR,
        request="",
        error_type=type(exc).__name__,
        error_message=str(exc)[:500],
        command=command,
        traceback_snippet=tb[-2500:],
    )


def _format_outcome_report(
    outcome: SelfUpgradeOutcome,
    *,
    secret_literals: list[str] | None,
) -> FeedbackReport:
    t = outcome.trigger
    if t.mode == SelfUpgradeMode.AUTO_REPAIR:
        title = "Ultron self-repair report"
        kind: Literal["self_upgrade", "self_repair", "info"] = "self_repair"
        intro = (
            f"**Why:** Runtime code error triggered automatic self-repair.\n"
            f"**Trigger:** `{t.error_type}` — "
            f"{sanitize_for_discord(t.error_message or 'n/a', secret_literals=secret_literals)}"
        )
        if t.command:
            intro += f"\n**Command:** `/{t.command}`"
    else:
        title = "Ultron self-upgrade report"
        kind = "self_upgrade"
        intro = "**Why:** Admin-requested self-upgrade (`/upgrade`)."

    sections = [intro]

    if outcome.agent_result is not None:
        r = outcome.agent_result
        sections.append(
            f"**Agent session:** `{r.session_id}` · {r.duration_seconds:.0f}s · exit {r.exit_code}"
        )
        summary = sanitize_for_discord(
            r.discord_text(secret_literals=secret_literals),
            secret_literals=secret_literals,
        )
        if summary:
            sections.append(f"**Solution applied:**\n{summary[:3500]}")

    if outcome.verify_steps:
        verify_lines = "\n".join(
            f"• {sanitize_for_discord(s, secret_literals=secret_literals)}" for s in outcome.verify_steps
        )
        status = "passed" if outcome.verify_ok else "failed"
        sections.append(f"**Verification ({status}):**\n{verify_lines}")

    if outcome.restarted:
        sections.append(
            "**Restart:** systemd will restart Ultron (`systemctl restart --no-block`); "
            "expect a brief disconnect (~10–30 seconds)."
        )
    elif outcome.failure_reason:
        sections.append(
            f"**Status:** {sanitize_for_discord(outcome.failure_reason, secret_literals=secret_literals)}"
        )

    if outcome.user_action:
        sections.append(f"**Action required:** {outcome.user_action}")

    return FeedbackReport(title=title, body="\n\n".join(sections), kind=kind)


async def run_self_upgrade(
    bot: discord.Client,
    env: EnvSettings,
    app_cfg: AppConfig,
    trigger: SelfUpgradeTrigger,
    *,
    interaction: discord.Interaction | None = None,
    defer_interaction: bool = False,
    secret_literals: list[str] | None = None,
) -> SelfUpgradeOutcome:
    """Self-upgrade / self-repair via cursor-agent (used by /upgrade and automatic recovery)."""
    if defer_interaction and interaction is not None and not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False, thinking=True)

    user_request = (
        build_auto_repair_request(trigger)
        if trigger.mode == SelfUpgradeMode.AUTO_REPAIR
        else trigger.request
    )
    session_context = (
        f"Mode: {trigger.mode.value}. "
        f"Error: {trigger.error_type}: {trigger.error_message}. "
        "Discord output must never include secrets (.env, tokens, API keys)."
    )

    agent_result: CursorAgentResult | None = None
    try:
        agent_result = await call_self_upgrade_agent(
            app_cfg=app_cfg,
            env=env,
            user_request=user_request,
            session_context=session_context,
        )
    except TimeoutError as e:
        outcome = SelfUpgradeOutcome(
            trigger=trigger,
            agent_result=None,
            verify_ok=False,
            verify_steps=[],
            verify_error=None,
            restarted=False,
            user_action=(
                "Fix manually or retry with a smaller scope. "
                "Consider raising ULTRON_SELF_UPGRADE_TIMEOUT_SECONDS."
            ),
            failure_reason=f"cursor-agent timed out: {e}",
        )
        await send_feedback(
            bot,
            app_cfg,
            _format_outcome_report(outcome, secret_literals=secret_literals),
            interaction=interaction,
            secret_literals=secret_literals,
        )
        return outcome
    except Exception as e:
        logger.exception("run_self_upgrade agent launch failed")
        outcome = SelfUpgradeOutcome(
            trigger=trigger,
            agent_result=None,
            verify_ok=False,
            verify_steps=[],
            verify_error=None,
            restarted=False,
            user_action="Check host logs and cursor-agent installation.",
            failure_reason=f"{type(e).__name__}: {e}",
        )
        await send_feedback(
            bot,
            app_cfg,
            _format_outcome_report(outcome, secret_literals=secret_literals),
            interaction=interaction,
            secret_literals=secret_literals,
        )
        return outcome

    if agent_result is None or not agent_result.ok:
        outcome = SelfUpgradeOutcome(
            trigger=trigger,
            agent_result=agent_result,
            verify_ok=False,
            verify_steps=[],
            verify_error=None,
            restarted=False,
            user_action=(
                "Review the agent log under data/self-upgrade/ "
                "and fix manually or retry /upgrade."
            ),
            failure_reason="cursor-agent exited with a non-zero status.",
        )
        await send_feedback(
            bot,
            app_cfg,
            _format_outcome_report(outcome, secret_literals=secret_literals),
            interaction=interaction,
            secret_literals=secret_literals,
        )
        return outcome

    verify = await verify_ultron_install(env)
    if not verify.ok:
        outcome = SelfUpgradeOutcome(
            trigger=trigger,
            agent_result=agent_result,
            verify_ok=False,
            verify_steps=verify.steps,
            verify_error=verify.error,
            restarted=False,
            user_action=(
                f"Run `./scripts/ultron-dump.sh` or `systemctl restart {env.systemd_unit}` manually "
                "after fixing the codebase. Do not assume the bot is healthy."
            ),
            failure_reason="Post-upgrade verification failed.",
        )
        await send_feedback(
            bot,
            app_cfg,
            _format_outcome_report(outcome, secret_literals=secret_literals),
            interaction=interaction,
            secret_literals=secret_literals,
        )
        return outcome

    if trigger.mode == SelfUpgradeMode.AUTO_REPAIR:
        _write_last_auto_repair_ts(env.state_dir)

    outcome = SelfUpgradeOutcome(
        trigger=trigger,
        agent_result=agent_result,
        verify_ok=True,
        verify_steps=verify.steps,
        verify_error=None,
        restarted=True,
        user_action=None,
        failure_reason=None,
    )
    await send_feedback(
        bot,
        app_cfg,
        _format_outcome_report(outcome, secret_literals=secret_literals),
        interaction=interaction,
        secret_literals=secret_literals,
    )

    request_systemd_restart(env.systemd_unit)
    await bot.close()
    return outcome
