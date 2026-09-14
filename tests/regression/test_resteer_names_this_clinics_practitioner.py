"""CA66bd0930 (14 Sep 2026, JV, live) — a JV caller heard "I'll get that
logged for Jonathan now." Jonathan is Vital Edge's practitioner; JV's is
Marcus. OPEN_DEFECTS_2026-09-14.md row #2.

`_FALSE_CALLBACK_RESTEER` had Jonathan hard-coded since 14 Aug (39387203).
Since 2 Sep all three clinics run the same commit, so a name in engine code
is every clinic's name. The line now reads the practitioner from
clinic.json; a clinic with no single named practitioner gets "the team".
"""
import pytest

from app.media_streams.turn_handler import (
    _apply_callback_promise_gate,
    _false_callback_resteer,
)

PROMISE = "I've passed that on to Marcus — he'll be in touch with you directly."


@pytest.mark.parametrize("clinic_id, expected", [
    ("jv_v1", "Marcus"),
    ("vital_edge", "Jonathan"),
    ("northgate", "Priya"),
])
def test_the_resteer_names_the_clinics_own_practitioner(clinic_id, expected):
    session = {"clinic_id": clinic_id}
    out = _apply_callback_promise_gate(PROMISE, session)
    assert out == f"One moment — I'll get that logged for {expected} now."


def test_a_clinic_without_a_named_practitioner_gets_the_team():
    assert _false_callback_resteer({"clinic_id": "theorem"}) == (
        "One moment — I'll get that logged for the team now."
    )
    assert _false_callback_resteer({}) == (
        "One moment — I'll get that logged for the team now."
    )
