from __future__ import annotations

from ultron.amvara.planner import parse_plan_json_text
from ultron.amvara.registry import build_amvara_registry
from ultron.config import AmvaraConfig


def _registry():
    return build_amvara_registry(
        AmvaraConfig(allowed_hosts=("amvara3", "amvara4"), merge_ssh_config=False)
    )


def test_parse_plan_amvara_and_note() -> None:
    raw = """{
      "kind": "plan",
      "steps": [
        {"kind": "amvara_audit", "host": "amvara3", "task": "Check RAM"},
        {"kind": "invoke", "command": "note", "args": {"issue_id": 7001, "text": "Audit summary"}}
      ]
    }"""
    outcome = parse_plan_json_text(raw, _registry())
    assert not hasattr(outcome, "detail")
    from ultron.amvara.planner import NLPlan

    assert isinstance(outcome, NLPlan)
    assert len(outcome.steps) == 2


def test_parse_plan_rejects_unknown_host() -> None:
    raw = """{
      "kind": "plan",
      "steps": [
        {"kind": "amvara_audit", "host": "amvara99", "task": "x"}
      ]
    }"""
    from ultron.amvara.planner import NLPlanParseError

    outcome = parse_plan_json_text(raw, _registry())
    assert isinstance(outcome, NLPlanParseError)


def test_parse_plan_rejects_forbidden_command() -> None:
    raw = """{
      "kind": "plan",
      "steps": [
        {"kind": "invoke", "command": "pi", "args": {}}
      ]
    }"""
    from ultron.amvara.planner import NLPlanParseError

    outcome = parse_plan_json_text(raw, _registry())
    assert isinstance(outcome, NLPlanParseError)
