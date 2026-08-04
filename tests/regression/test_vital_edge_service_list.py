"""Vital Edge's service list and prices, as Jonathan gave them on 2026-08-04.

Source of truth is his WhatsApp message:

    1) 30mins neck back and shoulders £65
    2) sports massage (injury specific) or for athletes £125
    3) deep tissue massage (for muscle mobilisation) £125
    4) Coming soon ANF — Amino neural therapy

and, on the 60/90 question: "£125 60 mins ... £180 for 90 mins".

Three services were withdrawn in the same change — Stress Buster, Muscle /
Nerve Injury, Facial Release — and the 90-minute price moved £175 -> £180.

These tests exist because the service list is spread across EIGHT blocks of
clinic.json (services, prompt_facts, sms_price_line, faq, team_and_availability,
call_handling, treatment_guidance, stt_variants). Updating `services` alone
leaves Susie cheerfully offering a withdrawn treatment from the FAQ, or mapping
"stress buster" to a service_id that no longer exists.
"""
import json

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import build_clinic_prompt

WITHDRAWN_IDS = (
    "stress_buster_massage",
    "muscle_nerve_injury_massage",
    "facial_release_massage",
)

EXPECTED = {
    "neck_back_shoulders_massage": {"30min_in_clinic_gbp": 65},
    "sports_massage": {"60min_in_clinic_gbp": 125, "90min_in_clinic_gbp": 180},
    "deep_tissue_massage": {"60min_in_clinic_gbp": 125, "90min_in_clinic_gbp": 180},
}


def _clinic():
    return get_clinic("vital_edge")


def _rendered():
    prompt, _ = build_clinic_prompt({}, _clinic())
    return prompt


# ---------------------------------------------------------------------------
# The list itself
# ---------------------------------------------------------------------------
def test_exactly_three_bookable_services_at_the_agreed_prices():
    services = {s["service_id"]: s for s in _clinic()["services"]}
    assert set(services) == set(EXPECTED), (
        "Vital Edge's bookable service list drifted from Jonathan's 2026-08-04 list"
    )
    for sid, pricing in EXPECTED.items():
        assert services[sid]["pricing"] == pricing, f"{sid} price changed"


def test_no_withdrawn_service_is_bookable_or_mappable():
    clinic = _clinic()
    ids = {s["service_id"] for s in clinic["services"]}
    aliases = (clinic.get("stt_variants") or {}).get("services") or {}
    for dead in WITHDRAWN_IDS:
        assert dead not in ids, f"{dead} is still bookable"
        # The alias map is the subtler one: it renders as
        # "'stress buster' -> stress_buster_massage" in the prompt, which tells
        # Susie to book an ID that no longer exists.
        assert dead not in aliases, f"{dead} still has a spoken-phrase mapping"


def test_the_withdrawn_price_is_gone_everywhere():
    """£175 was the 90-minute price in FOUR places. Missing one under-quotes."""
    raw = json.dumps(_clinic(), ensure_ascii=False)
    assert "175" not in raw, "the withdrawn £175 90-minute price survives somewhere"
    assert "£175" not in _rendered()


# ---------------------------------------------------------------------------
# What Susie is actually told
# ---------------------------------------------------------------------------
def test_the_engine_does_not_deny_the_30_minute_session():
    """The duration block used to hardcode "there is no 30-minute session".

    Vital Edge now sells one at £65, so that sentence would have Susie refuse a
    real service. It was a clinic fact living in engine code.
    """
    prompt = _rendered()
    assert "there is no 30-minute session" not in prompt
    assert "£65" in prompt
    assert "Neck, Back and Shoulders" in prompt


def test_both_choice_services_get_their_own_duration_question():
    """A `break` used to emit the block for the FIRST choice service only."""
    prompt = _rendered()
    assert "DURATION QUESTION FOR SPORTS MASSAGE" in prompt
    assert "DURATION QUESTION FOR DEEP TISSUE MASSAGE" in prompt
    # And each scopes its "ONLY session lengths" claim to itself, rather than
    # asserting it clinic-wide over a clinic that also sells a fixed 30.
    assert "for a Sports Massage the ONLY session lengths" in prompt
    assert "for a Deep Tissue Massage the ONLY session lengths" in prompt


def test_anf_is_named_as_coming_soon_and_never_priced():
    prompt = _rendered()
    low = prompt.lower()
    assert "amino neural" in low
    assert "coming soon" in low
    # It must not acquire a price, and must not become bookable.
    assert "anf" not in {s["service_id"] for s in _clinic()["services"]}


def test_a_withdrawn_service_asked_for_by_name_is_handled_not_offered():
    """Susie should decline it and redirect — not book it, not stay silent."""
    prompt = _rendered()
    assert "no longer offers" in prompt
    for name in ("Stress Buster", "Muscle / Nerve Injury", "Facial Release"):
        assert name in prompt, f"{name} has no withdrawal handling"
