"""
tests/regression/test_write_ack_filler_gate.py
----------------------------------------------
FM-25 (2026-07-22, JV live call) — the write-ack filler "Just locking that in
now…" must NOT play unless the caller actually confirmed the booking.

Live reproduction: at "Shall I go ahead and book that in for you?" the caller
said "no don't book it in". No booking was written (correct — the FM-01 gate),
but the system still SPOKE "Just locking that in now…" because
`confirm_write_filler` keyed only off the prior assistant CTA and never checked
the caller's reply. The caller had to repeat "I said no don't book it in".

Sibling of FM-01: the confirm CTA being asked is necessary but not sufficient —
consent must be verified. Fix: `confirm_write_filler` now takes
`caller_confirmed`, computed at the call site from
`_book_reply_is_affirmative(messages)`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.filler_phrases import confirm_write_filler
from app.media_streams.llm_stream import _book_reply_is_affirmative

BOOK_CTA = {"conversation_history": [
    {"role": "user", "content": "quarter to eight suits me"},
    {"role": "assistant", "content": (
        "Just to confirm — Quentin, Wednesday at quarter to eight. "
        "Shall I go ahead and book that in for you?"
    )},
]}
MOVE_CTA = {"conversation_history": [
    {"role": "assistant", "content": "Right — shall I move it for you to Thursday?"},
]}
NO_CTA = {"conversation_history": [
    {"role": "assistant", "content": "How can I help you today?"},
]}


def test_write_ack_on_confirmed_yes():
    assert confirm_write_filler(BOOK_CTA, caller_confirmed=True) == "Just locking that in now…"


def test_no_write_ack_when_not_confirmed():
    """FM-25: caller said no/ambiguous → must NOT claim to be booking."""
    assert confirm_write_filler(BOOK_CTA, caller_confirmed=False) is None


def test_reschedule_ack_gated_too():
    assert confirm_write_filler(MOVE_CTA, caller_confirmed=True) == "Just moving that for you now…"
    assert confirm_write_filler(MOVE_CTA, caller_confirmed=False) is None


def test_no_ack_without_a_cta():
    assert confirm_write_filler(NO_CTA, caller_confirmed=True) is None


def test_live_no_reply_gives_no_write_ack():
    """The exact live utterance must resolve to caller_confirmed=False → no ack."""
    messages = [{"role": "user", "content": "no don't book it in"}]
    caller_confirmed = _book_reply_is_affirmative(messages)
    assert caller_confirmed is False
    assert confirm_write_filler(BOOK_CTA, caller_confirmed) is None


def test_live_yes_reply_gives_write_ack():
    messages = [{"role": "user", "content": "yes please go ahead"}]
    caller_confirmed = _book_reply_is_affirmative(messages)
    assert caller_confirmed is True
    assert confirm_write_filler(BOOK_CTA, caller_confirmed) == "Just locking that in now…"
