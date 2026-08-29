"""
Regression: ringing your own clinic lost your caller ID, twice over.

Live 2026-08-29, call CA7454c983a10dd3db7caee7dba3b06238 on the northgate demo
line, three hours after that clinic was given a `transfer_phone`.

`_is_clinic_own_number` reads `transfer_phone` first. The demo line's transfer
target is the owner's own mobile — and the owner is also who rings it to test.
So every one of their calls matched, the forwarded-call guard blanked
`twilio_from`, and two things followed:

  * Susie said "Thanks Quentin — I can't see a phone number on this call" and
    made them key in eleven digits. The router had logged
    `From=+447502211207`. The sentence was false and the DTMF ladder was
    fifteen wasted seconds on every demo call.

  * `session["twilio_from"]` is what `resolve_transfer_target` reads to refuse
    dialling a caller back to themselves. With it blanked that guard cannot
    fire — and worse than not firing, it then RETURNS the caller's own number,
    because the clinic has no `transfer_phone` of its own and the fallback is
    that same mobile. The fix of the night before was inert on exactly the call
    it was written for.

THE RULE
--------
Matching the clinic's own number was the whole test, and it cannot tell a
diverted call from the owner ringing their own line. Twilio's `ForwardedFrom`
can: it is set only when the call reached us via a diversion. It is now
required as positive evidence before a caller-ID is discarded.

WHAT THIS REOPENS, stated rather than hidden: a carrier that rewrites `From` to
the forwarding number and sends NO diversion header is indistinguishable from a
direct dial, and such a call now keeps a caller-ID that is the practitioner's —
the collision the guard exists to prevent. The call site logs at WARNING every
time that case could apply, so it surfaces rather than passing silently.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import (
    _is_clinic_own_number,
    _suppress_forwarded_caller_id,
)
from app.routes.realtime import resolve_transfer_target

OWNER = "+447502211207"          # the transfer target, and the test caller
CLINIC_LINE = "+447366263180"    # the Twilio number that was dialled
PATIENT = "+447700900999"


def _clinic(**over):
    """northgate as it stood when this fired: its transfer target is the owner."""
    clinic = {"transfer_phone": OWNER, "phone": CLINIC_LINE}
    clinic.update(over)
    return clinic


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
def test_the_owner_ringing_their_own_line_keeps_their_caller_id():
    """The live call. No diversion, so this is a direct dial, not a forward."""
    assert _suppress_forwarded_caller_id(OWNER, _clinic(), "") is False, (
        "the caller was told 'I can't see a phone number on this call' and "
        "made to key in eleven digits, on a call whose From was logged"
    )


def test_the_caller_id_that_survives_re_arms_the_self_dial_guard():
    """The money test, and the reason this is a P1 rather than an annoyance.

    `resolve_transfer_target` refuses to dial a caller back to themselves by
    comparing the target with `session["twilio_from"]`. Blank that and the
    refusal cannot happen — and what comes back is the caller's own number.
    """
    blanked = {"clinic_id": "northgate", "twilio_from": ""}
    kept = {"clinic_id": "northgate", "twilio_from": OWNER}

    assert resolve_transfer_target(kept) is None, (
        "the self-dial guard did not fire for a caller whose ID survived"
    )
    assert resolve_transfer_target(blanked) == OWNER, (
        "this is what the blanking used to produce — the caller's own number, "
        "handed back as the transfer target. Pinned so the coupling is not "
        "rediscovered by a third live call."
    )


# ---------------------------------------------------------------------------
# What the guard must still do
# ---------------------------------------------------------------------------
def test_a_genuinely_diverted_call_still_loses_the_artefact():
    """The case the guard exists for: the carrier rewrote From to the
    forwarding number AND told us so. Trusting it books every forwarded patient
    against the practitioner's number, and lookup_patient keys on phone — so
    patient B could read back and cancel patient A's appointment."""
    assert _suppress_forwarded_caller_id(OWNER, _clinic(), CLINIC_LINE) is True


def test_a_diverted_call_that_passed_the_patient_through_is_untouched():
    """The COMMON forwarding case. ForwardedFrom is present but From is the real
    caller, so the caller-ID is genuine and must be kept — a rule keyed on the
    diversion alone would throw away every forwarded patient's number."""
    assert _suppress_forwarded_caller_id(PATIENT, _clinic(), CLINIC_LINE) is False


@pytest.mark.parametrize("forwarded", ["", "   ", None])
def test_no_diversion_evidence_is_not_evidence(forwarded):
    """Absent, blank and whitespace all mean the same thing: nothing was said
    about a diversion. Only a real value counts."""
    assert _suppress_forwarded_caller_id(OWNER, _clinic(), forwarded) is False


def test_an_absent_caller_id_decides_nothing():
    assert _suppress_forwarded_caller_id("", _clinic(), CLINIC_LINE) is False


def test_every_clinic_owned_number_is_still_recognised():
    """The predicate underneath is unchanged — this commit narrows WHEN it is
    acted on, not what counts as one of the clinic's own numbers."""
    for key, block in (
        ("transfer_phone", {"transfer_phone": OWNER}),
        ("owner_alerts.phone", {"owner_alerts": {"phone": OWNER}}),
        ("call_overflow.dial_phone", {"call_overflow": {"dial_phone": OWNER}}),
        ("phone", {"phone": OWNER}),
    ):
        assert _is_clinic_own_number(OWNER, block), f"{key} no longer matches"
        assert _suppress_forwarded_caller_id(OWNER, block, CLINIC_LINE) is True
        assert _suppress_forwarded_caller_id(OWNER, block, "") is False


def test_the_number_is_matched_however_it_was_typed():
    """A clinic's own number is written by whoever onboarded them; the caller ID
    is always E.164."""
    assert _suppress_forwarded_caller_id(
        OWNER, {"transfer_phone": "07502 211207"}, CLINIC_LINE
    ) is True


# ---------------------------------------------------------------------------
# The demo line as it stands now
# ---------------------------------------------------------------------------
def test_northgate_carries_no_transfer_phone():
    """Reverted 2026-08-29 after the live call. The guard fix above is what
    makes setting it safe again; until someone decides the demo line needs a
    transfer target, it has none and the fallback answers.
    """
    from app.clinic_config import get_clinic

    assert not (get_clinic("northgate").get("transfer_phone") or "").strip(), (
        "northgate has a transfer_phone again — that is fine now, but check "
        "the ForwardedFrom guard is still in place before shipping it"
    )
