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
    _name_known,
    _next_booking_question_for,
    _phone_question_for,
    sanitise_response,
)
from app.prompts.clinic_template_prompt import _PHONE_STEP_MARKERS


READBACK = (
    "So that's Mark Da'ya, Monday the 10th of August at five in the evening. "
    "Shall I go ahead and book that in?"
)


# ── name before phone ──────────────────────────────────────────────────────
#
# The first version of this gate knew only about the phone. On CA36eb3f
# (2026-08-07) BOTH the name and the phone were missing when the model reached
# for the CTA; the gate substituted the phone question, the caller typed his
# number, and only then was he asked his name — one question before the
# booking, after he had already given the harder answer. The prompt orders
# these (name step 7, phone step 8, readback step 9); the gate inverted them.

def test_the_name_is_asked_before_the_phone():
    """The reproduction: nothing collected yet, so the NAME comes first."""
    out = sanitise_response(READBACK, {"booking_flow_active": True, "twilio_from": ""})
    assert "first name and surname" in out
    assert "keypad" not in out.lower()


def test_the_phone_follows_once_the_name_is_in():
    out = sanitise_response(
        READBACK,
        {"booking_flow_active": True, "twilio_from": "", "patient_name": "Quentin Rook"},
    )
    assert "keypad" in out.lower()
    assert "first name and surname" not in out


@pytest.mark.parametrize("session,expected", [
    ({}, False),
    ({"patient_name": "Quentin Rook"}, True),
    ({"collected": {"full_name": "Quentin Rook"}}, True),
    ({"collected": {"name": "Quentin"}}, True),
    ({"patient_name": "   "}, False),          # whitespace is not a name
    ({"collected": {"full_name": ""}}, False),
])
def test_name_known_reads_every_path_that_writes_it(session, expected):
    """
    Three keys because three paths write it: _v3_try_persist_name sets the
    top-level patient_name, collect_and_store writes into collected under
    either spelling. Missing one would re-ask a name we already have.
    """
    assert _name_known(session) is expected


def test_the_name_question_does_not_delete_itself():
    """Same trap as the phone question — it is re-scanned for a second CTA."""
    assert not _BOOKING_CTA_SENTENCE_RE.search(_next_booking_question_for({}))


def test_the_gate_fires_when_only_the_name_is_missing():
    """
    phone_confirmed alone is not enough to let the CTA through — the booking
    needs both, so a confirmed phone with no name must still be held back.
    """
    out = sanitise_response(
        READBACK, {"booking_flow_active": True, "phone_confirmed": True}
    )
    assert "shall i go ahead" not in out.lower()
    assert "first name and surname" in out


# ── the gate itself ────────────────────────────────────────────────────────

def test_the_cta_is_replaced_when_no_phone_is_confirmed():
    """
    The reproduction, with the caller-ID suppressed as it was on the call.
    Name present so this isolates the PHONE arm — the name arm is covered
    above.
    """
    out = sanitise_response(
        READBACK,
        {"booking_flow_active": True, "twilio_from": "", "patient_name": "Mark Da'ya"},
    )
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

def test_the_cta_passes_through_once_name_and_phone_are_both_in():
    """Both are required — the booking cannot be written without either."""
    session = {
        "booking_flow_active": True,
        "phone_confirmed": True,
        "patient_name": "Mark Da'ya",
    }
    assert sanitise_response(READBACK, session) == READBACK


def test_outside_a_booking_flow_passes_through_untouched():
    assert sanitise_response(READBACK, {}) == READBACK


@pytest.mark.parametrize("cta", [
    "So that is Tuesday the 12th at ten. Shall I go ahead and move it for you?",
    "Shall I go ahead and cancel that for you?",
    "Shall I go ahead and reschedule that?",
    "Shall I go ahead and put you through to Mark?",
])
def test_reschedule_and_cancel_ctas_are_untouched(cta):
    """
    "shall i go ahead" is the shared opener for ALL THREE write families. The
    first version of this gate matched it bare and replaced a reschedule
    confirmation with a request for a phone number — a working flow broken by a
    fix to a different one. Every alternative in the pattern must carry a
    BOOKING verb.
    """
    session = {"booking_flow_active": True, "twilio_from": ""}
    assert sanitise_response(cta, session) == cta


def test_the_pattern_never_matches_the_bare_opener():
    """Structural: the guard above is only as good as this staying true."""
    assert not _BOOKING_CTA_SENTENCE_RE.search("Shall I go ahead?")
    assert not _BOOKING_CTA_SENTENCE_RE.search("shall i go ahead and move it")


def test_an_early_booking_offer_is_not_a_write_cta():
    """
    "Would you like to book?" early in a call is a legitimate offer and must
    not be rewritten into a phone question. Only the WRITE confirmation waits
    for the number.
    """
    offer = "We do physiotherapy and shockwave. Would you like to book an appointment?"
    assert not _BOOKING_CTA_SENTENCE_RE.search(offer)


# ── the substituted question must work downstream ──────────────────────────

def test_the_keypad_form_explains_why_and_arms_the_keypad():
    """
    CA86dfad89 (A9a, 2026-08-16): jumping straight to "type on your keypad"
    with no reason confused the caller. The withheld form must say why first,
    and still arm DTMF capture.
    """
    ask = _phone_question_for({"twilio_from": ""})
    assert "can't see a phone number" in ask.lower()
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

    # Booking-specific phrasings: the write gate accepts them AND this gate
    # holds them back.
    for phrasing in ("book that in", "put that request through"):
        assert phrasing in src, (
            f"{phrasing!r} left the write gate — check this pattern still matches it"
        )
        assert _BOOKING_CTA_SENTENCE_RE.search(f"So that's Tom — {phrasing} for you?"), (
            f"{phrasing!r} opens the write gate but is not held back by Gate 5g"
        )

    # The bare opener is a DELIBERATE gap, not an oversight. The write gate can
    # afford it because it is only consulted for book_appointment, so the tool
    # name disambiguates. This gate has no such context — it sees a sentence —
    # and matching the bare opener would eat reschedule and cancel
    # confirmations, which share it.
    #
    # The gap is safe: a booking CTA phrased as a bare "shall I go ahead?" is
    # not held back here, but book_appointment's phone backstop still refuses
    # the write while phone_confirmed is unset. Worst case is the behaviour
    # that existed before this gate, not a booking on an unknown number.
    assert "shall i go ahead" in src
    assert not _BOOKING_CTA_SENTENCE_RE.search("So that's Tom — shall i go ahead for you?")


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
