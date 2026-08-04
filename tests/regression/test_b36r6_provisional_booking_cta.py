# tests/regression/test_b36r6_provisional_booking_cta.py
"""
B-36 R6 — a provisional clinic could not book, and said it had.

Vital Edge acceptance run, 2026-08-04, calls 1 and 7 (`CA094dcb41`,
`CAb408ed32`), build `4f4803e`, live:

    book_appointment -> BLOCKED (confirmation_required)
    spoken: "I've noted your preferred time and sent it to Jonathan to confirm"
    outcome: abandoned; nothing on the calendar

Call 8 then confirmed it from the other side: `lookup_patient` found no
appointment, because there had never been one.

**Two independent failures, pointing at each other:**

1. `_booking_confirmation_asked` matched only "shall i go ahead" / "book that
   in" — the CONFIRMED-booking wording. A provisional clinic's prompt mandates
   "shall I put that request through to {prac} to confirm?" and BANS "book",
   so the gate could never open. The write was refused every time the model
   obeyed its own prompt.

2. Gate 5f could not strip the resulting phantom, because the provisional
   closing deliberately avoids "booked"/"confirmed" — precisely the tokens
   `_FALSE_CONFIRM_CLAIM_RE` keys on. The prompt rule that keeps the clinic
   honest is what blinded the guard.

Neither is caused by the 4 Aug re-cut. Both are present on the pre-re-cut engine
(`23b8dbe`: gate literals at `llm_stream.py:1846-1847`, provisional CTA at
`clinic_template_prompt.py:1395`), so rolling back would not have fixed it.

It failed INTERMITTENTLY, which is why it survived weeks of live calls: when the
model drifted to "shall I go ahead and put that through", literal one matched and
the booking landed. Obedience is what broke it.

**Same defect, same repair, as `_move_confirmation_asked`** (`CA23199d08`,
3 Aug): a single-phrasing gate against a sentence the model composes.

What this file pins:
  P1  the gate accepts the provisional CTA, and still accepts both confirmed ones
  P2  Gate 5f sees the provisional closing as a claim — but NOT the pre-write
      CTA, and NOT after a successful write
  P3  the re-steer does not promise a CONFIRMED booking to a provisional caller
  P1+P3 are coupled: the re-steer must satisfy the gate, or the caller's next
      "yes" is judged against a CTA that was never asked
  and that all of it is inert for the confirmed-booking clinics.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams.llm_stream import _booking_confirmation_asked
from app.media_streams.turn_handler import (
    WRITE_FAMILY_BOOKING,
    WRITE_FAMILY_CANCEL,
    WRITE_FAMILY_RESCHEDULE,
    _armed_write_families,
    _clinic_is_provisional,
    _false_write_claim,
    _FALSE_CONFIRM_RESTEER,
    _FALSE_CONFIRM_RESTEER_PROVISIONAL,
    _resteer_for,
)

PROVISIONAL_CLINIC = "vital_edge"
CONFIRMED_CLINICS = ["jv_v1"]

# Verbatim from the live calls / the prompt default.
PENDING_CLOSING = (
    "I've noted your preferred time and sent it to Jonathan to confirm — "
    "your booking isn't finalised until you hear from him."
)
PROVISIONAL_CTA = (
    "So that's Sarah Whitfield, Friday the 8th of August at eleven in the "
    "morning — shall I put that request through to Jonathan to confirm?"
)


# ── P1 — the gate can see the CTA the prompt mandates ──────────────────────

def test_the_provisional_cta_opens_the_booking_gate():
    """The whole defect in one assertion."""
    assert _booking_confirmation_asked(PROVISIONAL_CTA) is True, (
        "book_appointment is still refused on the sentence Vital Edge's prompt "
        "requires — this is CA094dcb41 and CAb408ed32 unfixed."
    )


@pytest.mark.parametrize("cta", [
    "shall I go ahead and book that in?",
    "Shall I book that in for you?",
    "So that's Friday at eleven — shall I go ahead and book that in?",
])
def test_the_confirmed_ctas_still_open_it(cta):
    """Widening must ADD. jv_v1 and Theorem book through these."""
    assert _booking_confirmation_asked(cta) is True


@pytest.mark.parametrize("not_a_cta", [
    "What day works best for you?",
    "I'll put your details through to reception.",
    "Let me check what's available.",
    "",
])
def test_ordinary_speech_does_not_open_the_booking_gate(not_a_cta):
    """The dangerous direction: this predicate is FM-01's necessary half, and a
    false positive here removes the requirement that the question was asked."""
    assert _booking_confirmation_asked(not_a_cta) is False


# ── P2 — Gate 5f can see the provisional phantom ───────────────────────────

def test_the_provisional_closing_is_a_completion_claim():
    assert _false_write_claim(PENDING_CLOSING, WRITE_FAMILY_BOOKING) is True, (
        "the sentence spoken on CA094dcb41 with no write behind it is still "
        "invisible to Gate 5f"
    )


def test_the_pre_write_cta_is_not_a_completion_claim():
    """The CTA and the closing share vocabulary. Stripping the CTA would remove
    the question the booking gate needs to have been asked — a deadlock."""
    assert _false_write_claim(PROVISIONAL_CTA, WRITE_FAMILY_BOOKING) is False


def test_the_confirmed_closing_is_still_a_claim():
    assert _false_write_claim(
        "All booked — you're in for Friday the 8th at eleven.",
        WRITE_FAMILY_BOOKING,
    ) is True


@pytest.mark.parametrize("legitimate", [
    "I've sent you a text with the details.",
    "Your request is important to us.",
    "I've noted that down.",
    "Jonathan will confirm that with you directly.",
])
def test_the_new_pattern_does_not_swallow_ordinary_speech(legitimate):
    """This guard's documented failure mode is the OVER-fire — it abandoned a
    completed booking on 2026-06-12. An over-fire here deletes real speech."""
    assert _false_write_claim(legitimate, WRITE_FAMILY_BOOKING) is False


def test_a_successful_provisional_booking_is_never_seen_by_the_gate():
    """The over-fire that would matter most: stripping the REAL closing after a
    real write, leaving the caller told nothing.

    Safe because llm_stream sets booking_write_confirmed on ANY successful
    book_appointment, provisional included, which disarms the family.
    """
    session = {
        "clinic_id": PROVISIONAL_CLINIC,
        "booking_flow_active": True,
        "booking_write_confirmed": True,       # what a successful write sets
    }
    assert WRITE_FAMILY_BOOKING not in _armed_write_families(session), (
        "the booking family is armed after a SUCCESSFUL provisional write — "
        "Gate 5f would strip the legitimate closing"
    )


def test_the_refused_write_does_arm_the_family():
    """The CA094dcb41 shape: flow active, nothing written."""
    session = {
        "clinic_id": PROVISIONAL_CLINIC,
        "booking_flow_active": True,
        "booking_write_confirmed": False,
    }
    assert WRITE_FAMILY_BOOKING in _armed_write_families(session)


# ── P3 — the re-steer must not promise a confirmed booking ─────────────────

def test_the_provisional_resteer_is_used_for_the_provisional_clinic():
    assert _resteer_for(WRITE_FAMILY_BOOKING, {"clinic_id": PROVISIONAL_CLINIC}) == (
        _FALSE_CONFIRM_RESTEER_PROVISIONAL
    )


def test_the_provisional_resteer_never_promises_a_confirmed_booking():
    """VE's whole prompt exists to avoid this. Stripping a false claim and
    replacing it with a stronger one is a worse defect than the original."""
    low = _FALSE_CONFIRM_RESTEER_PROVISIONAL.lower()
    for banned in ("book that in", "all booked", "confirmed", "booked in"):
        assert banned not in low, (
            f"the provisional re-steer says {banned!r} — that is a promise "
            "Vital Edge cannot keep"
        )


@pytest.mark.parametrize("clinic_id", CONFIRMED_CLINICS)
def test_confirmed_clinics_keep_the_original_resteer(clinic_id):
    assert _resteer_for(WRITE_FAMILY_BOOKING, {"clinic_id": clinic_id}) == (
        _FALSE_CONFIRM_RESTEER
    )


def test_an_unresolvable_clinic_falls_back_to_the_confirmed_wording():
    """Fail-safe direction: the provisional wording understates a real booking
    and would not satisfy a confirmed clinic's gate."""
    assert _resteer_for(WRITE_FAMILY_BOOKING, {"clinic_id": "does-not-exist"}) == (
        _FALSE_CONFIRM_RESTEER
    )
    assert _resteer_for(WRITE_FAMILY_BOOKING, {}) == _FALSE_CONFIRM_RESTEER


@pytest.mark.parametrize("family", [WRITE_FAMILY_RESCHEDULE, WRITE_FAMILY_CANCEL])
def test_the_other_write_families_are_untouched(family):
    """B-36: never share a re-steer across families. The provisional split is
    the BOOKING family only."""
    assert _resteer_for(family, {"clinic_id": PROVISIONAL_CLINIC}) == (
        _resteer_for(family, {"clinic_id": "jv_v1"})
    )


# ── P1 + P3 are one mechanism and must move together ───────────────────────

def test_the_provisional_resteer_satisfies_the_booking_gate():
    """The re-steer becomes last_bot_prompt. If it does not satisfy the gate,
    the caller's next "yes" is judged against a CTA that was never asked and the
    write is refused a second time — the loop CAb408ed32 actually hit."""
    assert _booking_confirmation_asked(_FALSE_CONFIRM_RESTEER_PROVISIONAL) is True


def test_the_confirmed_resteer_still_satisfies_the_booking_gate():
    assert _booking_confirmation_asked(_FALSE_CONFIRM_RESTEER) is True


# ── end to end, through the gate the caller actually goes through ──────────
#
# The tests above call _resteer_for directly. That is not enough on its own:
# reverting the CALL SITE in sanitise_response leaves every one of them green
# while the provisional re-steer never reaches a caller. Measured — the first
# version of this file had exactly that hole. These go through the public entry
# point so the wiring is covered too.


def _refused_booking_session(clinic_id: str) -> dict:
    """A session in the CA094dcb41 state: booking refused, nothing written."""
    from app.media_streams.turn_handler import WRITE_REFUSED_KEY
    return {
        "clinic_id": clinic_id,
        "booking_flow_active": True,
        "booking_write_confirmed": False,
        WRITE_REFUSED_KEY: {WRITE_FAMILY_BOOKING: True},
    }


def test_end_to_end_the_provisional_phantom_is_replaced_not_spoken():
    """The live defect, through the public gate: the caller must not hear that
    the request was sent when no write happened."""
    from app.media_streams.turn_handler import sanitise_response
    out = sanitise_response(PENDING_CLOSING, _refused_booking_session(PROVISIONAL_CLINIC))
    assert "sent it to" not in out.lower(), (
        "the phantom reached the caller — this is CA094dcb41 verbatim"
    )
    assert out == _FALSE_CONFIRM_RESTEER_PROVISIONAL, (
        f"expected the provisional re-steer, got {out!r}"
    )


def test_end_to_end_the_provisional_caller_is_not_promised_a_confirmed_booking():
    """Pins the CALL SITE. If sanitise_response goes back to indexing
    _FAMILY_RESTEER directly, this fails and the direct tests do not."""
    from app.media_streams.turn_handler import sanitise_response
    out = sanitise_response(PENDING_CLOSING, _refused_booking_session(PROVISIONAL_CLINIC)).lower()
    for banned in ("book that in", "booked", "confirmed"):
        assert banned not in out, (
            f"Gate 5f replaced a provisional phantom with {banned!r} — a "
            "stronger promise than the sentence it removed"
        )


@pytest.mark.parametrize("clinic_id", CONFIRMED_CLINICS)
def test_end_to_end_confirmed_clinics_are_unchanged(clinic_id):
    """The two live confirmed clinics must see byte-identical behaviour."""
    from app.media_streams.turn_handler import sanitise_response
    out = sanitise_response(
        "All booked — you're in for Friday the 8th at eleven.",
        _refused_booking_session(clinic_id),
    )
    assert out == _FALSE_CONFIRM_RESTEER


def test_end_to_end_a_successful_provisional_closing_survives():
    """The over-fire that would matter most, through the public gate: after a
    real write the caller must still hear the real closing."""
    from app.media_streams.turn_handler import sanitise_response
    session = {
        "clinic_id": PROVISIONAL_CLINIC,
        "booking_flow_active": True,
        "booking_write_confirmed": True,
    }
    out = sanitise_response(PENDING_CLOSING, session)
    assert "sent it to Jonathan" in out, (
        "Gate 5f stripped a LEGITIMATE provisional closing after a successful "
        "write — the caller is told nothing at all. This guard has done exactly "
        "this before (2026-06-12, a completed booking abandoned)."
    )


# ── the switch, and the real prompt ────────────────────────────────────────

def test_provisional_is_read_from_the_booking_system_not_a_clinic_list():
    """Same switch that drives the provisional write path and prompt branch, so
    it cannot drift out of step with them."""
    assert _clinic_is_provisional({"clinic_id": PROVISIONAL_CLINIC}) is True
    for cid in CONFIRMED_CLINICS:
        assert _clinic_is_provisional({"clinic_id": cid}) is False
    assert get_clinic(PROVISIONAL_CLINIC)["booking_system"] == (
        "google_calendar_provisional"
    )


def test_the_real_rendered_prompt_still_matches_the_gate():
    """End to end, against the prompt Vital Edge actually runs.

    The unit tests above use the sentence as observed on the call. This asserts
    the PROMPT still teaches something the gate accepts — a reword of
    `readback_cta` is exactly how this defect was born, and it would otherwise
    re-open silently.
    """
    from app.prompts.clinic_template_prompt import build_clinic_prompt
    static, _ = build_clinic_prompt({}, get_clinic(PROVISIONAL_CLINIC))
    assert "put that request through" in static.lower(), (
        "Vital Edge's prompt no longer teaches the CTA the booking gate "
        "accepts — book_appointment will be refused on every call again."
    )
