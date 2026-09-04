"""Which clinics route hold phrases through the arbiter, and why.

`hold_speech_enabled` defaults to FALSE and is opted into with ONE top-level
key in clinic.json. That default is what made folding the clinic branches onto
one engine audibly neutral: the arbiter changes what a caller hears while they
wait, so each clinic was switched on only once someone had listened to it.

  northgate   from the start — the demo line, and every call this week
  vital_edge  2026-09-01
  jv_v1       2026-09-04  <- this file

⚠️ THE KEY GOES UNDER `operational`, AND ONLY THERE. `hold_speech_enabled`
reads `get_clinic(...).get("hold_speech")` — top level — but that top-level
value is BUILT by `app/clinic_config.py:1582`:

    clinic["hold_speech"] = bool(op.get("hold_speech", False))

an assignment, not an `or`. So a `hold_speech` written at the top level of
clinic.json is not merely ignored, it is overwritten with False. (`calendar_id`
~25 lines above was given an `or` after exactly that bug sent one tenant's
bookings into another's calendar; this key has not had that treatment.) The
failure is inaudible either way — `hold_speech_enabled` fails to False by
design, so the clinic keeps its pre-arbiter behaviour and nothing logs. That is
why every assertion here goes through `get_clinic`, never through the JSON.

WHY JV WAS SAFE TO TURN ON. Its configuration is identical in the two respects
the arbiter reads: `booking_system` is `google_calendar`, so `provisional` is
False, and it has a named practitioner. That is northgate's shape — the one the
arbiter has run longest. It therefore draws WRITE_BOOK ("Right, booking you
in -") and can never draw PENDING_REQUEST, which names the practitioner and
would claim a request where JV makes a real booking. VE is the provisional one
and keeps that pool.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.hold_speech import clinic_facts, hold_speech_enabled

#: Clinics answering a real patient line.
PATIENT_LINES = ["northgate", "jv_v1", "vital_edge"]


@pytest.mark.parametrize("clinic_id", PATIENT_LINES)
def test_the_arbiter_is_on(clinic_id):
    """Read through `get_clinic`, never the file. A key that does not survive
    the loader is dead config that reads like working config."""
    assert hold_speech_enabled({"clinic_id": clinic_id}), (
        f"{clinic_id} is not routing hold phrases through the arbiter — check "
        f"the key is TOP-LEVEL `hold_speech`, not `operational.hold_speech`"
    )


def test_the_key_is_under_operational_and_survives_the_loader():
    """Pins the trap: `operational` is where it goes, and a top-level key is
    destroyed rather than ignored."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app" / "clinics"
    for clinic_id in PATIENT_LINES:
        raw = json.loads(
            (root / clinic_id / "clinic.json").read_text(encoding="utf-8")
        )
        assert (raw.get("operational") or {}).get("hold_speech") is True, (
            f"{clinic_id}: the key must live under `operational` — that is the "
            f"only place the loader reads it from"
        )
        assert "hold_speech" not in raw, (
            f"{clinic_id} sets hold_speech at the TOP level of clinic.json, "
            f"where clinic_config.py:1582 overwrites it with False"
        )
        assert (get_clinic(clinic_id) or {}).get("hold_speech") is True, (
            f"{clinic_id}: set under `operational` but did not survive the "
            f"loader"
        )


def test_a_top_level_key_is_destroyed_by_the_loader():
    """States the hazard as behaviour, so it cannot be argued about again.

    This is the assertion the file shipped inverted: a previous pass read the
    accessor, saw top level, and never read the loader that builds the dict the
    accessor reads.
    """
    from app.clinic_config import _map_json_to_clinic_contract

    mapped = _map_json_to_clinic_contract(
        {"hold_speech": True, "operational": {}}
    )
    assert mapped.get("hold_speech") is False, (
        "a top-level hold_speech now survives the loader — if that is "
        "deliberate, this test and the docstring above are what to update"
    )


def test_jv_draws_the_booking_pool_and_not_the_provisional_one():
    """The one way turning JV on could have said something untrue.

    PENDING_REQUEST is "Sending that over to {practitioner} -", which is right
    for Vital Edge — it makes a request Jonathan confirms — and wrong for JV,
    which writes the booking. The discriminator is `booking_system`, so this
    asserts the fact rather than the wording.
    """
    provisional, practitioner = clinic_facts({"clinic_id": "jv_v1"})
    assert provisional is False, "JV would draw PENDING_REQUEST"
    assert practitioner, "a named practitioner is expected for JV"

    ve_provisional, _ = clinic_facts({"clinic_id": "vital_edge"})
    assert ve_provisional is True, "VE must keep the provisional pool"


def test_a_clinic_with_no_key_still_defaults_off():
    """The default is the safety property. Theorem has not been listened to on
    the arbiter, so it must keep the behaviour it runs today — and an unknown
    clinic must too."""
    assert not hold_speech_enabled({"clinic_id": "theorem"})
    assert not hold_speech_enabled({"clinic_id": "a-clinic-that-does-not-exist"})
    assert not hold_speech_enabled({})
    assert not hold_speech_enabled(None)
