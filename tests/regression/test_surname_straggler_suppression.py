"""Regression guard for Change A — silent surname-straggler suppression.

Under the name-first prompt, turn 1 already answers the caller's question AND
acknowledges the first name, so a trailing same-breath surname fragment ("Rock"
a beat after "Quentin") has nothing left to say. Before Change A it was
dispatched as a full LLM turn, which restated "Got it — Quentin Rock. Would you
like to go ahead and book that assessment?" — a readback that (a) violates the
never-read-back-the-surname rule and (b) is NOT one of the CTA booking phrases,
so it overwrote turn 1's matching CTA and the caller's next "yes please" failed
CTA-affirm → dead air → abandoned call (live call 2026-07-12 17:51).

Change A (connection.py, KEPT-straggler branch) attempts a silent back-fill at
that point and drops the turn when it succeeds. The whole decision reduces to:

    if _v3_try_persist_name(...):   # True  -> `continue` (turn suppressed)
        continue                    # False -> falls through unchanged

so these tests pin that gate: True on a real surname straggler, False on every
other short fragment — which is exactly what keeps the suppression from
swallowing a meaningful reply.
"""
from __future__ import annotations

from app.media_streams.connection import _v3_try_persist_name

# Turn 1's reply: FAQ answer + first-name ack + booking CTA. It deliberately
# does NOT contain the word "surname" — v3_awaiting_surname carries that, which
# is the whole reason a bare "Rock" a beat later needs the back-fill.
_TURN1_REPLY = (
    "Thanks Quentin — shockwave therapy is something we offer; we'd usually "
    "start with a physio assessment. Would you like to book one?"
)


def _failure_session() -> dict:
    """First name locked, no surname yet, awaiting surname — the state the
    'Rock' straggler lands in once turn 1 has completed."""
    return {
        "collected": {"name": "Quentin"},
        "patient_name": "Quentin",
        "v3_awaiting_surname": True,
        "v3_name_collection_active": True,
        "last_bot_prompt": _TURN1_REPLY,
    }


def test_bare_surname_straggler_is_captured_and_suppressed():
    """The 17:51 failure: 'Rock' must back-fill silently so the turn is dropped."""
    s = _failure_session()
    suppressed = _v3_try_persist_name(s, s["last_bot_prompt"], caller_utterance="Rock")

    assert suppressed is True, "gate must return True so Change A's `continue` fires"
    assert s["patient_name"] == "Quentin Rock"
    assert s["collected"]["name"] == "Quentin Rock"
    assert s["v3_awaiting_surname"] is False
    assert s["v3_name_collection_active"] is False


def test_non_surname_fragment_is_not_swallowed():
    """A short affirmation must fall through to a normal turn, not be eaten."""
    s = _failure_session()
    suppressed = _v3_try_persist_name(s, s["last_bot_prompt"], caller_utterance="yes please")

    assert suppressed is False, "must fall through so the affirmation is handled"
    assert s["patient_name"] == "Quentin", "no bogus surname may be stored"


def test_no_first_name_yet_keeps_pending_transcript_path():
    """In-flight ordering: turn 1 not finished, no first name stored — the gate
    must decline so the existing pending_transcript route is left untouched."""
    s = {
        "collected": {},
        "patient_name": "",
        "v3_awaiting_surname": False,
        "last_bot_prompt": "Brilliant — could I quickly take your first name and surname?",
    }
    suppressed = _v3_try_persist_name(s, s["last_bot_prompt"], caller_utterance="Rock")

    assert suppressed is False


def test_no_backfill_after_booking_confirmed():
    """A stray 'Rock' after the booking is locked must not attach a surname."""
    s = _failure_session()
    s["booking_confirmed"] = True
    suppressed = _v3_try_persist_name(s, s["last_bot_prompt"], caller_utterance="Rock")

    assert suppressed is False
    assert s["patient_name"] == "Quentin"
