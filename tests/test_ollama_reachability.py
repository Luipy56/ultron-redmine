from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from ultron.ollama_reachability import (
    REASON_BUSY_PROBE,
    REASON_BUSY_PS,
    REASON_UNREACHABLE,
    ensure_ollama_ready_for_inference,
    ollama_ps_has_loaded_models,
    ollama_is_busy_from_ps,
)


def test_ollama_ps_has_loaded_models() -> None:
    assert ollama_ps_has_loaded_models(None) is False
    assert ollama_ps_has_loaded_models({}) is False
    assert ollama_ps_has_loaded_models({"models": []}) is False
    assert ollama_ps_has_loaded_models({"models": [{"name": "gemma"}]}) is True


def test_ollama_is_busy_from_ps_respects_flag() -> None:
    with patch(
        "ultron.ollama_reachability.fetch_ollama_ps_sync",
        return_value={"models": [{"name": "x"}]},
    ):
        assert ollama_is_busy_from_ps("http://h/v1", busy_if_models_loaded=False) is False
        assert ollama_is_busy_from_ps("http://h/v1", busy_if_models_loaded=True) is True


def test_ensure_ready_unreachable() -> None:
    async def _run() -> None:
        with patch("ultron.ollama_reachability.ensure_ollama_reachable", return_value=(False, False)):
            r = await ensure_ollama_ready_for_inference("http://127.0.0.1:11434/v1", model="m")
        assert r.ok is False
        assert r.reason == REASON_UNREACHABLE

    asyncio.run(_run())


def test_ensure_ready_busy_if_models_loaded() -> None:
    async def _run() -> None:
        with patch("ultron.ollama_reachability.ensure_ollama_reachable", return_value=(True, False)):
            with patch("ultron.ollama_reachability.ollama_is_busy", return_value=True):
                r = await ensure_ollama_ready_for_inference(
                    "http://127.0.0.1:11434/v1",
                    model="m",
                    busy_check=True,
                    busy_if_models_loaded=True,
                    inference_probe_seconds=0,
                )
        assert r.ok is False
        assert r.reason == REASON_BUSY_PS

    asyncio.run(_run())


def test_ensure_ready_probe_timeout() -> None:
    async def _run() -> None:
        with patch("ultron.ollama_reachability.ensure_ollama_reachable", return_value=(True, False)):
            with patch(
                "ultron.ollama_reachability.inference_probe",
                return_value=(False, REASON_BUSY_PROBE),
            ):
                r = await ensure_ollama_ready_for_inference(
                    "http://127.0.0.1:11434/v1",
                    model="gemma",
                    busy_check=True,
                    busy_if_models_loaded=False,
                    inference_probe_seconds=12,
                )
        assert r.ok is False
        assert r.reason == REASON_BUSY_PROBE

    asyncio.run(_run())


def test_ensure_ready_ok_skips_probe_when_disabled() -> None:
    async def _run() -> None:
        with patch("ultron.ollama_reachability.ensure_ollama_reachable", return_value=(True, False)):
            r = await ensure_ollama_ready_for_inference(
                "http://127.0.0.1:11434/v1",
                model="gemma",
                busy_check=False,
                inference_probe_seconds=12,
            )
        assert r.ok is True
        assert r.reason is None

    asyncio.run(_run())


def test_ensure_ready_ok_with_successful_probe() -> None:
    async def _run() -> None:
        with patch("ultron.ollama_reachability.ensure_ollama_reachable", return_value=(True, False)):
            with patch("ultron.ollama_reachability.inference_probe", return_value=(True, None)):
                r = await ensure_ollama_ready_for_inference(
                    "http://127.0.0.1:11434/v1",
                    model="gemma",
                    busy_check=True,
                    inference_probe_seconds=12,
                )
        assert r.ok is True

    asyncio.run(_run())


def test_fetch_ps_payload_shape_roundtrip() -> None:
    """Document expected /api/ps JSON shape used by busy_if_models_loaded."""
    payload = json.loads('{"models":[{"name":"llama","size_vram":1}]}')
    assert ollama_ps_has_loaded_models(payload) is True
