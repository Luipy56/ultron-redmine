"""Parse OpenSSH client config for Amvara host aliases."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_AMVARA_HOST_RE = re.compile(r"^amvara\d+$", re.IGNORECASE)


@dataclass(frozen=True)
class SshHostEntry:
    name: str
    hostname: str | None
    user: str | None
    port: int = 22


def _normalize_host_name(name: str) -> str:
    return name.strip().casefold()


def parse_ssh_config_hosts(path: Path) -> dict[str, SshHostEntry]:
    """Return ``amvaraN`` Host blocks from an OpenSSH config file."""
    if not path.is_file():
        return {}

    entries: dict[str, SshHostEntry] = {}
    current_names: list[str] = []
    current_hostname: str | None = None
    current_user: str | None = None
    current_port: int | None = None

    def flush() -> None:
        nonlocal current_names, current_hostname, current_user, current_port
        for raw in current_names:
            key = _normalize_host_name(raw)
            if not _AMVARA_HOST_RE.match(key):
                continue
            entries[key] = SshHostEntry(
                name=key,
                hostname=current_hostname,
                user=current_user,
                port=current_port if current_port is not None else 22,
            )
        current_names = []
        current_hostname = None
        current_user = None
        current_port = None

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lower = stripped.lower()
        if lower.startswith("host "):
            flush()
            pattern = stripped[5:].strip()
            current_names = [p for p in pattern.split() if p and p != "*"]
            continue
        if lower.startswith("hostname "):
            current_hostname = stripped.split(None, 1)[1].strip() if " " in stripped else None
            continue
        if lower.startswith("user "):
            current_user = stripped.split(None, 1)[1].strip() if " " in stripped else None
            continue
        if lower.startswith("port "):
            try:
                current_port = int(stripped.split(None, 1)[1].strip())
            except (IndexError, ValueError):
                current_port = None
            continue

    flush()
    return entries
