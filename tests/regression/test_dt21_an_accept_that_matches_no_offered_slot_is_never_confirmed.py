"""
Regression: "10 to 12 works" against an offer of 10:30 / 11:20 / 12:10 is a
question back to the caller, never a confirmation.

Spec row DT-21; owner decision D-p (2026-09-11); invariant 2.

CA7ebc0083, demo line, build 66f7dec3, 11 Sep 2026 00:04. Susie had read
Monday's half past ten, twenty past eleven and ten past twelve. The caller
said "10 to 12 works" -- STT for "ten past twelve", or a slip -- and the
model said "So that's Monday the 14th of September at twenty to twelve",
three times. 11:40 was never offered and is not on the diary.

Two layers now stand between that utterance and that sentence. The guard
(invariant 1) catches the SENTENCE. This catches the ACT: an accept that
resolves to no offered slot is answered with "did you mean <the one offered
time it could be>?" when there is exactly one, and with the offer again when
there is not. The offer is narrowed to that one time, so the caller's "yes"
books it -- the same record a resolved time leaves (V5) -- and the sentence
is a question, so nothing has been confirmed.
"""
from __future__ import annotations

import pytest

from app.tools import slot_fact_guard as guard
from app.tools import slot_followup as sf
from app.tools.receptionist_tools import _spoken_slot_time
from app.tools.slot_offer import (
    apply_offer_to_session,
    build_slot_offer,
    offer_as_record,
)

MON = "2026-09-14"
GRID = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10", "13:00",
        "13:50", "14:40", "15:30", "16:20", "17:10"]


def _day(date, label, times):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t) for t in times],
        "slots": [{"start": f"{date}T{t}:00+01:00"} for t in times],
    }


def _the_00_04_table():
    """Monday's full grid in the payload; 10:30 / 11:20 / 12:10 on the table,
    recorded through the one writer."""
    payload = [_day(MON, "Monday 14th September", GRID)]
    s = {"clinic_id": "northgate", "available_days": payload}
    shown = _day(MON, "Monday 14th September", ["10:30", "11:20", "12:10"])
    offer = build_slot_offer([shown], pretrimmed=False)
    apply_offer_to_session(s, offer_as_record(offer), offer.chunks)
    s["_slot_presentation_mode"] = "single_day"
    return s, offer


def _say(s, text):
    guard.note_caller_speech(s, text)
    return sf.try_unspoken_followup_speech(s, text)


# ── the call ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("said", [
    "10 to 12 works",                 # what STT sent at 00:04
    "ten to twelve works",            # the word form
    "yeah 10 to 12 please",
    "ten to twelve",
])
def test_the_00_04_utterance_is_a_question_naming_ten_past_twelve(said):
    s, _ = _the_00_04_table()
    out = _say(s, said)
    assert out == ("Just to check — did you mean ten past twelve in the afternoon "
                   "on Monday 14th September?"), out


def test_nothing_is_confirmed_and_twenty_to_twelve_is_never_spoken():
    s, _ = _the_00_04_table()
    out = _say(s, "10 to 12 works")
    assert "twenty to twelve" not in out
    assert not out.lower().startswith(("so that's", "yes"))
    assert out.endswith("?")
    assert guard.check_outgoing(s, out).clean


def test_the_offer_narrows_to_that_one_time_so_yes_books_it():
    """The record a resolved time leaves (V5): `last_offered_slots` is now the
    one slot and the keypad map is superseded (B-80), so 'yes' resolves to it
    and a stale keypress cannot pick 10:30.

    NOT `selected_slot`. Until 12 Sep this test pinned the narrowed offer as
    the caller's SELECTION -- "did you mean ten past twelve?" is a question,
    and the slot is chosen when the caller says yes, not when Susie asks.
    Defect B (CA5c69c585): that build-time write put a never-heard slot into
    `collected.selected_slot`. The yes is answered from `last_offered_slots`,
    which is what this test is for."""
    s, _ = _the_00_04_table()
    _say(s, "10 to 12 works")
    assert [o["start"][:16] for o in s["last_offered_slots"]] == [f"{MON}T12:10"]
    assert "selected_slot" not in s, "a question narrowed the offer; it did not choose"
    assert s.get("v3_slot_map_superseded")


# ── plausibility ───────────────────────────────────────────────────────────

def test_a_round_hour_against_one_close_offered_time_is_a_question():
    """'eleven works' with 11:20 on the table. Since 12 Sep (DT-7/8, owner)
    the named-time resolver sits above this row and searches the day's
    BOOKABLE times, heard or not, so 11:00 -> 11:20 is answered there: one
    real time, said as the nearest, as a yes/no. Same act, same shape of
    answer as this row's "did you mean" -- and the offer narrows to it."""
    s, _ = _the_00_04_table()
    out = _say(s, "eleven works")
    assert out.startswith("The nearest I've got to eleven in the morning is twenty past eleven")
    assert out.endswith("?")
    assert [o["start"][:16] for o in s["last_offered_slots"]] == [f"{MON}T11:20"]


def test_a_quarter_mark_between_two_offered_times_gets_the_nearer():
    """'quarter to eleven works' is 10:45: fifteen minutes from 10:30 and
    thirty-five from 11:20. Not a tie, so not a coin toss -- since 12 Sep the
    far-nearest rule (DT-8) answers it with the nearer one, said as the
    nearest. The "eleven" inside it is a component of 10:45, not a second
    request for 11:00 (see `asked_clock_times`), so 11:20 is not preferred."""
    s, offer = _the_00_04_table()
    out = _say(s, "quarter to eleven works")
    assert out.startswith("The nearest I've got to quarter to eleven in the morning is half past ten")


def test_two_plausible_offered_times_reread_the_offer():
    """On a dense rota -- 10:00 and 10:15 both on the table -- 'ten past ten
    works' is within ten minutes of each. Two candidates, no question naming
    one of them: the offer again."""
    date = MON
    days = [_day(date, "Monday 14th September", ["10:00", "10:15", "10:30"])]
    s = {"clinic_id": "northgate", "available_days": days}
    offer = build_slot_offer(days, pretrimmed=False)
    apply_offer_to_session(s, offer_as_record(offer), offer.chunks)
    s["_slot_presentation_mode"] = "single_day"
    out = _say(s, "ten past ten works")
    assert out == "Sorry — I didn't catch which one. " + offer.text


def test_a_time_far_from_everything_offered_rereads_when_it_is_an_accept():
    """15:35 is on nobody's grid, off the spoken marks (so the resolver gives
    it no tolerance -- inv. 18) and not a slip for any offered time; an accept
    naming it gets the offer again, not a guess."""
    s, offer = _the_00_04_table()
    out = _say(s, "twenty five to four works")
    assert out == "Sorry — I didn't catch which one. " + offer.text


def test_a_real_unoffered_time_off_the_readout_is_still_a_request():
    s, offer = _the_00_04_table()
    out = _say(s, "half past three works")
    # 15:30 is bookable but unoffered, so DT-7 answers it as a real request
    # rather than this row: "Yes -- half past three ... is free".
    assert out.startswith("Yes — half past three")


def test_a_time_matching_nothing_at_all_rereads_when_it_is_an_accept():
    days = [_day(MON, "Monday 14th September", ["10:30", "11:20", "12:10"])]
    s = {"clinic_id": "northgate", "available_days": days}
    offer = build_slot_offer(days, pretrimmed=False)
    apply_offer_to_session(s, offer_as_record(offer), offer.chunks)
    s["_slot_presentation_mode"] = "single_day"
    out = _say(s, "3 o'clock works")
    assert out == "Sorry — I didn't catch which one. " + offer.text


# ── what it must not touch ─────────────────────────────────────────────────

def test_a_real_unoffered_time_is_still_answered_as_that_time():
    """DT-7 stays above this row: a diary time the caller names, offered or
    not, is a request for that time."""
    s, _ = _the_00_04_table()
    out = _say(s, "one o'clock works")
    assert out.startswith("Yes — one in the afternoon on Monday 14th September is free")


@pytest.mark.parametrize("said", [
    "what else have you got",
    "anything later",
    "a different day",
    "say that again",
    "none of those work",
])
def test_these_do_not_reach_the_clarifier(said):
    s, _ = _the_00_04_table()
    out = _say(s, said)
    assert not (out or "").startswith(("Just to check", "Sorry — I didn't catch")), out


def test_no_clock_time_named_means_not_this_row():
    """'that one works' is the accept reader's, and if it declined there is
    nothing for a clarifier to be plausible against."""
    s, _ = _the_00_04_table()
    assert sf.accept_clarify_speech(s, "yeah that one works") is None


# ── the slip rule, on its own ──────────────────────────────────────────────

@pytest.mark.parametrize("asked, offered, plausible", [
    ("11:50", "12:10", True),    # ten TO twelve  <-> ten PAST twelve
    ("12:00", "12:10", True),    # "twelve" against 12:10
    ("11:00", "11:20", True),    # round hour, resolver tolerance
    ("11:45", "12:15", True),    # quarter TO <-> quarter PAST
    ("11:40", "12:10", False),   # twenty to twelve is nobody's slip for 12:10
    ("11:30", "12:30", False),   # half eleven / half twelve: two times, not a slip
    ("11:55", "12:10", False),   # five to twelve: 15 min, not a mirror
    ("10:45", "10:30", False),   # quarter to eleven vs half ten: 15 min
    ("10:45", "11:20", False),
    ("13:00", "12:10", False),
])
def test_plausible_slip(asked, offered, plausible):
    assert sf._plausible_slip(asked, offered) is plausible
