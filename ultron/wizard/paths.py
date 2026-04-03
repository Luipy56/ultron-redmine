"""Path helpers for the wizard."""

from __future__ import annotations

from pathlib import Path


def resolve_config_path(env: dict[str, str], *, cwd: Path | None = None) -> Path:
    """Resolve ``CONFIG_PATH`` the same way operators run the bot (relative to cwd)."""
    cwd = cwd or Path.cwd()
    raw = (env.get("CONFIG_PATH") or "config.yaml").strip() or "config.yaml"
    p = Path(raw)
    return (p if p.is_absolute() else cwd / p).resolve()
