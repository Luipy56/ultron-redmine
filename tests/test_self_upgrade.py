from __future__ import annotations

from unittest.mock import patch

import pytest

from ultron.self_upgrade import (
    SelfUpgradeMode,
    SelfUpgradeTrigger,
    auto_repair_allowed,
    build_auto_repair_request,
    is_likely_code_bug,
    make_auto_repair_trigger,
    request_systemd_restart,
)


def test_is_likely_code_bug_import_error() -> None:
    assert is_likely_code_bug(ImportError("no module"))


def test_is_likely_code_bug_wrapped_cause() -> None:
    try:
        raise AttributeError("missing")
    except AttributeError as inner:
        outer = RuntimeError("wrapped")
        outer.__cause__ = inner
    assert is_likely_code_bug(outer)


def test_is_likely_code_bug_runtime_user_error() -> None:
    assert not is_likely_code_bug(ValueError("bad user input"))


def test_build_auto_repair_request() -> None:
    trigger = SelfUpgradeTrigger(
        mode=SelfUpgradeMode.AUTO_REPAIR,
        request="",
        error_type="AttributeError",
        error_message="no foo",
        command="summary",
    )
    text = build_auto_repair_request(trigger)
    assert "AttributeError" in text
    assert "/summary" in text


def test_make_auto_repair_trigger() -> None:
    try:
        raise NameError("x")
    except NameError as e:
        t = make_auto_repair_trigger(e, command="pi")
    assert t.mode == SelfUpgradeMode.AUTO_REPAIR
    assert t.error_type == "NameError"


def test_auto_repair_allowed_disabled(tmp_path) -> None:
    from ultron.settings import EnvSettings
    from ultron.config import EnvironmentBindings

    env = EnvSettings(
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
        ultron_project_root=tmp_path,
        self_upgrade_prompt_path=None,
        self_upgrade_timeout_seconds=1800,
        self_repair_enabled=False,
        systemd_unit="ultron.service",
    )
    assert not auto_repair_allowed(env)


def test_request_systemd_restart() -> None:
    with patch("ultron.self_upgrade.subprocess.Popen") as mock_popen:
        request_systemd_restart("ultron.service")
    mock_popen.assert_called_once()
    args = mock_popen.call_args[0][0]
    assert args[0:3] == ["systemctl", "restart", "--no-block"]


def test_create_upgrade_feat_task(tmp_path) -> None:
    from ultron.settings import EnvSettings
    from ultron.config import EnvironmentBindings
    from ultron.self_upgrade import create_upgrade_feat_task, SelfUpgradeMode

    root = tmp_path / "repo"
    (root / "autoagents" / "tasks").mkdir(parents=True)
    env = EnvSettings(
        discord_token="x",
        discord_guild_id=None,
        discord_application_id=None,
        redmine_url="https://redmine.example",
        redmine_api_key="k",
        llm_enabled=False,
        llm_base_url="",
        llm_api_key="",
        llm_model="(none)",
        config_path="config.yaml",
        state_dir=tmp_path / "state",
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
    path = create_upgrade_feat_task(
        env,
        request="Add a /ping latency field",
        mode=SelfUpgradeMode.OPERATOR,
        issue_id=7406,
    )
    assert path.name.startswith("FEAT-7406-")
    text = path.read_text(encoding="utf-8")
    assert "#7406" in text
    assert "Add a /ping latency field" in text


def test_upgrade_redmine_issue_id_default() -> None:
    from ultron.self_upgrade import DEFAULT_UPGRADE_REDMINE_ISSUE_ID, upgrade_redmine_issue_id

    assert upgrade_redmine_issue_id() == DEFAULT_UPGRADE_REDMINE_ISSUE_ID
    assert DEFAULT_UPGRADE_REDMINE_ISSUE_ID == 7406
