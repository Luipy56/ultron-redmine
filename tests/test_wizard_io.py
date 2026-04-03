from __future__ import annotations

from pathlib import Path

import pytest

from ultron.config import load_config
from ultron.wizard.env_io import apply_env_updates, parse_env_file, read_env_lines, write_env_merged
from ultron.wizard.masking import is_sensitive_key, mask_secret
from ultron.wizard.paths import resolve_config_path
from ultron.wizard.yaml_io import deep_merge, dump_yaml, load_default_config_from_example


def test_mask_secret() -> None:
    assert "****" in mask_secret("DISCORD_TOKEN", "abcdefgh")
    assert mask_secret("REDMINE_URL", "https://x") == "https://x"


def test_is_sensitive_key() -> None:
    assert is_sensitive_key("DISCORD_TOKEN") is True
    assert is_sensitive_key("REDMINE_URL") is False


def test_apply_env_updates_replaces_and_appends(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("FOO=1\nBAR=two\n", encoding="utf-8")
    lines = read_env_lines(p)
    merged = apply_env_updates(lines, {"FOO": "99", "NEWKEY": "x"})
    assert "FOO=99" in merged
    assert any(line.startswith("NEWKEY=") for line in merged)
    write_env_merged(p, lines, {"FOO": "99", "NEWKEY": "x"})
    assert parse_env_file(p)["FOO"] == "99"
    assert parse_env_file(p)["NEWKEY"] == "x"


def test_deep_merge() -> None:
    base = {"a": 1, "discord": {"x": 1}}
    assert deep_merge(base, {"discord": {"y": 2}}) == {"a": 1, "discord": {"x": 1, "y": 2}}


def test_resolve_config_path() -> None:
    env = {"CONFIG_PATH": "sub/cfg.yaml"}
    cwd = Path("/tmp/wz")
    assert resolve_config_path(env, cwd=cwd) == (cwd / "sub/cfg.yaml").resolve()


def test_wizard_default_yaml_loads_with_ultron_config(tmp_path: Path) -> None:
    """Dumped default structure must parse with ``load_config``."""
    repo = Path(__file__).resolve().parent.parent
    (tmp_path / "config.example.yaml").write_text(
        (repo / "config.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    d = load_default_config_from_example(tmp_path)
    p = tmp_path / "out.yaml"
    p.write_text(dump_yaml(d), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.timezone == "UTC"
