"""
Regression: two of twelve times, described to the caller as all of them.

§2.2 of OPEN_DEFECTS_2026-09-03.md — CA91020004, northgate, 2 Sep 2026.
The model-visible tool result held TWELVE Monday times:

    08:00 08:50 09:40 10:30 11:20 12:10 13:00 13:50 14:40 15:30 16:20 17:10

Two were read out. The caller then asked what else there was, and was told:

    "the slots I have that day are eight in the morning or ten past five."

Turn 4 made no fresh check_availability call — by design, BOOKING STEPS 6 says
to answer a day-pick from the existing data. So the model had all twelve in
front of it and contradicted its own payload.

── THE DECISION, AND WHY IT IS NOT THE ONE THE DOC EXPECTED ───────────────────
The doc framed this as "B-97 and B-99 are in direct conflict and multi_day
resolves it by saying nothing. Needs a decision, not a patch."

Investigated, the conflict dissolves: `more_times` has two consumers, and B-99
only objects to one of them.

  * the SPOKEN tail, "…a few others that day" — genuinely day-less after a
    three-day readout, so B-99 suppresses it correctly. UNCHANGED here.
  * the model's licence to claim completeness — B-99 has no quarrel with this,
    and it was never the thing that needed suppressing.

Nor is the multi_day payload short of information: `_present_days` carries each
day's FULL `slot_times` (the truncation at receptionist_tools.py ~3480 is
inside the `single_day` branch only). The model was not missing data.

What was missing was a rule. BOOKING STEPS 5 already guards ONE direction —

    "Never offer or imply 'other'/'more' times for a day unless the
     check_availability data for that day actually contains times you have NOT
     already read out"

— which is B-97's over-promise failure, the one that once looped a caller into
hanging up. There was no rule against the opposite error, and that asymmetry is
this defect. The missing half now sits beside its twin.

Deliberately NOT more spoken words: the slot readout already runs 17.9s on a
call where Susie speaks 60% of the time (§2.8). This fix adds none.

── ALSO FOUND, AND NOT FIXED HERE ─────────────────────────────────────────────
Not one of the payload's four incompleteness fields — `more_times`,
`times_not_shown`, `days_not_shown`, `days_found_in_window` — appears anywhere
in the rendered 104k-character prompt. Each was written in response to a live
defect (B-94, B-97, B-98, B-116, B-117) and the model is never told what any of
them mean. That is a wider gap than this test covers; see the session notes.
"""
from __future__ import annotations

import pytest

from app.prompts.susie_system_prompt import build_system_prompt_parts

# Every clinic that reads out slots can make the claim, so unlike the CONDITION
# FLUENCY rule this one is not gated on a per-clinic library.
TEMPLATE_CLINICS = ["northgate", "jv_v1", "vital_edge"]


def _rendered(clinic_id: str) -> str:
    parts = build_system_prompt_parts({"clinic_id": clinic_id})
    return "".join(p for p in parts if isinstance(p, str))


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_the_completeness_rule_reaches_the_model(clinic_id):
    text = _rendered(clinic_id)
    assert "SAME RULE THE OTHER WAY ROUND" in text
    assert "Never state or imply that what you read out is ALL that day" in text


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_the_over_promise_guard_is_still_there(clinic_id):
    """THE guard. B-97's failure — promising times the retrieval path cannot
    produce — cost a caller who hung up, judge score 1. Fixing the
    under-claim must not trade it for the over-claim; both rules stand."""
    text = _rendered(clinic_id)
    assert "do not suggest more" in text
    assert "NOT already read out" in text


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_both_halves_sit_together(clinic_id):
    """Placement is the point: the asymmetry existed because one half was
    written and the other was not. They are two sentences apart so neither can
    be read without the other."""
    text = _rendered(clinic_id)
    twin = text.index("do not suggest more")
    new = text.index("SAME RULE THE OTHER WAY ROUND")
    assert 0 < new - twin < 400


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_the_live_sentence_is_named_as_wrong(clinic_id):
    """The repo's convention: rules carry their evidence. A rule with the
    offending sentence in it survives a re-wording pass that a bare
    prohibition does not."""
    text = _rendered(clinic_id)
    assert "the slots I have that day" in text
    assert "twelve Monday" in text


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_describing_the_offer_is_still_allowed(clinic_id):
    """The rule must not push the model into refusing to say anything about
    what it has — 'I've got X or Y' is correct and must stay explicitly
    sanctioned, or this trades a false claim for a silence."""
    text = _rendered(clinic_id)
    assert "is safe" in text
    assert "Describe what you are OFFERING" in text


def test_the_spoken_tail_is_untouched_on_multi_day():
    """B-99 stands. The fix is a rule about what may be CLAIMED, not a change
    to what is SPOKEN — the readout gains no words. If this fails, someone has
    reopened the tail on multi_day, which is a different decision."""
    import inspect

    from app.tools import slot_offer

    src = inspect.getsource(slot_offer)
    assert 'if more and mode == "single_day":' in src, (
        "the more-times tail is no longer single_day-only — B-99 was reopened"
    )
