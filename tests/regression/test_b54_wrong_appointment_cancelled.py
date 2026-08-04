"""B-54 — the caller was never told there was more than one appointment, so
they could not ask for a different one.

CA156fa25206ffa7b15cb3474b617c8672 (2026-08-03 22:46, build 68077af59dd3). The
caller rang to cancel the appointment they had booked four minutes earlier
(15 Aug 11:45). The log:

    lookup_patient (gcal): match 1/15 name='Quentin Rock'
                           AMBIGUOUS — name must be read back (B-42)
    tool result: appointment_time "2026-08-05T20:30:00+01:00"
    B-42: looked-up name 'Quentin Rock' was spoken — identity gate satisfied
    cancel_appointment → success
    cancelled_event: "... for Marcus — Quentin Rock", was_at 2026-08-05T20:30

**A different, real calendar event was cancelled.**

Why B-42 did not stop it: B-42 answers "is this the right PERSON". All 15
matches were the same person, so saying the name settled nothing — the caller
said "yes it is" to their own name and appointment #1 was cancelled. There is
no path for "that's me, but not that appointment", and the caller was never
told the other 14 existed.

Both halves are now covered here.

The STEERING half (`c273475`) states the count, requires the day and date and
time, and extends the `next=true` escape to "not the appointment they meant".

The GATE half adds a second latch on the APPOINTMENT axis:
`_note_lookup_slot_spoken` ([llm_stream.py](../../app/media_streams/llm_stream.py))
sets `_lookup_slot_spoken` only when the matched appointment's DATE reaches TTS,
and `_lookup_identity_unconfirmed` now requires both latches.

Note the cause this file originally recorded was wrong, and it is worth not
re-inheriting: the open gap was written up as the gate "never checking that the
caller agreed". On CA156fa25 the caller DID agree — they said "yes it is" to
their own name. Requiring agreement would have changed nothing. The agreement
was about the PERSON while the write was about an APPOINTMENT, which is why the
new latch is on the date rather than on a second yes.

B-42's guarantee is untouched and must stay that way: a nameless read-back still
blocks. This change only ever ADDS a condition.
"""
import inspect

import pytest

from app.media_streams import llm_stream as ls
from app.tools.receptionist_tools import (
    LOOKUP_AMBIGUOUS_KEY,
    LOOKUP_MATCH_COUNT_KEY,
    LOOKUP_NAME_SPOKEN_KEY,
    LOOKUP_SLOT_SPOKEN_KEY,
    _LOOKUP_AMBIGUOUS_RULE,
    _note_lookup_ambiguity,
    _with_ambiguity_rule,
)


def _rule(name="Quentin Rock", total=15):
    return _with_ambiguity_rule({"found": True}, name, total)["caller_message_rule"]


# ── the caller must learn that other appointments exist ────────────────────


def test_the_rule_states_how_many_appointments_there_are():
    """The root cause. On CA156fa25 the caller had no way to know 14 others
    existed, so "yes it is" was the only sensible answer to the one they were
    read."""
    assert "15" in _rule(total=15)


@pytest.mark.parametrize("total", [2, 3, 15])
def test_the_count_is_the_real_count(total):
    assert str(total) in _rule(total=total)


def test_the_rule_tells_the_model_to_say_how_many():
    r = _LOOKUP_AMBIGUOUS_RULE.lower()
    assert "say how many" in r
    assert "do not know others exist" in r


# ── "that's me, but not that appointment" must be a path ───────────────────


def test_wrong_appointment_is_an_escape_route_not_just_wrong_person():
    """Before this change the rule stepped with next=true ONLY 'if they say it
    is not them'. Same person, several appointments — the exact CA156fa25 shape
    — had no escape at all."""
    r = _LOOKUP_AMBIGUOUS_RULE.lower()
    assert "not the appointment they meant" in r
    assert "next=true" in r


def test_the_rule_still_requires_the_day_and_time():
    """A name alone does not identify WHICH appointment.

    Tightened with the gate half: the placeholder now carries the DATE as well
    as the weekday. `[day] at [time]` could render as a bare "Tuesday", which
    does not distinguish one appointment in a course of treatment from the
    next — and would leave the B-54 latch shut, looping the caller."""
    r = _LOOKUP_AMBIGUOUS_RULE.lower()
    assert "the day, the date and the time" in r
    assert "[day] the [date] at [time]" in r


# ── B-42's guarantees must survive unchanged ───────────────────────────────


def test_b42_identity_contract_is_not_weakened():
    """This change ADDS to the rule. If any of B-42/B-44's pinned literals were
    dropped to make room, the shared-phone / different-person case regresses —
    which is a worse defect than the one being fixed."""
    r = _LOOKUP_AMBIGUOUS_RULE
    assert "SAY THE NAME" in r
    assert "is that you?" in r
    assert "reschedule or cancel" in r
    assert "next=true" in r


def test_still_silent_on_a_single_match():
    """One match is the overwhelmingly common case and must stay a single turn."""
    assert "caller_message_rule" not in _with_ambiguity_rule({"found": True}, "Q", 1)


def test_a_missing_name_still_does_not_crash_the_format():
    """Now formats TWO fields, so there are two ways to raise KeyError."""
    out = _with_ambiguity_rule({}, "", 5)
    assert "that patient" in out["caller_message_rule"]
    assert "5" in out["caller_message_rule"]


# ── the count reaches the write-gate message too ───────────────────────────


def test_the_match_count_is_recorded_on_the_session():
    """The gate refusal message lives in llm_stream and only has the session.
    Before this change the session carried the ambiguity BOOLEAN but not the
    count, so the refusal could not tell the caller how many there were."""
    session = {}
    _note_lookup_ambiguity(session, 15)
    assert session[LOOKUP_MATCH_COUNT_KEY] == 15
    assert session[LOOKUP_AMBIGUOUS_KEY] is True
    assert session[LOOKUP_NAME_SPOKEN_KEY] is False


def test_stepping_to_the_next_match_refreshes_the_count_and_clears_name_spoken():
    """next=true changes which appointment is selected, so a name spoken for the
    previous one must not satisfy the gate for this one."""
    session = {LOOKUP_NAME_SPOKEN_KEY: True}
    _note_lookup_ambiguity(session, 3)
    assert session[LOOKUP_NAME_SPOKEN_KEY] is False
    assert session[LOOKUP_MATCH_COUNT_KEY] == 3


def test_gate_message_and_rule_still_agree():
    """Two texts instructing the model about the same situation. B-44 already
    pinned this; the new obligations must land in BOTH or one trains the model
    out of the other."""
    gate_src = inspect.getsource(ls.LLMStream._execute_tools)
    i = gate_src.find("identity_confirmation_required")
    region = gate_src[i:i + 2000]
    for token in ("next=true", "is that you"):
        assert token in region
        assert token in _LOOKUP_AMBIGUOUS_RULE
    # the two NEW obligations
    assert "HOW MANY" in region
    assert "not the appointment they" in region


# ── the gate half — CLOSED, on the appointment axis ───────────────────────
#
# The scope marker that used to live here asserted the open behaviour so the gap
# would not be mistaken for closed. It is replaced rather than deleted, and the
# reasoning it carried is worth keeping straight, because it named the wrong
# cause: it said the gate "never checks that the caller AGREED". On CA156fa25
# the caller DID agree — the register's own narrative has them saying "yes it
# is" to their own name. Requiring agreement would have changed nothing.
#
# What was missing is that the agreement was about the PERSON and the write was
# about an APPOINTMENT. So the second latch is on the appointment axis: the
# matched date must reach TTS before a destructive write is allowed.


def _post_lookup(when: str = "2026-08-05T20:30:00+01:00", total: int = 15) -> dict:
    """The CA156fa25 session: 15 matches, all the same person, emitted match #1
    on 5 Aug — while the caller meant the one booked four minutes earlier."""
    s = {"_lookup_patient_name": "Quentin Rock",
         "_lookup_appointment_datetime": when}
    _note_lookup_ambiguity(s, total)
    return s


def test_the_verbatim_call_is_now_blocked():
    """The name was spoken and the gate opened. It must not any more."""
    s = _post_lookup()
    ls._note_lookup_name_spoken(s, "I've got an appointment for Quentin Rock")
    ls._note_lookup_slot_spoken(s, "I've got an appointment for Quentin Rock")
    assert ls._lookup_identity_unconfirmed(s) is True, (
        "the name alone reopened the write path that cancelled the wrong event"
    )


def test_speaking_the_date_opens_it():
    s = _post_lookup()
    spoken = ("I've got 15 on this number — this one's for Quentin Rock on "
              "Wednesday the 5th at half past eight in the evening. Is that "
              "you? And is that the one you mean?")
    ls._note_lookup_name_spoken(s, spoken)
    ls._note_lookup_slot_spoken(s, spoken)
    assert ls._lookup_identity_unconfirmed(s) is False


def test_the_weekday_alone_is_not_enough():
    """A course of treatment is weekly — "Wednesday" names several of them."""
    s = _post_lookup()
    ls._note_lookup_slot_spoken(s, "that one's on Wednesday")
    assert s.get(LOOKUP_SLOT_SPOKEN_KEY) is not True


def test_the_date_alone_is_not_enough():
    """Symmetric: a bare "the 5th" with no weekday is thin evidence, and the
    dangerous direction here is a false POSITIVE."""
    s = _post_lookup()
    ls._note_lookup_slot_spoken(s, "that one's the 5th")
    assert s.get(LOOKUP_SLOT_SPOKEN_KEY) is not True


def test_a_different_date_does_not_open_it():
    """The wrong date must not satisfy the latch — that is the whole failure."""
    s = _post_lookup()
    ls._note_lookup_slot_spoken(s, "I've got one for you on Friday the 14th")
    assert s.get(LOOKUP_SLOT_SPOKEN_KEY) is not True


def test_the_word_form_of_the_ordinal_counts():
    """TTS may render "5th" as "fifth"; the caller heard the date either way."""
    s = _post_lookup()
    ls._note_lookup_slot_spoken(s, "that's the Wednesday, the fifth of August")
    assert s.get(LOOKUP_SLOT_SPOKEN_KEY) is True


def test_an_unparseable_datetime_fails_closed():
    """A destructive write on the input we understand least must not proceed.
    The caller is not stranded: the refusal is a message to the model, so the
    turn continues and degrades to taking a message."""
    s = _post_lookup(when="not-a-date")
    ls._note_lookup_slot_spoken(s, "Wednesday the 5th")
    assert s.get(LOOKUP_SLOT_SPOKEN_KEY) is not True


def test_stepping_to_the_next_match_re_arms_the_slot_latch():
    """`next=true` changes WHICH APPOINTMENT is in play — the reason this latch
    exists. Carrying the previous match's confirmation forward would reproduce
    B-54 one step down the list."""
    s = _post_lookup()
    ls._note_lookup_slot_spoken(s, "Wednesday the 5th")
    assert s.get(LOOKUP_SLOT_SPOKEN_KEY) is True
    _note_lookup_ambiguity(s, 15)                    # what _emit does on step
    s["_lookup_appointment_datetime"] = "2026-08-15T11:45:00+01:00"
    assert s.get(LOOKUP_SLOT_SPOKEN_KEY) is False
    assert ls._lookup_identity_unconfirmed(s) is True


def test_a_single_match_is_still_never_blocked():
    """The overwhelmingly common case. Both latches hang off the ambiguity
    flag, so one appointment on the number must not grow an extra turn."""
    s = _post_lookup(total=1)
    assert ls._lookup_identity_unconfirmed(s) is False


def test_same_day_duplicates_are_a_known_residual():
    """Stated, not hidden. The latch matches the DATE, not the time, because
    matching spoken times reliably is a false-negative factory and on this path
    a false negative loops a caller entitled to cancel. Two appointments on the
    same date therefore still resolve to whichever the lookup emitted first."""
    s = _post_lookup()
    ls._note_lookup_slot_spoken(s, "Wednesday the 5th at nine in the morning")
    assert s.get(LOOKUP_SLOT_SPOKEN_KEY) is True, (
        "documents the residual: the 20:30 appointment's latch is satisfied by "
        "speaking a DIFFERENT time on the same date"
    )
