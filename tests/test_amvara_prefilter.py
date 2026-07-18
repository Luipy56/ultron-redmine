from __future__ import annotations

from ultron.amvara.prefilter import MessageIntent, classify_message, extract_amvara_hosts, extract_issue_ids


def test_extract_amvara_hosts() -> None:
    assert extract_amvara_hosts("connect to amvara3 and check RAM") == ("amvara3",)
    assert extract_amvara_hosts("AMVARA10 and amvara2") == ("amvara10", "amvara2")


def test_classify_amvara_only() -> None:
    r = classify_message("Ultron connect to amvara3 and tell me about RAM")
    assert r.intent == MessageIntent.AMVARA_ONLY
    assert r.amvara_hosts == ("amvara3",)


def test_classify_redmine_only() -> None:
    r = classify_message("summarize ticket 7001")
    assert r.intent == MessageIntent.REDMINE_ONLY
    assert r.issue_ids == (7001,)


def test_classify_compound() -> None:
    r = classify_message(
        "connect to amvara3, check journal and add a summary to Redmine issue 7001"
    )
    assert r.intent == MessageIntent.COMPOUND
    assert "amvara3" in r.amvara_hosts
    assert 7001 in r.issue_ids


def test_classify_general() -> None:
    r = classify_message("hello there")
    assert r.intent == MessageIntent.GENERAL


def test_classify_find_issue() -> None:
    r = classify_message("find issue about sso login failure")
    assert r.intent == MessageIntent.REDMINE_ONLY
    assert r.has_redmine_signal
