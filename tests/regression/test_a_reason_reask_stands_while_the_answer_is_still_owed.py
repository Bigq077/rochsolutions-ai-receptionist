"""CA66bd0930 (14 Sep 2026, JV, live), 09:49:53-55 — the root of row #1.

    Susie   "what's the appointment for?"            <-- latched, answer pending
    Caller  "thank you"                              <-- STT; answers nothing
    Model   "what's the appointment for?"            <-- correct: re-ask
    Gate5   reason question removed — nothing askable left; asked the
            outstanding step instead: 'Before I do that — could I take your
            first name and surname?'                 <-- WRONG turn

Gate 5b-r's one free strip exists for a caller who ANSWERED and was asked
again in fresh wording. It fired on a caller who had not answered, deleted
the right question, asked for the name instead — and the caller's name
answer then became the reason (row #1). While `_reason_answer_pending` is
armed (the engine itself is still waiting), a re-ask is the right turn.
"""
from unittest.mock import patch

from app.media_streams.first_turn_extractor import commit_reason_answer
from app.media_streams.llm_stream import note_reason_question_asked
from app.media_streams.turn_handler import sanitise_response

ASK = "what's the appointment for?"
OPENING = "hi i'd like to book in please"
_JV = {"prompt_facts": {"reason_question": "What's the appointment for?"}}


def _session():
    s = {"clinic_id": "jv_v1", "conversation_history": [{"role": "user", "content": OPENING}]}
    with patch("app.clinic_config.get_clinic", return_value=_JV):
        note_reason_question_asked(s, ASK)
    s["conversation_history"].append({"role": "assistant", "content": ASK})
    commit_reason_answer(s, OPENING)          # arming turn
    return s


def test_the_reask_after_a_non_answer_is_spoken_not_swapped_for_the_name():
    s = _session()
    assert commit_reason_answer(s, "thank you") is False
    assert s.get("_reason_answer_pending") is True
    s["_turn_user_text"] = "thank you"
    out = sanitise_response("Right — what's the appointment for?", s)
    assert "appointment for" in out.lower(), out
    assert "surname" not in out.lower()


def test_a_reask_after_the_caller_answered_is_still_stripped():
    """The rule the free strip was written for: answered, asked again."""
    s = _session()
    assert commit_reason_answer(s, "my shoulder's been playing up") is True
    assert "_reason_answer_pending" not in s
    s["_turn_user_text"] = "my shoulder's been playing up"
    out = sanitise_response("Got it — what's the appointment for?", s)
    assert "appointment for" not in out.lower()


def test_the_free_strip_survives_once_the_wait_is_over():
    """Two non-answers exhaust the pending bound; the strip may then fire
    once, as before — the loop guard is untouched."""
    s = _session()
    commit_reason_answer(s, "thank you")
    commit_reason_answer(s, "um")
    assert "_reason_answer_pending" not in s
    s["_turn_user_text"] = "um"
    out = sanitise_response("what's the appointment for?", s)
    assert "appointment for" not in out.lower()
