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


class _SlashPhaseMixin:
    """Inject %(slash_phase_colored)s for LogRecords with extra slash_phase=INPUT|OUTPUT|ERROR|DENIED."""

    _PHASE_COLORS = {
        "INPUT": "bold_cyan",
        "OUTPUT": "bold_green",
        "ERROR": "bold_red",
        "DENIED": "bold_purple",
    }

    def _slash_phase_prefix(self, record: logging.LogRecord) -> str:
        phase = getattr(record, "slash_phase", None)
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


class UltronColoredFormatter(_SlashPhaseMixin, colorlog.ColoredFormatter):
    def format(self, record: logging.LogRecord) -> str:
        record.slash_phase_colored = self._slash_phase_prefix(record)  # type: ignore[attr-defined]
        return super().format(record)


class UltronPlainFormatter(_SlashPhaseMixin, logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.slash_phase_colored = self._slash_phase_prefix(record)  # type: ignore[attr-defined]
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
        "%(slash_phase_colored)s%(name)s: %(message)s"
    )
    _line_fmt_plain = "%(asctime)s %(levelname)-8s %(slash_phase_colored)s%(name)s: %(message)s"
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
    load_dotenv()
    parser = argparse.ArgumentParser(prog="ultron")
    sub = parser.add_subparsers(dest="cmd")
    p_add = sub.add_parser("add", help="Operator commands (run on the bot host)")
    add_sub = p_add.add_subparsers(dest="add_cmd", required=True)
    p_tok = add_sub.add_parser("token", help="Approve a user using the token from /token")
    p_tok.add_argument("token", help="Token string")
    args = parser.parse_args()
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
    from ultron.llm import LLMBackend, LLMChainClient, LLMClient, format_llm_endpoint
    from ultron.redmine import RedmineClient, RedmineError
    from ultron.settings import load_env

    log = logging.getLogger("ultron")
    env = load_env()
    cfg_path = Path(env.config_path)
    if not cfg_path.is_file():
        log.error("STARTUP | config file not found: %s", cfg_path.resolve())
        sys.exit(1)
    try:
        app_cfg = load_config(cfg_path)
    except ValueError as e:
        log.error("STARTUP | invalid config: %s", e)
        sys.exit(1)

    redmine = RedmineClient(base_url=env.redmine_url, api_key=env.redmine_api_key)
    log.info("STARTUP | testing Redmine connection | base_url=%s", env.redmine_url.rstrip("/"))
    try:
        await redmine.verify_connection()
    except RedmineError as e:
        log.error("STARTUP | Redmine connection failed: %s", e)
        sys.exit(1)
    except httpx.RequestError as e:
        log.error("STARTUP | Redmine connection failed (network): %s", e)
        sys.exit(1)
    log.info("STARTUP | Redmine OK")

    llm: LLMBackend
    if app_cfg.llm_chain is not None:
        try:
            resolved = resolve_llm_chain(app_cfg.llm_chain)
        except RuntimeError as e:
            log.error("%s", e)
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
        )
    bot = UltronBot(env=env, app_cfg=app_cfg, redmine=redmine, llm=llm)
    try:
        await bot.start(env.discord_token)
    finally:
        await bot.close()


if __name__ == "__main__":
    main()
