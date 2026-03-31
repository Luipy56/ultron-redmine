from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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
    cfg_path = Path(env.config_path)
    if not cfg_path.is_file():
        log.error("Config file not found: %s", cfg_path.resolve())
        sys.exit(1)
    app_cfg = load_config(cfg_path)
    redmine = RedmineClient(base_url=env.redmine_url, api_key=env.redmine_api_key)
    llm = LLMClient(base_url=env.llm_base_url, api_key=env.llm_api_key, model=env.llm_model)
    bot = UltronBot(env=env, app_cfg=app_cfg, redmine=redmine, llm=llm)
    try:
        await bot.start(env.discord_token)
    finally:
        await bot.close()


if __name__ == "__main__":
    main()
