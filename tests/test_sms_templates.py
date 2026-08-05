# tests/test_sms_templates.py
"""
Tests for app/sms_templates.py — build_sms() and build_maps_link().

Coverage:
  1. New patient includes first-visit arrival note
  2. Returning patient omits first-visit arrival note
  3. Slot label is split correctly (day / time)
  4. Patient first name only (not full name)
  5. Missing optional fields don't raise
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helper — minimal session dict
# ---------------------------------------------------------------------------

def _session(
    name: str = "Jane Smith",
    patient_type: str = "NEW",
    slot_label: str = "Friday 18 July at 2:30pm",
) -> dict:
    return {
        "collected": {
            "name": name,
            "patient_type": patient_type,
        },
        "selected_slot_label": slot_label,
    }


# ---------------------------------------------------------------------------
# 1. New patient → arrival note present
# ---------------------------------------------------------------------------

def test_new_patient_includes_first_visit_note(monkeypatch):
    """The wording is gone (2026-08-05) but the NEW branch still selects the
    note slot — see the _SENTINEL comment further down."""
    from app.sms_templates import build_sms
    monkeypatch.setattr("app.sms_templates.FIRST_VISIT_NOTE", "[[FIRST-VISIT-NOTE]]")
    body = build_sms(_session(patient_type="NEW"))
    assert "[[FIRST-VISIT-NOTE]]" in body


# ---------------------------------------------------------------------------
# 2. Returning patient → no arrival note
# ---------------------------------------------------------------------------

def test_returning_patient_omits_first_visit_note():
    from app.sms_templates import build_sms
    body = build_sms(_session(patient_type="RETURNING"))
    assert "5 mins early" not in body
    assert "[[FIRST-VISIT-NOTE]]" not in body


# ---------------------------------------------------------------------------
# 3. Slot label split — day and time appear separately
# ---------------------------------------------------------------------------

def test_slot_label_split_day_and_time():
    from app.sms_templates import build_sms
    body = build_sms(_session(slot_label="Tuesday 22 July at 10:00am"))
    assert "Tuesday 22 July" in body
    assert "10:00am" in body


# ---------------------------------------------------------------------------
# 4. Only first name used
# ---------------------------------------------------------------------------

def test_only_first_name_in_body():
    from app.sms_templates import build_sms
    body = build_sms(_session(name="Jane Smith"))
    assert "Jane" in body
    assert "Smith" not in body


# ---------------------------------------------------------------------------
# 5. Missing optional fields don't raise
# ---------------------------------------------------------------------------

def test_missing_fields_do_not_raise():
    from app.sms_templates import build_sms
    # Completely empty session
    body = build_sms({})
    assert isinstance(body, str)
    assert len(body) > 0


def test_missing_slot_label_does_not_raise():
    from app.sms_templates import build_sms
    session = {"collected": {"name": "Alice", "patient_type": "NEW"}}
    body = build_sms(session)
    assert "Alice" in body


def test_missing_name_falls_back_to_there():
    from app.sms_templates import build_sms
    session = {"collected": {}, "selected_slot_label": "Monday 7 July at 9:00am"}
    body = build_sms(session)
    assert "there" in body


# ---------------------------------------------------------------------------
# build_maps_link helper
# ---------------------------------------------------------------------------

def test_build_maps_link_encodes_address():
    from app.sms_templates import build_maps_link
    link = build_maps_link("123 High Street, Alcester B49 5AD")
    assert link.startswith("https://maps.google.com/?q=")
    assert "123" in link


def test_build_maps_link_empty_returns_empty():
    from app.sms_templates import build_maps_link
    assert build_maps_link("") == ""
    assert build_maps_link(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Call-3 P3 (2026-07-19): new-vs-returning derivation + spelling-confirm scope
# ---------------------------------------------------------------------------
# The media-streams template flow never writes collected.patient_type, so every
# caller used to default to the new-patient flavour. build_sms now falls back
# to session["new_or_returning"], then infers from the booked service id
# (treatment/follow-up => returning), and only then defaults new. The
# spelling-confirm safety line is decoupled from first_visit: any template-
# clinic caller whose full name was captured gets it (a returning caller's
# surname is just as vulnerable to a silent STT homophone).

# The arrival note itself was removed from the message on 2026-08-05 at the
# owner's instruction, so "5 mins early" is no longer in any body. The
# new-vs-returning inference it used to reveal is still live and still worth
# guarding, so these patch a sentinel into the note slot and assert on that
# instead of on wording that a future copy edit can delete again.
_SENTINEL = "[[FIRST-VISIT-NOTE]]"


def test_booked_treatment_session_infers_returning(monkeypatch):
    from app.sms_templates import build_sms
    monkeypatch.setattr("app.sms_templates.FIRST_VISIT_NOTE", _SENTINEL)
    session = {
        "collected": {"name": "Quinton Rock"},          # no patient_type
        "_booked_service": "msk_treatment_session",
        "selected_slot_label": "Thursday 23 July at 5:40pm",
    }
    body = build_sms(session)
    assert _SENTINEL not in body, "returning follow-up got the first-visit note"


def test_booked_initial_assessment_stays_new(monkeypatch):
    from app.sms_templates import build_sms
    monkeypatch.setattr("app.sms_templates.FIRST_VISIT_NOTE", _SENTINEL)
    session = {
        "collected": {"name": "Jane Smith"},
        "_booked_service": "msk_initial_assessment",
        "selected_slot_label": "Monday 20 July at 4:30pm",
    }
    assert _SENTINEL in build_sms(session)


def test_new_or_returning_session_key_respected(monkeypatch):
    from app.sms_templates import build_sms
    monkeypatch.setattr("app.sms_templates.FIRST_VISIT_NOTE", _SENTINEL)
    session = {
        "collected": {"name": "Jane Smith"},
        "new_or_returning": "returning",
        "selected_slot_label": "Monday 20 July at 4:30pm",
    }
    assert _SENTINEL not in build_sms(session)


def test_no_signal_defaults_to_new(monkeypatch):
    from app.sms_templates import build_sms
    monkeypatch.setattr("app.sms_templates.FIRST_VISIT_NOTE", _SENTINEL)
    body = build_sms({"collected": {"name": "Jane Smith"}})
    assert _SENTINEL in body


def test_returning_plan_with_full_name_does_not_raise():
    # Latent NameError: the returning_plan branch never defined first_visit,
    # which the spelling-confirm elif then read. Fixed alongside the P3.
    from app.sms_templates import build_sms
    body = build_sms({
        "returning_plan": True,
        "collected": {"name": "Jane Smith"},
        "clinic_id": "jv_v1",
    })
    assert isinstance(body, str) and len(body) > 0


def test_spelling_confirm_fires_for_returning_template_caller():
    from app.sms_templates import build_sms
    session = {
        "clinic_id": "jv_v1",                            # template clinic
        "collected": {"name": "Quinton Rock"},           # full name captured
        "_booked_service": "msk_treatment_session",      # returning
        "selected_slot_label": "Thursday 23 July at 5:40pm",
    }
    body = build_sms(session)
    assert "spelled differently" in body, (
        "returning template-clinic caller lost the spelling-confirm safety line"
    )
    assert "5 mins early" not in body                    # note still returning


def test_spelling_confirm_absent_for_non_template_clinic():
    from app.sms_templates import build_sms
    body = build_sms({
        "collected": {"name": "Jane Smith", "patient_type": "RETURNING"},
    })
    assert "spelled differently" not in body
