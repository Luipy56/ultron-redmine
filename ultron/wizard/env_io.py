"""Read and write `.env` with in-place key updates (preserves unrelated lines)."""

from __future__ import annotations

import re
from pathlib import Path

_ENV_LINE_RE = re.compile(
    r"^[ \t]*(?:export[ \t]+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(?P<val>.*)$"
)


def _unquote_val(raw: str) -> str:
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1].replace('\\"', '"').replace("\\n", "\n")
    return s


def _quote_val(value: str) -> str:
    if not value:
        return ""
    if re.search(r'[\s#"\']', value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    """Load KEY=value pairs from a dotenv-style file (ignores blank lines and # comments)."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(line)
        if m:
            out[m.group("key")] = _unquote_val(m.group("val"))
    return out


def read_env_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def apply_env_updates(lines: list[str], updates: dict[str, str]) -> list[str]:
    """Return new lines: replace existing keys, append missing keys at the end."""
    key_at_index: dict[str, int] = {}
    for i, line in enumerate(lines):
        m = _ENV_LINE_RE.match(line)
        if m:
            key_at_index[m.group("key")] = i
    out = list(lines)
    for key, value in updates.items():
        new_line = f"{key}={_quote_val(value)}"
        if key in key_at_index:
            out[key_at_index[key]] = new_line
        else:
            out.append(new_line)
    return out


def write_env_merged(path: Path, existing_lines: list[str], updates: dict[str, str]) -> None:
    """Write `.env` by merging ``updates`` into ``existing_lines``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = apply_env_updates(existing_lines, updates)
    text = "\n".join(merged)
    if text and not text.endswith("\n"):
        text += "\n"
    elif not text:
        text = ""
    path.write_text(text, encoding="utf-8")
