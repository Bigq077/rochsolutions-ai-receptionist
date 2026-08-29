"""Mark's condition, as a test: it must not add a question for someone booking.

He declined clinical screening — fastest possible booking, and jv_v1's six
screens each cost the caller a question. That decision stands and is pinned
elsewhere. What he agreed to on 2026-08-29 is narrower, and he had already
agreed to it in substance: his prompt tells Susie to say "call 999" when a
caller describes an emergency, and call_handling.emergency_message is his own
wording. The only thing missing was a deterministic trigger.

He said yes "as long as it doesn't add another question to somebody who wants to
book". That is an acceptance criterion, so it is asserted rather than promised.

Two things make it true, and both are pinned below:

1. Theorem has NO `screens`, so nothing can ever arm. `update_screening_state`
   can only return "emergency" or "none" for it.
2. `screening_enabled()` is still FALSE for Theorem — the intercept keys on
   `emergency_red_flags` being configured, NOT on `enabled`. So the pin on
   theorem-onboarding that says "Mark does not want clinical screening ... do
   not fix this test" keeps passing untouched, which is the point: his decision
   is not being reinterpreted, it is being left alone.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams import clinical_screening as cs

THEOREM_IDS = ["theorem", "theorem_v2", "theorem_v3"]

# What people actually say to a physio clinic they want an appointment at.
BOOKING_TALK = [
    "id like to book an appointment please",
    "can i get in this week",
    "my lower back has been really painful for a few weeks",
    "ive got a shoulder problem",
    "how much is an assessment",
    "do you have anything on thursday",
    "its been hurting since i lifted something at work",
    "i twisted my knee playing football",
    "yes that time works",
    "my name is quentin",
]


@pytest.mark.parametrize("clinic_id", THEOREM_IDS)
def test_no_booking_utterance_ever_arms_anything(clinic_id):
    """THE condition. Nothing a caller says on the way to a booking may arm."""
    clinic = get_clinic(clinic_id)
    for utterance in BOOKING_TALK:
        result = cs.update_screening_state({}, clinic, utterance)
        assert result["action"] == "none", (
            f"{clinic_id} reacted to {utterance!r} with "
            f"{result['action']!r} — that is an extra turn for someone who "
            "just wants to book, which is exactly what Mark said no to."
        )


@pytest.mark.parametrize("clinic_id", THEOREM_IDS)
def test_theorem_still_has_no_proactive_screens(clinic_id):
    """The mechanism behind the guarantee above. If this list ever grows,
    Theorem starts asking safety questions before booking — a clinical scope
    change and a client decision, not a code review comment."""
    assert cs._screens(get_clinic(clinic_id)) == []


@pytest.mark.parametrize("clinic_id", THEOREM_IDS)
def test_marks_no_screening_decision_is_untouched(clinic_id):
    """theorem-onboarding pins exactly this and says "do not fix this test".

    The intercept was built so that pin keeps passing rather than needing to be
    re-argued: it reads `emergency_red_flags`, not `enabled`.
    """
    assert not cs.screening_enabled(get_clinic(clinic_id))


@pytest.mark.parametrize("clinic_id", THEOREM_IDS)
def test_a_volunteered_emergency_is_still_intercepted(clinic_id):
    clinic = get_clinic(clinic_id)
    for utterance in ("im having chest pain",
                      "i think hes having a stroke",
                      "he's collapsed"):
        result = cs.update_screening_state({}, clinic, utterance)
        assert result["action"] == "emergency", utterance
        assert "999" in (result["speak"] or "")


def test_the_two_switches_are_genuinely_independent():
    """The split is the whole reason Mark could say yes without changing his mind.

    Before it, `detect_emergency` read its keywords through `screening_config`,
    which returns nothing unless `enabled` is set — so "no screening" and "no
    deterministic 999" were one switch, and a clinic had to accept triage to get
    an emergency response.
    """
    screens_only = {"clinical_screening": {"enabled": True, "screens": [{"id": "x"}]}}
    emergency_only = {"clinical_screening": {
        "emergency_red_flags": {"keywords": ["chest pain"]}}}

    assert cs.screening_enabled(screens_only)
    assert not cs.emergency_intercept_enabled(screens_only)

    assert not cs.screening_enabled(emergency_only)
    assert cs.emergency_intercept_enabled(emergency_only)


def test_a_clinic_can_opt_out_of_interception_explicitly():
    """Wording available to the model, but no deterministic trigger."""
    opted_out = {"clinical_screening": {
        "emergency_intercept": False,
        "emergency_red_flags": {"keywords": ["chest pain"]}}}
    assert not cs.emergency_intercept_enabled(opted_out)
    assert not cs.detect_emergency("i have chest pain", opted_out)
