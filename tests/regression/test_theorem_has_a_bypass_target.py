# tests/regression/test_theorem_has_a_bypass_target.py
"""
Mark's emergency bypass must have somewhere to ring.

`app/clinic_call_mode.py` lets a clinic text OFF to their Susie line and have
their own phone ring first — the seatbelt PRODUCTION_READINESS_PLAN.md Phase 5
marks "never cut". The toggle flips `call_overflow.enabled`; the router then
reads `call_overflow.dial_phone` and drops the whole human-first branch when it
is empty:

    if _human_first and _dial_phone:      # app/media_streams/router.py

Theorem went live on ~14 Aug with no `call_overflow` block at all, so Mark could
have texted OFF, been told his routing had changed, and had Susie keep answering
every call.

Two branch-specific traps this pins, both of which have cost time before:

1. **The config is not in clinic.json.** On this branch Theorem is a hardcoded
   `CLINICS` entry in `app/clinic_config.py`. Editing
   `app/clinics/theorem/clinic.json` changes nothing for Mark.
2. **The live line is `theorem_v3`, not `theorem`.** `theorem_v2` and
   `theorem_v3` are `deepcopy`s of `theorem` taken at import time, so the block
   must exist on `theorem` *before* those copies are made. A block added after
   them would pass a naive test against `theorem` and still leave the live line
   bare.
"""

import pytest

from app.clinic_call_mode import resolve_overflow
from app.clinic_config import TWILIO_TO_CLINIC, get_clinic

THEOREM_IDS = ["theorem", "theorem_v2", "theorem_v3"]
MARK_LIVE_NUMBER = "+447380841468"


def test_marks_live_number_still_maps_to_theorem_v3():
    # If this ever changes, the parametrised checks below stop covering the
    # line that actually rings.
    assert TWILIO_TO_CLINIC.get(MARK_LIVE_NUMBER) == "theorem_v3"


@pytest.mark.parametrize("clinic_id", THEOREM_IDS)
def test_the_bypass_has_a_number_to_ring(clinic_id):
    overflow = (get_clinic(clinic_id) or {}).get("call_overflow") or {}
    dial = (overflow.get("dial_phone") or "").strip()
    assert dial.startswith("+"), (
        f"{clinic_id} call_overflow.dial_phone must be a non-empty E.164 "
        f"number — without it the OFF text reports success and changes "
        f"nothing; got {dial!r}"
    )


def test_the_deepcopies_carry_the_block_not_just_the_parent():
    # The specific failure this guards: a block added below the deepcopy lines.
    parent = (get_clinic("theorem") or {}).get("call_overflow") or {}
    live = (get_clinic("theorem_v3") or {}).get("call_overflow") or {}
    assert live.get("dial_phone") == parent.get("dial_phone") != None


@pytest.mark.parametrize("clinic_id", THEOREM_IDS)
def test_the_practitioner_is_told_to_press_1(clinic_id):
    # A silent whisper leg is a practitioner who answers, hears nothing and
    # hangs up — which sends the caller to Susie having wasted the ring.
    overflow = (get_clinic(clinic_id) or {}).get("call_overflow") or {}
    assert "1" in (overflow.get("whisper_text") or "")


@pytest.mark.parametrize("clinic_id", THEOREM_IDS)
async def test_human_first_stays_off_until_mark_asks_for_it(clinic_id):
    # Adding the target must not change how a single call routes today.
    clinic = get_clinic(clinic_id) or {}
    human_first, reason = await resolve_overflow(clinic_id, clinic)
    assert human_first is False
    assert reason.startswith("config")
