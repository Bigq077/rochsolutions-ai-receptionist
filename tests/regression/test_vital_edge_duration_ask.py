# tests/regression/test_vital_edge_duration_ask.py
"""
Vital Edge: Susie must ASK 60 vs 90 minutes for a Deep Tissue Massage before
offering times / booking.

After the provisional-availability fix (dropping the misleading slot `end`, so
the model stops refusing 90-minute requests), a 2026-07-24 16:10 test call
booked a Deep Tissue Massage without ever asking which length the caller
wanted. The 60/90 choice was never a coded step — only incidental model
behaviour — so it silently defaulted to 60 minutes (per
_book_appointment_provisional) and the wrong price could be quoted. Deep Tissue
is the ONLY Vital Edge service with a length choice (60 min £125 / 90 min £175).

Fix: a `duration_choice_note` in vital_edge prompt_facts, rendered by
_render_provisional_booking (the provisional-model booking-instructions block),
instructing Susie to ask 60 vs 90 with both prices BEFORE offering times and to
pass duration_minutes to book_appointment. Both the config field and the render
hook are asserted here.
"""

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import _render_provisional_booking


def test_duration_choice_note_present_in_config():
    # get_clinic() is the runtime entrypoint that flattens clinic.json into the
    # dict build_clinic_prompt receives (booking_system, prompt_facts, ...).
    clinic = get_clinic("vital_edge")
    note = (clinic.get("prompt_facts") or {}).get("duration_choice_note", "")
    assert note, "vital_edge must declare a duration_choice_note"
    # Must name both lengths, both prices, and the duration to pass through.
    for token in ("60", "90", "£125", "£175", "duration_minutes"):
        assert token in note, f"duration_choice_note missing {token!r}"


def test_duration_ask_renders_into_provisional_booking_section():
    clinic = get_clinic("vital_edge")
    assert clinic.get("booking_system") == "google_calendar_provisional"
    rendered = _render_provisional_booking(clinic, {"practitioner": "Jonathan"})
    assert "SESSION LENGTH:" in rendered, "duration instruction not rendered"
    # Behavioural core: ask before offering times, pass the chosen duration.
    assert "60" in rendered and "90" in rendered
    assert "duration_minutes" in rendered
    assert "before offering" in rendered.lower()


def test_duration_note_absent_for_non_provisional_clinic():
    """The render hook is gated on the provisional model — a non-provisional
    clinic (or one without the note) must not emit the SESSION LENGTH block."""
    clinic = {"booking_system": "acuity", "prompt_facts": {"duration_choice_note": "x"}}
    assert _render_provisional_booking(clinic, {"practitioner": "X"}) == ""
