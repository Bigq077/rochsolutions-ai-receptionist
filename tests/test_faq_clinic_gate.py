"""
tests/test_faq_clinic_gate.py
-----------------------------
F14 — the FAQ clinic-gate must NOT fire for clinic-independent questions.

Sweep Call 4, turn 8: "are any of the two clinics open on easter monday" matched
_FAQ_CLINIC_SPECIFIC_RE (via "open") and injected "Which clinic?" — but a bank
holiday closes BOTH clinics, so the answer is clinic-independent. The caller then
couldn't escape the which-clinic ladder and abandoned the call.

_faq_needs_clinic() decides whether the gate should fire: a clinic-specific topic
(parking / address / hours / transport) DOES gate, UNLESS the utterance is about a
bank holiday / closure (same at both clinics), which does NOT.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_streams.connection import _faq_needs_clinic


# ── clinic-specific topics SHOULD gate ──────────────────────────────────────
def test_parking_needs_clinic():
    assert _faq_needs_clinic("do you have parking") is True


def test_opening_hours_needs_clinic():
    assert _faq_needs_clinic("what are your opening hours") is True


def test_address_needs_clinic():
    assert _faq_needs_clinic("what's the address") is True


# ── bank-holiday / closure questions must NOT gate (F14) ─────────────────────
def test_easter_monday_does_not_gate():
    assert _faq_needs_clinic(
        "are any of the two clinics open on easter monday"
    ) is False


def test_bank_holiday_does_not_gate():
    assert _faq_needs_clinic("are you open on bank holidays") is False


def test_christmas_does_not_gate():
    assert _faq_needs_clinic("are you open over christmas") is False


# ── non-clinic-specific questions never gate ────────────────────────────────
def test_price_question_does_not_gate():
    assert _faq_needs_clinic("how much is a session") is False
