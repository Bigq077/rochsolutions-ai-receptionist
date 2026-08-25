"""Theorem sees children from 7, and nothing in the engine enforced it.

Until this port, `theorem-onboarding` had no age gate whatsoever: no
`_AGE_PATTERNS`, no `capture_under_age`, no `under_age_blocks_booking`. The
clinic's own policy machinery could not help either — `never_autobook` is read
by no Python at all, and `evaluate_policy_gate` is reachable only from
`flow.py`, which no live clinic enters. The only thing between an under-7 and a
confirmed booking was the model choosing to decline.

Two properties matter here and they pull against each other:

  * a false NEGATIVE leaves the prompt's own decline in place — degraded, but
    the behaviour that existed before;
  * a false POSITIVE refuses a legitimate adult booking and gives the caller no
    way to talk past it, on a live clinic line.

So the detector is deliberately narrow — a bare number is never an age — and
the second half of this file is the one that protects Mark's revenue.

The minimum is DERIVED from config everywhere it is spoken. The wording used to
hardcode "18", which was true only of Vital Edge; on this branch that would
have refused correctly and then quoted a policy Mark does not hold.
"""

import pytest

from app.clinic_config import get_clinic
from app.media_streams.connection import capture_under_age
from app.media_streams.llm_stream import under_age_blocks_booking
from app.tools.receptionist_tools import (
    minimum_age_years,
    under_age_from_utterance,
)


# ── the policy is configured, and inherited by the live line ────────────────

@pytest.mark.parametrize("clinic_id", ["theorem", "theorem_v2", "theorem_v3"])
def test_the_minimum_is_seven_on_every_theorem_id(clinic_id):
    """theorem_v3 is the live patient line (+447380841468). It inherits by
    deepcopy from "theorem", so the key must be declared above that copy."""
    assert minimum_age_years(get_clinic(clinic_id)) == 7


def test_the_other_clinics_are_untouched():
    # vital_edge keeps its own, different minimum — proving the number is read
    # per clinic and not baked into the engine.
    assert minimum_age_years(get_clinic("vital_edge")) == 18
    # jv_v1's stated policy is the OPPOSITE — "No minimum age". Absent means no
    # gate, and this key must never appear there.
    assert minimum_age_years(get_clinic("jv_v1")) is None


# ── it arms when it should ──────────────────────────────────────────────────

@pytest.mark.parametrize("utterance,expected", [
    ("my son is 5", 5),
    ("he's five", 5),
    ("she's 6", 6),
    ("my daughter is four", 4),
    ("the patient is 3", 3),
    ("aged 6", 6),
    ("he is 5 years old", 5),
])
def test_an_under_seven_is_detected(utterance, expected):
    assert under_age_from_utterance(get_clinic("theorem_v3"), utterance) == expected


# ── it does NOT arm when it must not ────────────────────────────────────────
# Every one of these refusing a booking would be a live revenue loss with no
# way for the caller to recover, so they are pinned individually.

@pytest.mark.parametrize("utterance", [
    "I'm 34", "he's 45",
    "my son is 12", "she's 9", "he's seven",      # at/above the minimum
    "can you do 5 o'clock", "half past 5", "quarter to 6", "at 6pm",
    "the 5th of September", "5 minutes",
    "my appointment is 5", "the best time is 6",  # subject is not a person
    "number 5 Church Street", "07380 841468",
    "6", "five",                                  # a bare number is never an age
])
def test_these_are_not_ages_and_must_not_block_a_booking(utterance):
    assert under_age_from_utterance(get_clinic("theorem_v3"), utterance) is None


def test_exactly_the_minimum_is_allowed():
    """7 is old enough. An off-by-one here turns away the clinic's youngest
    real patients."""
    assert under_age_from_utterance(get_clinic("theorem_v3"), "he's 7") is None
    assert under_age_from_utterance(get_clinic("theorem_v3"), "he's seven") is None
    assert under_age_from_utterance(get_clinic("theorem_v3"), "he's 6") == 6


# ── the latch, and the block ────────────────────────────────────────────────

def test_capture_latches_and_blocks_the_booking():
    session = {"clinic_id": "theorem_v3"}
    assert capture_under_age(session, "my son is 5") == 5
    assert session["_under_age_declared"] == 5
    assert under_age_blocks_booking(session) is True


def test_the_latch_survives_later_numbers_in_the_call():
    """An age is a fact about the caller, not a preference. A later utterance
    full of numbers must not unlatch a safeguarding decision."""
    session = {"clinic_id": "theorem_v3"}
    capture_under_age(session, "my son is 5")
    capture_under_age(session, "can you do 3 o'clock on the 12th")
    assert session["_under_age_declared"] == 5
    assert under_age_blocks_booking(session) is True


def test_an_adult_booking_is_never_blocked():
    session = {"clinic_id": "theorem_v3"}
    capture_under_age(session, "I'm 34 and I'd like Tuesday")
    assert not session.get("_under_age_declared")
    assert under_age_blocks_booking(session) is False


def test_capture_never_raises_on_a_broken_session():
    """A failure here must leave the prompt's decline in force, not drop the
    call."""
    assert capture_under_age({"clinic_id": "does_not_exist"}, "my son is 5") is None
    assert capture_under_age({}, "my son is 5") is None
    assert capture_under_age({"clinic_id": "theorem_v3"}, "") is None


# ── the refusal names SEVEN, not eighteen ───────────────────────────────────

def test_the_call_state_line_names_the_clinics_own_minimum():
    from app.prompts.susie_system_prompt import build_system_prompt_parts

    session = {"clinic_id": "theorem_v3", "_under_age_declared": 5}
    parts = build_system_prompt_parts(session)
    text = "\n".join(p for p in parts if isinstance(p, str)) \
        if isinstance(parts, (list, tuple)) else str(parts)

    assert "minimum age of 7" in text
    assert "aged 7 and over" in text
    assert "18" not in text.split("minimum age of")[1][:200], (
        "the under-age wording quotes 18 — that is Vital Edge's policy, not "
        "Mark's, and it would refuse correctly while citing a rule he does not have"
    )


def test_the_tool_refusal_derives_the_minimum_rather_than_hardcoding_it():
    import inspect
    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    assert "under_age_declined" in src
    assert 'aged {_min} and over' in src, (
        "the refusal message must interpolate the clinic's own minimum; a "
        "literal here is how the 18 got quoted to a clinic whose minimum is 7"
    )
    assert "aged 18 and over" not in src


def test_the_gate_is_first_in_the_book_appointment_chain():
    """Theorem short-circuits to the Acuity executor. If this branch sat after
    the provider split it would never run on this clinic."""
    import inspect
    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream._execute_tools) \
        if hasattr(llm_stream, "_execute_tools") else inspect.getsource(llm_stream)
    i_age = src.index("under_age_blocks_booking(session)")
    i_slot = src.index("_slot_date_disagrees_with_speech(args, session)")
    assert i_age < i_slot, (
        "the under-age gate must be the FIRST book_appointment branch — there "
        "is no point validating a slot for an appointment that cannot happen"
    )
