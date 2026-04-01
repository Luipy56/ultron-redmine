from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import colorlog
from dotenv import load_dotenv


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stderr)
    if sys.stderr.isatty():
        fmt = colorlog.ColoredFormatter(
            "%(asctime)s %(log_color)s%(levelname)-8s%(reset)s %(name)s: %(message)s",
            # colorlog only supports names from its palette (e.g. purple, not "magenta").
            log_colors={
                "DEBUG": "cyan",
                "INFO": "yellow",
                "WARNING": "purple",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    else:
        fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
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
    from ultron.config import load_config
    from ultron.llm import LLMClient
    from ultron.redmine import RedmineClient
    from ultron.settings import load_env

    log = logging.getLogger("ultron")
    env = load_env()
    log.info("LLM model: %s", env.llm_model)
    cfg_path = Path(env.config_path)
    if not cfg_path.is_file():
        log.error("Config file not found: %s", cfg_path.resolve())
        sys.exit(1)
    app_cfg = load_config(cfg_path)
    redmine = RedmineClient(base_url=env.redmine_url, api_key=env.redmine_api_key)
    llm = LLMClient(
        base_url=env.llm_base_url,
        api_key=env.llm_api_key,
        model=env.llm_model,
        timeout=env.llm_timeout_seconds,
        max_retries=env.llm_max_retries,
    )
    bot = UltronBot(env=env, app_cfg=app_cfg, redmine=redmine, llm=llm)
    try:
        await bot.start(env.discord_token)
    finally:
        await bot.close()


if __name__ == "__main__":
    main()
