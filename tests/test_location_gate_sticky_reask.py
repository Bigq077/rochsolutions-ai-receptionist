"""
tests/test_location_gate_sticky_reask.py
----------------------------------------
Clinic-resolver v2-2 — sticky re-ask after the escape hatch (sign-off sweep
Call 12; the tester got stuck in an OUTER loop even after the keypad loop was
broken).

The booking→location gate fires when:
    v3_booking_intent AND not v3_location_asked AND not v3_location_confirmed

The ladder escape hatch (v2-1 era) cleared v3_location_asked et al. and routed
the caller to the LLM — but left v3_booking_intent=True and never confirmed a
clinic.  So on the VERY NEXT utterance the gate re-armed and re-asked "Awlstuh
or Redditch?" from scratch: escape breaks the inner keypad loop, the gate
rebuilds an outer one.

Fix: _disengage_location_gate() stands the gate down FULLY — it also clears
v3_booking_intent, so _location_gate_should_fire() stays False on the next turn.
Booking still resumes: a fresh booking cue re-sets the intent latch elsewhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_streams.connection import (
    _disengage_location_gate,
    _location_gate_should_fire,
)


# ── the gate predicate (single source of truth for connection.py ~6099) ──────
def test_gate_fires_when_intent_and_unresolved():
    assert _location_gate_should_fire(
        {"v3_booking_intent": True}
    ) is True


def test_gate_silent_when_location_already_asked():
    assert _location_gate_should_fire(
        {"v3_booking_intent": True, "v3_location_asked": True}
    ) is False


def test_gate_silent_when_location_confirmed():
    assert _location_gate_should_fire(
        {"v3_booking_intent": True, "v3_location_confirmed": True}
    ) is False


def test_gate_silent_without_booking_intent():
    assert _location_gate_should_fire({}) is False
    assert _location_gate_should_fire({"v3_booking_intent": False}) is False


# ── the disengage helper (escape hatch) ──────────────────────────────────────
def test_disengage_clears_every_location_flag():
    session = {
        "v3_location_asked": True,
        "v3_location_q_active": True,
        "v3_awaiting_use_this_clinic": True,
        "v3_awaiting_location_dtmf": True,
        "v3_location_reask_count": 3,
        "v3_booking_intent": True,
    }
    _disengage_location_gate(session)
    assert session["v3_location_asked"] is False
    assert session["v3_location_q_active"] is False
    assert session["v3_awaiting_use_this_clinic"] is False
    assert session["v3_awaiting_location_dtmf"] is False
    assert session["v3_location_reask_count"] == 0
    # the crux of v2-2: the booking-intent latch must be cleared too
    assert session["v3_booking_intent"] is False


def test_disengage_stops_the_sticky_reask():
    # A caller mid-booking who exhausted the keypad: gate WOULD re-fire...
    session = {"v3_booking_intent": True, "v3_location_asked": True}
    # (escape first clears v3_location_asked, which alone would re-arm the gate)
    _disengage_location_gate(session)
    # ...after a full disengage it must stay silent on the next turn.
    assert _location_gate_should_fire(session) is False
