"""CAea8abdb (2 Sep 2026, Vital Edge, live) — the reason was asked twice.

The call, in order:

    Susie   "Is there a particular area or concern you're looking to address?"
    Caller  "I'm a full-time athlete and I just need some recovery work,
             so probably sports massage — is that what you'd recommend?"
    Susie   "For an athlete focused on recovery, yes — a Sports Massage is
             exactly the right fit. Would you like to book a session?"
    Caller  "Uh yes please"
    Susie   "Right — Is there a particular area or reason for the massage,
             like back tension, general stress, or something else?"   <-- again
    Caller  "I'm just... I'm just an athlete who's kind of working quite
             regularly"                                    <-- repeats himself

Three independent holes, all covered here:

  1. `note_reason_question_asked` did not recognise the FIRST ask, so the
     once-only latch never armed and the second ask was unguarded.
  2. Nothing recorded the caller's ANSWER into the canonical reason slot on a
     free-form clinic, so `_reason_already_known` stayed False all call.
  3. CALL STATE carries an explicit "do NOT ask again, in any wording"
     instruction, but it is gated on that same latch — so it was never
     rendered, and the model had no way to know it had already asked.
"""
from unittest.mock import patch

import pytest

from app.media_streams.first_turn_extractor import commit_reason_answer
from app.media_streams.llm_stream import note_reason_question_asked
from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import _b7_call_state

FIRST_ASK = "Is there a particular area or concern you're looking to address?"
SECOND_ASK = (
    "Right — Is there a particular area or reason for the massage — like "
    "back tension, general stress, or something else?"
)
ANSWER = (
    "i'm just i'm just an athlete who's kind of working quite regularly"
)

_OPTED_IN = {"prompt_facts": {"reason_question": SECOND_ASK}}


def _session():
    return {"clinic_id": "vital_edge"}


def _latch(session, spoken, opted_in=True):
    cfg = _OPTED_IN if opted_in else {"prompt_facts": {}}
    with patch("app.clinic_config.get_clinic", return_value=cfg):
        return note_reason_question_asked(session, spoken)


# ── 1. the first ask must arm the latch ──────────────────────────────────────

def test_first_ask_arms_the_latch():
    """The wording actually spoken on the call. Was unmatched; asked twice."""
    s = _session()
    assert _latch(s, FIRST_ASK) is True
    assert s["_reason_question_asked"] is True


def test_mandated_wording_still_arms_the_latch():
    s = _session()
    assert _latch(s, SECOND_ASK) is True


@pytest.mark.parametrize("spoken", [
    "Would you like to book a session with Jonathan?",
    "Is there a particular day or time that works best for you?",
    "Could I take your first name and surname?",
    "So that's Wednesday the 9th of September at one in the afternoon.",
])
def test_ordinary_booking_questions_do_not_arm_the_latch(spoken):
    """The latch must not fire on any other question in the booking flow."""
    s = _session()
    assert _latch(s, spoken) is False
    assert "_reason_question_asked" not in s


def test_latch_is_inert_for_a_clinic_that_did_not_opt_in():
    """jv_v1 / theorem must render and behave byte-identically."""
    s = _session()
    assert _latch(s, FIRST_ASK, opted_in=False) is False
    assert "_reason_answer_pending" not in s


# ── 2. the caller's answer must be recorded ──────────────────────────────────

def _arm(s):
    """Latch, then burn the arming turn the way the live call site does."""
    _latch(s, FIRST_ASK)
    assert s.get("_reason_answer_pending") is True
    # The turn that PROVOKED the question. Must not be taken as its answer.
    assert commit_reason_answer(s, "um yeah hi there i'd like to book an "
                                   "appointment please") is False
    assert "reason" not in s


def test_the_provoking_turn_is_never_taken_as_the_answer():
    """CA20ed370 (2 Sep 2026, live) — the whole point of the arming guard.

    The question latched at 22:01:48.546 and 3ms later the "answer" recorded
    was the caller's booking request. The real answer arrived eight seconds
    later and was dropped, because a reason on record is never overwritten.
    """
    s = _session()
    _latch(s, "Let's get you booked in — What's the appointment for?")
    provoking = "um yeah hi there i'd like to book an appointment please"
    assert commit_reason_answer(s, provoking) is False
    assert "reason" not in s
    # still armed, waiting for the real answer
    assert s.get("_reason_answer_pending") is True

    answer = "um i've been getting tightness in my hamstring after training"
    assert commit_reason_answer(s, answer) is True
    assert s["reason"] == answer


def test_a_bare_booking_request_is_never_a_reason():
    """It says what the caller wants done, not what it is for."""
    s = _session()
    _arm(s)
    assert commit_reason_answer(s, "i'd like to book an appointment") is False
    assert "reason" not in s


def test_a_booking_request_that_also_carries_a_reason_is_kept():
    s = _session()
    _arm(s)
    utt = "i'd like to book an appointment for my knee pain"
    assert commit_reason_answer(s, utt) is True
    assert s["reason"] == utt


def test_answer_is_captured_into_the_canonical_reason_slot():
    s = _session()
    _arm(s)
    assert commit_reason_answer(s, ANSWER) is True
    assert s["reason"] == ANSWER
    assert s["collected"]["reason"] == ANSWER
    assert "_reason_answer_pending" not in s


def test_answer_containing_a_question_is_still_captured():
    """The turn that also asks something is still the answer to what was asked.

    This is the turn the T-7/T-11 question gate discarded wholesale.
    """
    s = _session()
    _arm(s)
    utt = (
        "i'm a full-time athlete and i just need some recovery work so "
        "probably sports massage then is that what you would recommend"
    )
    assert commit_reason_answer(s, utt) is True
    assert s["reason"] == utt


def test_capture_never_fires_unless_the_question_was_asked():
    s = _session()
    assert commit_reason_answer(s, ANSWER) is False
    assert "reason" not in s


def test_capture_never_overwrites_a_reason_already_on_record():
    s = _session()
    s["reason"] = "lower back pain"
    _latch(s, FIRST_ASK)
    commit_reason_answer(s, "the provoking turn")
    assert commit_reason_answer(s, ANSWER) is False
    assert s["reason"] == "lower back pain"


@pytest.mark.parametrize("non_answer", ["yes", "Yeah", "ok", "um", "sorry?"])
def test_a_bare_affirmation_is_not_a_reason(non_answer):
    """Writing "yes" into the booking is worse than writing nothing."""
    s = _session()
    _arm(s)
    assert commit_reason_answer(s, non_answer) is False
    assert "reason" not in s


def test_pending_flag_is_bounded_and_cannot_drift():
    """It must not survive to capture a reply to some later question."""
    s = _session()
    _arm(s)
    assert commit_reason_answer(s, "um") is False
    assert s.get("_reason_answer_pending") is True   # one filler tolerated
    assert commit_reason_answer(s, "yes") is False
    assert "_reason_answer_pending" not in s          # then dropped
    assert commit_reason_answer(s, "07502211207") is False
    assert "reason" not in s


# ── 3. an armed latch must reach the model ───────────────────────────────────

def test_call_state_tells_the_model_it_has_already_asked():
    """The instruction exists; it is gated on the latch this fix arms.

    Without the latch the sentence is never rendered, so the model composes
    each turn with no memory of having asked — which is the whole reason
    "ask ONCE" cannot be a property of the prompt alone.
    """
    clinic = get_clinic("vital_edge")
    s = _session()
    _latch(s, FIRST_ASK)
    out = _b7_call_state(s, clinic, {})
    assert "do NOT ask what the appointment is for again" in out


def test_call_state_is_silent_before_the_question_is_asked():
    clinic = get_clinic("vital_edge")
    out = _b7_call_state(_session(), clinic, {})
    assert "do NOT ask what the appointment is for again" not in out
