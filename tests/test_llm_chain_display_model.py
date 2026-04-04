"""LLMChainClient: user-visible model label must follow the selected chain slot."""

from __future__ import annotations

from ultron.config import LLMProviderResolved
from ultron.llm import LLMChainClient


def test_display_model_for_start_respects_slot() -> None:
    r0 = LLMProviderResolved(
        base_url="http://first.example/v1",
        models=("gpt-slot0",),
        api_key="k0",
        timeout_seconds=60.0,
        max_retries=0,
        name="a",
    )
    r1 = LLMProviderResolved(
        base_url="http://second.example/v1",
        models=("llama-slot1",),
        api_key="k1",
        timeout_seconds=60.0,
        max_retries=0,
        name="b",
    )
    chain = LLMChainClient.from_resolved((r0, r1))
    assert chain.model == "gpt-slot0"
    assert chain.display_model_for_start(None) == "gpt-slot0"
    assert chain.display_model_for_start("0") == "gpt-slot0"
    assert chain.display_model_for_start("1") == "llama-slot1"
