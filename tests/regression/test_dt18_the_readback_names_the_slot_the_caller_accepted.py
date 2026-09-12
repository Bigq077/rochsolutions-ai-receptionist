"""
Regression: the booking read-back names the slot the caller accepted.

Spec row DT-18 ("confirm THAT slot — day, date, time"); invariant 2.

Vital Edge CAea24df48, 12 September 2026 12:52, build dfaa0b02, a LIVE
patient line:

    12:52:21  'do you have anything around 12 on midday'
              caller ACCEPTED 2026-09-14T12:00:00+01:00
    12:52:22  "Midday on Monday the 14th — shall I put that one in for you?"
    12:52:28  'uh yes please'  ->  "Could I take your first name and surname?"
    12:52:36  name.  12:52:49  phone.
    12:52:51  "So that's Quentin Rook, Monday the 14th of September at
               FIVE IN THE EVENING — shall I put that request through to
               Jonathan to confirm?"

17:00 was a real Monday slot, spoken two turns earlier ("I also have five in
the evening"), so nothing caught it: Gate 5 checks whether the time is in an
offer and it was; the slot-fact guard checks whether the time is in the diary
and it is. That is invariant 4, not invariant 1 — the guard's own docstring
says so in capitals.

THREE holes lined up, and each is closed here:

 1. the accept reader took an existence QUESTION as a pick, because the
    utterance contained the spoken label "midday" and
    `utterance_is_slot_selection` is containment against those labels. The
    11 Sep fix for "what about monday morning" deferred the BAND fallback to
    a question pattern and left the TIME-label path alone;
 2. `ACCEPTED_SLOT_KEY` is per-TURN by design (P6b), so it was long gone by
    the read-back three turns later;
 3. neither speech-derived phrase key was set — the model's reply carried no
    slot phrase, and "shall I put that one in for you?" is not one of
    `_SPOKEN_COMMITMENT_RE`'s markers — so the read-back prompt fell to its
    last branch, which asks the model to fill the time in from memory.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import utterance_is_slot_selection
from app.tools import slot_fact_guard as guard
from app.tools import slot_followup as sf
from app.tools.receptionist_tools import _spoken_slot_time
from app.tools.slot_offer import (
    apply_offer_to_session,
    build_slot_offer,
    offer_as_record,
)

MON = "2026-09-14"
#: Vital Edge's real Monday, from the diary reader on the call.
VE_MONDAY = ["12:00", "16:00", "17:00", "18:00"]


def _day(date, label, times):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t) for t in times],
        "slots": [{"start": f"{date}T{t}:00+01:00"} for t in times],
    }


def _vital_edge():
    days = [_day(MON, "Monday 14th September", VE_MONDAY)]
    s = {"clinic_id": "vital_edge", "available_days": days}
    offer = build_slot_offer(days, pretrimmed=False)
    apply_offer_to_session(s, offer_as_record(offer), offer.chunks)
    s["_slot_presentation_mode"] = "single_day"
    return s


def _say(s, text):
    guard.note_caller_speech(s, text)
    return sf.try_unspoken_followup_speech(s, text)


# ── 1. a question is not an acceptance ─────────────────────────────────────

def test_the_12_52_utterance_is_not_read_as_a_pick():
    s = _vital_edge()
    said = "um do you have any do you have anything around 12 on midday"
    assert sf.slot_accepted_by_caller(s, said) is None


def test_it_still_contains_a_spoken_label_so_the_old_reader_would_take_it():
    """The cause, kept as a test: containment against the offer's labels is
    what made an existence question look like a choice."""
    s = _vital_edge()
    said = "um do you have any do you have anything around 12 on midday"
    assert utterance_is_slot_selection(said, s), "the shape no longer reproduces"


@pytest.mark.parametrize("said", [
    "do you have midday",
    "have you got anything at midday",
    "is there anything at midday",
    "any slots at midday",
    "what have you got around midday",
])
def test_existence_questions_never_accept(said):
    assert sf.slot_accepted_by_caller(_vital_edge(), said) is None


def test_what_about_a_named_time_is_left_as_the_11_sep_decision_had_it():
    """`_DAY_REQUEST_RE` holds "what about"; this rule deliberately does not.
    On 11 Sep a day plus an EXPLICIT TIME was decided to name its slot ("what
    about monday at eight" picks 08:00) while a day plus a band does not, and
    `test_a_day_and_band_named_is_a_question_not_a_pick` pins both halves.

    The consequence, recorded rather than hidden: "what about midday" still
    reads as choosing midday. Consistent with 11 Sep, and D-s means the
    read-back names midday rather than drifting — but a caller who was only
    asking has been moved a step. Flagged for the owner in the spec."""
    got = sf.slot_accepted_by_caller(_vital_edge(), "what about midday")
    assert got and got[11:16] == "12:00"


@pytest.mark.parametrize("said, want", [
    ("midday works", "12:00"),
    ("yeah midday please", "12:00"),
    ("the midday one", "12:00"),
    ("midday", "12:00"),
    ("midday's fine", "12:00"),
    ("let's do midday", "12:00"),
    ("i'll take midday", "12:00"),
    # "can I have" / "could I take" ask FOR the slot: acceptances, and the
    # reason this rule is narrower than `_DAY_REQUEST_RE`, which holds them.
    ("can i have midday", "12:00"),
    ("could i take the midday one", "12:00"),
    ("yeah the six in the evening works", "18:00"),
    ("number two", "17:00"),
])
def test_real_acceptances_still_accept(said, want):
    got = sf.slot_accepted_by_caller(_vital_edge(), said)
    assert got and got[11:16] == want, got


def test_the_question_now_reaches_the_producer_and_narrows_the_offer():
    s = _vital_edge()
    out = _say(s, "um do you have any do you have anything around 12 on midday")
    assert out == ("Yes — midday on Monday 14th September is free. "
                   "Shall I book that in for you?")
    assert [o["start"][:16] for o in s["last_offered_slots"]] == [f"{MON}T12:00"]


# ── 2. the read-back has an engine-authored source ─────────────────────────

def test_the_whole_call_now_reads_back_midday():
    """The 12:52 sequence, end to end."""
    s = _vital_edge()
    _say(s, "yeah what else on monday")
    _say(s, "say that again")
    _say(s, "um do you have any do you have anything around 12 on midday")
    # name and phone follow; the read-back fires two turns later.
    assert sf.readback_slot_phrase(s) == "Monday 14th September at midday"
    assert "five in the evening" not in sf.readback_slot_phrase(s)


def test_a_named_acceptance_is_pinned_for_the_call_not_the_turn():
    s = _vital_edge()
    sf.note_accepted_slot(s, f"{MON}T17:00:00")
    # `ACCEPTED_SLOT_KEY` is per-turn and may be gone; this one is not.
    s.pop(sf.ACCEPTED_SLOT_KEY, None)
    assert sf.readback_slot_phrase(s) == "Monday 14th September at five in the evening"


def test_a_changed_mind_takes_the_newest_slot():
    s = _vital_edge()
    sf.note_accepted_slot(s, f"{MON}T12:00:00")
    sf.note_accepted_slot(s, f"{MON}T18:00:00")
    assert sf.readback_slot_phrase(s) == "Monday 14th September at six in the evening"


def test_the_phrase_is_built_from_the_payload_not_from_speech():
    """D-n: the engine authors the slot fact. The phrase uses the payload's
    own spoken label, so it cannot drift from the diary."""
    s = _vital_edge()
    sf.note_accepted_slot(s, f"{MON}T16:00:00")
    assert sf.readback_slot_phrase(s) == "Monday 14th September at four in the afternoon"
    assert _spoken_slot_time("16:00") == "four in the afternoon"


def test_several_slots_on_the_table_and_no_acceptance_declines():
    """Nothing here knows which was chosen, so it says nothing and the
    speech-derived keys downstream get their turn."""
    assert sf.readback_slot_phrase(_vital_edge()) == ""


def test_a_slot_no_longer_in_the_diary_is_not_pinned():
    s = _vital_edge()
    sf.note_accepted_slot(s, f"{MON}T09:00:00")     # never on Vital Edge's Monday
    assert sf.readback_slot_phrase(s) == ""


@pytest.mark.parametrize("session", [None, "not a session", 42, {}])
def test_the_readers_never_raise(session):
    assert sf.readback_slot_phrase(session) == ""
    assert sf.accepted_slot_phrase(session) == ""
    sf.note_accepted_slot(session, f"{MON}T12:00:00")


def test_a_landed_booking_clears_the_pin():
    """The pin carries ONE agreement to its read-back; a booking consumes it.
    B-75's shape — an arm outliving the turn that needed it — is why this is
    explicit rather than left to expire."""
    import inspect
    from app.tools import receptionist_tools as rt
    src = inspect.getsource(rt)
    assert src.count('session.pop("_accepted_slot_record", None)') == 2, (
        "both booking_confirmed sites must clear the pin"
    )
