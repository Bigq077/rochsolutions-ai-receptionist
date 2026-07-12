"""Unit + end-to-end tests for app.obs.judge."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from app.obs import judge


def _good_json(**over) -> str:
    payload = {
        "outcome": "booked",
        "quality_score": 5,
        "intent_resolved": True,
        "failure_tags": [],
        "action_needed": "none",
        "evidence": "Caller booked Monday cleanly.",
        "rubric_version": judge.RUBRIC_VERSION,
    }
    payload.update(over)
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# parse_and_validate — pure, no API
# ---------------------------------------------------------------------------

def test_parse_valid_json(fixture_call):
    j = judge.parse_and_validate(_good_json(), fixture_call)
    assert j["quality_score"] == 5
    assert j["outcome"] == "booked"
    assert j["intent_resolved"] is True
    assert j["failure_tags"] == []
    assert j["rubric_version"] == judge.RUBRIC_VERSION
    assert j["call_sid"] == "CAjudge0001"


def test_parse_strips_code_fences_and_prose(fixture_call):
    text = "Here is my judgement:\n```json\n" + _good_json(quality_score=3) + "\n```\nThanks!"
    j = judge.parse_and_validate(text, fixture_call)
    assert j["quality_score"] == 3


def test_parse_clamps_out_of_range_score(fixture_call):
    assert judge.parse_and_validate(_good_json(quality_score=9), fixture_call)["quality_score"] == 5
    assert judge.parse_and_validate(_good_json(quality_score=0), fixture_call)["quality_score"] == 1


def test_parse_filters_unknown_tags(fixture_call):
    text = _good_json(failure_tags=["wrong_info", "made_up_tag", "loop"])
    j = judge.parse_and_validate(text, fixture_call)
    assert j["failure_tags"] == ["wrong_info", "loop"]


def test_parse_rejects_bad_outcome_but_keeps_row(fixture_call):
    j = judge.parse_and_validate(_good_json(outcome="nonsense"), fixture_call)
    assert j is not None
    assert j["outcome"] is None
    assert j["quality_score"] == 5


def test_parse_returns_none_on_garbage(fixture_call):
    assert judge.parse_and_validate("no json here", fixture_call) is None
    assert judge.parse_and_validate("", fixture_call) is None


def test_parse_returns_none_without_score(fixture_call):
    text = json.dumps({"outcome": "booked", "failure_tags": []})
    assert judge.parse_and_validate(text, fixture_call) is None


# ---------------------------------------------------------------------------
# Tiered alerting (v2): action_needed parsing + callback/review routing
# ---------------------------------------------------------------------------

def test_parse_reads_action_needed(fixture_call):
    j = judge.parse_and_validate(_good_json(quality_score=1, action_needed="callback"), fixture_call)
    assert j["action_needed"] == "callback"


def test_parse_defaults_action_from_score_when_missing(fixture_call):
    # No action_needed field → low score defaults to review, good score to none.
    low = json.dumps({"outcome": "abandoned", "quality_score": 2, "failure_tags": ["loop"]})
    assert judge.parse_and_validate(low, fixture_call)["action_needed"] == "review"
    good = json.dumps({"outcome": "booked", "quality_score": 5, "failure_tags": []})
    assert judge.parse_and_validate(good, fixture_call)["action_needed"] == "none"


def test_missed_escalation_forces_callback(fixture_call):
    # Even if the model says "review", a missed escalation is upgraded to callback.
    text = _good_json(quality_score=4, failure_tags=["missed_escalation"], action_needed="review")
    assert judge.parse_and_validate(text, fixture_call)["action_needed"] == "callback"


def test_needs_callback_and_needs_review_are_exclusive():
    cb = {"action_needed": "callback", "quality_score": 1, "failure_tags": []}
    rv = {"action_needed": "review", "quality_score": 2, "failure_tags": ["loop"]}
    ok = {"action_needed": "none", "quality_score": 5, "failure_tags": []}
    assert judge.needs_callback(cb) is True and judge.needs_review(cb) is False
    assert judge.needs_review(rv) is True and judge.needs_callback(rv) is False
    assert judge.needs_callback(ok) is False and judge.needs_review(ok) is False


def test_missed_escalation_never_falls_to_review():
    esc = {"action_needed": "review", "quality_score": 4, "failure_tags": ["missed_escalation"]}
    assert judge.needs_callback(esc) is True
    assert judge.needs_review(esc) is False


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------

def test_prompt_contains_transcript_and_rubric(fixture_call):
    prompt = judge.build_prompt(fixture_call)
    assert "Monday please." in prompt
    assert judge.RUBRIC_VERSION in prompt
    assert "theorem" in prompt


# ---------------------------------------------------------------------------
# judge_call — gated + mocked model
# ---------------------------------------------------------------------------

async def test_judge_call_disabled_returns_none(fixture_call):
    # judge_enabled fixture NOT used → flag off
    assert await judge.judge_call(fixture_call) is None


async def test_judge_call_with_mocked_model(judge_enabled, fixture_call):
    with patch("app.obs.judge._call_model", new=AsyncMock(return_value=_good_json(quality_score=4))):
        j = await judge.judge_call(fixture_call)
    assert j["quality_score"] == 4


async def test_judge_call_swallows_model_error(judge_enabled, fixture_call):
    with patch("app.obs.judge._call_model", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert await judge.judge_call(fixture_call) is None


# ---------------------------------------------------------------------------
# run_and_store — end to end (store + judge + alert bridge), all offline
# ---------------------------------------------------------------------------

async def test_run_and_store_persists_judgement(sqlite_store, judge_enabled, fixture_record, fixture_turns):
    sqlite_store.capture_call(fixture_record, fixture_turns)
    with patch("app.obs.judge._call_model", new=AsyncMock(return_value=_good_json(quality_score=5))):
        j = await judge.run_and_store("CAjudge0001")
    assert j["quality_score"] == 5
    stored = sqlite_store.get_call("CAjudge0001")
    assert stored["quality_score"] == 5
    assert stored["rubric_version"] == judge.RUBRIC_VERSION
    assert stored["judged_at"] is not None


async def test_run_and_store_callback_call_texts_immediately(
    sqlite_store, judge_enabled, alerts_on, fixture_record, fixture_turns
):
    """A callback-classified call texts the operator now, with the caller's number."""
    sqlite_store.capture_call(fixture_record, fixture_turns)
    cb = _good_json(quality_score=1, outcome="abandoned",
                    failure_tags=["dead_end"], action_needed="callback", intent_resolved=False)
    with patch("app.obs.alerts.send_sms", new=AsyncMock(return_value="SM1")) as mock_sms, \
         patch("app.obs.judge._call_model", new=AsyncMock(return_value=cb)):
        await judge.run_and_store("CAjudge0001")
    mock_sms.assert_awaited_once()
    msg = mock_sms.await_args.kwargs["message"]
    assert "CALL BACK" in msg
    assert "+440000000000" in msg  # the caller's number, so Quentin knows who to ring
    assert sqlite_store.get_call("CAjudge0001")["action_needed"] == "callback"


async def test_run_and_store_review_call_stays_silent(
    sqlite_store, judge_enabled, alerts_on, fixture_record, fixture_turns
):
    """A merely-clumsy 'review' call does NOT text — it waits for the daily digest."""
    sqlite_store.capture_call(fixture_record, fixture_turns)
    rv = _good_json(quality_score=2, outcome="no_booking",
                    failure_tags=["loop"], action_needed="review")
    with patch("app.obs.alerts.send_sms", new=AsyncMock(return_value="SM1")) as mock_sms, \
         patch("app.obs.judge._call_model", new=AsyncMock(return_value=rv)):
        await judge.run_and_store("CAjudge0001")
    mock_sms.assert_not_awaited()
    assert sqlite_store.get_call("CAjudge0001")["action_needed"] == "review"


async def test_run_and_store_missed_escalation_texts_even_if_model_said_review(
    sqlite_store, judge_enabled, alerts_on, fixture_record, fixture_turns
):
    sqlite_store.capture_call(fixture_record, fixture_turns)
    esc = _good_json(quality_score=3, failure_tags=["missed_escalation"], action_needed="review")
    with patch("app.obs.alerts.send_sms", new=AsyncMock(return_value="SM1")) as mock_sms, \
         patch("app.obs.judge._call_model", new=AsyncMock(return_value=esc)):
        await judge.run_and_store("CAjudge0001")
    mock_sms.assert_awaited_once()  # safety floor upgraded it to a callback


async def test_run_and_store_good_call_no_alert(
    sqlite_store, judge_enabled, alerts_on, fixture_record, fixture_turns
):
    sqlite_store.capture_call(fixture_record, fixture_turns)
    with patch("app.obs.alerts.send_sms", new=AsyncMock(return_value="SM1")) as mock_sms, \
         patch("app.obs.judge._call_model", new=AsyncMock(return_value=_good_json(quality_score=5))):
        await judge.run_and_store("CAjudge0001")
    mock_sms.assert_not_awaited()


async def test_run_and_store_missing_call_returns_none(sqlite_store, judge_enabled):
    with patch("app.obs.judge._call_model", new=AsyncMock(return_value=_good_json())):
        assert await judge.run_and_store("CAnope") is None
