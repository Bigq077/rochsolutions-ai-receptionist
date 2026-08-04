# tests/regression/test_reason_question_once.py
"""
The reason question: right wording per clinic, and asked exactly once.

`CA86c320ef036b39946ed0bbc47d2b0c14`, 4 Aug 2026, Vital Edge, live:

    caller  "um yeah like a deep tissue massage"
    Susie   "Right — What's the appointment for?"                      <- ask 1
    caller  "um i'd just like to book a massage please"
    Susie   "Noted — and is there a particular area or reason for the
             massage, like back tension, general stress, or something
             else?"                                                    <- ask 2
    caller  "um just general stress"            -> finally the real reason

Two separate problems, and the useful one is counter-intuitive:

1. **The good question was not in the prompt.** Ask 1 is the mandated literal
   (`clinic_template_prompt.py`, rule 1b). Ask 2 — the one that actually got an
   answer, because it offers examples — was improvised by the model and appears
   nowhere in the template or clinic.json. Nothing guaranteed it would ever be
   asked that way again.

2. **"Ask ONCE" was already the rule.** Rule 1b says so in those words. It was
   not honoured, and prompt text cannot honour it: the model composes each turn
   without a reliable memory of having asked. Once is a property of the CALL.

So the wording moves into clinic.json (physio callers arrive with a problem;
massage callers arrive with tension or stress, and a bare "what's it for?" reads
as a blank page), and the once-only guarantee moves into engine state — the same
division of labour as `capture_duration_choice` and the B-42/B-54 latches.

Also fixed: rule 1b counted "a service by name" as the reason. For a massage-only
clinic that is wrong — "a deep tissue massage" says which treatment, not what it
is for, and it is the second that the session is planned around. Made per-clinic,
defaulting to the old behaviour.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams.llm_stream import note_reason_question_asked
from app.prompts.clinic_template_prompt import build_clinic_prompt

VE, JV = "vital_edge", "jv_v1"


def _static(cid):
    return build_clinic_prompt({}, get_clinic(cid))[0]


def _dynamic(session, cid):
    return build_clinic_prompt(session, get_clinic(cid))[1]


# ── the wording, per clinic ────────────────────────────────────────────────

def test_vital_edge_asks_the_personalised_question():
    s = _static(VE)
    assert "particular area or reason for the massage" in s, (
        "Vital Edge is back to the bare 'What's the appointment for?', which on "
        "CA86c320ef the caller answered by restating booking intent"
    )
    assert "back tension, general stress" in s, (
        "the examples are the point — they are what turned a blank question "
        "into an answerable one"
    )


def test_vital_edge_no_longer_carries_the_generic_question():
    assert "'What's the appointment for?'" not in _static(VE)


def test_the_physio_clinics_keep_the_original_wording():
    """Your callers arrive with a problem; ours arrive with stress. The default
    must not move, or this becomes a change to a live clinic nobody asked for."""
    assert "'What's the appointment for?'" in _static(JV)
    assert "particular area or reason for the massage" not in _static(JV)


# ── whether naming a service settles it ────────────────────────────────────

def test_a_service_name_is_not_the_reason_for_a_massage_clinic():
    s = _static(VE)
    assert "Naming a SERVICE is NOT the reason here" in s
    assert "or a service by name" not in s, (
        "Vital Edge still treats 'a deep tissue massage' as the reason, so the "
        "question is skipped and the therapist gets the treatment name instead "
        "of the problem"
    )


def test_a_service_name_is_still_the_reason_for_physio():
    s = _static(JV)
    assert "or a service by name" in s
    assert "Naming a SERVICE is NOT the reason here" not in s


# ── ask-once: the engine half ──────────────────────────────────────────────

@pytest.mark.parametrize("spoken", [
    "Right — What's the appointment for?",                       # ask 1, verbatim
    "Noted — and is there a particular area or reason for the massage, "
    "like back tension, general stress, or something else?",     # ask 2, verbatim
    "Is there a particular area or reason for the massage — like back "
    "tension, general stress, or something else?",               # the new literal
    "What brings you in?",
    "What's it for?",
])
def test_every_form_of_the_question_latches(spoken):
    """Matched on INTENT, not on one clinic's literal. The second ask on
    CA86c320ef shared no wording with the first — a literal match would have
    missed precisely the turn that mattered."""
    session = {}
    assert note_reason_question_asked(session, spoken) is True
    assert session["_reason_question_asked"] is True


@pytest.mark.parametrize("spoken", [
    "What day works best for you?",
    "So that's Friday the 7th at eleven — shall I book that in?",
    "Could I take your first name and surname?",
    "Thanks Quentin.",
    "",
    None,
])
def test_ordinary_turns_do_not_latch(spoken):
    """A false latch suppresses a question that was never asked, and the reason
    never gets collected — book_appointment then REFUSES for want of one."""
    session = {}
    assert note_reason_question_asked(session, spoken) is False
    assert "_reason_question_asked" not in session


def test_the_latch_holds_for_the_rest_of_the_call():
    session = {}
    note_reason_question_asked(session, "What's the appointment for?")
    assert note_reason_question_asked(session, "What day suits you?") is True
    assert session["_reason_question_asked"] is True


def test_the_latch_reaches_the_model_as_call_state():
    """The latch is inert unless the prompt tells the model about it."""
    dyn = _dynamic({"clinic_id": VE, "_reason_question_asked": True}, VE)
    assert "ALREADY been asked" in dyn
    assert "do NOT ask what the appointment is for again" in dyn


def test_no_call_state_line_before_it_is_asked():
    assert "ALREADY been asked" not in _dynamic({"clinic_id": VE}, VE)


def test_every_other_clinic_renders_byte_identical():
    """The containment claim, checkable rather than asserted.

    The first version of this change put the once-only tightening in SHARED
    rule 1b text, which moved jv_v1's prompt — a live physio clinic getting a
    prompt edit to fix a massage clinic's defect. Both the wording and the
    once-only guard are now gated on the clinic supplying its own
    reason_question, so a clinic that never asked for any of this is untouched.
    """
    from tests.regression.test_b55_provisional_reschedule_closing import (
        UNCHANGED_CLINIC_PROMPTS, _sha,
    )
    for clinic_id, expected in UNCHANGED_CLINIC_PROMPTS.items():
        assert _sha(clinic_id) == expected, (
            f"{clinic_id}'s prompt moved. The reason-question change is scoped "
            "to clinics that opt in via prompt_facts.reason_question — if this "
            "fails, it has leaked into shared text again."
        )


def test_the_gate_is_the_clinic_opting_in():
    """A clinic with no custom wording gets neither the new question nor the
    once-only instruction — one switch, not two that can drift apart."""
    from app.prompts.clinic_template_prompt import build_clinic_prompt as _b
    jv = _b({}, get_clinic(JV))[0]
    assert "do not ask a second, differently-worded" not in jv
    ve = _static(VE)
    assert "do not ask a second, differently-worded" in ve


def test_the_latch_is_wired_into_the_turn_loop():
    """Without the call site every test above passes while nothing latches."""
    import inspect
    from app.media_streams import llm_stream as ls
    assert "note_reason_question_asked(session, _display_reply)" in inspect.getsource(ls), (
        "the latch is orphaned — 'ask ONCE' is back to being only an instruction"
    )
