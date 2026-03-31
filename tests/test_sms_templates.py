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

def test_new_patient_includes_first_visit_note():
    from app.sms_templates import build_sms
    body = build_sms(_session(patient_type="NEW"))
    assert "5 mins early" in body


# ---------------------------------------------------------------------------
# 2. Returning patient → no arrival note
# ---------------------------------------------------------------------------

def test_returning_patient_omits_first_visit_note():
    from app.sms_templates import build_sms
    body = build_sms(_session(patient_type="RETURNING"))
    assert "5 mins early" not in body


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
