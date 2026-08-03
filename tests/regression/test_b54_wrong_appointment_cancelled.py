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

This file covers the STEERING half only. The gate half — `_note_name_spoken`
([llm_stream.py](../../app/media_streams/llm_stream.py)) flips the moment any
name token reaches TTS, without the caller having agreed to anything — is
deliberately NOT changed here. It is a behaviour change on the destructive path
and needs its own measurement. `test_the_gate_half_is_still_open` pins that so
the gap is not mistaken for closed.
"""
import inspect

import pytest

from app.media_streams import llm_stream as ls
from app.tools.receptionist_tools import (
    LOOKUP_AMBIGUOUS_KEY,
    LOOKUP_MATCH_COUNT_KEY,
    LOOKUP_NAME_SPOKEN_KEY,
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
    """A name alone does not identify WHICH appointment."""
    r = _LOOKUP_AMBIGUOUS_RULE.lower()
    assert "day and time" in r
    assert "[day] at [time]" in r


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


# ── what is deliberately NOT fixed ─────────────────────────────────────────


def test_the_gate_half_is_still_open():
    """SCOPE MARKER, not an endorsement.

    `_note_name_spoken` satisfies the B-42 gate as soon as a name token reaches
    TTS — it never checks that the caller AGREED, and never considers which
    appointment. On CA156fa25 that is precisely what let the cancel through.
    Steering reduces the odds; it is not a guard.

    This asserts the CURRENT behaviour so that closing the gap is a deliberate,
    tested change rather than an accident. If this test starts failing, someone
    has changed the gate — good, but it needs its own measurement against the
    27 legitimate lines and the shared-phone case.
    """
    session = {"_lookup_patient_name": "Quentin Rock"}
    ls._note_lookup_name_spoken(session, "I've got an appointment for Quentin Rock")
    assert session.get(LOOKUP_NAME_SPOKEN_KEY) is True, (
        "gate still flips on the name being SPOKEN, with no caller agreement"
    )
