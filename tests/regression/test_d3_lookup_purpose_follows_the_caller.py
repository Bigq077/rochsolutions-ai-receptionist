# tests/regression/test_d3_lookup_purpose_follows_the_caller.py
"""
D3 - `lookup_patient` ran as a reschedule on a cancel.

Demo line, 9 Sep 2026 03:35:58. The caller asked to cancel, the engine recorded
the intent correctly (`[ms_conn v3] caller intent = cancel`), and the model then
called `lookup_patient(purpose="reschedule")` anyway.

It had been told to. The schema's own description reads

    "'cancel' or 'reschedule' - look up an upcoming appointment."

which says the two values are interchangeable for the only thing the model can
observe about them. The enum lists three values and the description distinguishes
one of them.

`_lookup_purpose` is not decoration. Two sites read it and both change what the
caller is offered:

  * `_reschedule_busy_block` - subtracts the appointment being MOVED from the
    availability grid, so a cancel silently loses its own slot from the diary;
  * `_reschedule_duration_override` - sizes any later grid by the appointment's
    length rather than by the service the caller named.

Since `6f277d41` the engine has an answer of its own, taken from the caller's own
words by `hold_speech.classify_intent` and stored as `v3_caller_intent`. It never
downgrades to "booking", so a stored "cancel" or "reschedule" is something the
caller actually said. That is the authority; the model's label is a suggestion.

The correction is deliberately limited to the cancel/reschedule pair. "history"
is a different operation and is never rewritten, and an unset or "booking" intent
leaves the model's value alone - the engine only overrules where it knows better.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import (
    LOOKUP_PURPOSE_KEY,
    TOOL_LOOKUP_PATIENT,
    _authoritative_lookup_purpose,
)


# ── the schema must not tell the model the two values are the same ──────────

def test_the_schema_distinguishes_cancel_from_reschedule():
    """The description must describe each value, not lump the pair together."""
    desc = TOOL_LOOKUP_PATIENT["input_schema"]["properties"]["purpose"]["description"]
    lower = desc.lower()
    assert "'cancel' or 'reschedule' — look up" not in desc
    assert "'cancel' or 'reschedule' - look up" not in desc
    # Each value gets its own clause naming what the caller wants.
    assert "cancel" in lower and "reschedule" in lower and "history" in lower
    # The distinguishing fact: one removes the appointment, the other moves it.
    assert "keep" in lower or "move" in lower or "different time" in lower


# ── the engine's recorded intent wins over the model's label ────────────────

@pytest.mark.parametrize(
    "intent, asked, expected",
    [
        # The live defect: caller cancelled, model said reschedule.
        ("cancel", "reschedule", "cancel"),
        # The mirror, for the same reason.
        ("reschedule", "cancel", "reschedule"),
        # Agreement is left alone.
        ("cancel", "cancel", "cancel"),
        ("reschedule", "reschedule", "reschedule"),
        # No recorded intent, or a booking: the engine knows nothing better.
        (None, "reschedule", "reschedule"),
        ("booking", "reschedule", "reschedule"),
        ("booking", "cancel", "cancel"),
        # 'history' is a different operation and is never rewritten, in
        # either direction.
        ("cancel", "history", "history"),
        ("reschedule", "history", "history"),
    ],
)
def test_the_recorded_intent_decides(intent, asked, expected):
    session = {} if intent is None else {"v3_caller_intent": intent}
    assert _authoritative_lookup_purpose(asked, session) == expected


def test_it_never_raises_on_junk():
    """A lookup must not fail because the intent bookkeeping is odd."""
    for session in ({}, {"v3_caller_intent": None}, {"v3_caller_intent": 7}):
        assert _authoritative_lookup_purpose("reschedule", session) == "reschedule"
    assert _authoritative_lookup_purpose(None, {"v3_caller_intent": "cancel"}) is not None
    assert _authoritative_lookup_purpose("", {"v3_caller_intent": "cancel"}) == "cancel"


# ── and the executor records the corrected value ────────────────────────────

def test_the_executor_stores_the_corrected_purpose():
    """`_lookup_purpose` is what the two reschedule gates read, so the
    correction has to reach the session, not just the local variable."""
    session = {"v3_caller_intent": "cancel"}

    async def _fake_gcal(args, sess):
        return {"found": False, "message": "stub"}

    with patch.object(rt, "uses_acuity", lambda s: False), \
            patch.object(rt, "_lookup_patient_gcal", _fake_gcal):
        asyncio.run(rt._exec_lookup_patient(
            {"purpose": "reschedule", "phone": "07502211207"}, session
        ))

    assert session[LOOKUP_PURPOSE_KEY] == "cancel"


def test_a_genuine_reschedule_still_arms_the_reschedule_gates():
    """B-77 must survive: a caller who asked to move an appointment keeps the
    sizing and busy-block behaviour that depends on this key."""
    session = {"v3_caller_intent": "reschedule"}

    async def _fake_gcal(args, sess):
        return {"found": False, "message": "stub"}

    with patch.object(rt, "uses_acuity", lambda s: False), \
            patch.object(rt, "_lookup_patient_gcal", _fake_gcal):
        asyncio.run(rt._exec_lookup_patient(
            {"purpose": "reschedule", "phone": "07502211207"}, session
        ))

    assert session[LOOKUP_PURPOSE_KEY] == "reschedule"
