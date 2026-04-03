"""Mutable state for an interactive wizard session."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WizardState:
    """Working copy of `.env` keys and ``config.yaml`` data."""

    repo_root: Path
    env_path: Path
    env_lines: list[str] = field(default_factory=list)
    #: All keys read from / written to `.env` for this session.
    env: dict[str, str] = field(default_factory=dict)
    config_path: Path = field(default_factory=Path)
    yaml_data: dict[str, Any] = field(default_factory=dict)

    def env_get(self, key: str, default: str = "") -> str:
        return self.env.get(key, default)

    def env_set(self, key: str, value: str) -> None:
        self.env[key] = value

    def ensure_yaml(self) -> None:
        if not self.yaml_data:
            from ultron.wizard.yaml_io import load_default_config_from_example

            self.yaml_data = load_default_config_from_example(self.repo_root)
