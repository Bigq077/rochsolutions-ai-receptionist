"""
Tests for the canonical Theorem source-of-truth layer
(app/clinics/theorem/canonical.py).

These prove:
  1. The canonical object imports and exposes every required category/field.
  2. The canonical pricing table holds the confirmed values and no legacy ones.
  3. Practitioner schedule is internally consistent and matches the new rules.
  4. Booking / age / service routing flags are present and correct.
  5. Known conflicts (old duplicated values) are explicitly flagged, not hidden.
  6. The live clinic_config booking prices stay in sync with canonical.
"""
from __future__ import annotations

import pytest

from app.clinics.theorem import canonical as c


# ── 1. Import + required structure ───────────────────────────────────────────

def test_canonical_imports_and_is_dict():
    obj = c.get_canonical()
    assert isinstance(obj, dict) and obj, "CANONICAL must be a non-empty dict"
    assert obj["schema_version"] >= 1
    assert "Mark" in obj["source_of_truth"]


def test_all_required_categories_present():
    obj = c.get_canonical()
    for category in c.REQUIRED_CATEGORIES:
        assert category in obj, f"missing canonical category: {category}"
        assert obj[category], f"canonical category is empty: {category}"


def test_identity_fields():
    ident = c.IDENTITY
    assert ident["display_name"] == "Theorem Health and Wellness"
    assert ident["inbound_phone_e164"] == "+447380841468"
    assert ident["transfer_phone_e164"] == "+447870166861"
    assert ident["contact_email"] == "info@theoremhealth.co.uk"
    assert ident["company_number"] == "08116105"
    assert "theoremhealth.co.uk" in " ".join(ident["websites"])


def test_locations_present():
    locs = c.LOCATIONS
    assert set(locs) == {"alcester", "redditch"}
    assert "B49 6AD" in locs["alcester"]["address_full"]
    assert "B97 4RH" in locs["redditch"]["address_full"]
    # Alcester arrival/signage wording reflects Mark's latest edit.
    assert "Everyone Active sport centre and Theorem Health signage" in \
        locs["alcester"]["arrival_note"]


# ── 2. Canonical pricing table ────────────────────────────────────────────────

def test_confirmed_prices():
    assert c.get_price("physio_assessment") == 85.00
    assert c.get_price("physio_followup") == 85.00
    assert c.get_price("acupuncture") == 85.00
    assert c.get_price("psychotherapy") == 85.00
    assert c.get_price("remedial_rehab") == 65.00
    assert c.get_price("prescribing") == 12.50
    assert c.get_price("standalone_shockwave_laser") == 130.00
    assert c.get_price("wellness_massage") == 85.00
    assert c.PACKAGES["shockwave_laser_4"]["price_gbp"] == 468.00
    assert c.SURCHARGES["specialist_equipment"]["price_gbp"] == 45.00


def test_no_legacy_prices_anywhere_in_canonical():
    """Old conflicting prices must not appear in the canonical numeric values."""
    legacy = {75.0, 120.0, 420.0}
    numeric_prices = {v for v in c.PRICES.values() if isinstance(v, (int, float))}
    assert not (numeric_prices & legacy), (
        f"legacy price(s) leaked into canonical: {numeric_prices & legacy}"
    )


def test_enquiry_only_services_have_no_invented_price():
    for sid in ("reiki_energy_healing", "auricular_acupuncture", "hypnotherapy"):
        assert c.is_enquiry_only(sid), f"{sid} should be enquiry-only"
        assert c.get_price(sid) is None, f"{sid} must not have an invented price"


def test_new_vs_returning_no_price_difference():
    assert "£85" in c.NEW_VS_RETURNING_PRICE_DIFFERENCE
    assert "75" not in c.NEW_VS_RETURNING_PRICE_DIFFERENCE


# ── 3. Practitioner schedule (single canonical source) ────────────────────────

def test_mark_schedule():
    assert c.practitioner_days("mark", "alcester") == ["mon", "tue", "wed", "fri"]
    assert c.practitioner_days("mark", "redditch") == ["thu"]
    assert "injection therapy" in c.PRACTITIONERS["mark"]["role"]


def test_leanne_not_at_redditch_and_thursday_only():
    assert c.practitioner_days("leanne", "alcester") == ["thu"]
    assert c.practitioner_days("leanne", "redditch") == []


def test_practitioner_summary_matches_schedule():
    s = c.PRACTITIONER_SUMMARY
    assert "Mon/Tue/Wed/Fri" in s
    assert "Leanne is not available at Redditch" in s
    assert "Thu/Fri" not in s  # old Leanne pattern must be gone


# ── 4. Service routing / locations ────────────────────────────────────────────

def test_shockwave_laser_not_default_booking_route():
    for sid in ("shockwave_therapy", "class_iv_laser"):
        assert c.is_decided_in_session(sid)
        assert c.SERVICES[sid].get("default_booking_route") is False


def test_psychotherapy_and_massage_alcester_only():
    assert c.SERVICES["psychotherapy"]["locations"] == ["alcester"]
    assert c.SERVICES["wellness_massage"]["locations"] == ["alcester"]


def test_assessment_is_default_route():
    assert c.SERVICES["physio_assessment"].get("default_booking_route") is True


# ── 5. Booking, insurance, escalation, sms, stt ──────────────────────────────

def test_same_day_not_allowed_and_24h_notice():
    bp = c.BOOKING_POLICIES
    assert bp["same_day_allowed"] is False
    assert bp["minimum_notice_hours"] == 24
    assert bp["earliest_bookable"] == "tomorrow"
    assert bp["booking_window_days"] == 180
    assert bp["no_waitlist"] is True


def test_insurance_self_pay_no_bupa():
    ins = c.INSURANCE
    assert ins["model"] == "self-pay"
    assert "Bupa" in ins["not_accepted"]
    assert ins["gp_referral_required"] is False


def test_escalation_rules():
    esc = c.ESCALATION
    assert esc["transfer_phone_e164"] == "+447870166861"
    assert esc["complaints_handler"] == "Mark"
    assert len(esc["transfer_only_when"]) == 3


def test_sms_rules():
    sms = c.SMS
    assert sms["phone_readback_digit_by_digit"] is True
    assert sms["sms_name"] == "Theorem Health and Wellness"


def test_stt_pronunciation():
    stt = c.STT_PRONUNCIATION
    assert stt["tts_say"]["Alcester"] == "Awlstuh"
    assert "alcester" in stt["alcester_spoken_variants"]
    assert "redditch" in stt["redditch_spoken_variants"]


def test_safety_boundaries():
    saf = c.SAFETY
    assert saf["informational_not_diagnostic"] is True
    assert "999" in saf["emergency_message"]


# ── 6. Age policy is flagged for human confirmation (not silently guessed) ────

def test_age_policy_value_and_confirmation_flag():
    ap = c.AGE_POLICY
    assert ap["min_patient_age"] == 7
    # Must be flagged for human confirmation given the historical 15+ conflict.
    assert ap["human_confirmation_required"] is True
    assert "paediatric" in ap["faq_answer"].lower()


def test_unconfirmed_facts_lists_age_policy():
    assert "age_policy.min_patient_age" in c.unconfirmed_facts()


# ── 7. Known conflicts are tracked, not hidden ────────────────────────────────

def test_known_conflicts_registered():
    kc = c.KNOWN_CONFLICTS
    # The cancellation-fee disagreement must remain documented (now resolved).
    assert "cancellation_fee_pct" in kc
    assert kc["cancellation_fee_pct"]["canonical"] == 100
    assert kc["cancellation_fee_pct"]["conflicting_value"] == 75
    # Reconciled on 2026-06-29 — clinic_config now matches canonical.
    assert kc["cancellation_fee_pct"]["resolved"] is True


def test_clinic_config_cancellation_fee_matches_canonical():
    """Cancellation fee is reconciled to 100% across canonical and clinic_config."""
    from app.clinic_config import get_clinic
    clinic = get_clinic("theorem")
    assert clinic.get("cancellation_fee_pct") == c.BOOKING_POLICIES["late_cancellation_fee_pct"]
    assert clinic.get("cancellation_fee_pct") == 100


# ── 8. Low-risk wiring: live config prices derive from canonical ──────────────

def test_clinic_config_booking_prices_in_sync_with_canonical():
    from app.clinic_config import THEOREM_APPOINTMENT_TYPES
    expected = {
        "physio_assessment": c.get_price("physio_assessment"),
        "physio_followup":   c.get_price("physio_followup"),
        "remedial_rehab":    c.get_price("remedial_rehab"),
        "rehab_pt":          c.get_price("remedial_rehab"),
        "prescribing":       c.get_price("prescribing"),
        "acupuncture":       c.get_price("acupuncture"),
        "psychotherapy":     c.get_price("psychotherapy"),
    }
    for apt_id, price in expected.items():
        assert THEOREM_APPOINTMENT_TYPES[apt_id]["price_gbp"] == price, (
            f"{apt_id} price drifted from canonical"
        )
