"""Mark declined clinical screening. Canonical could not see that decision.

WHY THIS FILE EXISTS

`theorem-onboarding` carries tests/regression/test_theorem_config_port.py, whose
docstring says: "CLINICAL SCREENING. Mark does not want it ... screening is off
by ABSENCE. An absence is easy to undo by accident, which is why it is pinned
here rather than left as a convention." Its test adds: "That is a clinical scope
change and a client decision — it is not a code review comment. Do not 'fix'
this test."

That pin exists ONLY on that branch. Canonical never had it — so on 2026-08-29 a
clinical_screening block was added to CLINICS["theorem"] here, the whole suite
stayed green, and the contradiction was found only when scripts/port.py tried to
carry it to Mark's branch and his own tests refused it.

Canonical is where all work now starts. A client decision that is invisible here
is a decision that will be broken here, repeatedly, by people acting in good
faith. So it is mirrored — not moved: the branch keeps its own pin.

WHAT THIS DOES NOT DECIDE

Whether Mark would accept an EMERGENCY INTERCEPT is a separate question and an
open one. `detect_emergency` reads its keywords from the same
`clinical_screening` block, so today "no screening" and "no deterministic 999
response" are the same switch — Theorem has the wording (call_handling.
emergency_message) but nothing to trigger it deterministically.

That is a question for Mark, not an inference from this test. If he says yes,
this file and his branch's pin both get re-aimed IN THE SAME COMMIT, to assert
what he actually decided — no proactive screens — rather than the mechanism
that currently stands in for it.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams.clinical_screening import _screens, screening_enabled


@pytest.mark.parametrize("clinic_id", ["theorem", "theorem_v2", "theorem_v3"])
def test_theorem_has_no_clinical_screening(clinic_id):
    """Mirrors theorem-onboarding's pin so canonical stops being blind to it."""
    clinic = get_clinic(clinic_id)
    assert not screening_enabled(clinic), (
        "a clinical_screening block was added to Theorem. Mark declined "
        "clinical triage — see tests/regression/test_theorem_config_port.py on "
        "theorem-onboarding. This is a client decision, not a code review "
        "comment: ask him, do not delete this test."
    )
    assert _screens(clinic) == []


def test_the_other_clinics_are_unaffected_by_that_decision():
    """Mark's decision is Mark's. It must not be read as a house default.

    jv_v1 dropped out of this test on 2026-09-05. It used to be the example
    of a clinic that had NOT followed Mark -- then the owner took the same
    posture for JV, by a separate decision recorded in jv_v1/clinic.json and
    pinned in test_jv_mirrors_the_demo_line_screening_posture.py.

    The point of this test is unchanged and still worth having: one clinic's
    configuration must never be inferred from another's. So it now asserts
    what is still true -- vital_edge kept its own settings, and JV's posture
    came from JV's own config rather than from Mark's.
    """
    assert screening_enabled(get_clinic("vital_edge")), (
        "vital_edge lost its emergency intercept — that was baad8ab3 and is a "
        "different clinic's decision")
    jv = get_clinic("jv_v1")
    # Read the RAW config, not `_screens()`: that helper goes through
    # `screening_config()`, which returns nothing once `enabled` is false --
    # so it answers "is screening running", not "are the screens on file".
    on_file = (jv.get("clinical_screening") or {}).get("screens") or []
    assert len(on_file) == 6, (
        "jv_v1's six screens must stay on file — they are switched off by a "
        "boolean, not deleted, so the decision stays reversible"
    )
    assert (jv.get("clinical_screening") or {}).get("enabled") is False, (
        "jv_v1 is off by its OWN recorded decision (2026-09-05); if that key "
        "has gone, the posture is being inherited rather than chosen"
    )
