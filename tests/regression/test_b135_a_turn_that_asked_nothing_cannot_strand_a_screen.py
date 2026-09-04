"""
Regression: "take your time" closed a clinical screen's answer window.

B-135 — CA9c39d09fe12bfc1e971a7c79571e6139, northgate, build 12c5af8bb1ef,
4 September 2026.

    07:47:43  screen trauma_fracture ARMED + asked
    07:47:51  'okay'                                    -> unclear
    07:47:55  "take your time — just let me know how it's feeling at the
               moment."                                  <- asks NOTHING
    07:48:04  "no it's fine it's fine no nothing too serious"
    07:48:04  screen trauma_fracture STRANDED — re-asking once

A clear negative answer to a trauma screen, discarded. The screen was then put
a THIRD time at 07:50:23 — after the caller had picked a slot and given his
name — and he answered "no it's not you've already asked me". Three asks
spanning 2m50s of a 246-second call.

── WHY THE WINDOW SHUT ────────────────────────────────────────────────────────
`_question_was_asked` decides the window from `last_bot_prompt` +
`last_question`. `on_transcript_received` CLEARS `last_question` on every
caller turn, and the patience line then replaced `last_bot_prompt` — so both
halves described a turn that had put no question to the caller at all, and the
window closed on a sentence whose entire purpose was to say "take your time".

A pending screen stays the outstanding question until something else is ASKED.
The rule is decided on the ABSENCE of a question, never on wording, so nothing
here pins a phrase — see `write-gates-match-one-literal`.

── WHY IT DECLINES ON DOUBT ───────────────────────────────────────────────────
Grading a reply against the wrong question is worse than re-asking, so every
uncertain reading returns False and keeps today's behaviour. The truncation
case matters most: B-31 caps `last_bot_prompt` at 200 characters and eats the
'?', so on a long turn the absence of a question mark proves nothing.
"""
from __future__ import annotations

import pytest

from app.media_streams.clinical_screening import (
    _LAST_BOT_PROMPT_CAP,
    _question_was_asked,
)

SCREEN = {
    "id": "trauma_fracture",
    "screen_question": (
        "Is it too painful to use it or put your weight through it, and is "
        "there any marked swelling, or does it look out of shape?"
    ),
}
QUESTION = SCREEN["screen_question"]
PATIENCE = "take your time - just let me know how it's feeling at the moment."


def test_the_patience_line_no_longer_strands_the_screen():
    """THE live defect. His "no it's fine, nothing too serious" was thrown
    away and a trauma screen was put to him twice more."""
    assert _question_was_asked(
        {"last_bot_prompt": PATIENCE, "last_question": ""},
        SCREEN,
        a_silent_turn_keeps_it_open=True,
    ) is True


def test_the_screen_question_itself_still_matches():
    assert _question_was_asked(
        {"last_bot_prompt": QUESTION, "last_question": QUESTION}, SCREEN
    ) is True


# ---------------------------------------------------------------------------
# The guards. Each is a way to grade a reply against the wrong question.
# ---------------------------------------------------------------------------
def test_a_different_question_closes_the_window():
    """THE guard. Once something else has been ASKED, the caller is answering
    that, and grading it against a trauma screen would be a clinical error."""
    other = "Could I take your first name and surname?"
    assert _question_was_asked(
        {"last_bot_prompt": other, "last_question": other},
        SCREEN,
        a_silent_turn_keeps_it_open=True,
    ) is False


def test_a_question_mark_in_the_prompt_closes_the_window():
    assert _question_was_asked(
        {"last_bot_prompt": "Shall I go ahead and book that in?", "last_question": ""},
        SCREEN,
        a_silent_turn_keeps_it_open=True,
    ) is False


def test_a_truncated_prompt_declines():
    """B-31 caps last_bot_prompt at 200 chars and can eat the '?'. On a turn
    that long, the absence of a question mark proves nothing, so this must not
    be read as "asked nothing"."""
    assert _question_was_asked(
        {"last_bot_prompt": "x" * _LAST_BOT_PROMPT_CAP, "last_question": ""},
        SCREEN,
        a_silent_turn_keeps_it_open=True,
    ) is False


@pytest.mark.parametrize("session", [{}, {"last_bot_prompt": ""},
                                     {"last_bot_prompt": "   "}])
def test_nothing_to_reason_from_declines(session):
    assert _question_was_asked(
        session, SCREEN, a_silent_turn_keeps_it_open=True
    ) is False


def test_an_unconfigured_screen_takes_the_untouched_path():
    """A screen with no configured question has no distinctive words, so the
    function returns `q in last` ABOVE this change and never reaches it.

    That path answers True, because the empty string is a substring of
    everything. Recorded here rather than "fixed": it is pre-existing, nothing
    in this defect depends on it, and altering the reach of a clinical gate on
    the way past a different fix is how the last four of these were caused.
    The value of this test is that it PINS the change as not having touched it.
    """
    assert _question_was_asked(
        {"last_bot_prompt": "hello"}, {"screen_question": ""}
    ) is True


# ---------------------------------------------------------------------------
# THE blast-radius guard — caught while scoping this fix, not after shipping it
# ---------------------------------------------------------------------------
def test_the_double_ask_guard_is_not_widened():
    """`_question_was_asked` has TWO callers that need OPPOSITE answers.

    The pending-screen caller asks "is my question still the outstanding one?"
    The DOUBLE-ASK guard (clinical_screening.py, reached when a screen has just
    been TRIGGERED and none is pending) asks "did the MODEL already ask this?"
    — and if it says yes, the caller's own COMPLAINT is graded as the screen's
    answer and the screen is never asked.

    The first version of B-135 changed the shared body and would have widened
    BOTH. A caller saying "I rolled my ankle" after any statement turn would
    have had a trauma screen silently marked answered. That is the failure this
    whole layer exists to prevent, so the new reading is opt-in and OFF by
    default, and this test is the reason the default must stay OFF.
    """
    silent_turn = {"last_bot_prompt": PATIENCE, "last_question": ""}

    assert _question_was_asked(silent_turn, SCREEN) is False
    assert _question_was_asked(
        silent_turn, SCREEN, a_silent_turn_keeps_it_open=True
    ) is True
