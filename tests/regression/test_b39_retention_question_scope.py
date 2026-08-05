# tests/regression/test_b39_retention_question_scope.py
"""
B-39 — the retention question is asked up to three times, and it reaches callers
who are not cancelling anything.

Two defects, one sentence:

* **Repetition.** `CA66d6f1b4` asked *"would you like to reschedule…, or cancel
  it altogether?"* three times across 27 seconds, the third after the caller had
  said "cancel" plainly. On `CAe74ceae7` the caller answered with the canonical
  phrase, verbatim from the question, and Susie re-emitted the whole question
  **in the same turn as actioning the cancellation** — the caller heard the
  question, then immediately heard it being done. The register records two
  attempts to narrow this to short answers or ambiguous tokens; both were
  withdrawn. It is not about the answer's shape.

* **Wrong flow.** Owner instruction, 2026-08-05: the question must exist on the
  cancel path only. Offering to cancel an appointment to a caller who rang to
  MOVE it invites them to lose a booking they were trying to keep.

The cause of the repetition was in the prompt's own wording: the cancel branch
said the question was *"REQUIRED on the cancel path EVERY TIME"*. That is true
of the call and false of the turn, and "every time" is the reading that loops.
It now states a count.

**The sentence cannot simply be deleted.** `_cancel_retention_asked` opens the
cancel write gate on `"altogether"`, so removing the wording from the cancel
path would hard-block `cancel_appointment` on every template clinic — the
mirror image of `B-57`, which is the same coupling failing in the other
direction. The gate-facing literal is asserted here for that reason.
"""
from __future__ import annotations

import pytest

from app.media_streams import llm_stream as ls
from tests.regression.test_b57_theorem_cancel_gate import _rendered

# The two engines that render a reschedule/cancel flow on a LIVE line:
# template_v1 (jv_v1 — the demo line, vital_edge — Jonathan's clinic) and
# theorem_v3 (Mark's clinic). demo and theorem are FlowEngine clinics and route
# through flow.py, which has always skipped the step on a reschedule
# (`CONFIRM_RESCHEDULE_OR_CANCEL` is advanced past when intent == "reschedule").
LIVE_FREEFORM_CLINICS = ["jv_v1", "vital_edge", "theorem_v3"]
TEMPLATE_CLINICS = ["jv_v1", "vital_edge"]


# ---------------------------------------------------------------------------
# 1. Wrong flow — the question is cancel-only
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("clinic_id", LIVE_FREEFORM_CLINICS)
def test_the_reschedule_path_is_told_not_to_offer_cancelling(clinic_id):
    low = _rendered(clinic_id).lower()
    assert "never ask a caller who is" in low, (
        f"{clinic_id}'s reschedule branch does not forbid the retention "
        f"question — a caller moving an appointment can be offered a cancel"
    )
    assert "rather cancel" in low


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_the_reschedule_branch_still_asks_nothing_at_all(clinic_id):
    """The pre-existing instruction is the load-bearing half — the new sentence
    explains it rather than replacing it."""
    low = _rendered(clinic_id).lower()
    assert "do not ask anything — go straight to the" in low


# ---------------------------------------------------------------------------
# 2. Repetition — a count, not "every time"
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("clinic_id", LIVE_FREEFORM_CLINICS)
def test_the_question_is_bounded_to_one_ask(clinic_id):
    low = _rendered(clinic_id).lower()
    assert "ask it once" in low, (
        f"{clinic_id} does not bound the retention question to a single ask"
    )


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_the_every_time_wording_is_gone(clinic_id):
    """The exact phrase whose second reading produced the loop.

    Matched as the whole clause, not on "every time" alone — that phrase is used
    legitimately elsewhere in the prompt ("say 'pounds' every time"), and a bare
    substring test fails on the pricing block instead of on this one.
    """
    low = _rendered(clinic_id).lower()
    assert "on the cancel path every time" not in low, (
        "the 'REQUIRED ... EVERY TIME' wording is back on the cancel path — it "
        "reads as 'every turn' and that is B-39"
    )


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_the_same_turn_case_is_named(clinic_id):
    """CAe74ceae7's shape: the question spoken alongside the cancellation."""
    low = _rendered(clinic_id).lower()
    assert "same turn as actioning" in low


# ---------------------------------------------------------------------------
# 3. The coupling that stops this being a one-line deletion
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_the_cancel_path_keeps_the_wording_its_write_gate_needs(clinic_id):
    """Scoping the question out of the reschedule flow must not scope it out of
    the cancel flow: `_cancel_retention_asked` opens on "altogether", and with
    no such turn on record cancel_appointment is refused. B-57 in reverse."""
    low = _rendered(clinic_id).lower()
    assert "or cancel it altogether?" in low
    assert ls._cancel_retention_asked("or cancel it altogether?") is True


def test_theorem_does_not_depend_on_the_retention_wording_to_cancel():
    """theorem_v3 reaches its cancel through a direct CTA, so the question is
    genuinely optional there — which is why it can be narrowed to the ambiguous
    opening without blocking anything. Asserted so the asymmetry between the two
    engines is recorded rather than rediscovered."""
    low = _rendered("theorem_v3").lower()
    assert "shall i go ahead and cancel that?" in low
    assert ls._cancel_retention_asked("shall i go ahead and cancel that?") is True
