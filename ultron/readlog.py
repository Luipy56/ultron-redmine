from __future__ import annotations

import logging


def log_read_payload(*, label: str, text: str) -> None:
    """Log full text Ultron ingested (Redmine body, Discord input, LLM prompts). Use only when config allows."""
    log = logging.getLogger("ultron.read")
    log.info("read_payload label=%s chars=%s\n%s", label, len(text), text)
