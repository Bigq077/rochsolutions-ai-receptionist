# tests/regression/test_unspoken_slot_followup.py
"""
V5 regression: caller asks for a real time that was never spoken — must still
be offerable / confirmable from session["available_days"].

Live failures (2026-07-28): CAaf76d3, CA7d424b, CAfbe9e5 — speech cap (and the
payload reshape that followed) left full availability in session, but the model
answered from what it had already said. Re-calling check_availability cannot
fix this: a fresh fetch leads with the earliest times again, and
already_retrieved tells the model to "present the existing slots".

Fix: deterministic remaining = available_days − last_offered; on "later"/else
offer the next two; on a specific time in remaining, confirm it.
"""
from __future__ import annotations

from app.tools.slot_followup import (
    remaining_slots_after_offer,
    next_slot_batch,
    utterance_requests_more_slots,
    utterance_requests_different_day,
    resolve_requested_time,
    format_next_batch_speech,
    format_time_available_speech,
    apply_next_batch_to_session,
    apply_resolved_time_to_session,
    build_followup_tool_result,
)


def _thu_six() -> list[dict]:
    """Empty Thursday 6 Aug — six theoretical evening slots."""
    date = "2026-08-06"
    times = ["16:30", "17:15", "18:00", "18:45", "19:30", "20:15"]
    spoken = [
        "half past four in the afternoon",
        "quarter past five in the evening",
        "six in the evening",
        "quarter to seven in the evening",
        "half past seven in the evening",
        "quarter past eight in the evening",
    ]
    return [{
        "date": date,
        "day_label": "Thursday 6th August",
        "slot_times": times,
        "slot_times_spoken": spoken,
        "slots": [
            {"start": f"{date}T{t}:00", "end": f"{date}T{t}:40"}
            for t in times
        ],
    }]


def _offered_first_two(days: list[dict]) -> list[dict]:
    return list(days[0]["slots"][:2])


# ── remaining / next batch ───────────────────────────────────────────────────

def test_remaining_after_first_two_starts_at_six():
    days = _thu_six()
    rem = remaining_slots_after_offer(days, _offered_first_two(days))
    assert [s["time"] for s in rem] == ["18:00", "18:45", "19:30", "20:15"]


def test_next_batch_is_two_with_more_flag():
    days = _thu_six()
    rem = remaining_slots_after_offer(days, _offered_first_two(days))
    batch, more = next_slot_batch(rem, n=2)
    assert [s["time"] for s in batch] == ["18:00", "18:45"]
    assert more is True


def test_next_batch_more_false_when_exactly_two_left():
    days = _thu_six()
    # offered first four → two left
    offered = list(days[0]["slots"][:4])
    rem = remaining_slots_after_offer(days, offered)
    batch, more = next_slot_batch(rem, n=2)
    assert [s["time"] for s in batch] == ["19:30", "20:15"]
    assert more is False


def test_remaining_empty_when_all_offered():
    days = _thu_six()
    rem = remaining_slots_after_offer(days, list(days[0]["slots"]))
    assert rem == []
    batch, more = next_slot_batch(rem, n=2)
    assert batch == []
    assert more is False


# ── intent ───────────────────────────────────────────────────────────────────

def test_later_and_else_request_more_slots():
    assert utterance_requests_more_slots("nice do you have anything later")
    assert utterance_requests_more_slots("any other times?")
    assert utterance_requests_more_slots("read out every slot")
    assert utterance_requests_more_slots("do you have any more")


def test_different_day_is_not_same_day_more():
    assert utterance_requests_different_day("shall I look at a different day")
    assert utterance_requests_more_slots("shall I look at a different day") is False


# ── resolve specific unspoken time (V5) ──────────────────────────────────────

def test_resolve_half_past_seven_in_remaining():
    days = _thu_six()
    rem = remaining_slots_after_offer(days, _offered_first_two(days))
    hit = resolve_requested_time("say like 730", rem)
    assert hit is not None
    assert hit["time"] == "19:30"


def test_resolve_quarter_to_eight_in_remaining():
    days = _thu_six()
    # Wed-style: offer first two, ask for 19:45 — use a wed-shaped day
    date = "2026-08-05"
    days = [{
        "date": date,
        "day_label": "Wednesday 5th August",
        "slot_times": ["17:30", "18:15", "19:45"],
        "slot_times_spoken": [
            "half past five in the evening",
            "quarter past six in the evening",
            "quarter to eight in the evening",
        ],
        "slots": [
            {"start": f"{date}T17:30:00", "end": f"{date}T18:10:00"},
            {"start": f"{date}T18:15:00", "end": f"{date}T18:55:00"},
            {"start": f"{date}T19:45:00", "end": f"{date}T20:25:00"},
        ],
    }]
    rem = remaining_slots_after_offer(days, list(days[0]["slots"][:2]))
    hit = resolve_requested_time("how about quarter to eight", rem)
    assert hit is not None
    assert hit["time"] == "19:45"


def test_resolve_six_in_remaining():
    days = _thu_six()
    rem = remaining_slots_after_offer(days, _offered_first_two(days))
    hit = resolve_requested_time("do you have six", rem)
    assert hit is not None
    assert hit["time"] == "18:00"


def test_resolve_misses_time_not_in_remaining():
    days = _thu_six()
    rem = remaining_slots_after_offer(days, _offered_first_two(days))
    # 16:30 was already offered — not in remaining
    assert resolve_requested_time("half past four", rem) is None
    assert resolve_requested_time("nine in the morning", rem) is None


# ── speech + session apply ───────────────────────────────────────────────────

def test_format_next_batch_mentions_both_times_and_more():
    days = _thu_six()
    rem = remaining_slots_after_offer(days, _offered_first_two(days))
    batch, more = next_slot_batch(rem, n=2)
    speech = format_next_batch_speech(batch, more)
    assert "six in the evening" in speech
    assert "quarter to seven" in speech
    assert "few others" in speech.lower() or "more" in speech.lower()


def test_format_resolved_time_confirms_availability():
    days = _thu_six()
    rem = remaining_slots_after_offer(days, _offered_first_two(days))
    hit = resolve_requested_time("half past seven", rem)
    speech = format_time_available_speech(hit)
    assert "half past seven" in speech
    assert "book" in speech.lower() or "work" in speech.lower()


def test_apply_next_batch_updates_last_offered():
    days = _thu_six()
    session = {
        "available_days": days,
        "last_offered_slots": _offered_first_two(days),
    }
    rem = remaining_slots_after_offer(days, session["last_offered_slots"])
    batch, more = next_slot_batch(rem, n=2)
    speech = apply_next_batch_to_session(session, batch, more)
    assert len(session["last_offered_slots"]) == 2
    assert session["last_offered_slots"][0]["start"].startswith("2026-08-06T18:00")
    assert "six in the evening" in speech


def test_apply_resolved_time_sets_selection():
    days = _thu_six()
    session = {
        "available_days": days,
        "last_offered_slots": _offered_first_two(days),
    }
    rem = remaining_slots_after_offer(days, session["last_offered_slots"])
    hit = resolve_requested_time("half past seven", rem)
    speech = apply_resolved_time_to_session(session, hit)
    assert session["last_offered_slots"][0]["start"].startswith("2026-08-06T19:30")
    assert "half past seven" in speech


def test_build_followup_tool_result_has_first_day_not_amputating_available_days():
    days = _thu_six()
    rem = remaining_slots_after_offer(days, _offered_first_two(days))
    batch, more = next_slot_batch(rem, n=2)
    result = build_followup_tool_result(days, batch, more)
    assert result["presentation_mode"] == "single_day"
    assert len(result["first_day"]["slot_times"]) == 2
    assert result["first_day"]["more_times"] is True
    # Full day still in available_days for later turns / resolve
    assert len(result["available_days"][0]["slot_times"]) == 6
