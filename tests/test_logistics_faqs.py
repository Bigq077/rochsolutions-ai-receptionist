"""
tests/test_logistics_faqs.py
----------------------------
F24/F26 regression lock: logistics questions must have real, reachable answers
(so the F24/F26 prompt rule can route them via get_clinic_info instead of the
clinical-deflection "That's one for the practitioner").

- F26: "can I book online?"  → online_booking present, mentions the website.
- F24: "over the phone / video?" → online_consultations present, says in-person only.

Prompt behaviour itself (routing logistics away from the practitioner line) is
phone-verified; this locks the facts it depends on so they can't silently vanish.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clinic_config import get_clinic


def _faq() -> dict:
    return get_clinic("theorem").get("faq", {})


def test_online_booking_fact_present_and_reachable():
    faq = _faq()
    assert "online_booking" in faq, "online_booking FAQ missing (F26)"
    text = faq["online_booking"].lower()
    assert "theoremhealth.co.uk" in text
    assert "book" in text


def test_online_consultations_fact_is_in_person_only():
    faq = _faq()
    assert "online_consultations" in faq, "online_consultations FAQ missing (F24)"
    text = faq["online_consultations"].lower()
    assert "in-person" in text or "in person" in text
    # Must NOT promise phone/video appointments.
    assert "no" in text


def test_get_clinic_info_tool_exposes_logistics_topics():
    """The tool module must list these topics so the LLM can call them."""
    from app.tools import receptionist_tools as rt
    src = Path(rt.__file__).read_text()
    assert "online_booking" in src
    assert "online_consultations" in src
