"""B-145 — "yeah Monday works" must leave a Monday offer on the table.

`CAa0389cae74d3ba76e220ab0280972101`, northgate, 2026-09-05 23:10, build
`25c18f44`.

Susie read a three-day offer. The caller said "oh yeah monday works". The
acknowledgement was right —

    23:10:12.834  situational head (slot_picked): 'Monday it is —'

— and then the MODEL narrowed the day in prose:

    23:10:14.583  synthesise: "that day I've got eight in the morning or ten
                  past five in the evening"
    23:10:14.639  slot map active — day_selection: {'1': 'Monday 7th September',
                  '2': 'Tuesday 8th September', '3': 'Wednesday 9th September'}

Those are the two Monday slots it had already read out. Monday's payload held
TWELVE. One missing producer, four consequences on the same turn:

  1. 2 of 12 times spoken — no lookup behind the acknowledgement;
  2. no "and I've a few others that day" tail. That tail is `single_day` only
     by construction, and no single_day offer was ever built, so the caller
     was given two times with no caveat at all;
  3. the keypad still held three DAYS — pressing 1 would have re-picked Monday;
  4. the NEXT turn broke on the same state. "um 10 past 5 in the evening suits"
     could not be resolved, because `slot_accepted_by_caller`'s lone-date
     branch needs the offer to hold exactly ONE date and three were still on
     it — so `_hs_picking` stayed false and a TIME_BAND head promised a lookup
     in front of a confirmation.

`named_day_speech` declines an acceptance ON PURPOSE and that stays right: a
caller who accepts must be acknowledged, not read a list as though they had
asked a question. `day_acceptance_speech` is the other half of that decision,
not a reversal — the acknowledgement leads and the day behind it comes from the
producer instead of from the model.

The fixture is the D-B one, unchanged, because it is the same live state: the
two calls are the same offer answered by two different caller turns.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    day_acceptance_speech,
    named_day_speech,
    record_spoken_slots,
    slot_accepted_by_caller,
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


def _after_multi_day_readout(clinic_id="northgate"):
    """The session exactly as the live call left it: three days offered, two
    times spoken on each, twelve held on every one."""
    session = {
        "clinic_id": clinic_id,
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


# ── The live utterance, and the acceptances around it ───────────────────────

@pytest.mark.parametrize("utterance,day", [
    ("oh yeah monday works", "Monday"),          # the live one
    ("yeah monday works", "Monday"),
    ("monday works for me", "Monday"),
    ("tuesday suits", "Tuesday"),
    ("wednesday would be great", "Wednesday"),
])
def test_an_accepted_day_is_answered_by_the_producer(utterance, day):
    """The model never gets the turn, so it cannot answer from its own context."""
    spoken = try_unspoken_followup_speech(_after_multi_day_readout(), utterance)
    assert spoken, f"{utterance!r} still falls through to the model"
    assert day in spoken, f"{utterance!r} -> {spoken!r}"


def test_the_acknowledgement_leads():
    """"Monday it is —" is what the caller heard live and what they must keep.

    This producer answers before the streaming call, so the head that used to
    come from `llm_stream` has to be spoken here or not at all. Losing it is
    the regression `named_day_speech`'s own comment refuses to cause.
    """
    spoken = try_unspoken_followup_speech(
        _after_multi_day_readout(), "oh yeah monday works"
    )
    assert spoken.startswith("Monday it is"), spoken


def test_the_acknowledgement_is_per_clinic_like_every_other_head():
    """A clinic that has not opted into hold speech gets the offer and no head."""
    spoken = day_acceptance_speech(
        _after_multi_day_readout(clinic_id="__no_such_clinic__"),
        "oh yeah monday works",
    )
    assert spoken
    assert not spoken.startswith("Monday it is"), spoken


def test_more_than_the_two_already_read_are_offered():
    """The defect in one assertion. The caller heard 2 of 12 and was given no
    reason to think there were more."""
    spoken = try_unspoken_followup_speech(
        _after_multi_day_readout(), "oh yeah monday works"
    )
    already_heard = {"eight in the morning", "ten past five in the evening"}
    named = {s for s in SPOKEN if s in spoken}
    assert named - already_heard, (
        f"only the two already-read times were offered again: {spoken!r}"
    )


def test_the_keypad_stops_pointing_at_days():
    """Consequence 3. Pressing 1 must select a TIME on the accepted day, not
    re-select the day."""
    session = _after_multi_day_readout()
    try_unspoken_followup_speech(session, "oh yeah monday works")
    keypad = session.get("v3_dtmf_slot_map") or {}
    assert keypad, "the acceptance left no keypad map at all"
    assert not any("Monday 7th September" == v for v in keypad.values()), keypad
    assert not any("Tuesday" in str(v) for v in keypad.values()), keypad


def test_the_offer_narrows_to_one_date():
    """Consequence 4, and the one that broke the FOLLOWING turn.

    `slot_accepted_by_caller` can answer a bare time only when the offer holds
    exactly one date. Three were still on it, so "10 past 5 in the evening
    suits" resolved to nothing and a TIME_BAND head promised a lookup in front
    of a confirmation.
    """
    session = _after_multi_day_readout()
    assert slot_accepted_by_caller(session, "um 10 past 5 in the evening suits") is None

    try_unspoken_followup_speech(session, "oh yeah monday works")

    # `_slot_presentation_mode` is deliberately NOT asserted: it records what
    # the last `check_availability` decided, not what the last offer said, and
    # no producer writes it. That split is real and pre-dates this fix — the
    # D-B path narrows to one day and leaves it reading "multi_day" too. It is
    # a separate concern and changing it here would be a second one.
    dates = {str((o or {}).get("start") or "")[:10]
             for o in (session.get("last_offered_slots") or [])}
    assert dates == {"2026-09-07"}, dates
    assert slot_accepted_by_caller(
        session, "um 10 past 5 in the evening suits"
    ) is not None


# ── What must NOT change ────────────────────────────────────────────────────

def test_a_request_still_belongs_to_the_named_day_producer():
    """"what about Monday" is a question. It must keep D-B's answer, with no
    acknowledgement in front of it — acknowledging a question the caller has
    not answered is the promised-work defect wearing the other hat."""
    session = _after_multi_day_readout()
    assert day_acceptance_speech(session, "uh yeah check for monday please") is None
    assert named_day_speech(session, "uh yeah check for monday please")


def test_a_time_pick_is_left_to_the_resolver():
    """"Tuesday at ten past five" names a TIME. Narrowing the day underneath a
    caller who has already chosen would talk over them."""
    session = _after_multi_day_readout()
    assert day_acceptance_speech(
        session, "tuesday at ten past five in the evening works"
    ) is None


@pytest.mark.parametrize("utterance", [
    "what else have you got",
    "have you got a different day",
    "monday doesn't work",
    "not monday",
    "number two",
    "yeah that works",
])
def test_deny_by_default_is_preserved(utterance):
    """Everything that is not an unambiguous day acceptance still declines —
    each of these has its own path and this must not steal any of them."""
    assert day_acceptance_speech(_after_multi_day_readout(), utterance) is None


def test_a_single_day_offer_is_not_re_read():
    """On single_day the accepted day is already the only one on the table."""
    session = _after_multi_day_readout()
    session["_slot_presentation_mode"] = "single_day"
    assert day_acceptance_speech(session, "oh yeah monday works") is None


# ── B-145c — the refusal that read as an acceptance ─────────────────────────

@pytest.mark.parametrize("utterance", [
    "monday doesn't work",
    "monday does not work",
    "no monday's no good",
    "monday's not great",
    "monday won't work for me",
    "i can't do monday",
    "not monday, thanks",
])
def test_a_refused_day_is_never_accepted(utterance):
    """`_DAY_ACCEPT_RE` matches `works?` — and "monday doesn't work" contains it.

    Live cost before the producer existed: "Monday it is —" spoken to a caller
    who had just refused Monday. Cost after it: Monday READ OUT to them. Both
    are speech that contradicts what the caller said, generated from a record
    that agrees with the speech — the duplicate-write family's shape.
    """
    from app.tools.slot_followup import day_accepted_by_caller
    session = _after_multi_day_readout()
    assert day_accepted_by_caller(session, utterance) is None, utterance
    assert day_acceptance_speech(session, utterance) is None, utterance
