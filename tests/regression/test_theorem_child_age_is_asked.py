"""Susie must ASK a child's age, and be told when that age is under the floor.

Background — two live calls on build 8819dc50bd4b, 2026-08-25. A parent rang
Mark's line about their son and Susie went from symptom to booking without ever
establishing how old he was.

Nothing had been deleted. Until that morning the CLINIC block read "Adults
fifteen and over only", which the model treated as a hard restriction and
checked against off its own bat; correcting the age policy to 7 removed its
motivation to check. The ask had never been a rule, which is why it disappeared
without anything going red.

That matters because the deterministic under-age gate
(`connection.capture_under_age` -> `_under_age_declared` -> the
`book_appointment` refusal in `llm_stream`) only arms from an age the caller
STATES. With nothing prompting them to state one, the gate sat dormant on
exactly the calls it exists for: an under-7 could have been booked.

Two halves, both pinned here:

  1. the ASK, in the theorem_v3 policies block; and
  2. the TELL — a CALL STATE clause naming the declared age, which Theorem
     did not have. The write-time refusal is clinic-agnostic and did fire, but
     it fires at the END: without the clause a parent is walked through day,
     time, name and number for an appointment that cannot exist.

Emergent behaviour is not a safeguard. This file is here so that the next
person who reworks the age wording finds out immediately if the ask goes with
it.
"""

from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams.connection import capture_under_age
from app.prompts.susie_system_prompt import build_system_prompt_parts
from app.tools.receptionist_tools import minimum_age_years

# theorem_v3 is the id that routes to `_build_theorem_v3`; plain "theorem"
# renders the other, unused builder. Skipped rather than failed on a branch
# that carries no Theorem clinic, so this file can be ported as-is.
CLINIC_ID = "theorem_v3"

pytestmark = pytest.mark.skipif(
    minimum_age_years(get_clinic(CLINIC_ID) or {}) is None,
    reason="no Theorem clinic with a configured minimum age on this branch",
)


def _parts(**session_extra) -> tuple:
    session = {
        "call_sid": "CAtest_child_age",
        "clinic_id": CLINIC_ID,
        "collected": {},
    }
    session.update(session_extra)
    return build_system_prompt_parts(session)


# ---------------------------------------------------------------------------
# 1. The ASK
# ---------------------------------------------------------------------------
def test_prompt_tells_susie_to_ask_a_child_s_age():
    static, _ = _parts()
    assert "BOOKING FOR A CHILD" in static, (
        "the child age question is gone from the theorem_v3 prompt. It is the "
        "only thing that makes the caller state an age, and the under-age gate "
        "cannot arm without one."
    )


def test_the_ask_covers_the_words_a_parent_actually_uses():
    static, _ = _parts()
    rule = static[static.index("BOOKING FOR A CHILD"):][:900]
    # "my son was playing football" was the live call. A trigger list that
    # only caught the word "child" would have missed it.
    for word in ("son", "daughter", "child", "kid", "grandson"):
        assert word in rule, f"{word!r} no longer triggers the age question"


def test_susie_is_not_told_to_lead_with_the_minimum_age():
    """The ask must not become a challenge.

    Most children are over 7. Opening with the floor turns an ordinary booking
    question into a refusal forming, which is what the 14:51 call sounded like
    when the prompt still said fifteen and the caller rang off.
    """
    static, _ = _parts()
    rule = static[static.index("BOOKING FOR A CHILD"):][:900]
    assert "Do NOT volunteer the minimum age" in rule


# ---------------------------------------------------------------------------
# 2. The TELL
# ---------------------------------------------------------------------------
def test_call_state_is_silent_until_an_age_is_actually_declared():
    _, dynamic = _parts()
    assert "UNDER this clinic" not in dynamic, (
        "the under-age clause rendered without any declared age — it would "
        "refuse every caller"
    )


def test_call_state_names_the_age_and_forbids_the_walk_up():
    _, dynamic = _parts(_under_age_declared=5)
    assert "UNDER this clinic" in dynamic
    assert "5" in dynamic
    # The point of the clause: not "decline at the end", but "do not collect".
    for forbidden in ("Do not offer times", "do not ask for a day"):
        assert forbidden in dynamic


def test_the_minimum_is_read_from_config_never_written_as_a_literal():
    """A safeguarding sentence must not depend on copywriting.

    If the clinic changes its floor, the spoken number has to follow it. This
    is the same rule the engine-side refusal in llm_stream already keeps.
    """
    expected = minimum_age_years(get_clinic(CLINIC_ID) or {})
    _, dynamic = _parts(_under_age_declared=1)
    assert f"minimum age of {expected}" in dynamic
    assert f"aged {expected} and over" in dynamic


def test_the_decline_keeps_the_gp_referral_the_clinic_asked_for():
    """canonical.py AGE_POLICY and clinic_config children_policy both send
    under-7s to the clinic AND to their GP. Naming only the floor would drop
    half of the clinic's own answer."""
    _, dynamic = _parts(_under_age_declared=3)
    assert "GP" in dynamic
    assert "paediatric" in dynamic


# ---------------------------------------------------------------------------
# 3. The chain that connects them
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("he's five", 5),
        ("he's 5", 5),
        ("she's six years old", 6),
        # At and above the floor: no gate, no decline.
        ("he's seven", None),
        ("he's 12", None),
        # Ages are not the only small numbers in a booking call.
        ("can we do half past five", None),
        ("yeah about five o'clock", None),
        ("my son was playing football", None),
    ],
)
def test_capture_arms_from_what_a_parent_says(utterance, expected):
    session = {"clinic_id": CLINIC_ID}
    assert capture_under_age(session, utterance) == expected
    assert session.get("_under_age_declared") == expected


def test_capture_reaches_call_state_end_to_end():
    """The two halves are wired to each other, not just individually correct."""
    session = {
        "call_sid": "CAtest_child_age",
        "clinic_id": CLINIC_ID,
        "collected": {},
    }
    capture_under_age(session, "he's four")
    _, dynamic = build_system_prompt_parts(session)
    assert "UNDER this clinic" in dynamic
    assert "4" in dynamic
