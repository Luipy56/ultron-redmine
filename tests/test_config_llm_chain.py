from __future__ import annotations

from pathlib import Path

import pytest

from ultron.config import llm_chain_slash_flags, load_config
from ultron.settings import _config_file_has_llm_chain

_MINIMAL_TOP = """
timezone: UTC
discord: {}
reports: {}
report_schedule: []
logging: {}
"""

def _entry(enabled: str, model: str = "m1") -> str:
    return f"""
  - enabled: {enabled}
    base_url: https://example.com/v1
    model: {model}
    api_key_env: MY_KEY
"""


def test_llm_chain_model_as_list(tmp_path: Path) -> None:
    body = (
        _MINIMAL_TOP
        + "llm_chain:\n"
        + """
  - enabled: true
    base_url: https://example.com/v1
    model:
      - primary-m
      - alt-m
    api_key_env: MY_KEY
"""
    )
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.llm_chain is not None
    assert cfg.llm_chain[0].models == ("primary-m", "alt-m")
    assert cfg.llm_chain[0].model == "primary-m"


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
    assert cfg.llm_chain[0].models == ("m2",)
    assert cfg.llm_chain[0].model == "m2"


def test_llm_chain_all_disabled_is_none(tmp_path: Path) -> None:
    body = _MINIMAL_TOP + "llm_chain:\n" + _entry("false")
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.warns(UserWarning, match="llm_chain"):
        cfg = load_config(p)
    assert cfg.llm_chain is None


def test_config_file_has_llm_chain_false_when_all_disabled(tmp_path: Path) -> None:
    body = _MINIMAL_TOP + "llm_chain:\n" + _entry("false")
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.warns(UserWarning, match="llm_chain"):
        assert _config_file_has_llm_chain(str(p)) is False


def test_config_file_has_llm_chain_true_when_enabled_present(tmp_path: Path) -> None:
    body = _MINIMAL_TOP + "llm_chain:\n" + _entry("true")
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    assert _config_file_has_llm_chain(str(p)) is True


def test_import_ultron_config_module() -> None:
    from ultron import config as cfg

    assert hasattr(cfg, "load_config")


def test_llm_chain_slash_flags_empty_chain(tmp_path: Path) -> None:
    p = tmp_path / "cfg.yaml"
    p.write_text(_MINIMAL_TOP + "llm_chain: []\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.llm_chain is None
    assert llm_chain_slash_flags(cfg.llm_chain) == (False, False)


def test_llm_chain_slash_flags_single_provider_single_model(tmp_path: Path) -> None:
    body = _MINIMAL_TOP + "llm_chain:\n" + _entry("true", "only-one")
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.llm_chain is not None
    assert llm_chain_slash_flags(cfg.llm_chain) == (True, True)


def test_llm_chain_slash_flags_multi_model_only(tmp_path: Path) -> None:
    body = (
        _MINIMAL_TOP
        + "llm_chain:\n"
        + """
  - enabled: true
    base_url: https://example.com/v1
    model:
      - a
      - b
    api_key_env: MY_KEY
"""
    )
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.llm_chain is not None
    assert len(cfg.llm_chain) == 1
    assert llm_chain_slash_flags(cfg.llm_chain) == (True, True)
