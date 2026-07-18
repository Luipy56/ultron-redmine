"""Regression: ca/pi prompts must forbid aggressive deletes (Redmine #7406)."""

from __future__ import annotations

from pathlib import Path

import pytest

_PROMPTS = Path(__file__).resolve().parents[1] / "ultron" / "prompts"
_RULE = Path(__file__).resolve().parents[1] / ".cursor" / "rules" / "ultron-no-aggressive-delete.mdc"

_CA_PI_PROMPT_FILES = (
    "ca-pi-no-aggressive-delete.md",
    "pi-ops.md",
    "ca-amvara-remote.md",
    "pi-amvara-local.md",
    "pi-amvara-remote.md",
)


@pytest.mark.parametrize("name", _CA_PI_PROMPT_FILES)
def test_ca_pi_prompts_forbid_aggressive_rm(name: str) -> None:
    text = (_PROMPTS / name).read_text(encoding="utf-8")
    assert "rm -rf /" in text
    assert "Never" in text or "never" in text
    assert "delete" in text.casefold()


def test_cursor_rule_no_aggressive_delete_exists() -> None:
    text = _RULE.read_text(encoding="utf-8")
    assert "alwaysApply: true" in text
    assert "rm -rf /" in text
    assert "Never" in text or "never" in text
