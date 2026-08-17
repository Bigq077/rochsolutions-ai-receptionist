"""
Regression: the template clinics' RESCHEDULE / CANCEL flow must not assume a
caller ID exists.

`lookup_patient` keys on phone. A caller whose number is withheld can only reach
their own appointment by typing it, so every sentence in this flow that assumes
the number arrived from caller ID is a sentence that can strand them.

WHAT THIS IS AND IS NOT. Theorem had the dead-end version (`2652ca2`): its turn 2
asserted the number existed and gated the keypad behind "only if the caller
DECLINES that number", so with nothing to decline there was no recovery at all.
The template clinics were NEVER in that state — their CALL STATE block (ported in
`4cf79d9`) disclaims "every read-it-back instruction elsewhere in this prompt"
and mandates the keypad line directly, and that line arms keypad capture. So this
is hardening, not a dead-end fix, and the tests below are written to say which is
which — `test_call_state_still_disclaims_the_static_readback` is the one that
would catch a real regression, because it pins the mechanism that made the old
wording survivable.

The three sentences corrected on 2026-08-11:

  1. "Then READ THE BOOKING NUMBER BACK. You ALREADY HAVE it" — unconditional,
     and false for a withheld caller. Now branch (a).
  2. "Are you sure the number you're CALLING ON is the one your booking is
     under?" — a typed number is not one you are calling on.
  3. "phone=<the calling number>" on the re-lookup — same assumption.

Structural note: this block is built in `_spine(clinic, tk, dc)`, which takes no
session, and lands in the STATIC half of the prompt. It therefore cannot branch
on caller ID the way `_build_theorem_v3` does — both branches are always present
and CALL STATE selects between them. That is why (1) is phrased as a routing
instruction rather than as one rendered branch.
"""
from __future__ import annotations

import re

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import build_clinic_prompt

# Both clinics served by the template engine. A per-clinic prompt fix that
# reaches only one of them is the bug this repo keeps re-finding.
TEMPLATE_CLINICS = ["jv_v1", "vital_edge"]

# Decline / wrong-number keypad (caller already knows why). Kept for (a).
KEYPAD_LINE = (
    "No problem — go ahead and type the number on your keypad. "
    "You can press the star key to reset at any time."
)
# Withheld / no CLI (CA86dfad89 A9a): say why, then the same keypad arming.
KEYPAD_LINE_WITHHELD = (
    "I can't see a phone number on this call — could you type the number on "
    "your keypad? You can press the star key to reset at any time."
)


def _static(clinic_id: str, cli: str = "") -> str:
    session = {
        "call_sid": "CAtest_rc_nocli",
        "clinic_id": clinic_id,
        "collected": {},
        "twilio_from_local": cli,
        "twilio_from": cli,
    }
    return build_clinic_prompt(session, get_clinic(clinic_id))[0]


def _dynamic(clinic_id: str, cli: str = "") -> str:
    session = {
        "call_sid": "CAtest_rc_nocli",
        "clinic_id": clinic_id,
        "collected": {},
        "twilio_from_local": cli,
        "twilio_from": cli,
    }
    return build_clinic_prompt(session, get_clinic(clinic_id))[1]


def _flow(clinic_id: str) -> str:
    """The RESCHEDULE / CANCEL block only — assertions elsewhere are not ours."""
    text = _static(clinic_id)
    i = text.find("RESCHEDULE / CANCEL FLOW")
    assert i != -1, f"{clinic_id} renders no reschedule/cancel flow"
    return text[i:i + 5000]


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_the_flow_branches_on_whether_a_number_exists(clinic_id):
    flow = _flow(clinic_id)
    assert "(a) CALL STATE GIVES YOU A CALLER PHONE" in flow
    assert "(b) CALL STATE SAYS THERE IS NO CALLER ID" in flow


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_the_readback_claim_is_confined_to_the_caller_id_branch(clinic_id):
    """"You ALREADY HAVE it" must not be reachable before branch (a) opens.

    This is the actual defect: an unconditional claim that a number exists.
    """
    flow = _flow(clinic_id)
    claim = flow.find("You ALREADY HAVE it")
    branch_a = flow.find("(a) CALL STATE GIVES YOU A CALLER PHONE")
    assert claim != -1 and branch_a != -1
    assert branch_a < claim, (
        "the prompt asserts the caller's number exists before establishing "
        "that CALL STATE actually gave one — for a withheld caller that is "
        "false, and it contradicts the CALL STATE block"
    )


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_the_no_caller_id_branch_asks_for_the_keypad_and_arms_it(clinic_id):
    """The keypad line must satisfy the predicate that opens DTMF capture.

    Without this the caller types their number into a closed buffer and the
    digits are discarded — a silent failure that sounds like being ignored.
    """
    from app.media_streams.connection import _is_keypad_arming_line

    flow = _flow(clinic_id)
    b = flow.find("(b) CALL STATE SAYS THERE IS NO CALLER ID")
    branch_b = flow[b:b + 900]

    assert KEYPAD_LINE_WITHHELD in branch_b, (
        "branch (b) does not mandate the withheld keypad line"
    )
    assert _is_keypad_arming_line(KEYPAD_LINE_WITHHELD), (
        "the mandated withheld line no longer arms keypad capture — typed "
        "digits will land in a closed buffer"
    )


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_the_no_caller_id_branch_rules_out_the_wrong_recoveries(clinic_id):
    """Named, because the model reaches for these three when it has nothing."""
    b = _flow(clinic_id).find("(b) CALL STATE SAYS THERE IS NO CALLER ID")
    branch_b = _flow(clinic_id)[b:b + 900]
    assert "NOTHING to read" in branch_b
    assert "Do NOT say any digits" in branch_b
    assert "number you're calling from" in branch_b  # named as forbidden


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_no_question_assumes_the_number_came_from_caller_id(clinic_id):
    """Sentences 2 and 3. A typed number is not one you are 'calling on'.

    Scoped to sentences that ASK or ACT on the number; the branch (b) text
    names these phrasings in order to forbid them, which is the opposite bug.
    """
    flow = _flow(clinic_id)
    for sentence in re.split(r"(?<=[.!?])\s+", flow):
        if "do NOT" in sentence or "never" in sentence.lower():
            continue  # a prohibition naming the phrasing is correct
        assert not re.search(r"you're calling on|the calling number", sentence, re.I), (
            "this sentence assumes the number arrived from caller ID: "
            f"{' '.join(sentence.split())[:160]}"
        )


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_call_state_still_disclaims_the_static_readback(clinic_id):
    """The mechanism that made the OLD wording survivable — do not lose it.

    The static half cannot branch on session, so both branches are always
    present. What actually tells the model which one it is in is this CALL
    STATE line. If it ever narrows, the static (a) text becomes reachable for
    a withheld caller again and this whole flow regresses to Theorem's
    dead end.
    """
    dyn = _dynamic(clinic_id, cli="")
    assert "NO caller ID on this call" in dyn
    assert "does NOT apply on this call" in dyn
    assert KEYPAD_LINE_WITHHELD in dyn

    # ...and it must NOT fire when a caller ID is present, or the common case
    # gets told it has no number.
    assert "NO caller ID on this call" not in _dynamic(clinic_id, cli="07502211207")
