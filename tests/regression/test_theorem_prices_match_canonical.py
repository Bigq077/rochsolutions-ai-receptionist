"""Theorem's spoken prices must match canonical.py.

`latency-eval`'s `_build_theorem_v3` had drifted from Theorem's own canonical
data and from `main` (Mark's live branch), which agreed with each other:

    New patient assessment      £75   ->  £85
    Follow-up                   £75   ->  £85
    Acupuncture / Psychotherapy £75   ->  £85
    Standalone shockwave/laser  £120  ->  £130
    Package of four shockwave   £420  ->  £468
    Wellness Massage        "enquire" ->  £85

Found while re-verifying THEOREM_PORT_PLAN before the port. Ported as-is, Susie
would have quoted £75 for an £85 appointment on every pricing question, on a
paying client's line — see section 3a of that plan.

The prompt is prose and cannot be regression-tested the way logic can, so this
pins the one property that matters: the numbers Susie says are the numbers the
clinic charges.
"""
import re

import pytest

from app.prompts.susie_system_prompt import _build_theorem_v3


def _prompt() -> str:
    built = _build_theorem_v3({
        "clinic_id": "theorem_v3",
        "collected": {},
        "selected_location": "alcester",
        "v3_location_confirmed": True,
    })
    if isinstance(built, tuple):
        built = "\n".join(str(p) for p in built)
    return built


@pytest.fixture(scope="module")
def prompt():
    return _prompt()


# Service -> price, straight from app/clinics/theorem/canonical.py.
# Kept as a literal table rather than imported so a wrong edit to canonical
# cannot silently make this test agree with it.
CANONICAL_PRICES = {
    "New patient assessment": 85,
    "Follow-up": 85,
    "Rehabilitation": 65,
    "Standalone shockwave or Class IV Laser": 130,
    "Acupuncture, Psychotherapy": 85,
    "Wellness and Stress Relief Massage with In-light Therapy": 85,
}


@pytest.mark.parametrize("service,price", sorted(CANONICAL_PRICES.items()))
def test_each_service_is_quoted_at_the_canonical_price(prompt, service, price):
    m = re.search(re.escape(service) + r"[^\n£]*£\s*([0-9]+(?:\.[0-9]+)?)", prompt)
    assert m, f"{service!r} is not priced in the Theorem prompt at all"
    assert float(m.group(1)) == float(price), (
        f"{service}: prompt says £{m.group(1)}, canonical says £{price}"
    )


def test_the_headline_assessment_price_is_85_wherever_it_appears(prompt):
    """The PRICING QUESTIONS rule quotes the assessment fee separately from the
    PRICES block. Both said £75; a fix that misses one leaves Susie
    contradicting herself inside a single call."""
    for m in re.finditer(r"£\s*(\d+)\s*(?:new patient|follow-up)", prompt, re.I):
        assert m.group(1) == "85", f"found £{m.group(1)} for an appointment fee"


def test_no_seventy_five_pound_price_survives_anywhere(prompt):
    """£75 was the stale number in every place it appeared. It is not a valid
    price for ANY Theorem service, so its presence is always a defect."""
    assert "£75" not in prompt
    assert "seventy-five pounds" not in prompt.lower()


def test_the_prices_that_are_not_85_are_left_alone(prompt):
    """Guard against an over-broad "everything is £85" edit. Rehabilitation and
    Prescribing are genuinely cheaper and the shockwave procedures genuinely
    dearer; flattening them would overcharge a £12.50 prescribing consult by
    nearly seven times."""
    assert "£65" in prompt, "Rehabilitation price lost"
    assert "£12.50" in prompt, "Prescribing price lost"
    assert "£130" in prompt, "Standalone shockwave/laser price lost"
    assert "£45" in prompt, "shockwave/laser surcharge lost"
    assert "£468" in prompt, "package-of-four price lost"


def test_services_with_no_canonical_price_still_say_enquire(prompt):
    """Reiki and Auricular Acupuncture are NOT in canonical.py. Susie must not
    invent a price for them — the rule that says so has to survive."""
    assert "enquire for pricing" in prompt
    assert "never invent a price" in prompt
    tail = prompt[prompt.find("enquire for pricing") - 200:]
    assert "Reiki" in tail
    assert "Auricular" in tail
