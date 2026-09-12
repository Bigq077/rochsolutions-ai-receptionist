"""D-t: a timing preference from an EARLIER turn is echoed back and confirmed,
never carried from the model's memory into date_hint.

`CA5c69c585` (12 Sep 17:36) and `CAddd98ce0` (12 Sep 20:52), northgate demo
line, the same shape both times:

    caller (symptom turn): "... i'm free tuesdays and thursdays but only
                            after 4 i also need to know about parking"
    Susie : parking answer, then "would you like to get booked in ...?"
    caller: "uh yes go for it"
    tool  : check_availability(date_hint="Tuesdays and Thursdays after 4pm")
                                ^ the MODEL's paraphrase, two turns later

The prompt's Step 2 said SKIP the timing question whenever any signal had
"ALREADY" been given, so the model obeyed and built the date_hint from
memory. "after 4pm" parsed to a 16:00 clock time (the caller had named no
clock time), the one-slot arm (D-r) fired on it, the fact guard retracted
the sentence (16:00 is in no payload), and defects A, B, E and L followed
from that one hint. Owner decision 12 Sep (D-t):

  * timing given IN THE SAME BREATH as the booking request, or urgency
    wherever it was said, still skips the question -- CA3e342642 (JV Bolton,
    24 Jul, 33 s lost re-asking "as soon as possible") stays fixed;
  * timing given in an EARLIER turn is ECHOED: "You mentioned <their words>
    -- shall I look at those, or is there another time that suits?", and the
    caller's answer -- this turn's utterance -- is the date_hint.

Semantic assertions on the rendered spine, in the style of
test_asap_is_a_timing_answer.py, so a rewording that keeps the rule passes
and a rewording that loses it fails.
"""
from __future__ import annotations

import re

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import build_clinic_prompt

TEMPLATE_CLINICS = ["jv_v1", "vital_edge", "northgate"]


@pytest.fixture(scope="module", params=TEMPLATE_CLINICS)
def static_prompt(request):
    static, _dynamic = build_clinic_prompt({}, get_clinic(request.param))
    return static


def _step_two(static_prompt: str) -> str:
    """Step 2's text, whichever modality variant this clinic renders."""
    m = re.search(r"\n2\. (?:MODALITY THEN TIMING|TIMING)\..*?\n3\. ", static_prompt, re.S)
    assert m, "Step 2 not found"
    return m.group(0)


def test_step_two_echoes_an_earlier_turns_timing(static_prompt):
    step_two = _step_two(static_prompt)
    assert "ECHO, DON'T SKIP" in step_two
    assert "EARLIER turn" in step_two
    assert "You mentioned" in step_two
    assert "WAIT" in step_two


def test_the_skip_is_now_same_breath_or_urgency_only(static_prompt):
    step_two = _step_two(static_prompt)
    assert "IN THE SAME BREATH" in step_two
    assert "urgency" in step_two.lower()
    # The old wording that let the model reuse anything heard at any point.
    assert "has ALREADY given" not in step_two, (
        "Step 2 still skips the timing question on any signal heard earlier "
        "in the call -- the CA5c69c585 shape"
    )


def test_step_three_forbids_a_paraphrased_date_hint(static_prompt):
    m = re.search(r"\n3\. Treat the answer.*?\nTIME PREFERENCE GATE", static_prompt, re.S)
    assert m, "Step 3 not found"
    step_three = m.group(0)
    assert "never your own paraphrase" in step_three
    assert "EARLIER turn" in step_three
    assert "IN THE SAME BREATH" in step_three


def test_the_gate_agrees_with_step_two(static_prompt):
    i = static_prompt.index("TIME PREFERENCE GATE — applies ONLY once booking intent")
    gate = static_prompt[i:i + 900]
    assert "SAME BREATH" in gate
    assert "EARLIER turn is echoed" in gate
    assert "any time signal is sufficient; call" not in gate


def test_urgency_still_skips_wherever_it_was_said(static_prompt):
    """CA3e342642 must not reopen: ASAP is a complete answer from any turn."""
    assert "URGENCY IS A COMPLETE TIMING ANSWER" in static_prompt
    i = static_prompt.index("URGENCY IS A COMPLETE TIMING ANSWER")
    bullet = static_prompt[i:i + 700]
    assert "do NOT ask the timing question" in bullet
    assert "wherever the caller said it" in bullet
