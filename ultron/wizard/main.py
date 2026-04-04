"""Interactive terminal configuration wizard entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ultron.wizard.env_io import parse_env_file, read_env_lines, write_env_merged
from ultron.wizard.masking import is_sensitive_key, mask_secret
from ultron.wizard.sections import (
    section_admins,
    section_discord_bot,
    section_discord_server,
    section_llm,
    section_paths,
    section_redmine,
    section_yaml_behavior,
)
from ultron.wizard.state import WizardState
from ultron.wizard.paths import resolve_config_path
from ultron.wizard.ui import ReturnToMenu, ask, patch_question_with_return
from ultron.wizard.yaml_io import dump_yaml, load_default_config_from_example, load_yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _load_questionary() -> Any:
    try:
        import questionary
    except ImportError as e:
        raise RuntimeError(
            "The wizard requires the optional 'wizard' extra. Install with:\n"
            "  pip install 'ultron-bot[wizard]'\n"
        ) from e
    return questionary


def _print_env_summary(env: dict[str, str]) -> None:
    keys = sorted(env.keys())
    for k in keys:
        v = env[k]
        display = mask_secret(k, v) if is_sensitive_key(k) else (v or "(empty)")
        print(f"  {k}={display}")


def _print_yaml_summary(y: dict[str, Any], *, max_chars: int = 1200) -> None:
    s = dump_yaml(y)
    if len(s) > max_chars:
        print(s[:max_chars] + f"\n... ({len(s)} chars total)\n")
    else:
        print(s)


def run_wizard() -> int:
    q = _load_questionary()

    repo_root = _repo_root()
    env_path = repo_root / ".env"
    env_lines = read_env_lines(env_path)
    env = parse_env_file(env_path)

    config_path = resolve_config_path(env)
    yaml_data = load_yaml(config_path)
    if not yaml_data:
        yaml_data = load_default_config_from_example(repo_root)

    state = WizardState(
        repo_root=repo_root,
        env_path=env_path,
        env_lines=env_lines,
        env=dict(env),
        config_path=config_path,
        yaml_data=yaml_data,
    )

    print("Ultron configuration wizard (terminal)\n")
    print("Tip: Y/N/r in prompts; r in lists; ^R in text fields.\n")
    print(f"Repository root: {repo_root}")
    print(f".env path: {env_path} ({'exists' if env_path.is_file() else 'missing'})")
    print(f"config.yaml path: {config_path} ({'exists' if config_path.is_file() else 'will be created'})\n")

    if env_path.is_file() or config_path.is_file():
        print("Current environment (secrets masked):")
        _print_env_summary(state.env)
        print()
        try:
            qu = patch_question_with_return(
                q.confirm(
                    "Load these values and continue to the main menu?",
                    default=True,
                    qmark=">",
                    instruction="(Y/n/r)",
                )
            )
            if not ask(qu):
                print("Aborted.")
                return 0
        except ReturnToMenu:
            pass

    menu_labels = [
        "Paths (CONFIG_PATH, ULTRON_STATE_DIR)",
        "Redmine (URL, API key, test)",
        "Discord bot (token, application ID)",
        "Discord server & channels (guild, logs, reports)",
        "Admins & owner contact",
        "Language model (.env + llm_chain)",
        "YAML: timezone, discord toggles (metadata header, slash LLM hints, nl_commands), listings, report_schedule, logging, status strings",
        "Review & save",
        "Exit without saving",
    ]

    section_handlers = [
        lambda: section_paths(q, state),
        lambda: section_redmine(q, state),
        lambda: section_discord_bot(q, state),
        lambda: section_discord_server(q, state),
        lambda: section_admins(q, state),
        lambda: section_llm(q, state),
        lambda: section_yaml_behavior(q, state),
    ]

    while True:
        state.config_path = resolve_config_path(state.env)

        # use_shortcuts=True binds 1–9 (see questionary InquirerControl.SHORTCUT_KEYS).
        try:
            qu = patch_question_with_return(
                q.select(
                    "Main menu — press 1–9 or use arrows, Enter to confirm",
                    choices=menu_labels,
                    use_shortcuts=True,
                    use_arrow_keys=True,
                    qmark=">",
                    instruction="(r)",
                )
            )
            choice = ask(qu)
        except ReturnToMenu:
            continue

        if choice is None:
            print("Exit without saving.")
            return 0
        try:
            idx = menu_labels.index(choice)
        except ValueError:
            continue
        if idx == len(menu_labels) - 1:
            print("Exit without saving.")
            return 0

        if idx == len(menu_labels) - 2:
            state.config_path = resolve_config_path(state.env)
            print("\n--- Review ---\n")
            print("Environment (masked secrets):")
            _print_env_summary(state.env)
            print("\nconfig.yaml preview:\n")
            _print_yaml_summary(state.yaml_data)
            try:
                qu = patch_question_with_return(
                    q.confirm(
                        "Write .env and config.yaml now?",
                        default=False,
                        qmark=">",
                        instruction="(y/N/r)",
                    )
                )
                if not ask(qu):
                    continue
            except ReturnToMenu:
                continue
            try:
                keys_to_write = dict(state.env)
                write_env_merged(state.env_path, state.env_lines, keys_to_write)
                state.config_path.parent.mkdir(parents=True, exist_ok=True)
                state.config_path.write_text(dump_yaml(state.yaml_data), encoding="utf-8")
                print(f"\nWrote:\n  {state.env_path}\n  {state.config_path}\n")
                print("Restart the bot to apply changes.")
                return 0
            except OSError as e:
                print(f"Write failed: {e}", file=sys.stderr)
                return 1

        if idx is not None and 0 <= idx < len(section_handlers):
            try:
                section_handlers[idx]()
            except ReturnToMenu:
                continue
            except KeyboardInterrupt:
                print("\nInterrupted.")
                return 130

    return 0


def main() -> None:
    raise SystemExit(run_wizard())
