# tests/regression/test_b57_theorem_cancel_gate.py
"""
B-57 — Theorem could not cancel, because its mandated CTA did not satisfy the
cancel gate.

`_cancel_retention_asked` required the word `"altogether"`, which is the
TEMPLATE prompt's retention question. Theorem's prompt mandates a different
sentence by name — *"the CTA is always 'shall I go ahead and cancel that?'"* —
so on that clinic `cancel_appointment` was refused every time the model obeyed
its own instructions. Same shape as B-36 R6 (booking, provisional wording) and
`CA23199d08` (reschedule): a single-literal gate against a sentence the prompt
composes.

Two halves, and the second is the one that actually completes a call:

1. the gate must SEE Theorem's CTA;
2. a caller answering that CTA with "yes please" must be able to consent.
   Demanding an explicit "cancel" token is right against the retention question,
   where "yes" answers an OR and identifies nothing — and wrong against a CTA
   that names one action. `B-44` recorded the cost: a caller stating the
   intention to cancel four times across 89 seconds.

The tests are written against the REAL predicates and the REAL rendered prompts,
not against re-typed literals. A literal test would have passed throughout the
period the defect was live, because both sides were internally consistent — it
is the COUPLING between prompt and gate that was broken.
"""
from __future__ import annotations

import hashlib
import re

import pytest

from app.media_streams import llm_stream as ls
from app.media_streams import turn_handler as th
from app.prompts.susie_system_prompt import build_system_prompt_parts


def _rendered(clinic_id: str) -> str:
    session = {
        "call_sid": "CAtest_b57",
        "clinic_id": clinic_id,
        "booking_flow_active": True,
        "collected": {},
    }
    static, dynamic = build_system_prompt_parts(session)
    return f"{static}\n\n{dynamic}"


def _session(last_bot_prompt: str = "", last_question: str = "") -> dict:
    return {
        ls.F_LAST_BOT_PROMPT: last_bot_prompt,
        ls.F_LAST_QUESTION: last_question,
    }


def _msgs(text: str) -> list:
    return [{"role": "user", "content": text}]


# The two sentences the two live prompt engines mandate. Both must open the gate.
THEOREM_CTA = (
    "So that's Sarah's appointment on Tuesday the 12th of May at two in the "
    "afternoon at Alcester — shall I go ahead and cancel that?"
)
TEMPLATE_RETENTION_Q = (
    "Would you like to reschedule this appointment, or cancel it altogether?"
)


# ---------------------------------------------------------------------------
# 1. The gate must see both mandated shapes
# ---------------------------------------------------------------------------
def test_theorem_mandated_cta_opens_the_cancel_gate():
    """The B-57 defect itself. False before the fix."""
    assert ls._cancel_retention_asked(THEOREM_CTA) is True


def test_the_retention_question_still_opens_the_cancel_gate():
    assert ls._cancel_retention_asked(TEMPLATE_RETENTION_Q) is True


@pytest.mark.parametrize(
    "clinic_id,literal",
    [
        ("theorem_v3", "shall i go ahead and cancel that?"),
        ("jv_v1", "cancel it altogether?"),
    ],
)
def test_the_prompt_and_the_gate_are_coupled(clinic_id, literal):
    """The test that would have CAUGHT B-57.

    Each prompt is read for the cancel wording it actually mandates, and that
    wording is put through the real gate. If a prompt is reworded so the gate
    can no longer see it, this fails here rather than on a live cancel.
    """
    low = _rendered(clinic_id).lower()
    assert literal in low, (
        f"{clinic_id} no longer mandates {literal!r} — if that is intended, the "
        f"new wording must be checked against _cancel_retention_asked here"
    )
    assert ls._cancel_retention_asked(literal) is True, (
        f"{clinic_id} is told to say {literal!r} and the cancel gate cannot see "
        f"it — cancel_appointment will be refused on every call"
    )


# ---------------------------------------------------------------------------
# 2. Controls — widening must not let another family's question cancel
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "prompt",
    [
        th._FALSE_CONFIRM_RESTEER,
        th._FALSE_RESCHEDULE_RESTEER,
        th._FALSE_CONFIRM_RESTEER_PROVISIONAL,
        "Shall I go ahead and book that in for you?",
        "So that's Sarah, Monday at three — shall I go ahead and move that?",
        "Shall I put that request through to Jonathan to confirm?",
    ],
)
def test_no_other_write_question_arms_the_cancel_gate(prompt):
    """B-36 R5's leak, on the arm B-57 widens: a booking or reschedule CTA must
    never leave a cancel CTA on record, or the caller's next yes could delete a
    real appointment."""
    assert ls._cancel_retention_asked(prompt) is False


@pytest.mark.parametrize(
    "statement",
    [
        "I'm cancelling that for you now.",
        "That's all done — your appointment has been cancelled.",
        "I can see an appointment on Tuesday the 12th — is that the right one?",
    ],
)
def test_a_statement_is_not_an_ask(statement):
    """The read-back and the claim are not consent questions. Requiring an ask
    shape AND a cancel verb is what keeps them out."""
    assert ls._cancel_retention_asked(statement) is False


def test_an_empty_prompt_does_not_arm_the_gate():
    assert ls._cancel_retention_asked("") is False
    assert ls._cancel_retention_asked(None) is False


# ---------------------------------------------------------------------------
# 3. Consent — the half that completes the call
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("reply", ["yes please", "yeah go ahead", "yes that's right"])
def test_a_clear_yes_consents_after_a_direct_cancel_cta(reply):
    """Theorem's caller says "yes please". Before the fix that blocked, the
    re-steer asked an OR-question, and "yes" blocked again — B-44's loop."""
    s = _session(last_bot_prompt=THEOREM_CTA)
    assert ls._cancel_reply_consents(_msgs(reply), s) is True


@pytest.mark.parametrize("reply", ["yes please", "yes", "go ahead", "ok"])
def test_a_bare_yes_still_does_not_consent_to_the_retention_question(reply):
    """FM-23's original reasoning, preserved. Against "reschedule, or cancel?" a
    yes identifies neither option, so it must not delete anything."""
    s = _session(last_bot_prompt=TEMPLATE_RETENTION_Q)
    assert ls._cancel_reply_consents(_msgs(reply), s) is False


@pytest.mark.parametrize("reply", ["yes please", "yes", "go ahead"])
def test_a_bare_yes_does_not_consent_after_the_cancel_resteer(reply):
    """The re-steer offers keeping the appointment, so it is an OR too."""
    s = _session(last_bot_prompt=th._FALSE_CANCEL_RESTEER)
    assert ls._cancel_reply_consents(_msgs(reply), s) is False


@pytest.mark.parametrize(
    "reply",
    [
        "no don't cancel it",
        "no, leave it",
        "actually keep it",
        "no",
        "yes but hang on",
        "can we reschedule instead",
        "move it to Thursday",
    ],
)
def test_negation_correction_and_retention_still_block_after_a_direct_cta(reply):
    """Everything FM-23 blocked, it still blocks. The direct-CTA arm is reached
    only after those checks — a destructive write must never fire while the
    caller is negating or changing their mind."""
    s = _session(last_bot_prompt=THEOREM_CTA)
    assert ls._cancel_reply_consents(_msgs(reply), s) is False


def test_an_unsettled_reply_blocks_rather_than_guessing():
    """L1 returns 'unsure' rather than a verdict, and unsure must not delete.
    No classifier is consulted here — this gate is deterministic."""
    s = _session(last_bot_prompt=THEOREM_CTA)
    assert ls._cancel_reply_consents(_msgs("what time was that again"), s) is False


def test_an_explicit_cancel_token_still_consents_with_no_session():
    """Back-compat: the session argument is optional and the token arm is
    unchanged, so nothing that consented before stops consenting."""
    assert ls._cancel_reply_consents(_msgs("yes cancel it"), None) is True
    assert ls._cancel_reply_consents(_msgs("cancel it please"), None) is True


def test_a_bare_yes_blocks_when_nothing_is_known_about_the_question():
    """Fail closed: with no session there is no evidence the CTA named one
    action, so the token requirement stands."""
    assert ls._cancel_reply_consents(_msgs("yes please"), None) is False


def test_consent_reads_the_uncapped_question_too():
    """B-38: last_bot_prompt is capped at 200 characters and a cancel read-back
    naming service, practitioner and site runs past it. If consent only read the
    capped copy, the truncated CTA would silently fall back to demanding the
    token — the same truncation defect, one gate later."""
    long_cta = (
        "So that's Sarah Kettleborough's Initial Assessment with Marcus at "
        "Flexspace Bolton on Tuesday the 12th of August at half past six in "
        "the evening, that's the forty minute musculoskeletal one — shall I go "
        "ahead and cancel that?"
    )
    assert len(long_cta) > 200, "fixture no longer exercises the cap"
    s = _session(last_bot_prompt=long_cta[:200], last_question=long_cta)
    assert ls._cancel_retention_asked(long_cta[:200]) is False, (
        "fixture drift: the truncation no longer removes the CTA"
    )
    assert ls._cancel_reply_consents(_msgs("yes please"), s) is True


# ---------------------------------------------------------------------------
# 4. Scope — no prompt moved
# ---------------------------------------------------------------------------
# B-57 is a gate fix. Every clinic's spoken script must be unchanged, or the fix
# leaked out of the gate. Re-baseline only with an owner-confirmed prompt change:
#   python -c "from tests.regression.test_b57_theorem_cancel_gate import _sha; \
#              print(_sha('theorem_v3'))"
# Re-pinned 2026-08-05 for B-39: the retention question was scoped to the
# cancel path and bounded to one ask, which moves jv_v1, vital_edge and
# theorem_v3 deliberately. demo and theorem must NOT move — they are
# FlowEngine clinics and render neither block.
UNMOVED_PROMPTS = {
    "demo": "17c7162e49200716",
    # Re-pinned 2026-08-05. BOOKING STEPS 1 gained a condition-led opening
    # exception, gated on the condition library — so jv_v1 moves and every
    # clinic without one (vital_edge, demo, theorem, theorem_v3) is
    # byte-identical, which is the containment claim this table exists for.
    # See tests/regression/test_condition_led_opening.py.
    "jv_v1": "b393a5c7c1c6c8e7",
    "theorem": "8565be9a48a7a9aa",
    "theorem_v3": "d5d26ee076213608",
    "vital_edge": "102b1509d8665af3",
}


def _sha(clinic_id: str) -> str:
    """Hash the rendered prompt with today's date redacted.

    The prompt interpolates the current date, so a raw hash of it changes every
    midnight. `test_b55_provisional_reschedule_closing` pins raw hashes and was
    consequently failing on four clinics on 2026-08-05 for no reason but the
    calendar — a scope guarantee that cries wolf daily is one nobody reads, and
    it was silently contributing 4 of the standing 99 baseline failures.

    Redacting weekday names, month names and digit runs makes the pin stable
    while still catching any change to the WORDING, which is what "did the fix
    leak into the spoken script" actually asks. The trade is that a change
    affecting only numbers would slip past — acceptable here, since the fix
    under test touches no prompt at all.
    """
    text = _rendered(clinic_id)
    text = _DATEISH_RE.sub("<date>", text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


_DATEISH_RE = re.compile(
    r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\b(?:january|february|march|april|may|june|july|august|september"
    r"|october|november|december)\b"
    r"|\d+",
    re.IGNORECASE,
)


@pytest.mark.parametrize("clinic_id,expected", sorted(UNMOVED_PROMPTS.items()))
def test_no_clinic_prompt_moved(clinic_id, expected):
    assert _sha(clinic_id) == expected
