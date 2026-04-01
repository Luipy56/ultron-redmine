from __future__ import annotations

import sys

from ultron.settings import load_env
from ultron.state_store import consume_token_add_whitelist


def cmd_add_token(token: str) -> int:
    """Approve a pending Discord user by token. Returns process exit code."""
    try:
        env = load_env()
        uid = consume_token_add_whitelist(env.state_dir, token)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    except OSError as e:
        print(f"state file error: {e}", file=sys.stderr)
        return 1
    print(f"Whitelisted Discord user id {uid}.")
    return 0
