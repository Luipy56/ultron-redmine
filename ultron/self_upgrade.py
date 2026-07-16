from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal
from uuid import uuid4

import discord
from discord import app_commands

from ultron.config import AppConfig
from ultron.feedback import FeedbackReport, send_feedback
from ultron.redmine import RedmineClient, RedmineError
from ultron.sanitize import sanitize_for_discord
from ultron.settings import EnvSettings

logger = logging.getLogger(__name__)

#: Journal notes for every /upgrade and auto-repair outcome go to this Redmine issue.
DEFAULT_UPGRADE_REDMINE_ISSUE_ID = 7406

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


@dataclass(frozen=True)
class AutoagentsShotResult:
    """Outcome of one autoagents upgrade shot (FEAT → implement → test)."""

    session_id: str
    exit_code: int
    stdout: str
    stderr: str
    task_path: Path
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def discord_text(self, *, secret_literals: list[str] | None = None) -> str:
        body = (self.stdout or "").strip()
        if not body and self.stderr.strip():
            body = self.stderr.strip()
        if not body:
            body = f"autoagents shot finished with exit code {self.exit_code} and no output."
        # Keep Discord-sized; prefer the tail (last agent steps).
        if len(body) > 3500:
            body = "…\n" + body[-3500:]
        if not self.ok:
            err = self.stderr.strip()
            if err and err not in body:
                body = f"{body}\n\n**stderr:**\n```\n{err[:1500]}\n```"
        return sanitize_for_discord(body, secret_literals=secret_literals)


async def _run_step(
    args: list[str],
    *,
    cwd: str,
    label: str,
    env: dict[str, str] | None = None,
) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=env if env is not None else os.environ.copy(),
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


async def run_ultron_dump(env: EnvSettings) -> tuple[bool, list[str]]:
    """Run ``scripts/ultron-dump.sh`` install steps only; caller handles restart."""
    root = env.ultron_project_root.resolve()
    dump = root / "scripts" / "ultron-dump.sh"
    if not dump.is_file():
        return False, [f"ultron-dump.sh missing: {dump}"]
    env_vars = os.environ.copy()
    env_vars["ULTRON_SYSTEMD_UNIT"] = env.systemd_unit
    env_vars["ULTRON_DUMP_SKIP_RESTART"] = "1"
    ok, line = await _run_step(
        ["bash", str(dump)],
        cwd=str(root),
        label="ultron-dump.sh",
        env=env_vars,
    )
    return ok, [line]


def upgrade_redmine_issue_id() -> int:
    raw = os.environ.get("ULTRON_UPGRADE_REDMINE_ISSUE", "").strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_UPGRADE_REDMINE_ISSUE_ID


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
    shot_result: AutoagentsShotResult | None
    verify_ok: bool
    verify_steps: list[str]
    verify_error: str | None
    restarted: bool
    user_action: str | None
    failure_reason: str | None = None
    redmine_issue_id: int | None = None
    redmine_note_ok: bool = False
    dump_ok: bool | None = None
    task_path: Path | None = None

    # Back-compat alias used by older tests / callers expecting agent_result.
    @property
    def agent_result(self) -> AutoagentsShotResult | None:
        return self.shot_result


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
            "After fixing: run pytest for affected tests and the self-upgrade checklist "
            "(pip install -e ., import test, py_compile).",
            "Ultron will verify, dump, report to Redmine, and restart automatically if checks pass.",
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


def _slugify(text: str, *, max_len: int = 48) -> str:
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s[:max_len] or "upgrade").strip("-")


def create_upgrade_feat_task(
    env: EnvSettings,
    *,
    request: str,
    mode: SelfUpgradeMode,
    issue_id: int,
) -> Path:
    """Write a FEAT-*.md under autoagents/tasks/ for the upgrade shot."""
    tasks_dir = env.ultron_project_root / "autoagents" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    slug = _slugify(request.splitlines()[0] if request.strip() else mode.value)
    filename = f"FEAT-{issue_id}-{now}-{slug}.md"
    path = tasks_dir / filename
    title = (
        f"Self-repair: {mode.value}"
        if mode == SelfUpgradeMode.AUTO_REPAIR
        else f"Self-upgrade: {request.strip().splitlines()[0][:80]}"
    )
    redmine_base = env.redmine_url.rstrip("/")
    body = f"""# {title}

## Tracker
- **Redmine:** #{issue_id} — {redmine_base}/issues/{issue_id}
- **Source:** Discord `/upgrade` ({mode.value})

## Problem / goal

{request.strip() or "(no request text)"}

## High-level instructions for coder

- Implement the request above in the Ultron checkout (`ultron/`, `tests/`, `scripts/`, `docs/` as needed).
- Prefer a **minimal diff**; match existing Ultron style.
- English for Discord-facing strings; never commit secrets or `.env`.
- After implementation: append **Testing instructions**, rename this file to **UNTESTED-…**.
- Bump patch version in `pyproject.toml` and `ultron/__init__.py` together when shipping code changes.
- Do **not** restart Ultron yourself — the `/upgrade` orchestrator runs dump + systemd restart.

## Testing instructions

_(Coder fills before UNTESTED rename)_

- [ ] `.venv/bin/pytest -q` (or scoped paths) passes
- [ ] Import check: `from ultron.bot import UltronBot`
- [ ] No secrets in the diff
"""
    path.write_text(body, encoding="utf-8")
    logger.info("Created upgrade FEAT task: %s", path)
    return path


async def run_autoagents_upgrade_shot(
    env: EnvSettings,
    *,
    task_path: Path,
    timeout_seconds: float | None = None,
) -> AutoagentsShotResult:
    """Run ``autoagents/ultron-agent-loop.sh shot`` (FEAT → handoff → tester → closing)."""
    root = env.ultron_project_root.resolve()
    script = root / "autoagents" / "ultron-agent-loop.sh"
    if not script.is_file():
        raise RuntimeError(f"autoagents loop missing: {script}")

    session_id = uuid4().hex[:12]
    timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else env.self_upgrade_timeout_seconds
    )
    log_dir = env.state_dir / "self-upgrade"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{session_id}.log"

    run_env = os.environ.copy()
    # Do not git-sync over the FEAT we just wrote.
    run_env["AGENT_GIT_SYNC"] = "0"
    run_env["AGENT_CURSOR_TIMEOUT"] = run_env.get("AGENT_CURSOR_TIMEOUT", "1")

    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        "bash",
        str(script),
        "shot",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(root / "autoagents"),
        env=run_env,
    )
    try:
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        try:
            await proc.communicate()
        except Exception:
            pass
        raise TimeoutError(
            f"autoagents shot timed out after {timeout:.0f}s (task={task_path.name})"
        ) from None

    output = out_b.decode("utf-8", errors="replace") if out_b else ""
    try:
        log_path.write_text(output, encoding="utf-8")
    except OSError:
        logger.warning("Could not write upgrade shot log to %s", log_path)

    duration = time.monotonic() - t0
    return AutoagentsShotResult(
        session_id=session_id,
        exit_code=int(proc.returncode or 0),
        stdout=output,
        stderr="",
        task_path=task_path,
        duration_seconds=duration,
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
        intro = "**Why:** Admin-requested self-upgrade (`/upgrade` via autoagents)."

    sections = [intro]

    if outcome.task_path is not None:
        sections.append(f"**FEAT task:** `{outcome.task_path.name}`")

    if outcome.shot_result is not None:
        r = outcome.shot_result
        sections.append(
            f"**Autoagents shot:** `{r.session_id}` · {r.duration_seconds:.0f}s · exit {r.exit_code}"
        )
        summary = sanitize_for_discord(
            r.discord_text(secret_literals=secret_literals),
            secret_literals=secret_literals,
        )
        if summary:
            sections.append(f"**Shot log (tail):**\n```\n{summary[:3000]}\n```")

    if outcome.verify_steps:
        verify_lines = "\n".join(
            f"• {sanitize_for_discord(s, secret_literals=secret_literals)}" for s in outcome.verify_steps
        )
        status = "passed" if outcome.verify_ok else "failed"
        sections.append(f"**Verification ({status}):**\n{verify_lines}")

    if outcome.dump_ok is True:
        sections.append("**Dump:** `scripts/ultron-dump.sh` completed (pip/npm).")
    elif outcome.dump_ok is False:
        sections.append("**Dump:** `scripts/ultron-dump.sh` failed — see verification / host logs.")

    if outcome.redmine_issue_id is not None:
        note_status = "posted" if outcome.redmine_note_ok else "failed to post"
        sections.append(f"**Redmine:** issue #{outcome.redmine_issue_id} note {note_status}.")

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


def _outcome_redmine_notes(
    outcome: SelfUpgradeOutcome,
    *,
    secret_literals: list[str] | None,
) -> str:
    """Plain-text journal note for Redmine (no Discord markdown overload)."""
    report = _format_outcome_report(outcome, secret_literals=secret_literals)
    text = f"{report.title}\n\n{report.body}"
    # Redmine journals: keep readable; strip some Discord-only markers lightly.
    text = text.replace("**", "")
    return sanitize_for_discord(text, secret_literals=secret_literals)[:12000]


async def _report_to_redmine(
    env: EnvSettings,
    outcome: SelfUpgradeOutcome,
    *,
    secret_literals: list[str] | None,
) -> bool:
    issue_id = outcome.redmine_issue_id or upgrade_redmine_issue_id()
    notes = _outcome_redmine_notes(outcome, secret_literals=secret_literals)
    try:
        client = RedmineClient(base_url=env.redmine_url, api_key=env.redmine_api_key)
        await client.add_note(issue_id, notes)
        logger.info("Posted /upgrade report to Redmine #%s", issue_id)
        return True
    except (RedmineError, Exception) as e:
        logger.exception("Failed to post /upgrade report to Redmine #%s: %s", issue_id, e)
        return False


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
    """Self-upgrade / self-repair via autoagents FEAT + shot, then dump, Redmine note, restart."""
    if defer_interaction and interaction is not None and not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False, thinking=True)

    issue_id = upgrade_redmine_issue_id()
    user_request = (
        build_auto_repair_request(trigger)
        if trigger.mode == SelfUpgradeMode.AUTO_REPAIR
        else trigger.request
    )

    task_path: Path | None = None
    try:
        task_path = create_upgrade_feat_task(
            env,
            request=user_request,
            mode=trigger.mode,
            issue_id=issue_id,
        )
    except Exception as e:
        logger.exception("Failed to create upgrade FEAT task")
        outcome = SelfUpgradeOutcome(
            trigger=trigger,
            shot_result=None,
            verify_ok=False,
            verify_steps=[],
            verify_error=None,
            restarted=False,
            user_action="Check autoagents/tasks/ permissions and retry /upgrade.",
            failure_reason=f"Could not create FEAT task: {type(e).__name__}: {e}",
            redmine_issue_id=issue_id,
            task_path=None,
        )
        outcome = await _finalize_outcome(
            bot,
            env,
            app_cfg,
            outcome,
            interaction=interaction,
            secret_literals=secret_literals,
            do_dump=False,
            do_restart=False,
        )
        return outcome

    shot_result: AutoagentsShotResult | None = None
    try:
        shot_result = await run_autoagents_upgrade_shot(env, task_path=task_path)
    except TimeoutError as e:
        outcome = SelfUpgradeOutcome(
            trigger=trigger,
            shot_result=None,
            verify_ok=False,
            verify_steps=[],
            verify_error=None,
            restarted=False,
            user_action=(
                "Fix manually or retry with a smaller scope. "
                "Consider raising ULTRON_SELF_UPGRADE_TIMEOUT_SECONDS."
            ),
            failure_reason=f"autoagents shot timed out: {e}",
            redmine_issue_id=issue_id,
            task_path=task_path,
        )
        return await _finalize_outcome(
            bot,
            env,
            app_cfg,
            outcome,
            interaction=interaction,
            secret_literals=secret_literals,
            do_dump=False,
            do_restart=False,
        )
    except Exception as e:
        logger.exception("run_self_upgrade autoagents shot failed")
        outcome = SelfUpgradeOutcome(
            trigger=trigger,
            shot_result=None,
            verify_ok=False,
            verify_steps=[],
            verify_error=None,
            restarted=False,
            user_action="Check host logs, cursor-agent, and autoagents/ultron-agent-loop.sh.",
            failure_reason=f"{type(e).__name__}: {e}",
            redmine_issue_id=issue_id,
            task_path=task_path,
        )
        return await _finalize_outcome(
            bot,
            env,
            app_cfg,
            outcome,
            interaction=interaction,
            secret_literals=secret_literals,
            do_dump=False,
            do_restart=False,
        )

    if shot_result is None or not shot_result.ok:
        outcome = SelfUpgradeOutcome(
            trigger=trigger,
            shot_result=shot_result,
            verify_ok=False,
            verify_steps=[],
            verify_error=None,
            restarted=False,
            user_action=(
                "Review the shot log under data/self-upgrade/ "
                "and the FEAT/WIP task under autoagents/tasks/; retry /upgrade if needed."
            ),
            failure_reason="autoagents shot exited with a non-zero status.",
            redmine_issue_id=issue_id,
            task_path=task_path,
        )
        return await _finalize_outcome(
            bot,
            env,
            app_cfg,
            outcome,
            interaction=interaction,
            secret_literals=secret_literals,
            do_dump=False,
            do_restart=False,
        )

    # If the FEAT file is still untouched, the shot likely no-op'd (e.g. no cursor-agent).
    if task_path.is_file() and task_path.name.startswith("FEAT-"):
        outcome = SelfUpgradeOutcome(
            trigger=trigger,
            shot_result=shot_result,
            verify_ok=False,
            verify_steps=[],
            verify_error=None,
            restarted=False,
            user_action=(
                "Ensure cursor-agent is on PATH and cursor_agent.enabled is true; "
                "then retry /upgrade."
            ),
            failure_reason=(
                f"FEAT task still present after shot ({task_path.name}) — "
                "coder did not pick it up."
            ),
            redmine_issue_id=issue_id,
            task_path=task_path,
        )
        return await _finalize_outcome(
            bot,
            env,
            app_cfg,
            outcome,
            interaction=interaction,
            secret_literals=secret_literals,
            do_dump=False,
            do_restart=False,
        )

    verify = await verify_ultron_install(env)
    if not verify.ok:
        outcome = SelfUpgradeOutcome(
            trigger=trigger,
            shot_result=shot_result,
            verify_ok=False,
            verify_steps=verify.steps,
            verify_error=verify.error,
            restarted=False,
            user_action=(
                f"Run `./scripts/ultron-dump.sh` or `systemctl restart {env.systemd_unit}` manually "
                "after fixing the codebase. Do not assume the bot is healthy."
            ),
            failure_reason="Post-upgrade verification failed.",
            redmine_issue_id=issue_id,
            task_path=task_path,
        )
        return await _finalize_outcome(
            bot,
            env,
            app_cfg,
            outcome,
            interaction=interaction,
            secret_literals=secret_literals,
            do_dump=False,
            do_restart=False,
        )

    if trigger.mode == SelfUpgradeMode.AUTO_REPAIR:
        _write_last_auto_repair_ts(env.state_dir)

    outcome = SelfUpgradeOutcome(
        trigger=trigger,
        shot_result=shot_result,
        verify_ok=True,
        verify_steps=verify.steps,
        verify_error=None,
        restarted=True,
        user_action=None,
        failure_reason=None,
        redmine_issue_id=issue_id,
        task_path=task_path,
    )
    return await _finalize_outcome(
        bot,
        env,
        app_cfg,
        outcome,
        interaction=interaction,
        secret_literals=secret_literals,
        do_dump=True,
        do_restart=True,
    )


async def _finalize_outcome(
    bot: discord.Client,
    env: EnvSettings,
    app_cfg: AppConfig,
    outcome: SelfUpgradeOutcome,
    *,
    interaction: discord.Interaction | None,
    secret_literals: list[str] | None,
    do_dump: bool,
    do_restart: bool,
) -> SelfUpgradeOutcome:
    dump_ok: bool | None = None
    if do_dump:
        dump_ok, dump_steps = await run_ultron_dump(env)
        outcome = SelfUpgradeOutcome(
            trigger=outcome.trigger,
            shot_result=outcome.shot_result,
            verify_ok=outcome.verify_ok and dump_ok,
            verify_steps=list(outcome.verify_steps) + dump_steps,
            verify_error=outcome.verify_error if dump_ok else "ultron-dump.sh failed",
            restarted=do_restart and dump_ok,
            user_action=outcome.user_action
            if dump_ok
            else (
                f"Dump failed — run `./scripts/ultron-dump.sh` or "
                f"`systemctl restart {env.systemd_unit}` manually."
            ),
            failure_reason=outcome.failure_reason if dump_ok else "ultron-dump.sh failed.",
            redmine_issue_id=outcome.redmine_issue_id,
            redmine_note_ok=False,
            dump_ok=dump_ok,
            task_path=outcome.task_path,
        )
        if not dump_ok:
            do_restart = False
    else:
        outcome = SelfUpgradeOutcome(
            trigger=outcome.trigger,
            shot_result=outcome.shot_result,
            verify_ok=outcome.verify_ok,
            verify_steps=outcome.verify_steps,
            verify_error=outcome.verify_error,
            restarted=False,
            user_action=outcome.user_action,
            failure_reason=outcome.failure_reason,
            redmine_issue_id=outcome.redmine_issue_id,
            redmine_note_ok=False,
            dump_ok=None,
            task_path=outcome.task_path,
        )

    redmine_ok = await _report_to_redmine(env, outcome, secret_literals=secret_literals)
    outcome = SelfUpgradeOutcome(
        trigger=outcome.trigger,
        shot_result=outcome.shot_result,
        verify_ok=outcome.verify_ok,
        verify_steps=outcome.verify_steps,
        verify_error=outcome.verify_error,
        restarted=outcome.restarted,
        user_action=outcome.user_action,
        failure_reason=outcome.failure_reason,
        redmine_issue_id=outcome.redmine_issue_id or upgrade_redmine_issue_id(),
        redmine_note_ok=redmine_ok,
        dump_ok=outcome.dump_ok,
        task_path=outcome.task_path,
    )

    await send_feedback(
        bot,
        app_cfg,
        _format_outcome_report(outcome, secret_literals=secret_literals),
        interaction=interaction,
        secret_literals=secret_literals,
    )

    if do_restart and outcome.verify_ok:
        request_systemd_restart(env.systemd_unit)
        await bot.close()
    return outcome
