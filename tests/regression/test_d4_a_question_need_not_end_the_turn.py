# tests/regression/test_d4_a_question_need_not_end_the_turn.py
"""
D4 - the watchdog armed small talk over an outstanding question.

Theorem, 9 Sep 2026 03:28:30. Susie had just asked the phone-confirm question
and the caller had not answered yet:

    03:28:24.46  Susie: "Is the number you're calling on the one associated
                        with your booking? If so, just say 'use this number'."
    03:28:30.41  [ms_watchdog] question-less turn reached the arming family -
                               last_sent="If so, just say 'use this number'."
    03:28:30.41  [ms_watchdog] T-3 nudge armed - armed "Anything else you'd
                               like to know?"

The nudge does `_sh_w.last_question = _nudge_w`, so the outstanding phone
question was overwritten by an open invitation. It never spoke - the caller hung
up three seconds earlier - but a caller who had merely paused would have been
offered small talk instead of the question they were mid-way through answering.

THE CAUSE, which the handover recorded as unknown. The site that spoke does call
`on_question_asked(_V3_PHONE_CONFIRM_Q)` with the whole two-sentence string, so
`last_question` should have been populated and the BACKSTOP branch should have
fired. It was not, because every "did this turn ask a question?" predicate in
this file reads only the TAIL of the text:

    _is_question_worth_storing:  `t.endswith("?")`
    on_tts_finished:             `t.endswith("?")`
    the watchdog arm:            _prompt_contains_question(LAST SENTENCE)

`_V3_PHONE_CONFIRM_Q` ends on the instruction that follows its question, so all
three said "no question here" about a question. `on_question_asked` stored
nothing, `_sh_w.last_question` stayed empty, `_outstanding_q_w` was "", and the
T-3 branch was reached by construction.

This is not one string's problem. `_LOC_RUNG2_CONFIRM` has the identical shape
("...just say 'use this clinic'.") and was already worked around at the arming
site with a `v3_awaiting_use_this_clinic` special case - a second instance,
patched rather than fixed. The general form is that the predicate must read the
WHOLE turn's speech, which is what these tests pin. The `use_this_clinic`
special case is deliberately LEFT in place: removing a live guard entry is how
`f89a4c7e` happened.
"""
from __future__ import annotations

import app.media_streams.connection as C
from app.media_streams.connection import (
    _LOC_RUNG2_CONFIRM,
    _V3_PHONE_CONFIRM_Q,
    _is_question_worth_storing,
    _question_sentence,
    _turn_asks_a_question,
)


# ── the two live strings that started this ──────────────────────────────────

def test_the_phone_confirm_question_is_a_question():
    assert _turn_asks_a_question(_V3_PHONE_CONFIRM_Q)
    assert _is_question_worth_storing(_V3_PHONE_CONFIRM_Q)
    assert _question_sentence(_V3_PHONE_CONFIRM_Q) == (
        "Is the number you're calling on the one associated with your booking?"
    )


def test_the_biased_clinic_confirm_is_a_question():
    """The second instance of the same shape, which had its own workaround."""
    assert _turn_asks_a_question(_LOC_RUNG2_CONFIRM)
    assert _is_question_worth_storing(_LOC_RUNG2_CONFIRM)
    assert _question_sentence(_LOC_RUNG2_CONFIRM).endswith("?")


# ── the general form ────────────────────────────────────────────────────────

def test_a_question_followed_by_a_statement_still_counts():
    text = "Would you like me to book that in? Just say yes."
    assert _turn_asks_a_question(text)
    assert _question_sentence(text) == "Would you like me to book that in?"


def test_the_reask_replays_the_question_not_the_trailing_statement():
    """Spec W: a re-ask must not replay a whole multi-sentence paragraph, and
    it must not replay the half that asked nothing."""
    text = (
        "The clinic is open Monday to Friday. Would you like me to find you a "
        "time? I'll have a look now."
    )
    assert _question_sentence(text) == "Would you like me to find you a time?"


def test_a_turn_that_asks_nothing_is_still_not_a_question():
    for text in (
        "It's seventy five pounds.",
        "Free parking is available right outside.",
        "Right \u2014",
        "",
    ):
        assert not _turn_asks_a_question(text), text
        assert not _is_question_worth_storing(text), text


def test_never_store_phrases_still_win():
    """The filler and re-ask guards must not be widened by this."""
    for text in (
        "Sorry, I didn't quite catch that \u2014 could you say it again?",
        "One moment \u2014 shall I check that for you?",
        "Bear with me. Is that the right one?",
    ):
        assert not _is_question_worth_storing(text), text


def test_it_never_raises():
    for bad in (None, "", "   ", "???", "no punctuation at all"):
        _turn_asks_a_question(bad)
        _question_sentence(bad)


# ── and the handler actually stores it ──────────────────────────────────────

def _handler():
    return C.SilenceHandler.__new__(C.SilenceHandler)


def test_on_question_asked_stores_a_two_sentence_question():
    h = C.SilenceHandler.__new__(C.SilenceHandler)
    h.last_question = ""
    h.reask_count = 0
    h._no_input_reask_count = 0
    h._last_question_set_at = 0.0
    h._q_gen = 0
    h._watchdog_has_retired = True
    h._get_session = lambda: None
    h._reset_prompt_speech_guard_for_new_prompt = lambda: None
    h._restart_timer = lambda: None
    h._replay_flow_step = -1

    h.on_question_asked(_V3_PHONE_CONFIRM_Q)

    assert h.last_question == _V3_PHONE_CONFIRM_Q
    # And the BACKSTOP's own predicate agrees, so the arming family that fired
    # the T-3 nudge would now take the backstop branch instead.
    assert h._prompt_contains_question(h.last_question)
