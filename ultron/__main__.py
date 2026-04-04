from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import colorlog
import httpx
from dotenv import load_dotenv


def _load_dotenv() -> None:
    """Load `.env` from the repository root (directory that contains the `ultron` package).

    `load_dotenv()` without a path only searches upward from the current working directory,
    so secrets are missing when the bot is started from another cwd (systemd, cron, etc.).
    """
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if env_path.is_file():
        load_dotenv(env_path)
    else:
        load_dotenv()


class _PhaseColoredMixin:
    """Inject %(phase_colored)s after the level: chat_phase, else slash_phase, else startup_phase."""

    _PHASE_COLORS = {
        # Slash commands (extra slash_phase=…)
        "INPUT": "bold_cyan",
        "OUTPUT": "bold_green",
        "ERROR": "bold_red",
        "DENIED": "bold_purple",
        # Chat / @mention (extra chat_phase=…)
        "RECEIVED": "bold_blue",
        "IGNORE": "purple",
        "ROUTER": "bold_yellow",
        # Boot sequence in __main__._run (extra startup_phase=…)
        "STARTUP": "bold_light_purple",
    }

    def _phase_prefix(self, record: logging.LogRecord) -> str:
        phase = (
            getattr(record, "chat_phase", None)
            or getattr(record, "slash_phase", None)
            or getattr(record, "startup_phase", None)
        )
        if not phase:
            return ""
        if not sys.stderr.isatty():
            return f"[{phase}] "
        try:
            from colorlog.escape_codes import escape_codes as esc_map

            color_name = self._PHASE_COLORS.get(phase, "white")
            start = esc_map.get(color_name, "")
            reset = esc_map["reset"]
            return f"{start}[{phase}]{reset} "
        except Exception:
            return f"[{phase}] "


class UltronColoredFormatter(_PhaseColoredMixin, colorlog.ColoredFormatter):
    def format(self, record: logging.LogRecord) -> str:
        record.phase_colored = self._phase_prefix(record)  # type: ignore[attr-defined]
        return super().format(record)


class UltronPlainFormatter(_PhaseColoredMixin, logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.phase_colored = self._phase_prefix(record)  # type: ignore[attr-defined]
        return super().format(record)


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stderr)
    _line_fmt = (
        "%(asctime)s %(log_color)s%(levelname)-8s%(reset)s "
        "%(phase_colored)s%(name)s: %(message)s"
    )
    _line_fmt_plain = "%(asctime)s %(levelname)-8s %(phase_colored)s%(name)s: %(message)s"
    if sys.stderr.isatty():
        fmt = UltronColoredFormatter(
            _line_fmt,
            # colorlog palette: use light_yellow / bold_light_yellow for a clearer gold than plain yellow (33).
            log_colors={
                "DEBUG": "cyan",
                "INFO": "bold_light_yellow",
                "WARNING": "purple",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    else:
        fmt = UltronPlainFormatter(_line_fmt_plain)
    handler.setFormatter(fmt)
    root.addHandler(handler)

    logging.getLogger("discord.client").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(prog="ultron")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser(
        "wizard",
        aliases=["configure"],
        help="Interactive terminal configuration wizard (requires: pip install 'ultron-bot[wizard]')",
    )
    p_add = sub.add_parser("add", help="Operator commands (run on the bot host)")
    add_sub = p_add.add_subparsers(dest="add_cmd", required=True)
    p_tok = add_sub.add_parser("token", help="Approve a user using the token from /token")
    p_tok.add_argument("token", help="Token string")
    args = parser.parse_args()
    if args.cmd in ("wizard", "configure"):
        try:
            from ultron.wizard import run_wizard

            raise SystemExit(run_wizard())
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            raise SystemExit(1) from e
    if args.cmd == "add" and args.add_cmd == "token":
        from ultron.cli import cmd_add_token

        raise SystemExit(cmd_add_token(args.token))

    _configure_logging()
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        raise SystemExit(0) from None


async def _run() -> None:
    from ultron.bot import UltronBot
    from ultron.config import LLMProviderResolved, load_config, resolve_llm_chain
    from ultron.llm import LLMBackend, LLMChainClient, LLMClient, NullLLMBackend, format_llm_endpoint
    from ultron.redmine import RedmineClient, RedmineError
    from ultron.settings import load_env

    log = logging.getLogger("ultron")
    env = load_env()
    cfg_path = Path(env.config_path)
    _su = {"startup_phase": "STARTUP", "message_source": "startup"}
    if not cfg_path.is_file():
        log.error("config file not found: %s", cfg_path.resolve(), extra=_su)
        sys.exit(1)
    try:
        app_cfg = load_config(cfg_path)
    except ValueError as e:
        log.error("invalid config: %s", e, extra=_su)
        sys.exit(1)

    redmine = RedmineClient(base_url=env.redmine_url, api_key=env.redmine_api_key)
    log.info("testing Redmine connection | base_url=%s", env.redmine_url.rstrip("/"), extra=_su)
    try:
        await redmine.verify_connection()
    except RedmineError as e:
        log.error("Redmine connection failed: %s", e, extra=_su)
        sys.exit(1)
    except httpx.RequestError as e:
        log.error("Redmine connection failed (network): %s", e, extra=_su)
        sys.exit(1)
    log.info("Redmine OK", extra=_su)

    llm: LLMBackend
    if app_cfg.llm_chain is not None:
        try:
            resolved = resolve_llm_chain(app_cfg.llm_chain)
        except RuntimeError as e:
            log.error("%s", e, extra=_su)
            sys.exit(1)
        llm = LLMChainClient.from_resolved(resolved)
        chain_parts: list[str] = []
        for i, r in enumerate(resolved):
            label = r.name or f"[{i}]"
            chain_parts.append(
                f"{label}: model={r.model!r} endpoint={format_llm_endpoint(r.base_url)}"
            )
        log.info(
            "LLM configured | backend=chain | order=%s | primary_model=%r",
            " -> ".join(chain_parts),
            llm.model,
            extra=_su,
        )
    elif not env.llm_enabled:
        llm = NullLLMBackend()
        log.info(
            "LLM not configured | backend=none | Redmine slash commands and registration work; "
            "/summary, /ask_issue, and /note require a language model",
            extra=_su,
        )
    else:
        llm = LLMClient(
            base_url=env.llm_base_url,
            api_key=env.llm_api_key,
            model=env.llm_model,
            timeout=env.llm_timeout_seconds,
            max_retries=env.llm_max_retries,
        )
        log.info(
            "LLM configured | backend=single | model=%r | endpoint=%s",
            llm.model,
            format_llm_endpoint(llm.base_url),
            extra=_su,
        )
    if env.discord_message_content_intent:
        log.info(
            "Discord | message content intent: ON (privileged; portal must match — needed if mentions are empty without it)",
            extra=_su,
        )
    else:
        log.info(
            "Discord | message content intent: OFF (guild/DM message events still on; set "
            "DISCORD_MESSAGE_CONTENT_INTENT=1 + portal if @mentions do not trigger)",
            extra=_su,
        )
    nl_cfg = app_cfg.discord.nl_commands or env.ultron_nl_commands
    log.info(
        "Discord | NL @mention routing: %s (config discord.nl_commands=%s env ULTRON_NL_COMMANDS=%s; "
        "set discord.nl_commands false in YAML to use a fixed greeting only)",
        "ON" if nl_cfg else "OFF",
        app_cfg.discord.nl_commands,
        env.ultron_nl_commands,
        extra=_su,
    )
    if env.discord_guild_id is not None:
        log.info(
            "Discord | slash commands: will sync to guild %s (set DISCORD_GUILD_ID=0 for global sync)",
            env.discord_guild_id,
            extra=_su,
        )
    else:
        log.info(
            "Discord | slash commands: global sync (DISCORD_GUILD_ID=0 or global; may take up to ~1 hour)",
            extra=_su,
        )
    bot = UltronBot(env=env, app_cfg=app_cfg, redmine=redmine, llm=llm)
    try:
        await bot.start(env.discord_token)
    finally:
        await bot.close()


if __name__ == "__main__":
    main()
