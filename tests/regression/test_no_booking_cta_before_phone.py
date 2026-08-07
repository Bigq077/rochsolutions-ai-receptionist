"""
The booking confirmation cannot be asked before the phone number is confirmed.

book_appointment's A1 gate requires phone_confirmed. Asking "shall I go ahead
and book that in?" before that is asking a question the system cannot act on:
the caller says yes, the write is refused, and the entire readback-and-confirm
procedure has to be run again once the number has been collected.

CA76b44ae9, 2026-08-07 — a booking that succeeded, slowly:

    08:16:35  "So that's Mark Da'ya, Monday the 10th of August at five…
               shall I go ahead and book that in?"       ← no phone yet
    08:16:43  caller: "thank you yeah"                    ← he agreed HERE
    08:16:45  "Just locking that in now…"
    08:16:47  book_appointment BLOCKED — phone step skipped
              (model passed phone="unknown" rather than admit it had none)
    08:16:49  "Before I lock that in — could you type your number…"
    08:17:14  readback + CTA, 2nd time → "thank you"   → classified no
    08:17:27  CTA, 3rd time            → "yes please"  → booked

He agreed at 08:16:43; it happened at 08:17:35. Fifty-two seconds and two
extra confirmations, on a 173-second call — and in between he was told the
booking was being made and then asked for more information.

WHY A GATE AND NOT PROMPT TEXT. The theorem_v3 prompt already orders this
correctly (phone is step 8, the readback step 9, with an explicit branch for
"no caller ID → go straight to the keypad"). The model went to step 9 anyway.

WHY IN sanitise_response. The site that arms phone collection —
connection.py "(name confirmed — phone collection phase)" — runs AFTER
run_turn, by which time the CTA has already been spoken. The per-chunk
sanitiser is the last point before TTS.
"""

import inspect
import re

import pytest

from app.media_streams.connection import _is_keypad_arming_line
from app.media_streams.turn_handler import (
    _BOOKING_CTA_SENTENCE_RE,
    _phone_question_for,
    sanitise_response,
)
from app.prompts.clinic_template_prompt import _PHONE_STEP_MARKERS


READBACK = (
    "So that's Mark Da'ya, Monday the 10th of August at five in the evening. "
    "Shall I go ahead and book that in?"
)


# ── the gate itself ────────────────────────────────────────────────────────

def test_the_cta_is_replaced_when_no_phone_is_confirmed():
    """The reproduction, with the caller-ID suppressed as it was on the call."""
    out = sanitise_response(READBACK, {"booking_flow_active": True, "twilio_from": ""})
    assert "shall i go ahead" not in out.lower()
    assert "book that in" not in out.lower()
    assert "keypad" in out.lower()


def test_the_readback_itself_is_kept():
    """
    Only the question is replaced. The caller should still hear what they are
    agreeing to — stripping the readback would be a different defect.
    """
    out = sanitise_response(READBACK, {"booking_flow_active": True, "twilio_from": ""})
    assert "Mark Da'ya" in out
    assert "Monday the 10th of August" in out
    assert "five in the evening" in out


def test_the_turn_still_ends_in_a_question():
    """A turn with no question is a dead end — the same failure Gate 5c guards."""
    for session in (
        {"booking_flow_active": True, "twilio_from": ""},
        {"booking_flow_active": True, "twilio_from_local": "07760512084"},
    ):
        assert "?" in sanitise_response(READBACK, session)


def test_spacing_survives_the_substitution():
    """
    The pattern's [^.!?]* swallows the space after the previous sentence. This
    text becomes last_bot_prompt and conversation_history, where sentence
    splitters read it, so "evening.Before" is not acceptable.
    """
    out = sanitise_response(READBACK, {"booking_flow_active": True, "twilio_from": ""})
    assert ".Before" not in out
    assert not re.search(r"\s{2,}", out)


# ── it must not fire anywhere else ─────────────────────────────────────────

def test_confirmed_phone_passes_through_untouched():
    session = {"booking_flow_active": True, "phone_confirmed": True}
    assert sanitise_response(READBACK, session) == READBACK


def test_outside_a_booking_flow_passes_through_untouched():
    assert sanitise_response(READBACK, {}) == READBACK


def test_an_early_booking_offer_is_not_a_write_cta():
    """
    "Would you like to book?" early in a call is a legitimate offer and must
    not be rewritten into a phone question. Only the WRITE confirmation waits
    for the number.
    """
    offer = "We do physiotherapy and shockwave. Would you like to book an appointment?"
    assert not _BOOKING_CTA_SENTENCE_RE.search(offer)


# ── the substituted question must work downstream ──────────────────────────

def test_the_keypad_form_arms_the_keypad():
    """
    Otherwise the caller types their number into a closed keypad — the exact
    failure CA6e1024db records, where nine digits were discarded silently.
    """
    ask = _phone_question_for({"twilio_from": ""})
    assert _is_keypad_arming_line(ask)


@pytest.mark.parametrize("session", [
    {"twilio_from": ""},                        # withheld / suppressed
    {"twilio_from_local": "07760512084"},       # caller ID held
])
def test_both_forms_register_the_phone_step_as_asked(session):
    """
    _PHONE_STEP_MARKERS is what book_appointment's backstop reads to decide
    whether the phone question was ever put to the caller. A substitution that
    misses it would leave the backstop firing forever.
    """
    ask = _phone_question_for(session).lower()
    assert any(m in ask for m in _PHONE_STEP_MARKERS)


def test_the_caller_id_form_speaks_the_digits():
    """
    Never "is that the number you're calling from?" — the caller has not heard
    what we hold, and a blind yes writes a stranger's number to the booking.
    The prompt is emphatic about this; the substitution must honour it.
    """
    ask = _phone_question_for({"twilio_from_local": "07760512084"})
    assert "0 7 7 6 0 5 1 2 0 8 4" in ask


def test_the_replacement_does_not_delete_itself():
    """
    The result is re-scanned to strip any SECOND CTA in the chunk. A
    replacement carrying the CTA vocabulary ("Before I book that in…") matches
    that pass and vanishes, leaving a readback with no question. It did.
    """
    for session in ({"twilio_from": ""}, {"twilio_from_local": "07760512084"}):
        assert not _BOOKING_CTA_SENTENCE_RE.search(_phone_question_for(session))


# ── one vocabulary, two modules ────────────────────────────────────────────

def test_the_cta_pattern_matches_the_write_gate_vocabulary():
    """
    _BOOKING_CTA_SENTENCE_RE must recognise exactly the phrasings
    llm_stream._booking_confirmation_asked accepts as "the CTA was asked".
    If the write gate learns a fourth phrasing, this gate must learn it too, or
    that phrasing becomes a way to ask for confirmation with no phone on record.

    Not imported: llm_stream imports turn_handler, so the dependency cannot run
    the other way. Pinned here instead.
    """
    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream._booking_confirmation_asked)
    for phrasing in ("shall i go ahead", "book that in", "put that request through"):
        assert phrasing in src, (
            f"{phrasing!r} left the write gate — check this pattern still matches it"
        )
        assert _BOOKING_CTA_SENTENCE_RE.search(f"So that's Tom — {phrasing} for you?"), (
            f"{phrasing!r} opens the write gate but is not held back by Gate 5g"
        )


def test_the_backstop_steer_uses_the_same_question():
    """
    The tool refusal quotes a phone question back to the model. It used to
    hardcode the calling-number offer, which has no answer when there is no
    caller ID. It must use the same helper the gate substitutes.
    """
    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    assert "_phone_question_for(session)" in src, (
        "the phone_confirmation_required steer no longer uses the shared "
        "question — it can drift back to offering a number that does not exist"
    )
