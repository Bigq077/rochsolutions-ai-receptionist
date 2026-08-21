# tests/regression/test_every_live_clinic_has_a_bypass_target.py
"""
The emergency bypass must have somewhere to ring.

`app/clinic_call_mode.py` lets a clinic text OFF to their Susie number and have
their own phone ring first — the seatbelt that PRODUCTION_READINESS_PLAN.md
Phase 5 marks "never cut", and the only mitigation operable from a phone, at
speed, by a non-engineer.

But the toggle only flips `call_overflow.enabled`. The router then reads
`call_overflow.dial_phone`, and **ignores the toggle entirely when that is
empty**:

    if _human_first and _dial_phone:      # app/media_streams/router.py

So a clinic with no `call_overflow` block has a bypass that silently does
nothing: the clinic texts OFF, gets told routing changed, and Susie keeps
answering. Vital Edge was in exactly that state until 2026-08-21.

This pins the *precondition*, not the routing: every clinic served from
clinic.json must carry a dial_phone, so the toggle has a target the day it is
needed. `enabled` is deliberately NOT asserted — false is the correct default,
and the whole point of the toggle is that the default can be left off.

Theorem is out of scope here on purpose: its config is a hardcoded dict in
clinic_config.py, not clinic.json, so it is pinned on its own branch.
"""

import pytest

from app.clinic_call_mode import resolve_overflow
from app.clinic_config import get_clinic

# Clinics whose config is resolved from app/clinics/<id>/clinic.json.
BYPASS_CLINICS = ["vital_edge", "jv_v1"]


@pytest.mark.parametrize("clinic_id", BYPASS_CLINICS)
def test_the_clinic_has_a_call_overflow_block(clinic_id):
    clinic = get_clinic(clinic_id) or {}
    assert clinic.get("call_overflow"), (
        f"{clinic_id} has no call_overflow block — the OFF/ON text toggle "
        f"would report success and change nothing"
    )


@pytest.mark.parametrize("clinic_id", BYPASS_CLINICS)
def test_the_bypass_has_a_number_to_ring(clinic_id):
    clinic = get_clinic(clinic_id) or {}
    dial = ((clinic.get("call_overflow") or {}).get("dial_phone") or "").strip()
    assert dial.startswith("+"), (
        f"{clinic_id} call_overflow.dial_phone must be a non-empty E.164 "
        f"number — the router drops the whole human-first branch without it; "
        f"got {dial!r}"
    )


@pytest.mark.parametrize("clinic_id", BYPASS_CLINICS)
def test_the_practitioner_is_told_how_to_take_the_call(clinic_id):
    # A silent whisper leg is a practitioner who answers, hears nothing, and
    # hangs up — which routes the caller to Susie having wasted the ring.
    overflow = (get_clinic(clinic_id) or {}).get("call_overflow") or {}
    assert (overflow.get("whisper_text") or "").strip(), (
        f"{clinic_id} needs whisper_text — it is what tells the practitioner "
        f"to press 1"
    )
    assert "1" in overflow["whisper_text"]


@pytest.mark.parametrize("clinic_id", BYPASS_CLINICS)
async def test_resolution_never_raises_and_defaults_to_the_config(clinic_id):
    # resolve_overflow sits on the critical path of every inbound call. With no
    # Redis in the test environment it must degrade to the clinic.json value,
    # never to an exception — a clinic whose phone stops working because a
    # convenience feature had a bad day is the worse outcome.
    clinic = get_clinic(clinic_id) or {}
    human_first, reason = await resolve_overflow(clinic_id, clinic)
    assert human_first is bool(clinic["call_overflow"].get("enabled"))
    assert reason.startswith("config")
