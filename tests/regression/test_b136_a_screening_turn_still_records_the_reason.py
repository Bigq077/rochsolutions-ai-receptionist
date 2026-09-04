"""
Regression: the call's reason was recorded as "i said no you may".

B-136 — CA9c39d09fe12bfc1e971a7c79571e6139, northgate, build 12c5af8bb1ef,
4 September 2026.

    07:47:29  "what's the appointment for?"          <- the reason question
    07:47:43  "um yeah basically i was playing football um for my local team
               and i kind of just rolled my ankle what do you think i should
               do in terms of kind of the next steps"
    07:47:43  screen trauma_fracture ARMED by that utterance
    07:48:15  [first_turn] reason captured from the caller's answer:
               'i said no you may'

"i said no you may" is his reply to the SCREEN. It then travelled everywhere:

    07:51:20  pre-SMS reason: collected='i said no you may'
    07:51:25  Row built — outcome=reached_confirmation name=Quentin Rook

Anyone reading that Sheets row learns nothing about why he rang.

── CAUSE ──────────────────────────────────────────────────────────────────────
The `ask_screen` branch in connection.py answers the whole turn and returns —
it speaks, writes the history, saves the session and clears the LLM flags. So
the utterance never reaches `commit_reason_answer` further down the same
handler.

An utterance that ARMS a screen is very often the one that answers "what's the
appointment for?", because it has to describe a complaint to trigger a screen
at all. The reason flag therefore stayed armed and caught the NEXT utterance —
the screening answer.

`commit_reason_answer` already carries every guard this needs: it fires only
while the flag is armed, skips the utterance that provoked the question, and
never overwrites a reason already on record. So the fix is to call it on the
path that was skipping it, not to add logic.
"""
from __future__ import annotations

import inspect

from app.media_streams.first_turn_extractor import commit_reason_answer

FOOTBALL = (
    "um yeah basically i was playing football um for my local team and i kind "
    "of just rolled my ankle what do you think i should do in terms of kind of "
    "the next steps"
)
SCREEN_ANSWER = "i said no you may"


def _armed():
    """A session with the reason question asked and its arming turn consumed."""
    return {
        "_reason_answer_pending": True,
        "_reason_answer_armed_on": "um yeah i'd like to book an appointment",
        "collected": {},
    }


def test_the_complaint_is_captured_not_the_screening_answer():
    """THE live defect, in one assertion."""
    session = _armed()

    assert commit_reason_answer(session, FOOTBALL) is True
    assert "football" in session["reason"]
    assert "rolled my ankle" in session["reason"]


def test_the_screening_answer_can_no_longer_win_the_slot():
    """Once the complaint is on record, the reply to the screen must not
    replace it — `commit_reason_answer` never overwrites, and this pins that
    the ordering fix is what makes that guarantee useful."""
    session = _armed()
    commit_reason_answer(session, FOOTBALL)

    assert commit_reason_answer(session, SCREEN_ANSWER) is False
    assert "football" in session["reason"]
    assert session["collected"]["reason"] == session["reason"]


def test_without_the_fix_the_screening_answer_is_what_lands():
    """Pins the DEFECT: if the complaint never reaches the helper, the next
    utterance does — which is exactly what happened on the call."""
    session = _armed()

    assert commit_reason_answer(session, SCREEN_ANSWER) is True
    assert session["reason"] == SCREEN_ANSWER


def test_the_capture_is_wired_into_the_screening_branch():
    """The helper is easy to test and easy to leave uncalled — B-134 shipped a
    test file that stayed green with the fix neutered. This pins the call site
    inside the ask_screen branch itself."""
    from app.media_streams import connection

    src = inspect.getsource(connection)
    assert "_cs_commit_reason(self.session, utterance)" in src
    assert "commit_reason_answer as _cs_commit_reason" in src


def test_the_scripted_log_names_what_was_spoken():
    """The same branch logged the CALLER's words under "response spoken
    deterministically", so the 4 Sep log read as though Susie had said "um
    yeah basically i was playing football...". Both are named now."""
    from app.media_streams import connection

    src = inspect.getsource(connection)
    assert '_cs_result["action"], _cs_line[:80],' in src
    assert "(in reply to: %r)" in src
