"""CA66bd0930 (14 Sep 2026, JV, live) — the reason was captured from the
caller's answer to the NAME question. OPEN_DEFECTS_2026-09-14.md row #1.

The call, in order (obs transcript, turns 3–12):

    Susie   "what's the appointment for?"                      <-- flag armed
    Caller  "thank you"                                        <-- STT garble; turn 1 of 2
    Susie   "Before I do that — could I take your first name and surname?"
    Caller  "i'm a gardener"                                   <-- turn 2 of 2: CAPTURED as reason
    Susie   "What's the appointment for, could you tell me a little about
             what's bothering you?"                            <-- re-asked
    Caller  "i need some physio on my leg"                     <-- never recorded: "already"
    Susie   "Do you have a preference for when you'd like to come in?"
    Caller  "i've got a broken tibia"                          <-- never recorded: "already"

The owner alert, the summary and the Sheets row for a broken-tibia patient
say "i'm a gardener".

`commit_reason_answer` let `_reason_answer_pending` survive two caller turns
without checking what Susie asked in between. The rule now: while the flag
is pending, an utterance whose preceding Susie turn asked a DIFFERENT
question is that question's answer — skipped without spending the bound,
flag kept, so the re-asked reason question still captures.
"""
from unittest.mock import patch

import pytest

from app.media_streams.first_turn_extractor import (
    _REASON_ANSWER_MAX_SKIPS,
    commit_reason_answer,
)
from app.media_streams.llm_stream import note_reason_question_asked

OPENING = "hi i'd like to book in please"
REASON_ASK = "Let's get you booked in — what's the appointment for?"
GARBLE = "thank you"
NAME_ASK = "Before I do that — could I take your first name and surname?"
NAME_ANSWER = "i'm a gardener"
REASON_REASK = (
    "Thanks, got that — What's the appointment for, could you tell me a "
    "little about what's bothering you?"
)
REASON_ANSWER = "i need some physio on my leg"

_OPTED_IN = {"prompt_facts": {"reason_question": "what's the appointment for?"}}


def _susie(session, spoken):
    """Susie speaks: the model path appends the pair after the turn, so by
    the time the NEXT caller utterance is committed the tail of history is
    this reply. Arms the latch exactly as the live path does."""
    with patch("app.clinic_config.get_clinic", return_value=_OPTED_IN):
        note_reason_question_asked(session, spoken)
    session.setdefault("conversation_history", []).append(
        {"role": "assistant", "content": spoken}
    )


def _caller(session, utterance):
    """The caller speaks: `commit_reason_answer` runs BEFORE the LLM turn,
    so history still ends on Susie's last reply."""
    landed = commit_reason_answer(session, utterance)
    session.setdefault("conversation_history", []).append(
        {"role": "user", "content": utterance}
    )
    return landed


def _session():
    return {"clinic_id": "jv_v1"}


def _replay_to_the_name_turn():
    s = _session()
    s["conversation_history"] = [{"role": "user", "content": OPENING}]
    _susie(s, REASON_ASK)
    assert commit_reason_answer(s, OPENING) is False  # arming turn
    assert s.get("_reason_answer_pending") is True
    assert _caller(s, GARBLE) is False
    assert s.get("_reason_answer_pending") is True, "one filler is allowed"
    _susie(s, NAME_ASK)
    return s


# ── the defect ──────────────────────────────────────────────────────────────

def test_the_answer_to_the_name_question_is_not_the_reason():
    s = _replay_to_the_name_turn()
    assert _caller(s, NAME_ANSWER) is False
    assert not (s.get("reason") or "").strip()
    assert not ((s.get("collected") or {}).get("reason") or "").strip()


def test_skipping_the_name_turn_keeps_the_flag_and_spends_no_bound():
    s = _replay_to_the_name_turn()
    _caller(s, NAME_ANSWER)
    assert s.get("_reason_answer_pending") is True
    assert int(s.get("_reason_answer_turns") or 0) == 1, (
        "the garble spent one turn; the name answer must not spend another"
    )


def test_the_reasked_reason_question_still_captures():
    """Turns 9–10 of the call: the model re-asked and the caller answered.
    Live, `already` blocked it because 'i'm a gardener' was on record."""
    s = _replay_to_the_name_turn()
    _caller(s, NAME_ANSWER)
    _susie(s, REASON_REASK)
    assert _caller(s, REASON_ANSWER) is True
    assert s["reason"] == REASON_ANSWER
    assert s["collected"]["reason"] == REASON_ANSWER
    assert "_reason_answer_pending" not in s


@pytest.mark.parametrize("other_question", [
    NAME_ASK,
    "Thanks Sam — and your surname?",
    "And the best number to reach you on?",
    "Is that the Alcester or the Redditch clinic?",
    "Did you say Goner — is that right?",
    "Any of those work?",
])
def test_an_answer_to_any_other_question_is_skipped(other_question):
    s = _session()
    s["conversation_history"] = [{"role": "user", "content": OPENING}]
    _susie(s, REASON_ASK)
    commit_reason_answer(s, OPENING)
    _susie(s, other_question)
    assert _caller(s, "my knee") is False, other_question
    assert not (s.get("reason") or "").strip()
    assert s.get("_reason_answer_pending") is True


def test_the_models_reask_wording_is_recognised_as_the_reason_question():
    """Turn 9 verbatim. The clause after "for" outran the classifier's
    20-char window, so the re-ask was unrecognised — which would have made
    the fix above skip the real answer as 'a different question'."""
    from app.hold_speech import question_asks_the_reason
    assert question_asks_the_reason(REASON_REASK)
    assert question_asks_the_reason(
        "Could you tell me a little about what's bothering you?"
    )
    assert not question_asks_the_reason(NAME_ASK)


# ── what must NOT change ────────────────────────────────────────────────────

def test_the_very_next_turn_is_still_the_answer():
    """The normal call. Nothing between the question and the reply."""
    s = _session()
    s["conversation_history"] = [{"role": "user", "content": OPENING}]
    _susie(s, REASON_ASK)
    commit_reason_answer(s, OPENING)
    assert _caller(s, "my knee's been playing up") is True
    assert s["reason"] == "my knee's been playing up"


def test_a_non_question_susie_turn_does_not_block_the_answer():
    """A head or an acknowledgement between the ask and the reply is not a
    different question. Today's behaviour, kept."""
    s = _session()
    s["conversation_history"] = [{"role": "user", "content": OPENING}]
    _susie(s, REASON_ASK)
    commit_reason_answer(s, OPENING)
    _caller(s, "um")
    _susie(s, "Take your time —")
    assert _caller(s, "it's my shoulder") is True
    assert s["reason"] == "it's my shoulder"


def test_the_flag_cannot_drift_for_the_whole_call():
    """Skips are free but not unbounded: after `_REASON_ANSWER_MAX_SKIPS`
    other questions the flag is dropped rather than left armed to capture a
    reply to something unrelated twenty turns later."""
    s = _session()
    s["conversation_history"] = [{"role": "user", "content": OPENING}]
    _susie(s, REASON_ASK)
    commit_reason_answer(s, OPENING)
    for i in range(_REASON_ANSWER_MAX_SKIPS):
        _susie(s, f"Question number {i}, is that right?")
        assert _caller(s, "yes") is False
    assert "_reason_answer_pending" not in s
