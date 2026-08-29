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
    """Mark's decision is Mark's. It must not be read as a house default."""
    assert screening_enabled(get_clinic("vital_edge")), (
        "vital_edge lost its emergency intercept — that was baad8ab3 and is a "
        "different clinic's decision")
    assert screening_enabled(get_clinic("jv_v1"))
    assert len(_screens(get_clinic("jv_v1"))) > 0, "jv_v1 runs real screens"
