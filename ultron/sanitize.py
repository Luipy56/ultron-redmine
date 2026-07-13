from __future__ import annotations

import re

# Discord bot token shape (three dot-separated segments).
_DISCORD_TOKEN_RE = re.compile(
    r"\bMT[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{10,}\b"
)
_KV_SECRET_RE = re.compile(
    r"(?im)^(\s*(?:"
    r"api[_-]?key|secret|token|password|passwd|authorization|bearer"
    r")\s*[:=]\s*)(\S+)\s*$"
)
_BEARER_RE = re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9._\-+/=]{20,})")
_ENV_ASSIGN_RE = re.compile(
    r"(?im)^(\s*(?:"
    r"DISCORD_TOKEN|DISCORD_BOT_TOKEN|OPENAI_API_KEY|REDMINE_API_KEY|"
    r"LLM_API_KEY|GH_TOKEN|GITHUB_TOKEN|ULTRON_[A-Z_]*TOKEN"
    r")\s*=\s*)(.+)$"
)
_SSH_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:OPENSSH|RSA|EC|DSA|ENCRYPTED) PRIVATE KEY-----[\s\S]*?"
    r"-----END (?:OPENSSH|RSA|EC|DSA|ENCRYPTED) PRIVATE KEY-----"
)
_REDACT = "[REDACTED]"


def _redact_known_literals(text: str, literals: list[str]) -> str:
    out = text
    for val in literals:
        v = val.strip()
        if len(v) >= 8:
            out = out.replace(v, _REDACT)
    return out


def sanitize_for_discord(text: str, *, secret_literals: list[str] | None = None) -> str:
    """Strip likely secrets before Discord-bound agent output."""
    if not text:
        return text

    literals = list(secret_literals or [])
    out = _redact_known_literals(text, literals)
    out = _DISCORD_TOKEN_RE.sub(_REDACT, out)
    out = _KV_SECRET_RE.sub(rf"\1{_REDACT}", out)
    out = _BEARER_RE.sub(rf"\1{_REDACT}", out)
    out = _ENV_ASSIGN_RE.sub(rf"\1{_REDACT}", out)
    out = _SSH_PRIVATE_KEY_RE.sub(_REDACT, out)
    return out
