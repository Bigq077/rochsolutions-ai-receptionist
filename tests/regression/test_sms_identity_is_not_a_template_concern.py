"""
A clinic's name and phone number used to be resolved only inside the
`prompt_engine == "template_v1"` branch of build_sms(). Any clinic running a
different prompt builder fell through to the env defaults and sent:

    "Your appointment at the clinic is confirmed... call us on ."

while its own config held the real name and number the whole time. A patient
was told to ring a number that was not there.

Identity is not a template-engine concern. These tests pin the generic
behaviour, deliberately WITHOUT naming a real clinic_id — a test pinned to a
clinic measures that branch's clinic.json, which is how the Theorem version of
this test fails the moment it is ported anywhere else.
"""

import pytest

import app.sms_templates as st


def _session():
    return {
        "clinic_id": "any_clinic",
        "collected": {"name": "Quentin", "patient_type": "RETURNING"},
        "selected_slot_speech": "Tuesday at ten",
    }


def _build_with_clinic(monkeypatch, clinic: dict) -> str:
    """Render an SMS for a synthetic clinic config."""
    import app.clinic_config as cc
    monkeypatch.setattr(cc, "get_clinic", lambda *a, **k: clinic, raising=False)
    return st.build_sms(_session())


def test_a_non_template_clinic_still_gets_its_own_name_and_number(monkeypatch):
    """The bug: identity gated behind prompt_engine == 'template_v1'."""
    body = _build_with_clinic(monkeypatch, {
        "prompt_engine": "some_other_v9",       # NOT template_v1
        "clinic_name": "Pennine Physio",
        "phone": "01204 555111",
    })
    assert "Pennine Physio" in body, (
        "a clinic that is not a template clinic lost its own name and fell "
        f"through to the env default; got: {body!r}"
    )
    assert "01204 555111" in body, (
        "the caller was given no number to ring — the symptom was a trailing "
        f"'call us on .'; got: {body!r}"
    )


def test_sms_name_is_preferred_over_clinic_name(monkeypatch):
    body = _build_with_clinic(monkeypatch, {
        "prompt_engine": "some_other_v9",
        "sms_name": "Pennine",
        "clinic_name": "Pennine Physiotherapy Limited",
        "phone": "01204 555111",
    })
    assert "Pennine" in body
    assert "Pennine Physiotherapy Limited" not in body


def test_sms_phone_wins_over_phone(monkeypatch):
    """
    The number a patient should ring off the back of a text is the line the
    text came FROM, so "call us" and "reply to this message" reach the same
    place. `sms_phone` is per-clinic config; the fallback expression is generic.
    """
    body = _build_with_clinic(monkeypatch, {
        "prompt_engine": "some_other_v9",
        "clinic_name": "Pennine Physio",
        "sms_phone": "07700 900123",
        "phone": "01204 555111",
    })
    assert "07700 900123" in body
    assert "01204 555111" not in body, (
        "the switchboard number was used where the clinic set an SMS line"
    )


def test_phone_is_used_when_the_clinic_sets_no_sms_phone(monkeypatch):
    """Clinics that don't distinguish the two must be unaffected."""
    body = _build_with_clinic(monkeypatch, {
        "prompt_engine": "some_other_v9",
        "clinic_name": "Pennine Physio",
        "phone": "01204 555111",
    })
    assert "01204 555111" in body
