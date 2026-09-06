"""B-147 — "Monday doesn't work" must never be answered with Monday.

`CAdf1e02ca0ce4e83e9e02abf65bcacb02`, northgate, 2026-09-06 10:15:06, build
`cf592b11a2fa`. Susie read a three-day offer. The caller said, in full:

    'monday doesn't work'

and heard:

    [slot_followup] 'Monday 7th September' answered from the payload -- 3 of 17
    bookable times spoken, offer and keypad recorded, no tool call needed (D-B)
    slot map active — time_selection: {'1': 'eight in the morning', ...}

Monday read out, and the keypad renumbered onto the day the caller had just
ruled out.

WHY BOTH DOORS NEEDED THE SAME GUARD. `day_accepted_by_caller` already declined
this sentence — `_DAY_REFUSE_RE`, added the same night, because `_DAY_ACCEPT_RE`
matches `works?` and "doesn't work" contains it. `named_day_speech` had no such
guard: it asks only whether the caller NAMED a day, and naming a day to rule it
out looks identical to naming it to ask about it.
`utterance_requests_different_day` does not catch it either — that looks for
"another day", not for a refusal.

The request door is the louder of the two. The acceptance door mis-acknowledges;
this one reads the refused day out AND moves the keypad onto it, so the caller's
next "number 1" books a time on a day they rejected.

THE SAME DEFECT WEARS A THIRD HAT. In the more-slots branch, naming a day SCOPES
the answer to it — right for "what else have you got on Monday", exactly wrong
for "monday doesn't work, what else have you got", which was answered with more
Monday times. A refusal now un-scopes.

The honest answer to a refused day is the one "what else have you got" already
gets: the days he has not heard, spoken and recorded by `more_days_speech`.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    day_refused_by_caller,
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


OFFERED = [
    _day("2026-09-07", "Monday 7th September"),
    _day("2026-09-08", "Tuesday 8th September"),
    _day("2026-09-09", "Wednesday 9th September"),
]
UNHEARD = [
    _day("2026-09-10", "Thursday 10th September"),
    _day("2026-09-11", "Friday 11th September"),
    _day("2026-09-12", "Saturday 12th September"),
]


def _after_multi_day_readout():
    """Six days in the payload, three read out — the live shape."""
    session = {
        "clinic_id": "northgate",
        "available_days": OFFERED + UNHEARD,
        "_slot_presentation_mode": "multi_day",
        "last_offered_slots": [
            {"start": f"{d['date']}T08:00:00+01:00", "end": ""} for d in OFFERED
        ],
        "slot_labels": [d["day_label"] for d in OFFERED],
        "v3_dtmf_slot_map": {
            "1": "Monday 7th September",
            "2": "Tuesday 8th September",
            "3": "Wednesday 9th September",
        },
    }
    record_spoken_slots(session, [
        {"start": f"{d['date']}T{t}:00+01:00", "spoken": sp, "date": d["date"]}
        for d in OFFERED
        for t, sp in zip(("08:00", "17:10"),
                         ("eight in the morning", "ten past five in the evening"))
    ])
    return session


# ── The live sentence, and the refusals around it ───────────────────────────

@pytest.mark.parametrize("utterance", [
    "monday doesn't work",                       # the live one
    "monday does not work",
    "monday is no good",
    "i can't do monday",
    "not monday",
    "monday won't work for me",
    "monday doesn't work what else do you have",  # the more-slots hat
])
def test_a_refused_day_is_never_read_out(utterance):
    """The defect: the day the caller ruled out, read back to them."""
    session = _after_multi_day_readout()
    assert named_day_speech(session, utterance) is None, utterance

    spoken = try_unspoken_followup_speech(session, utterance) or ""
    assert "Monday" not in spoken, f"{utterance!r} -> {spoken!r}"


@pytest.mark.parametrize("utterance", [
    "monday doesn't work",
    "monday doesn't work what else do you have",
])
def test_a_refused_day_is_answered_with_the_days_not_heard(utterance):
    """The honest answer, and the one "what else have you got" already gives —
    from a producer, so speech and record cannot disagree."""
    spoken = try_unspoken_followup_speech(_after_multi_day_readout(), utterance)
    assert spoken, f"{utterance!r} fell through to the model"
    assert any(d["day_label"].split()[0] in spoken for d in UNHEARD), spoken


def test_the_keypad_never_lands_on_the_refused_day():
    """The consequence that outlives the turn: the caller's next "number 1"
    must not book a time on a day they rejected."""
    session = _after_multi_day_readout()
    try_unspoken_followup_speech(session, "monday doesn't work")
    keypad = session.get("v3_dtmf_slot_map") or {}
    assert not any("Monday" in str(v) for v in keypad.values()), keypad


def test_the_predicate_resolves_against_the_offer():
    session = _after_multi_day_readout()
    assert day_refused_by_caller(session, "monday doesn't work") == "2026-09-07"
    assert day_refused_by_caller(session, "tuesday's no good") == "2026-09-08"


# ── What must NOT change ────────────────────────────────────────────────────

@pytest.mark.parametrize("utterance,label", [
    ("uh yeah check for tuesday please", "Tuesday"),
    ("what about wednesday", "Wednesday"),
    ("can you do tuesday", "Tuesday"),
])
def test_a_plain_request_still_gets_that_day(utterance, label):
    """D-B unchanged. A day named WITHOUT a refusal is still a request."""
    spoken = try_unspoken_followup_speech(_after_multi_day_readout(), utterance)
    assert spoken and label in spoken, f"{utterance!r} -> {spoken!r}"


@pytest.mark.parametrize("utterance", [
    "uh yeah check for tuesday please",
    "what about wednesday",
    "what else have you got",
    "yeah monday works",
    "",
])
def test_deny_by_default(utterance):
    assert day_refused_by_caller(_after_multi_day_readout(), utterance) is None


def test_a_refusal_naming_no_offered_day_declines():
    """"that doesn't work" rules out nothing this function can name, and
    guessing which day they meant is exactly what deny-by-default forbids."""
    assert day_refused_by_caller(
        _after_multi_day_readout(), "that doesn't work"
    ) is None


def test_the_full_label_refusal_does_not_scope_to_the_refused_day():
    """The third hat, exercised where it actually bites.

    A BARE weekday never reaches the scoping branch — `day_named_by_caller`
    requires the payload's full label, so "monday doesn't work, what else" was
    already unscoped. The full label DOES resolve, and then naming the day
    scopes the more-slots answer onto it: the caller who ruled Monday out was
    read more Monday times. A refusal must un-scope.
    """
    said = "monday 7th september doesn't work, what else have you got"
    session = _after_multi_day_readout()

    from app.tools.slot_followup import day_named_by_caller
    assert day_named_by_caller(session["available_days"], said), (
        "fixture drift: this utterance must resolve a day, or the branch under "
        "test is never reached"
    )

    spoken = try_unspoken_followup_speech(session, said) or ""
    assert spoken, "fell through to the model"
    assert "Monday" not in spoken, spoken
    assert any(d["day_label"].split()[0] in spoken for d in UNHEARD), spoken
