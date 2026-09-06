"""
A complaint volunteered mid-call is the reason, even if nobody asked.

JV (jv_v1), 2026-09-05, build ed7f5c0ce1c0 -- the first call on the branch
after `ed7f5c0c` took the demo line's screening posture onto that patient
line. The turn order, verbatim:

    turn 1  "um yeah i'd like to know about pricing at your clinic"
    turn 2  (a location question)
    turn 3  "okay um yeah essentially my lower back's been really bad and
             my leg's gone numb"

and the call ended

    00:00:15.856  pre-summary reason: collected=None session=None -> None
    00:00:22.650  Row built -- outcome=abandoned name=Quentin Rook phone=yes
    00:00:24.446  Sheets append ok tab='CallSummaries' rows=1

Marcus's sheet -- JV's Sheets credentials work where the demo line's do not
-- got a 101-second row with a name, a number and no reason on it.

BOTH EXISTING DOORS WERE CORRECT AND BOTH WERE INERT.

`commit_opening_reason` reads `opening_utterance`, which
`note_opening_utterance` latches ONCE and never overwrites. Turn 1 was
substantive, so the latch was spent on the pricing question -- which carries
no complaint.

`commit_reason_answer` fires only while `_reason_answer_pending` is armed, and
only `note_reason_question_asked` arms it. Susie went from empathy straight to
the booking offer, so the question was never asked.

WHAT MADE IT REACHABLE. A third capture site existed by accident: the
clinical-screening short-circuit in connection.py calls both committers
(B-136/B-137), and an utterance that arms a screen is very often the same one
that describes the complaint -- it has to, to trigger a screen at all. On the
old posture "lower back ... leg's gone numb" armed `cauda_equina` and was
captured there. `ed7f5c0c` switched the screens off and deleted a capture path
nobody had listed as one.

WHY IT IS NOT COSMETIC. `book_appointment`'s A2 gate refuses any booking that
carries no reason, and jv_v1 opts INTO the reason question
(`prompt_facts.reason_question`), so Gate 5b-r is off for it. The refusal is
rescuable -- the model may pass `args["reason"]` itself -- but on this line it
is a coin toss standing between a caller and their appointment. This call hung
up at the phone-confirm step, so it never reached the gate.

THE RULE ADDED. When the caller volunteers a complaint and no reason is on
record, that IS the reason. Bounded by "no reason on record", so it fires at
most once per call and can never overwrite a reason said with more
deliberation. And it is added to `utterance_is_read_as_the_reason`, not
alongside it, so the scheduling captures cannot read the same sentence as a
booking preference -- fixing one door and shipping is exactly what put the
AM-only filter back on a live call after B-138's first attempt.
"""

import pytest

from app.media_streams.connection import _time_preference_tier
from app.media_streams.first_turn_extractor import (
    commit_opening_reason,
    commit_reason_answer,
    commit_volunteered_reason,
    note_opening_utterance,
    utterance_is_opening_reason,
    utterance_is_read_as_the_reason,
    utterance_is_reason_answer,
    utterance_is_volunteered_reason,
)


# The verbatim transcript, in order.
T1_PRICING = "um yeah i'd like to know about pricing at your clinic"
T2_LOCATION = "okay and whereabouts are you based"
T3_COMPLAINT = (
    "okay um yeah essentially my lower back's been really bad and "
    "my leg's gone numb"
)


def _live_call():
    """Replay the JV turn order through the helpers the live path calls.

    `note_opening_utterance` then `commit_opening_reason` is the pair
    llm_stream runs on every turn; the volunteered door is asked only when
    the opening door declines, which is the live ordering.
    """
    session: dict = {}
    for utterance in (T1_PRICING, T2_LOCATION, T3_COMPLAINT):
        note_opening_utterance(session, utterance)
        if not commit_opening_reason(session):
            commit_volunteered_reason(session, utterance)
    return session


# ---------------------------------------------------------------------------
# Red anchor: the two existing doors really are inert on this call
# ---------------------------------------------------------------------------
def test_the_opening_latch_is_spent_on_the_pricing_question():
    session: dict = {}
    note_opening_utterance(session, T1_PRICING)
    assert session["opening_utterance"] == T1_PRICING

    # ...and never moves, however substantive the later turn is.
    note_opening_utterance(session, T3_COMPLAINT)
    assert session["opening_utterance"] == T1_PRICING
    assert commit_opening_reason(session) is False


def test_the_reason_answer_door_never_arms():
    """Susie never asked, so nothing is pending and the door cannot fire."""
    session: dict = {}
    assert utterance_is_reason_answer(session, T3_COMPLAINT) is False
    assert commit_reason_answer(session, T3_COMPLAINT) is False


def test_the_complaint_is_not_an_opening_on_this_call():
    session: dict = {"opening_utterance": T1_PRICING}
    assert utterance_is_opening_reason(session, T3_COMPLAINT) is False


# ---------------------------------------------------------------------------
# The defect, and the fix
# ---------------------------------------------------------------------------
def test_the_volunteered_complaint_reaches_the_call_record():
    session = _live_call()
    assert session["reason"] == T3_COMPLAINT
    assert session["collected"]["reason"] == T3_COMPLAINT


def test_the_recorded_reason_still_contains_the_complaint():
    """The row Marcus reads has to say what is wrong, not just be non-empty."""
    session = _live_call()
    assert "lower back" in session["reason"]
    assert "numb" in session["reason"]


# ---------------------------------------------------------------------------
# The bounds
# ---------------------------------------------------------------------------
def test_it_never_overwrites_a_reason_already_on_record():
    session = {"reason": "shoulder rehab", "collected": {"reason": "shoulder rehab"}}
    assert commit_volunteered_reason(session, T3_COMPLAINT) is False
    assert session["reason"] == "shoulder rehab"


def test_the_opening_door_wins_when_both_could_fire():
    """A caller who opens with the complaint is served by the FIRST door."""
    session: dict = {}
    note_opening_utterance(session, T3_COMPLAINT)
    assert commit_opening_reason(session) is True
    before = session["reason"]
    assert commit_volunteered_reason(session, "and my knee too") is False
    assert session["reason"] == before


@pytest.mark.parametrize(
    "utterance",
    [
        "i'd like to book an appointment",
        "can i book in please",
        "yes",
        "no",
        "okay",
        "um",
        "",
        "   ",
        "how much is it",
    ],
)
def test_an_utterance_that_names_no_complaint_is_not_a_reason(utterance):
    """A booking REQUEST says what the caller wants done, not what it is for.

    Writing one into the booking satisfies the A2 gate with nothing, and the
    calendar entry then reads back as the caller's own request.
    """
    session: dict = {}
    assert commit_volunteered_reason(session, utterance) is False
    assert not session.get("reason")


def test_it_fires_at_most_once_per_call():
    session: dict = {}
    assert commit_volunteered_reason(session, T3_COMPLAINT) is True
    assert commit_volunteered_reason(session, "my shoulder is sore as well") is False
    assert session["reason"] == T3_COMPLAINT


# ---------------------------------------------------------------------------
# The other door onto the same sentence -- B-138's lesson
# ---------------------------------------------------------------------------
def test_the_scheduling_captures_see_the_third_door_too():
    session: dict = {"opening_utterance": T1_PRICING}
    assert utterance_is_read_as_the_reason(session, T3_COMPLAINT) is True


def test_a_volunteered_complaint_described_by_its_timing_banks_no_filter():
    """B-138's sentence, arriving through the third door instead.

    "every morning" is what makes it tendinopathy, not a request for an AM
    appointment. Without this the AM-only filter returns by a route the two
    earlier fixes do not cover.
    """
    volunteered = (
        "my achilles is stiff for the first few minutes every morning "
        "and it eases as i walk"
    )
    session: dict = {"opening_utterance": T1_PRICING}
    assert utterance_is_read_as_the_reason(session, volunteered) is True
    assert _time_preference_tier(
        volunteered, is_slot_pick=False, is_reason_answer=True
    ) == "none"


def test_the_predicate_does_not_flip_once_the_reason_is_committed():
    """Ordering independence.

    The live path asks the predicate in connection.py BEFORE run_turn commits
    the reason. If those two ever swap, the capture must not start reading the
    very sentence it just recorded as a scheduling preference.
    """
    session: dict = {"opening_utterance": T1_PRICING}
    assert utterance_is_volunteered_reason(session, T3_COMPLAINT) is True
    assert commit_volunteered_reason(session, T3_COMPLAINT) is True
    assert utterance_is_volunteered_reason(session, T3_COMPLAINT) is True
    assert utterance_is_read_as_the_reason(session, T3_COMPLAINT) is True
