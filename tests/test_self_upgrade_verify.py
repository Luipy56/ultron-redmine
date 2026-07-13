from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ultron.self_upgrade import verify_ultron_install
from ultron.settings import EnvSettings
from ultron.config import EnvironmentBindings


def _env(tmp_path: Path) -> EnvSettings:
    root = Path(__file__).resolve().parent.parent
    return EnvSettings(
        discord_token="x",
        discord_guild_id=None,
        discord_application_id=None,
        redmine_url="https://example.com",
        redmine_api_key="k",
        llm_enabled=False,
        llm_base_url="",
        llm_api_key="",
        llm_model="(none)",
        config_path="config.yaml",
        state_dir=tmp_path,
        bot_owner_contact=None,
        discord_admin_ids=frozenset(),
        discord_message_content_intent=False,
        ultron_nl_commands=False,
        environment_bindings=EnvironmentBindings(),
        ultron_project_root=root,
        self_upgrade_prompt_path=None,
        self_upgrade_timeout_seconds=1800,
        self_repair_enabled=True,
        systemd_unit="ultron.service",
    )


def test_verify_ultron_install_success(tmp_path) -> None:
    async def _run():
        with patch("ultron.self_upgrade._run_step", new_callable=AsyncMock) as mock_step:
            mock_step.side_effect = [
                (True, "pip install -e .: OK"),
                (True, "npm install: OK"),
                (True, "import ultron: OK"),
                (True, "py_compile core modules: OK"),
            ]
            result = await verify_ultron_install(_env(tmp_path))
        assert result.ok
        assert len(result.steps) == 4

    asyncio.run(_run())


def test_verify_ultron_install_pip_fail(tmp_path) -> None:
    async def _run():
        with patch("ultron.self_upgrade._run_step", new_callable=AsyncMock) as mock_step:
            mock_step.return_value = (False, "pip install -e .: FAILED")
            result = await verify_ultron_install(_env(tmp_path))
        assert not result.ok
        assert result.error == "Editable install failed"

    asyncio.run(_run())
