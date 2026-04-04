"""Tests for single-instance fcntl lock (Unix)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ultron.bot_instance_lock import acquire, release


def test_acquire_release_roundtrip(tmp_path: Path) -> None:
    fp = acquire(tmp_path)
    if fp is None:
        pytest.skip("fcntl locking not available on this platform")
    try:
        assert (tmp_path / ".ultron-discord-gateway.lock").is_file()
    finally:
        release(fp)


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "fork"),
    reason="subprocess lock contention test is Unix-oriented",
)
def test_second_process_cannot_acquire_while_first_holds_lock(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    hold_script = textwrap.dedent(
        """
        import sys
        import time
        from pathlib import Path

        from ultron.bot_instance_lock import acquire, release

        fp = acquire(Path(sys.argv[1]))
        time.sleep(4)
        release(fp)
        """
    )
    env = {**os.environ, "PYTHONPATH": str(repo_root)}
    proc = subprocess.Popen(
        [sys.executable, "-c", hold_script, str(tmp_path)],
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.5)
        with pytest.raises(RuntimeError, match="Another Ultron bot already holds"):
            acquire(tmp_path)
    finally:
        proc.wait(timeout=10)
        err = proc.stderr.read() if proc.stderr else b""
        if proc.returncode != 0 and err:
            raise AssertionError(err.decode(errors="replace")) from None
