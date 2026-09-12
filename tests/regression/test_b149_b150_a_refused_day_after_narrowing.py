"""B-149/B-150 — refusing a day AFTER the offer has narrowed.

`CA176b7a0da1b7120556e787a8630d2d9b`, northgate, 2026-09-06 22:07:59, build
`6817d766ba40`. The call that verified B-147 across a narrowing, and found the
two things B-147 does not cover:

    22:07:41  'um check for monday please'
              'Monday 7th September' answered from the payload (D-B)
    22:07:59  'um tuesday doesn't work'
    22:08:00  situational head (named_day): 'Let me see what Tuesday looks like —'
    22:08:03  "So sticking with Monday, I've got eight in the morning or ten
               past five in the evening"

Tuesday was NOT read out — B-147 held, which is what that call was for. But:

  **B-149** the head promised a Tuesday lookup and Susie then talked about
  Monday. Fifth instance of the promised-work defect and the first on a
  refusal. `_DAY` triggers NAMED_DAY on any weekday, and naming a day to rule
  it out is the one case where the caller wants LESS of that day.
  `utterance_accepts_an_offer` says False here — correctly, a refusal is not an
  acceptance — so B-145b's backstop could not cover it.

  **B-150** the refusal fell through to the MODEL instead of
  `more_days_speech`. `day_refused_by_caller` resolved the ruled-out day
  against `last_offered_slots`, which the Monday narrowing had reduced to
  Monday alone. The rule ("you can only refuse a day you were read") is right;
  the SCOPE was wrong — "were read" is a fact about the CALL, and Tuesday had
  been read out three turns earlier. The cumulative record has held it since.

WHY THE TWO FIXES FEED DIFFERENT ARGUMENTS. An acceptance sets
`slot_selection`, which suppresses the diary intents AND enables the
SLOT_PICKED head. A refusal must do only the first: "Tuesday it is —" spoken
over "tuesday doesn't work" would be far worse than the lie it replaces. So
`offer_refused` is its own argument and never touches `slot_selection`.
"""
from __future__ import annotations

import pytest

from app.hold_speech import (
    Intent,
    classify_intent,
    utterance_accepts_an_offer,
    utterance_refuses_an_offer,
)
from app.tools.slot_followup import (
    day_refused_by_caller,
    named_day_speech,
    record_spoken_slots,
    try_unspoken_followup_speech,
)

READOUT = "Any of those work?"

TIMES = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
         "13:00", "13:50", "14:40", "15:30", "16:20", "17:10"]
SPOKEN = ["eight in the morning", "ten to nine in the morning",
          "twenty to ten in the morning", "half past ten in the morning",
          "twenty past eleven in the morning", "ten past twelve",
          "one in the afternoon", "ten to two in the afternoon",
          "twenty to three in the afternoon", "half past three in the afternoon",
          "twenty past four in the afternoon", "ten past five in the evening"]


def _day(date, label):
    return {
        "date": date, "day_label": label,
        "slot_times": list(TIMES), "slot_times_spoken": list(SPOKEN),
        "times_not_shown": 0,
        "slots": [{"start": f"{date}T{t}:00+01:00", "end": ""} for t in TIMES],
    }


READ_OUT = [
    _day("2026-09-07", "Monday 7th September"),
    _day("2026-09-08", "Tuesday 8th September"),
    _day("2026-09-09", "Wednesday 9th September"),
]
UNHEARD = [
    _day("2026-09-10", "Thursday 10th September"),
    _day("2026-09-11", "Friday 11th September"),
    _day("2026-09-12", "Saturday 12th September"),
]


def _after_the_monday_narrowing():
    """The live state at 22:07:59: three days read out, then narrowed to Monday."""
    session = {
        "clinic_id": "northgate",
        "available_days": READ_OUT + UNHEARD,
        "_slot_presentation_mode": "multi_day",
        "last_offered_slots": [
            {"start": f"{d['date']}T08:00:00+01:00", "end": ""} for d in READ_OUT
        ],
        "slot_labels": [d["day_label"] for d in READ_OUT],
        "v3_dtmf_slot_map": {
            "1": "Monday 7th September",
            "2": "Tuesday 8th September",
            "3": "Wednesday 9th September",
        },
    }
    record_spoken_slots(session, [
        {"start": f"{d['date']}T{t}:00+01:00", "spoken": sp, "date": d["date"]}
        for d in READ_OUT
        for t, sp in zip(("08:00", "17:10"),
                         ("eight in the morning", "ten past five in the evening"))
    ])
    spoken = try_unspoken_followup_speech(session, "um check for monday please")
    assert spoken and "Monday" in spoken, "fixture drift: the narrowing did not happen"
    dates = {str((o or {}).get("start") or "")[:10]
             for o in session.get("last_offered_slots") or []}
    assert dates == {"2026-09-07"}, dates
    return session


# ── B-150: the refusal reaches the producer again ───────────────────────────

def test_the_live_turn_is_answered_by_a_producer():
    session = _after_the_monday_narrowing()
    assert day_refused_by_caller(session, "um tuesday doesn't work") == "2026-09-08"

    spoken = try_unspoken_followup_speech(session, "um tuesday doesn't work")
    assert spoken, "still falls through to the model"
    assert "Tuesday" not in spoken, spoken
    assert any(d["day_label"].split()[0] in spoken for d in UNHEARD), spoken


@pytest.mark.parametrize("utterance", [
    "um tuesday doesn't work",
    "tuesday is no good",
    "i can't do wednesday",
])
def test_a_day_read_out_earlier_in_the_call_still_resolves(utterance):
    """The cumulative record is what "were read" means — not the current offer."""
    assert day_refused_by_caller(_after_the_monday_narrowing(), utterance)


def test_a_day_nobody_offered_is_still_declined():
    """NOT widened to the payload the way `named_day_speech` is (B-148).

    A REQUEST may name a day the caller has never heard — that is the point of
    asking. A refusal of a day nobody offered rules out nothing, and resolving
    it would let `more_days_speech` be steered by a day the caller was never
    told about.
    """
    session = _after_the_monday_narrowing()
    for never_offered in ("thursday", "friday", "saturday"):
        assert day_refused_by_caller(
            session, f"{never_offered} doesn't work"
        ) is None, never_offered


def test_the_refused_day_is_never_read_out_either_way():
    """B-147 unchanged — this is the assertion the live call already passed."""
    session = _after_the_monday_narrowing()
    assert named_day_speech(session, "um tuesday doesn't work") is None
    spoken = try_unspoken_followup_speech(session, "um tuesday doesn't work") or ""
    assert "Tuesday" not in spoken, spoken


# ── B-149: no head promises a lookup of the refused day ─────────────────────

def test_the_lookup_head_is_suppressed_on_the_live_sentence():
    said = "um tuesday doesn't work"
    assert classify_intent(said, READOUT) == [Intent.NAMED_DAY], (
        "fixture drift: this used to produce the named_day head"
    )
    # 12 Sep 2026: a refusal earns "Not to worry -" (REFUSAL) instead of the
    # silence that became the apology. The Tuesday lookup head is still not it.
    assert classify_intent(said, READOUT, offer_refused=True) == [Intent.REFUSAL]


@pytest.mark.parametrize("utterance", [
    "um tuesday doesn't work",
    "monday doesn't work",
    "i can't do wednesday",
    "not thursday",
    "ten past five doesn't work",
    "eight in the morning won't work for me",
])
def test_a_refusal_is_recognised(utterance):
    assert utterance_refuses_an_offer(utterance), utterance


def test_a_refusal_never_becomes_a_pick():
    """The whole reason `offer_refused` is a separate argument.

    `slot_selection` suppresses the diary intents AND enables SLOT_PICKED. If a
    refusal fed that flag, "tuesday doesn't work" would render "Tuesday it is
    —" — a confirmation of the day the caller just ruled out, which is worse
    than the promised-lookup it replaces.
    """
    said = "um tuesday doesn't work"
    assert not utterance_accepts_an_offer(said)
    assert Intent.SLOT_PICKED not in classify_intent(
        said, READOUT, offer_refused=True
    )
    # And the arm is reachable at all, so the assertion above is not vacuous.
    assert Intent.SLOT_PICKED in classify_intent(
        "yeah tuesday works", READOUT, slot_selection=True
    )


@pytest.mark.parametrize("utterance", [
    "what about wednesday",
    "can you do tuesday",
    "mornings work better for me",
    "that doesn't work",
    "no",
    "",
])
def test_deny_by_default(utterance):
    """A refusal naming no day and no time rules out nothing this can name, and
    an ordinary request must keep its head — promising a lookup that IS
    happening is correct."""
    assert not utterance_refuses_an_offer(utterance), utterance


def test_a_plain_named_day_request_keeps_its_head():
    assert classify_intent("what about wednesday", READOUT) == [Intent.NAMED_DAY]


def test_the_reader_is_gated_on_the_slot_map():
    """Asked only while numbered options are on the table — the same rule the
    acceptance backstop follows, pinned so an edit cannot widen it silently."""
    import inspect

    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    marker = "_hs_refused = _refuses_an_offer(_hs_utterance)"
    assert marker in src, "the refusal backstop is not wired in"
    guard = src.split(marker)[0].rsplit("_hs_refused = False", 1)[-1]
    assert "v3_dtmf_slot_map" in guard, (
        "the refusal backstop must only be asked while an offer is on the table"
    )
    # It must feed `offer_refused`, never `slot_selection`.
    assert "offer_refused=_hs_refused" in src
    assert "slot_selection=_hs_refused" not in src
