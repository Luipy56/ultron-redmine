from __future__ import annotations

import fcntl
import json
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

WHITELIST_FILE = "whitelist.json"
ADMINS_FILE = "admins.json"
PENDING_FILE = "pending_tokens.json"
LOCK_FILE = ".lock"

TOKEN_TTL_SECONDS = 300

_whitelist_mtime: float | None = None
_whitelist_ids: frozenset[int] | None = None

_admins_mtime: float | None = None
_admins_ids: frozenset[int] | None = None


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=".tmp_",
        suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@contextmanager
def state_lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / LOCK_FILE
    with open(lock_path, "a", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        yield


def read_whitelist_raw(state_dir: Path) -> list[int]:
    path = state_dir / WHITELIST_FILE
    raw = _read_json_file(path, [])
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def read_admins_raw(state_dir: Path) -> list[int]:
    path = state_dir / ADMINS_FILE
    raw = _read_json_file(path, [])
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def is_admin(state_dir: Path, user_id: int, env_admin_ids: frozenset[int]) -> bool:
    """True if user_id is in DISCORD_ADMIN_IDS or in admins.json."""
    if user_id in env_admin_ids:
        return True
    global _admins_mtime, _admins_ids
    path = state_dir / ADMINS_FILE
    if not path.is_file():
        _admins_mtime = None
        _admins_ids = frozenset()
        return False
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    if _admins_ids is not None and _admins_mtime == mtime:
        return user_id in _admins_ids
    ids_list = read_admins_raw(state_dir)
    _admins_ids = frozenset(ids_list)
    _admins_mtime = mtime
    return user_id in _admins_ids


def is_user_whitelisted(state_dir: Path, user_id: int) -> bool:
    global _whitelist_mtime, _whitelist_ids
    path = state_dir / WHITELIST_FILE
    if not path.is_file():
        _whitelist_mtime = None
        _whitelist_ids = frozenset()
        return False
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    if _whitelist_ids is not None and _whitelist_mtime == mtime:
        return user_id in _whitelist_ids
    ids_list = read_whitelist_raw(state_dir)
    _whitelist_ids = frozenset(ids_list)
    _whitelist_mtime = mtime
    return user_id in _whitelist_ids


def register_pending_token(state_dir: Path, user_id: int) -> str:
    """Create a new token for user_id; replaces any previous pending token for that user."""
    with state_lock(state_dir):
        pending_path = state_dir / PENDING_FILE
        pending: dict[str, Any] = _read_json_file(pending_path, {})
        if not isinstance(pending, dict):
            pending = {}
        to_drop = [t for t, e in pending.items() if isinstance(e, dict) and e.get("user_id") == user_id]
        for t in to_drop:
            del pending[t]
        token = secrets.token_urlsafe(32)
        pending[token] = {
            "user_id": user_id,
            "expires_at": time.time() + TOKEN_TTL_SECONDS,
        }
        _atomic_write_json(pending_path, pending)
        return token


def consume_token_add_whitelist(state_dir: Path, token: str) -> int:
    """Validate token, add user to whitelist, remove pending entry. Returns Discord user id."""
    token = token.strip()
    if not token:
        raise ValueError("empty token")
    with state_lock(state_dir):
        pending_path = state_dir / PENDING_FILE
        pending: dict[str, Any] = _read_json_file(pending_path, {})
        if not isinstance(pending, dict):
            pending = {}
        entry = pending.get(token)
        if entry is None:
            raise ValueError("unknown token")
        if not isinstance(entry, dict):
            del pending[token]
            _atomic_write_json(pending_path, pending)
            raise ValueError("invalid token data")
        expires_at = entry.get("expires_at")
        uid_raw = entry.get("user_id")
        try:
            uid = int(uid_raw)
        except (TypeError, ValueError):
            del pending[token]
            _atomic_write_json(pending_path, pending)
            raise ValueError("invalid token data") from None
        now = time.time()
        try:
            exp = float(expires_at)
        except (TypeError, ValueError):
            del pending[token]
            _atomic_write_json(pending_path, pending)
            raise ValueError("invalid token data") from None
        if now > exp:
            del pending[token]
            _atomic_write_json(pending_path, pending)
            raise ValueError("token expired")
        del pending[token]
        wl_path = state_dir / WHITELIST_FILE
        ids = read_whitelist_raw(state_dir)
        if uid not in ids:
            ids.append(uid)
            ids.sort()
        _atomic_write_json(wl_path, ids)
        _atomic_write_json(pending_path, pending)
        global _whitelist_mtime, _whitelist_ids
        _whitelist_mtime = None
        _whitelist_ids = None
        return uid
