"""Defect E: after the guard retracts an offer, the turn still asks its question.

`CA5c69c585` (12 Sep 17:36, 15 s) and `CAddd98ce0` (12 Sep 20:52, ~30 s),
northgate demo line:

    20:52:12,829  slot_guard REPLACED a slot fact: "The nearest I've got to
                  four in the afternoon ..." -> 'Sorry — let me just
                  double-check that one for you.'
    20:52:15,428  BACKSTOP armed — turn asked nothing but a question is still
                  outstanding: 'Before I do that — could I take your first
                  name and surname?'
    20:52:26,938  slot_guard dropped the tail of a retracted offer: "Sorry, I
                  didn't catch that. Before I do that — could I take your
                  first name and "                      (watchdog re-ask)
    20:52:43,497  slot_guard dropped the tail of a retracted offer: 'Sorry —
                  could I take your first name and surname again?'
                                                        (safety-net re-ask)
    20:52:45      caller: 'hello'

Two safety mechanisms, each right alone: the guard's latch drops every later
chunk of a retracted turn so the caller never hears half an offer; the
watchdog re-asks the outstanding question inside the same turn. Together
they produced the one outcome both exist to prevent -- silence -- three
times in a row on one turn.

The latch now drops only what refers to the retracted offer (a time, a
weekday, a position, "that one / book that in / those"). A question that
names none of them -- the name question, either re-ask -- is spoken. Every
chunk below is verbatim from the two calls' `call_logs`.
"""
from __future__ import annotations

import pytest

from app.tools import slot_fact_guard as guard

TUE = "2026-09-15"
GRID = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10", "13:00",
        "13:50", "14:40", "15:30", "16:20", "17:10"]


def _retracted_session(monkeypatch):
    monkeypatch.setenv("SLOT_FACT_GUARD", "enforce")
    s = {
        "clinic_id": "northgate",
        "available_days": [{
            "date": TUE, "day_label": "Tuesday 15th September",
            "slot_times": GRID,
            "slots": [{"start": f"{TUE}T{t}:00+01:00"} for t in GRID],
        }],
    }
    v = guard.check_outgoing(
        s, "The nearest I've got to four in the afternoon is twenty past four "
           "in the afternoon on Tuesday 15th September.")
    assert v.text == guard.RECOVERY_SENTENCE and v.blocked
    return s


SPOKEN_AFTER_A_RETRACTION = [
    "Before I do that — could I take your first name and surname?",
    "Sorry, I didn't catch that. Before I do that — could I take your first name and surname?",
    "Sorry — could I take your first name and surname again?",
    "Anything else you'd like to know?",
    "Could I take the best number for the booking?",
]


@pytest.mark.parametrize("chunk", SPOKEN_AFTER_A_RETRACTION)
def test_a_question_naming_no_slot_is_spoken(monkeypatch, chunk):
    s = _retracted_session(monkeypatch)
    v = guard.check_outgoing(s, chunk)
    assert v.text == chunk, f"dropped after a retraction: {chunk!r}"
    assert not v.blocked


DROPPED_AFTER_A_RETRACTION = [
    "Shall I book that in for you?",
    "or ten past five in the evening on Tuesday — which suits?",
    "Number 1, eight in the morning. Number 2, twenty past four in the afternoon.",
    "Does that one work for you?",
    "So that's Tuesday the 15th at twenty past four.",
]


@pytest.mark.parametrize("chunk", DROPPED_AFTER_A_RETRACTION)
def test_the_rest_of_the_offer_is_still_dropped(monkeypatch, chunk):
    s = _retracted_session(monkeypatch)
    v = guard.check_outgoing(s, chunk)
    assert v.text == "", f"the caller heard part of a retracted offer: {chunk!r}"
    assert v.blocked


def test_the_latch_still_lapses_at_the_next_caller_turn(monkeypatch):
    s = _retracted_session(monkeypatch)
    guard.turn_boundary(s)
    v = guard.check_outgoing(s, "Shall I book that in for you?")
    assert v.text == "Shall I book that in for you?"
