"""A4 · confirmation loop — the phone-confirm matcher missed "a good number".

`CA587b103bd4a116b0a34b590f1893dbc4` (31 Jul 2026, build f0adf21). The call
reported `success=True, booking_confirmed=True` — A4 is invisible to every
outcome metric — but the caller answered the same two questions twice:

    17 Susie   …I've got you on 07502 211 207 — is that the best number…?
    18 caller  um yes that's a good number          <- _is_use_this_number False
    19 Susie   So that's Quentin, Tuesday… shall I go ahead and book that in?
    20 caller  yeah go ahead
    21 Susie   I've got you on 07502 211 207 …      <- verbatim repeat of 17
    22 caller  yes that's the best number           <- matched; loop escaped
    23 Susie   So that's Quentin, Tuesday…          <- verbatim repeat of 19

Turn 18 failed on both branches: "good number" was absent from
`_USE_THIS_NUMBER_SIGNALS`, and at six words the <=3-word bare-affirmative
fallback could not rescue it. `phone_confirmed` stayed unset, `book_appointment`
refused the write, the model re-asked — and because the booking-confirmation
gate in `llm_stream.py` requires its own question in the CURRENT turn, one
missing adjective cost FOUR turns rather than two.

This was the fourth patch to the same hand-maintained list ("best one" 7 Jul,
"correct number"/"right number" 30 Jul after a caller looped until they hung
up), so the fix covers the adjective SLOT rather than adding a fifth literal.

The false-negative half of this file matters more than the parametrised
acceptances: a miss costs a re-ask, a false accept books an unreachable patient.
The <=3-word cap is kept for exactly that reason and is asserted here.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import _is_use_this_number


# ── The five historical failures, all in one place ──────────────────────────
# Every phrase that has ever cost a live caller a loop. The register calls this
# regression test non-negotiable; parametrising it is what stops patch five.

@pytest.mark.parametrize("answer", [
    pytest.param("um yes that's a good number", id="CA587b103b-31jul"),
    pytest.param("yeah that's the best one",    id="07jul"),
    pytest.param("it is",                       id="27jul-verify-call"),
    pytest.param("yes that's the correct number", id="CA3145c15f-30jul"),
    pytest.param("yes",                         id="bare-yes-26jul"),
])
def test_every_historically_failing_phrase_confirms(answer):
    assert _is_use_this_number(answer) is True, (
        f"{answer!r} regressed — this phrase has already cost a live caller a loop"
    )


# ── The adjective slot ──────────────────────────────────────────────────────

@pytest.mark.parametrize("adj", [
    "good", "best", "right", "correct", "fine", "great", "perfect", "ideal",
    "only", "usual", "main", "current",
])
def test_positive_adjective_before_number_confirms(adj):
    assert _is_use_this_number(f"yes that's my {adj} number") is True


def test_adjective_slot_works_without_an_affirmative_word():
    """The caller need not say "yes" at all — echoing the noun phrase is an
    answer to "is that the best number?"."""
    assert _is_use_this_number("that's a good number") is True
    assert _is_use_this_number("that's the one i use") is False  # not the slot


# ── Leading disfluencies ────────────────────────────────────────────────────

@pytest.mark.parametrize("answer", [
    "um yeah that's fine",
    "uh um yes",
    "erm yes please",
    "well yeah sure",
    "oh yes",
])
def test_leading_filler_run_does_not_push_an_answer_out_of_range(answer):
    """The word count is an artefact of the noise, not of the answer."""
    assert _is_use_this_number(answer) is True


def test_a_turn_that_is_only_filler_is_not_a_confirmation():
    for noise in ("um", "uh um", "erm", "hmm"):
        assert _is_use_this_number(noise) is False, noise


# ── The half that matters more: nothing new is falsely accepted ─────────────

@pytest.mark.parametrize("answer", [
    "no a different number",
    "no, use another number",
    "that's the wrong number",
    "that's not a good number",
    "it's not the best number",
    "no that's not right",
    "um no that's not a good number",
    "can you use another number",
    "no",
    "nope",
])
def test_negative_intent_is_never_confirmed(answer):
    assert _is_use_this_number(answer) is False, (
        f"{answer!r} would book an unreachable patient"
    )


def test_the_word_cap_still_blocks_a_long_turn_containing_yes():
    """The cap is the guard against "yes, BUT…". Widening the adjective slot
    must not have widened this: a long turn that merely contains an affirmative
    still falls through to the LLM / keypad path."""
    assert _is_use_this_number(
        "yes but call me on my work phone instead"
    ) is False
    assert _is_use_this_number(
        "yeah i'd like to book that in and i'll give you a number after"
    ) is False


def test_filler_stripping_cannot_hide_a_negative():
    """The negative guard runs on the ORIGINAL text, before stripping."""
    assert _is_use_this_number("um no different number") is False
    assert _is_use_this_number("uh not that one") is False


def test_digits_read_aloud_are_not_a_confirmation():
    """A caller who declines and dictates must reach the digit path, not the
    confirm path — "oh" is a filler AND a spoken zero."""
    assert _is_use_this_number("oh seven five oh two two one one two oh seven") is False


# ── The deterministic gate must agree ───────────────────────────────────────
# The register claimed flow._HG_YES "also misses this phrase". It does not —
# _hg_bare_yes catches the "yes" in turn 18 with a word-bounded regex, so the
# defect was confined to the LLM path. What the flow gate DID miss is the same
# phrase without an affirmative word, which is what the semantic list now adds.

def _flow_gate_accepts(text: str) -> bool:
    """Replicate the CONFIRM_PHONE accept condition from flow.handle_transcript.

    The gate is inline in a 15k-line method, so it cannot be imported. The
    source assertions below are what keep this replica honest.
    """
    import re
    hg_bare_yes = bool(re.search(r"\b(?:yes|yeah|yep|yup)\b", text))
    hg_no = bool(re.search(r"\bno\b", text))
    semantic = (
        any(p in text for p in ("good number", "a good one",
                                "that's the best number", "that's the one"))
        and not hg_no
        and not any(n in text for n in ("not", "different", "another", "wrong"))
    )
    return (hg_bare_yes or semantic) and not hg_no


def test_flow_gate_already_accepted_the_observed_turn():
    """Documents the correction to the register: turn 18 passed this gate."""
    assert _flow_gate_accepts("um yes that's a good number") is True


def test_flow_gate_now_accepts_the_adjective_without_yes():
    assert _flow_gate_accepts("that's a good number") is True
    assert _flow_gate_accepts("that's not a good number") is False


def test_flow_semantic_list_actually_carries_the_new_phrases():
    """Guards the replica above against drifting from the real source."""
    import inspect
    from app.media_streams import flow as _flow

    src = inspect.getsource(_flow.FlowEngine.handle_transcript)
    assert '"good number", "a good one",' in src, (
        "flow._SEMANTIC_YES_PHRASES no longer carries the A4 additions — "
        "the replica in this file is now lying"
    )


def test_both_gates_agree_on_every_historical_phrase():
    """The divergence between the two implementations is the standing hazard;
    assert they agree rather than only that each works."""
    for answer in (
        "um yes that's a good number",
        "yeah that's the best one",
        "yes that's the correct number",
        "that's a good number",
    ):
        assert _is_use_this_number(answer) is _flow_gate_accepts(answer), (
            f"gates disagree on {answer!r}"
        )
