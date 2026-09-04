"""D-B — "what about Wednesday" answered from the payload, not from the model.

`CA90ccb117`, northgate, 2026-09-03 15:41, build `a66a34371749`.

Susie read a three-day offer. The caller said "uh yeah check for tuesday
please". The head fired CORRECTLY — "Let me have a look at Tuesday for you —"
— and then **no `check_availability` call ran at all**. The model answered from
the offer already in its context:

    "That day I've got ten to nine in the morning, or ten past five in the
     evening — which suits?"

Exactly the two Tuesday slots it had already read out. Tuesday's payload held
twelve. Three failures from one missing tool call:

  1. the head promised a lookup that never happened — the promised-work defect
     from the opposite direction to every previous instance: not a head in
     front of no work, but no work behind a justified head;
  2. the caller was re-read 2 of 12 and told nothing else existed;
  3. `v3_dtmf_slot_map` still held three DAYS while she had just spoken two
     TIMES, so pressing 1 would have selected Monday. Speech and record
     disagreed for the rest of the call.

`calls.slot_offers` recorded ONE entry for that call — the original multi-day
offer. The Tuesday reply went through no producer, so every guard downstream
was reading a record nobody had written. The obs column added that morning made
it visible in a single query.

THE FIX IS NOT TO MAKE THE MODEL CALL THE TOOL. That is trigger-side, and this
codebase has been wrong in that direction three times. It is also unnecessary:
the payload is already on the session, so the honest answer costs no tool call
— the same argument `more_days_speech` makes. This is that function with the
scope inverted: there it is the days he has NOT heard, here it is the one day
he just named.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    named_day_speech,
    record_spoken_slots,
    try_unspoken_followup_speech,
)

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


WEEK = [
    _day("2026-09-07", "Monday 7th September"),
    _day("2026-09-08", "Tuesday 8th September"),
    _day("2026-09-09", "Wednesday 9th September"),
]


def _after_multi_day_readout():
    """The session exactly as the live call left it: three days offered, two
    times spoken on each, twelve held on every one."""
    session = {
        "available_days": WEEK,
        "_slot_presentation_mode": "multi_day",
        "last_offered_slots": [
            {"start": f"{d['date']}T08:00:00+01:00", "end": ""} for d in WEEK
        ],
        "slot_labels": [d["day_label"] for d in WEEK],
        "v3_dtmf_slot_map": {
            "1": "Monday 7th September",
            "2": "Tuesday 8th September",
            "3": "Wednesday 9th September",
        },
    }
    record_spoken_slots(session, [
        {"start": f"{d['date']}T{t}:00+01:00", "spoken": sp, "date": d["date"]}
        for d in WEEK
        for t, sp in zip(("08:00", "17:10"),
                         ("eight in the morning", "ten past five in the evening"))
    ])
    return session


# ── The live utterance, and the shapes around it ────────────────────────────

@pytest.mark.parametrize("utterance,label", [
    ("uh yeah check for tuesday please", "Tuesday"),   # the live one
    ("what about wednesday", "Wednesday"),
    ("can you do tuesday", "Tuesday"),
    ("have you got anything on wednesday", "Wednesday"),
    ("is tuesday free", "Tuesday"),
])
def test_a_named_day_is_answered_from_the_payload(utterance, label):
    """Deterministic speech, so the model never gets the turn."""
    spoken = try_unspoken_followup_speech(_after_multi_day_readout(), utterance)
    assert spoken, f"{utterance!r} still falls through to the model"
    assert label in spoken, f"{utterance!r} -> {spoken!r}"


def test_more_than_the_two_already_read_are_offered():
    """The defect in one assertion. The caller heard 2 of 12 and was told that
    was the day. Any honest answer names times he has not had."""
    spoken = try_unspoken_followup_speech(
        _after_multi_day_readout(), "uh yeah check for tuesday please"
    )
    already_heard = {"ten to nine in the morning", "ten past five in the evening"}
    named = {s for s in SPOKEN if s in spoken}
    assert named - already_heard, (
        f"only times he had already been read were offered: {named}"
    )


def test_the_keypad_stops_pointing_at_days():
    """Failure 3, and the one a caller could have acted on.

    She spoke TIMES; the map still held DAYS, so `1` meant Monday. The offer
    must be recorded through `apply_offer_to_session` — the single writer — so
    speech and record cannot disagree.
    """
    session = _after_multi_day_readout()
    assert "Monday 7th September" in session["v3_dtmf_slot_map"].values()

    spoken = try_unspoken_followup_speech(session, "uh yeah check for tuesday please")
    assert spoken

    values = set((session.get("v3_dtmf_slot_map") or {}).values())
    assert values, "the keypad map was not rewritten"
    assert not any("September" in v for v in values), (
        f"the keypad still points at DAYS after a TIME readout: {values}"
    )
    for v in values:
        assert v in spoken, f"keypad value {v!r} was never spoken"


def test_the_day_under_discussion_moves_with_the_answer():
    """A follow-up after this must scope to Tuesday, not to day one."""
    session = _after_multi_day_readout()
    assert try_unspoken_followup_speech(session, "what about wednesday")
    assert session.get("v3_last_offered_day_iso") == "2026-09-09"


# ── What it must NOT take ───────────────────────────────────────────────────

@pytest.mark.parametrize("utterance,why", [
    ("yeah monday works", "an ACCEPTANCE — 'Monday it is —' was verified live 13:07:12"),
    ("monday please", "an acceptance"),
    ("tuesday at ten past five works", "a PICK — slot_accepted_by_caller owns it"),
    ("what else have you got", "the more-slots path, and more_days_speech owns it"),
    ("another day please", "the different-day path"),
    ("yes please", "names no day"),
    ("number two", "a position, not a day"),
])
def test_it_declines_everything_that_is_not_a_request_for_a_day(utterance, why):
    """Deny by default. Each of these already has an owner, and taking a turn
    from one of them would be a regression of a path that works."""
    assert named_day_speech(_after_multi_day_readout(), utterance) is None, why


def test_a_single_day_offer_is_left_alone():
    """On single_day the day under discussion is already the only one.
    `remaining_unspoken_on_current_day` owns it, and answering here would
    re-read the offer the caller just heard."""
    session = _after_multi_day_readout()
    session["_slot_presentation_mode"] = "single_day"
    assert named_day_speech(session, "what about tuesday") is None


def test_a_day_outside_the_payload_declines():
    """"What about Friday" when Friday was never fetched is a real lookup."""
    assert named_day_speech(_after_multi_day_readout(), "what about friday") is None


def test_it_never_raises_on_a_broken_session():
    """This runs on the live booking path. A producer fault must leave the
    caller with the model's answer, which is what they had before it existed."""
    for bad in ({}, {"available_days": "nonsense",
                     "_slot_presentation_mode": "multi_day"},
                {"_slot_presentation_mode": "multi_day", "available_days": [None]}):
        assert named_day_speech(bad, "what about tuesday") is None


def test_it_is_wired_into_the_dispatcher():
    """Pinned by source. `021c0fc0` shipped with a test that proved the unit
    and never the path, and the live call still failed — the blocker was the
    call site. Every behavioural test above goes through
    `try_unspoken_followup_speech`, but this states the requirement outright."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "app" / "tools" / "slot_followup.py").read_text(
        encoding="utf-8", errors="replace")
    assert "_named_day = named_day_speech(session, user_text)" in src, (
        "named_day_speech is no longer called from try_unspoken_followup_speech"
    )
