"""An accept of a heard slot is read back, not offered again.

CA4b4afa80, northgate demo line, 13 Sep 2026 21:53:54, build 103d0ae3:

    Susie : "Number 3, Wednesday the 16th — twenty to ten in the morning, or
             twenty past four in the afternoon. Any of those work?"
    caller: "yeah 20 to 10 on a wednesday works"
            -> [ms_conn v3] caller ACCEPTED 09:40 — pinned (P6b)
            -> [slot_followup] a time the caller named resolved on the day
               under discussion (DT-7/8)
    Susie : "Yes — twenty to ten in the morning on Wednesday 16th September
             is free. Shall I book that in for you?"
    caller: "uh yes go for it"
    Susie : "That one works — so that's Wednesday the 16th of September at
             twenty to ten — could I take your first name and surname?"

The engine resolved the accept and then, in the same turn, the DT-7/8
time-request route (d585450a, 11 Sep) answered the same words as if they
were a question about the time. An accept became an offer; the caller had
to accept twice; the second yes then earned a head. On every clinic line.

Now: an accept the engine has pinned this turn is read back from the
engine's own record (D-s) and goes straight to the name -- the pre-11-Sep
shape, minus the model's latency and the head that covered it. A question
about a time still gets DT-7/8's one-slot answer. A mixed utterance ("...but
can I ask something first") goes to the model.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    ACCEPTED_SLOT_KEY,
    ACCEPTED_SLOT_RECORD_KEY,
    accepted_slot_readback_speech,
    note_accepted_slot,
    record_spoken_slots,
    slot_accepted_by_caller,
    try_unspoken_followup_speech,
)

WED = "2026-09-16"
GRID = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10", "13:00",
        "13:50", "14:40", "15:30", "16:20", "17:10"]
SPOKEN = ["eight in the morning", "ten to nine in the morning",
          "twenty to ten in the morning", "half past ten in the morning",
          "twenty past eleven in the morning", "ten past twelve in the afternoon",
          "one in the afternoon", "ten to two in the afternoon",
          "twenty to three in the afternoon", "half past three in the afternoon",
          "twenty past four in the afternoon", "ten past five in the evening"]
READBACK = ("So that's Wednesday 16th September at twenty to ten in the morning "
            "— could I take your first name and surname?")


def _day(date, label):
    return {"date": date, "day_label": label, "slot_times": GRID,
            "slot_times_spoken": SPOKEN, "times_not_shown": 0}


def _after_the_readout():
    """The session as the multi-day readout left it: three days, two times
    heard on each, Wednesday's being 09:40 and 16:20."""
    s = {"available_days": [_day("2026-09-14", "Monday 14th September"),
                            _day("2026-09-15", "Tuesday 15th September"),
                            _day(WED, "Wednesday 16th September")],
         "last_offered_slots": [
             {"start": "2026-09-14T08:00:00+01:00"},
             {"start": "2026-09-15T08:50:00+01:00"},
             {"start": f"{WED}T09:40:00+01:00"},
         ],
         "conversation_history": [{"role": "assistant", "content":
             "Number 3, Wednesday the 16th — twenty to ten in the morning, or "
             "twenty past four in the afternoon. Any of those work?"}]}
    record_spoken_slots(s, [
        {"start": "2026-09-14T08:00:00+01:00"}, {"start": "2026-09-14T17:10:00+01:00"},
        {"start": "2026-09-15T08:50:00+01:00"}, {"start": "2026-09-15T16:20:00+01:00"},
        {"start": f"{WED}T09:40:00+01:00"}, {"start": f"{WED}T16:20:00+01:00"},
    ])
    return s


def _engine_turn(s, utterance):
    """What connection.py does before llm_stream: resolve and pin the accept."""
    s.pop(ACCEPTED_SLOT_KEY, None)
    iso = slot_accepted_by_caller(s, utterance)
    if iso:
        s[ACCEPTED_SLOT_KEY] = iso
        note_accepted_slot(s, iso)
    return iso


def test_ca4b4afa_the_pick_is_read_back_and_the_name_asked():
    s = _after_the_readout()
    assert _engine_turn(s, "yeah 20 to 10 on a wednesday works")[:19] == f"{WED}T09:40:00"
    out = try_unspoken_followup_speech(s, "yeah 20 to 10 on a wednesday works")
    assert out == READBACK, out
    assert "Shall I book that in" not in out
    assert "is free" not in out


def test_a_question_about_a_heard_time_still_gets_the_one_slot_answer():
    s = _after_the_readout()
    q = "is twenty to ten on wednesday still free"
    # The resolver PINS this (pre-existing; harmless for P6b). The read-back
    # must still not take it: a question falls through to DT-7/8.
    _engine_turn(s, q)
    out = try_unspoken_followup_speech(s, q)
    assert out is not None
    assert out.endswith("Shall I book that in for you?"), out


def test_an_unheard_time_is_still_an_offer():
    s = _after_the_readout()
    u = "ten past twelve on wednesday"
    assert _engine_turn(s, u) is None, "unheard: cannot have been accepted"
    out = try_unspoken_followup_speech(s, u)
    assert out is not None and "ten past twelve" in out
    assert out.endswith("Shall I book that in for you?")


def test_a_mixed_pick_goes_to_the_model():
    s = _after_the_readout()
    u = "20 to 10 on wednesday works but can i ask something first"
    # Pinned by hand: the resolver itself declines this shape today, which
    # already sends it to the model; if it ever pins it, the model still
    # takes the turn -- never the read-back, never a producer.
    s[ACCEPTED_SLOT_KEY] = f"{WED}T09:40:00"
    note_accepted_slot(s, f"{WED}T09:40:00")
    assert accepted_slot_readback_speech(s, u) is None
    assert try_unspoken_followup_speech(s, u) is None


def test_a_yes_to_a_one_slot_offer_is_read_back_the_same_way():
    # After DT-7/8 has answered a QUESTION ("Yes — ... Shall I book that
    # in?"), the yes pins the one slot (37cc1050) and gets the same read-back.
    from app.tools.slot_followup import apply_resolved_time_to_session

    s = _after_the_readout()
    slot = {"start": f"{WED}T12:10:00+01:00", "end": f"{WED}T12:55:00+01:00",
            "date": WED, "time": "12:10", "spoken": "ten past twelve in the afternoon",
            "day_label": "Wednesday 16th September"}
    offer = apply_resolved_time_to_session(s, slot, asked=["12:10"])
    s["conversation_history"] += [{"role": "user", "content": "ten past twelve on wednesday"},
                                  {"role": "assistant", "content": offer}]
    assert _engine_turn(s, "uh yes go for it")[:19] == f"{WED}T12:10:00"
    out = try_unspoken_followup_speech(s, "uh yes go for it")
    assert out == ("So that's Wednesday 16th September at ten past twelve in the "
                   "afternoon — could I take your first name and surname?"), out


def test_the_read_back_needs_the_engines_own_record():
    s = {ACCEPTED_SLOT_KEY: f"{WED}T09:40:00+01:00"}
    assert accepted_slot_readback_speech(s, "yeah that one") is None, "no record"
    s[ACCEPTED_SLOT_RECORD_KEY] = {"iso": f"{WED}T16:20:00", "phrase": "x"}
    assert accepted_slot_readback_speech(s, "yeah that one") is None, "record disagrees"


def test_once_a_name_is_on_record_the_model_takes_the_pick():
    # A reschedule: the dispatcher never runs, so the model reads back and
    # asks "shall I go ahead?" as before.
    s = _after_the_readout()
    s["patient_name"] = "Sarah Jones"
    _engine_turn(s, "yeah 20 to 10 on a wednesday works")
    assert try_unspoken_followup_speech(s, "yeah 20 to 10 on a wednesday works") is None
