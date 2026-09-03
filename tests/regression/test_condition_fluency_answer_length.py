"""
Regression: the sentence-length rule did not reach the turn that produced the
over-long sentence.

CA91020004728883f51fa90e325acb7ebc, northgate, 2 September 2026. The caller
described a sore ankle. Susie's reply was three chunks, ~20s of audio, and its
middle chunk was a SINGLE 198-character, ~35-word sentence. The caller talked
over it, and the barge-in recovery then asked him a question he had never
reached (B-132).

── WHY THIS WAS NOT NON-ADHERENCE ─────────────────────────────────────────────
It was first read as the model ignoring instructions. It was not. The rules
that govern a condition acknowledgement cap sentence COUNT:

    BOOKING STEPS 1, condition-led exception — "ONE short turn: one or two
                                               sentences"
    BOOKING STEPS 2, clinical complaint      — "one or two sentences of
                                               SPECIFIC understanding"

One 35-word sentence satisfies both. The ~20-word SENTENCE-LENGTH rule does
exist — but it lives in `_render_faq`, under the heading "FAQ", and a condition
acknowledgement inside the booking flow is not an FAQ answer. Every rule that
actually reached that turn was obeyed.

`_render_faq` had already written down why count alone is not enough: "one live
answer was only three sentences and still ran twenty seconds, on a single
138-character middle clause." That is this defect, one block over.

So the fix is SCOPING, not wording — tightening the FAQ prose further would
have changed nothing about this turn. The counterweight now sits in CONDITION
FLUENCY's THE STANDARD, beside the instruction that creates the length ("woven
together with THEIR specifics").

── CONTAINMENT ────────────────────────────────────────────────────────────────
The block renders only for a clinic shipping `condition_knowledge`. A clinic
without one never receives the "weave in their specifics" pressure, so it never
had the defect and must not gain the rule — that is asserted below, not
assumed, because a config key that never reaches the model has been mistaken
for a model failure three times in this repo.
"""
from __future__ import annotations

import pytest

from app.clinic_loader import load_clinic
from app.prompts.susie_system_prompt import build_system_prompt_parts


def _rendered(clinic_id: str) -> str:
    parts = build_system_prompt_parts({"clinic_id": clinic_id})
    return "".join(p for p in parts if isinstance(p, str))


def _has_library(clinic_id: str) -> bool:
    ck = (load_clinic(clinic_id) or {}).get("condition_knowledge") or {}
    return bool(ck.get("conditions"))


# The clinic the defect happened on, and the other template_v1 clinic.
WITH_LIBRARY = ["northgate", "jv_v1"]


@pytest.mark.parametrize("clinic_id", WITH_LIBRARY)
def test_the_length_rule_reaches_the_condition_turn(clinic_id):
    """The whole point: it must render where the long sentence is produced."""
    assert _has_library(clinic_id), f"{clinic_id} lost its condition library"
    text = _rendered(clinic_id)
    assert "AND IT IS SHORT" in text
    assert "CONDITION FLUENCY" in text


@pytest.mark.parametrize("clinic_id", WITH_LIBRARY)
def test_the_rule_caps_sentence_length_not_only_count(clinic_id):
    """The distinction the defect turned on. A cap on how MANY sentences is
    satisfied by one 35-word sentence — which is exactly what was said."""
    text = _rendered(clinic_id)
    assert "NO sentence longer than about twenty words" in text


@pytest.mark.parametrize("clinic_id", WITH_LIBRARY)
def test_the_rule_sits_inside_the_condition_fluency_block(clinic_id):
    """Placement is the fix. In the FAQ block it was already present and did
    not apply here; beside 'woven together with THEIR specifics' it does."""
    text = _rendered(clinic_id)
    start = text.index("CONDITION FLUENCY")
    tail = text[start:]
    standard = tail.index("THE STANDARD")
    short_rule = tail.index("AND IT IS SHORT")
    assert short_rule > standard, "the counterweight must follow the standard"
    # …and inside the same block, not merely later in the prompt.
    assert short_rule - standard < 2000


@pytest.mark.parametrize("clinic_id", WITH_LIBRARY)
def test_depth_is_still_demanded(clinic_id):
    """The guard. Brevity must not cancel the reason the block exists — a
    generic short reply was the ORIGINAL defect this block was written for, and
    trading one for the other would just swap which call goes wrong."""
    text = _rendered(clinic_id)
    assert "hallmark features" in text
    assert "would fit every condition equally" in text


def test_a_clinic_without_a_library_does_not_get_the_rule():
    """Containment, asserted rather than assumed. Vital Edge ships no
    condition_knowledge, so CONDITION FLUENCY does not render at all — it never
    carried the length pressure and must not carry the counterweight."""
    if _has_library("vital_edge"):
        pytest.skip("vital_edge gained a condition library; re-scope this test")
    text = _rendered("vital_edge")
    assert "CONDITION FLUENCY" not in text
    assert "AND IT IS SHORT" not in text


def test_the_faq_length_rule_is_still_there():
    """It was never wrong — only out of scope for the booking flow. Removing it
    while 'fixing' this would regress the seven-call review it was written for."""
    text = _rendered("northgate")
    assert "about twenty words, split it or cut it" in text
