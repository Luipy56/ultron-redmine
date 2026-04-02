from __future__ import annotations

from pathlib import Path

from ultron.config import load_config
from ultron.settings import _config_file_has_llm_chain

_MINIMAL_TOP = """
timezone: UTC
discord: {}
reports: {}
schedules:
  abandoned: {}
  stale_new: {}
logging: {}
"""

def _entry(enabled: str, model: str = "m1") -> str:
    return f"""
  - enabled: {enabled}
    base_url: https://example.com/v1
    model: {model}
    api_key_env: MY_KEY
"""


def test_llm_chain_skips_disabled_entries(tmp_path: Path) -> None:
    body = (
        _MINIMAL_TOP
        + "llm_chain:\n"
        + _entry("false", "skip")
        + _entry("true", "m2")
    )
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.llm_chain is not None
    assert len(cfg.llm_chain) == 1
    assert cfg.llm_chain[0].model == "m2"


def test_llm_chain_all_disabled_is_none(tmp_path: Path) -> None:
    body = _MINIMAL_TOP + "llm_chain:\n" + _entry("false")
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.llm_chain is None


def test_config_file_has_llm_chain_false_when_all_disabled(tmp_path: Path) -> None:
    body = _MINIMAL_TOP + "llm_chain:\n" + _entry("false")
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    assert _config_file_has_llm_chain(str(p)) is False


def test_config_file_has_llm_chain_true_when_enabled_present(tmp_path: Path) -> None:
    body = _MINIMAL_TOP + "llm_chain:\n" + _entry("true")
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    assert _config_file_has_llm_chain(str(p)) is True


def test_import_ultron_config_module() -> None:
    from ultron import config as cfg

    assert hasattr(cfg, "load_config")
