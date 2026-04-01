"""Structured tags for internal Redmine + LLM pipelines (grep: WORKFLOW)."""

from __future__ import annotations

import logging
import traceback

from ultron.llm import safe_exc_message

TAG = "WORKFLOW"


def wf_info(logger: logging.Logger, flow: str, step: str, fmt: str, *args: object) -> None:
    logger.info("%s | %s | %s | " + fmt, TAG, flow, step, *args)


def wf_exception(logger: logging.Logger, flow: str, exc: BaseException) -> None:
    """Log failure with traceback frames but without huge HTML bodies in exception messages."""
    tb = "".join(traceback.format_tb(exc.__traceback__)) if exc.__traceback__ else ""
    logger.error(
        "%s | %s | ERROR | %s: %s%s",
        TAG,
        flow,
        type(exc).__name__,
        safe_exc_message(exc),
        f"\n{tb}" if tb else "",
    )
