"""Prompt helpers: return-to-menu key bindings and safe ask."""

from __future__ import annotations

from typing import Any

# Do not import questionary at module load — keeps `pip install ultron-bot` working without [wizard].


class ReturnToMenu(Exception):
    """User chose to return to the main menu (see :func:`patch_question_with_return`)."""


def _return_kb_simple() -> Any:
    """``r`` / ``R`` — for confirm and select (no text typing)."""
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("r", eager=True)
    @kb.add("R", eager=True)
    def _(event: object) -> None:
        event.app.exit(exception=ReturnToMenu())  # type: ignore[attr-defined]

    return kb


def _return_kb_text() -> Any:
    """``Ctrl+r`` — for free-text prompts so plain ``r`` can appear in URLs and copy."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    kb = KeyBindings()

    @kb.add(Keys.ControlR, eager=True)
    def _(event: object) -> None:
        event.app.exit(exception=ReturnToMenu())  # type: ignore[attr-defined]

    return kb


def patch_question_with_return(question: Any, *, for_text: bool = False) -> Any:
    """Merge return-to-menu bindings so they take effect before generic handlers."""
    from prompt_toolkit.key_binding import merge_key_bindings

    extra = _return_kb_text() if for_text else _return_kb_simple()
    app = question.application
    app.key_bindings = merge_key_bindings([extra, app.key_bindings])
    return question


def ask(question: Any) -> Any:
    """Run the prompt; propagates :exc:`ReturnToMenu` (unlike ``Question.ask``)."""
    return question.unsafe_ask()
