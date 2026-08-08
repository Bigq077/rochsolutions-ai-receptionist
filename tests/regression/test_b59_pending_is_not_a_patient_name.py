"""
B-59 — a cancellation SMS greeted a live Vital Edge caller "Hi PENDING".

Provisional bookings are written to the calendar as
    "PENDING CONFIRMATION [— HOME VISIT] — <name> — <service> (<n> min)"
but the patient-name fallback (used whenever the event has no structured
"Patient:" description line — every event written before that line existed, and
anything created by hand) took the FIRST em-dash segment as the name. That
returned "PENDING CONFIRMATION", and the SMS greeting takes the first token, so
the caller was texted "Hi PENDING, your appointment on ... has been cancelled".

The same fallback feeds the reschedule writer, which would then have titled the
moved event "PENDING CONFIRMATION — PENDING CONFIRMATION — …", and the owner
notification, which would have named the cancelling caller "PENDING".

Two independent guarantees are pinned here:
  1. the parser resolves the real name out of a provisional title, and never
     returns a status word as a name;
  2. the SMS rendering boundary refuses a status word even if one reaches it.
"""

from datetime import datetime

from app.notifications.templates import format_cancellation_confirmation
from app.tools.receptionist_tools import (
    _gcal_event_patient_name,
    _gcal_event_service,
)


def _ev(summary: str, description: str = "") -> dict:
    return {"summary": summary, "description": description}


# ── 1. the parser ──────────────────────────────────────────────────────────

def test_provisional_title_yields_the_patient_not_the_status():
    ev = _ev("PENDING CONFIRMATION — Quentin Roch — Deep Tissue Massage (60 min)")
    assert _gcal_event_patient_name(ev) == "Quentin Roch"
    assert _gcal_event_service(ev) == "Deep Tissue Massage (60 min)"


def test_home_visit_marker_is_stripped_too():
    ev = _ev("PENDING CONFIRMATION — HOME VISIT — Quentin Roch — Sports Massage (90 min)")
    assert _gcal_event_patient_name(ev) == "Quentin Roch"


def test_a_status_only_title_yields_no_name():
    # Better to greet "there" than to greet the caller by a marker word.
    assert _gcal_event_patient_name(_ev("PENDING CONFIRMATION")) == ""


def test_structured_description_still_wins():
    ev = _ev(
        "PENDING CONFIRMATION — Quentin Roch — Deep Tissue Massage (60 min)",
        "PROVISIONAL booking requested via Susie (AI receptionist) — NOT yet confirmed.\n"
        "Patient: Quentin Roch\nPhone: +447700900123\nService: Deep Tissue Massage (60 min)",
    )
    assert _gcal_event_patient_name(ev) == "Quentin Roch"
    assert _gcal_event_service(ev) == "Deep Tissue Massage (60 min)"


def test_legacy_patient_first_title_is_unchanged():
    ev = _ev("Jane Smith — Sports Massage")
    assert _gcal_event_patient_name(ev) == "Jane Smith"
    assert _gcal_event_service(ev) == "Sports Massage"


def test_title_with_no_separator_is_unchanged():
    assert _gcal_event_patient_name(_ev("Jane Smith")) == "Jane Smith"
    assert _gcal_event_service(_ev("Jane Smith")) == ""


# ── 2. the rendering boundary ──────────────────────────────────────────────

def test_cancellation_sms_never_greets_a_status_word():
    when = datetime(2026, 8, 14, 10, 0)
    for junk in ("PENDING CONFIRMATION", "PENDING", "BOOKED", "Confirmed"):
        msg = format_cancellation_confirmation(
            junk, when, clinic_name="Vital Edge Therapy", clinic_phone="07426 779875",
        )
        assert msg.startswith("Hi there,"), msg


def test_cancellation_sms_still_greets_a_real_first_name():
    msg = format_cancellation_confirmation(
        "Quentin Roch", datetime(2026, 8, 14, 10, 0),
        clinic_name="Vital Edge Therapy", clinic_phone="07426 779875",
    )
    assert msg.startswith("Hi Quentin,")
