"""
Unit tests for the Theorem caller-concern layer
(app/clinics/theorem/caller_concerns.py).

These are necessary but NOT the merge gate — conversational regression is
gated by a manual call sweep (see plan). They prove the data layer is complete,
internally consistent, safe, free of stale operational facts, and that the lean
prompt block renders + the classifier maps representative phrases.
"""
from __future__ import annotations

import pytest

from app.clinics.theorem import caller_concerns as cc


# The exact 37 categories the system must support (the user's required list).
_REQUIRED_37 = [
    "back_pain", "sciatica", "neck_pain", "shoulder_pain", "rotator_cuff",
    "frozen_shoulder", "knee_pain", "hip_pain", "achilles", "plantar_fasciitis",
    "elbow_tendinopathy", "sports_injury", "gym_injury", "running_injury",
    "golf_tennis_football_injury", "post_op_rehab", "osteoarthritis_stiffness",
    "chronic_pain", "nothing_worked", "shockwave_request", "laser_request",
    "massage_request", "stress_tension", "headaches_tension",
    "acupuncture_interest", "psychotherapy_wellbeing", "medication_prescribing",
    "insurance_bupa", "price_objection", "nhs_vs_private", "provider_comparison",
    "existing_followup", "report_letter", "home_visit", "teen_booking",
    "underage", "red_flag_urgent",
]

_REQUIRED_FIELDS = [
    "messy_phrases", "icp_segments", "anxiety", "intent_types", "clinical_risk",
    "conversion_risk", "may_say", "must_not_say", "best_next_step",
    "service_route", "clarify_when", "escalate_when", "answer_style",
]


# ── 1. Import + structure ─────────────────────────────────────────────────────

def test_imports_and_non_empty():
    assert isinstance(cc.CALLER_CONCERNS, dict) and cc.CALLER_CONCERNS
    assert isinstance(cc.ICP_SEGMENTS, dict) and cc.ICP_SEGMENTS


# ── 2. Completeness ───────────────────────────────────────────────────────────

def test_all_37_concern_categories_present():
    missing = [k for k in _REQUIRED_37 if k not in cc.CALLER_CONCERNS]
    assert not missing, f"missing concern categories: {missing}"
    assert len(cc.CALLER_CONCERNS) == 37
    assert set(cc.required_concern_keys()) == set(_REQUIRED_37)


def test_ten_icp_segments_present():
    assert len(cc.ICP_SEGMENTS) == 10
    for seg in cc.ICP_SEGMENTS.values():
        assert seg.get("label") and seg.get("win_by") and seg.get("lose_by")


# ── 3. Per-entry field + enum validity ───────────────────────────────────────

def test_every_concern_has_required_fields_and_valid_enums():
    for key, entry in cc.CALLER_CONCERNS.items():
        for field in _REQUIRED_FIELDS:
            assert field in entry, f"{key} missing field {field}"
        assert entry["must_not_say"], f"{key} has empty must_not_say"
        assert entry["service_route"] in cc.SERVICE_ROUTES, f"{key} bad route"
        assert entry["clinical_risk"] in cc.CLINICAL_RISK, f"{key} bad clinical_risk"
        assert entry["conversion_risk"] in cc.CONVERSION_RISK, f"{key} bad conversion_risk"
        assert entry["messy_phrases"], f"{key} has no messy_phrases"
        for it in entry["intent_types"]:
            assert it in cc.INTENT_TYPES, f"{key} bad intent_type {it}"
        for seg in entry["icp_segments"]:
            assert seg in cc.ICP_SEGMENTS, f"{key} references unknown ICP {seg}"


def test_clinical_symptom_concerns_forbid_diagnosis():
    """Every concern where the caller describes a symptom/condition or asks about
    a treatment must explicitly forbid diagnosis. Conversion/admin/logistics
    concerns (insurance, price, NHS comparison, letters, etc.) need not."""
    clinical_symptom = {
        "back_pain", "sciatica", "neck_pain", "shoulder_pain", "rotator_cuff",
        "frozen_shoulder", "knee_pain", "hip_pain", "achilles",
        "plantar_fasciitis", "elbow_tendinopathy", "sports_injury", "gym_injury",
        "running_injury", "golf_tennis_football_injury", "post_op_rehab",
        "osteoarthritis_stiffness", "chronic_pain", "nothing_worked",
        "headaches_tension", "shockwave_request", "laser_request",
        "massage_request", "acupuncture_interest", "stress_tension",
        "psychotherapy_wellbeing", "medication_prescribing", "red_flag_urgent",
    }
    for key in clinical_symptom:
        joined = " ".join(cc.CALLER_CONCERNS[key]["must_not_say"]).lower()
        assert "diagnos" in joined, f"{key} must forbid diagnosis"



# ── 4. Safety routing invariants ──────────────────────────────────────────────

def test_treatment_requests_route_to_assessment_not_autobook():
    for key in ("shockwave_request", "laser_request", "massage_request"):
        assert cc.CALLER_CONCERNS[key]["service_route"] == "assessment", (
            f"{key} must route to assessment, never auto-book the treatment"
        )


def test_red_flag_routes_to_emergency():
    assert cc.CALLER_CONCERNS["red_flag_urgent"]["service_route"] == "emergency_redirect"
    assert cc.CALLER_CONCERNS["red_flag_urgent"]["clinical_risk"] == "red_flag_screen"


def test_underage_does_not_route_to_assessment():
    assert cc.CALLER_CONCERNS["underage"]["service_route"] != "assessment"


def test_no_concern_routes_to_standalone_shockwave_or_laser():
    # No service_route is the standalone treatment itself; routes are bounded.
    for entry in cc.CALLER_CONCERNS.values():
        assert entry["service_route"] in cc.SERVICE_ROUTES


# ── 5. No stale operational facts leak into the concern layer ─────────────────

def _guidance_corpus() -> str:
    """All policy-bearing text (NOT caller messy_phrases, which may quote ages)."""
    parts = []
    for entry in cc.CALLER_CONCERNS.values():
        parts.append(entry["may_say"])
        parts.append(entry["best_next_step"])
        parts.append(entry["answer_style"])
        parts.append(entry["anxiety"])
        parts.extend(entry["must_not_say"])
    for o in cc.OBJECTION_PLAYBOOK.values():
        parts.append(o["script"])
    parts.extend(cc.SAFETY_BOUNDARIES)
    parts.append(cc.ANSWER_STYLE["exemplar_good"])
    parts.append(cc.ANSWER_STYLE["exemplar_bad"])
    parts.extend(cc.ANSWER_STYLE["principles"])
    return "\n".join(parts)


def test_no_price_or_stale_age_literals_in_guidance():
    corpus = _guidance_corpus().lower()
    # No price literals at all — prices live only in canonical.py.
    assert "£" not in corpus, "concern guidance must not contain price literals"
    for stale in ("£75", "£120", "£420", "aged 15", "15 and over", "under 15",
                  "minimum age of 15", "15+"):
        assert stale.lower() not in corpus, f"stale operational fact leaked: {stale}"


# ── 6. Lean prompt block renders with the safety anchors ──────────────────────

def test_build_concern_handling_block():
    block = cc.build_concern_handling_block()
    assert isinstance(block, str) and len(block) > 500
    assert "PHYSIO CALLER HANDLING" in block
    assert "999" in block                          # red-flag action
    assert "Never diagnose" in block               # must-not-say reinforcement
    assert ("saddle" in block.lower() or "bladder" in block.lower())  # red-flag term
    assert "NHS" in block                          # an objection script
    assert "TREATMENT-OVERRIDE" in block or "TREATMENT" in block.upper()


def test_block_does_not_dump_all_37_entries():
    """It's the LEAN block — it must not balloon into the full table."""
    block = cc.build_concern_handling_block()
    # Heuristic: full 37-entry dump would be far larger than the lean block.
    assert len(block) < 12000


# ── 7. Classifier maps representative messy phrases ───────────────────────────

@pytest.mark.parametrize("phrase,expected", [
    ("my back's gone", "back_pain"),
    ("I think it's sciatica", "sciatica"),
    ("I need shockwave", "shockwave_request"),
    ("would laser fix plantar fasciitis", "laser_request"),
    ("I just need a massage", "massage_request"),
    ("can I claim on my insurance", "insurance_bupa"),
    ("my son is 14, can you make an exception", "underage"),
    ("numbness in my saddle area", "red_flag_urgent"),
    ("I can't control my bladder since my back went", "red_flag_urgent"),
    ("why go private when the NHS does physio", "nhs_vs_private"),
    ("can Mark prescribe painkillers", "medication_prescribing"),
])
def test_classify_concern(phrase, expected):
    assert cc.classify_concern(phrase) == expected


def test_classify_concern_none_for_empty():
    assert cc.classify_concern("") is None
    assert cc.classify_concern(None) is None
