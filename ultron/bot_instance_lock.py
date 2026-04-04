"""Single Discord gateway process per state directory (prevents duplicate bots on one host)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TextIO

logger = logging.getLogger(__name__)

_LOCK_NAME = ".ultron-discord-gateway.lock"


def acquire(state_dir: Path) -> TextIO | None:
    """Take an exclusive lock so only one bot process uses ``state_dir`` (Unix ``fcntl``).

    Returns an open file object that must stay open until shutdown, or ``None`` if locking
    is unavailable (e.g. Windows without ``fcntl``).
    """
    try:
        import fcntl
    except ImportError:
        logger.warning(
            "fcntl unavailable — cannot enforce a single bot instance lock; "
            "do not run two bots with the same Discord token."
        )
        return None

    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / _LOCK_NAME
    fp: TextIO
    fp = open(path, "w", encoding="utf-8")  # noqa: SIM115 — held until release()
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fp.close()
        raise RuntimeError(
            f"Another Ultron bot already holds the lock on {path} (same ULTRON_STATE_DIR). "
            "Only one Discord-connected process may use this data directory and token. "
            "Stop the duplicate container or `python -m ultron` process."
        ) from None
    fp.write(f"pid={os.getpid()}\n")
    fp.flush()
    return fp


def release(fp: TextIO | None) -> None:
    if fp is None:
        return
    try:
        import fcntl

        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fp.close()
    except OSError:
        pass
