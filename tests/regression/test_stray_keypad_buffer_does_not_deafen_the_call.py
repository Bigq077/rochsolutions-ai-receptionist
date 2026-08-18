"""
One stray keypress deafened a whole reschedule.

JV CA29d50a41db9234a16037a5c3f04c836d, 18 Aug 2026:

    21:58:39  "Just to confirm - I'm moving your appointment to Monday the
               31st... Shall I go ahead and move it for you?"
    21:58:40  DTMF raw digit='2' v3_phone_dtmf_active=False
    21:58:40  theorem_v3: auto-activating v3_phone_dtmf_active
                          ("booking in progress with no number on record")
    21:58:40  DTMF digit='2' buf='2'
    21:58:52  WATCHDOG_FIRE "Sorry, I didn't catch that. Shall I go ahead and
                             move it for you?"
    21:58:58  transcript suppressed - phone DTMF active: 'um yeah go for it'
    21:59:15  transcript suppressed - phone DTMF active: 'hello'
    21:59:22  transcript suppressed - phone DTMF active: 'you still there'
    21:59:48  outcome=reached_confirmation - nothing written, lost_total=0

Three links:

  1. A reschedule never collects a phone number, so `phone_confirmed` is never
     set and `_phone_outstanding` (booking_flow_active AND NOT phone_confirmed
     AND NOT phone_entered_by_keypad) stays true for the WHOLE call. Any digit
     therefore auto-arms phone collection - against a question that was about
     moving an appointment, not about phones.
  2. Nothing finalizes a buffer under 9 digits (the ladder in _handle_dtmf is
     >=11 / >=10 / >=9), so a 1-digit buffer sits there forever.
  3. A non-empty buffer suppresses every transcript. The only escapes are a
     name correction or one of {reset, clear, start over, wrong, mistake,
     again, restart} - and "um yeah go for it" is none of them.

So Susie asked a question, the caller answered it, and the answer was binned.

The opposite mistake is just as expensive: CA9758ceab (7 Aug) binned ELEVEN
digits by disarming a caller who was about to type. That call cannot regress
through this hatch - its buffer was EMPTY throughout, and this branch only
exists for a non-empty one - but the four conditions in
_stray_dtmf_buffer_yields_to_speech are what keep the two apart in general.
The tests below pin each of them from both sides.
"""

import inspect

import pytest

from app.media_streams import connection as c
from app.media_streams.connection import (
    _DTMF_MIN_FINALIZE_DIGITS,
    _stray_dtmf_buffer_yields_to_speech,
)


THE_RESCHEDULE_CTA = (
    "Just to confirm - I'm moving your appointment to Monday the 31st of "
    "August at half past four. Shall I go ahead and move it for you?"
)
THE_KEYPAD_ASK = (
    "Thanks Quentin - could you type your number on your keypad? "
    "You can press the star key to reset at any time."
)
# 5 words, no digit run -> passes _is_conversational_during_dtmf.
THE_ANSWER = "um yeah go for it"


def _reschedule_session(**over) -> dict:
    """The session as it stood at 21:58:58 on the live call."""
    s = {
        "clinic_id": "jv_v1",
        "booking_flow_active": True,
        "phone_confirmed": False,          # a reschedule never sets this
        "v3_phone_dtmf_active": True,
        "v3_phone_dtmf_armed_speculatively": True,
        "last_bot_prompt": THE_RESCHEDULE_CTA,
        "last_question": "Shall I go ahead and move it for you?",
    }
    s.update(over)
    return s


# -- the regression --------------------------------------------------------

def test_the_stray_digit_yields_to_the_answer():
    """The whole defect in one assertion."""
    assert _stray_dtmf_buffer_yields_to_speech(
        _reschedule_session(), "2", THE_ANSWER
    ), (
        "a 1-digit buffer armed on a guess outranked the caller answering the "
        "question Susie had just asked - the reschedule never happened"
    )


@pytest.mark.parametrize("buf", ["2", "27", "2749", "27491836"])
def test_any_buffer_too_short_to_finalize_yields(buf):
    """Nothing under 9 digits can ever become a number, so none of it is worth
    a suppressed caller."""
    assert _stray_dtmf_buffer_yields_to_speech(
        _reschedule_session(), buf, THE_ANSWER
    ), f"buffer {buf!r} ({len(buf)} digits) can never finalize but still suppressed"


# -- the four guards, each pinned from the blocking side -------------------

@pytest.mark.parametrize("buf", ["274918365", "27491836521"])
def test_a_buffer_that_could_still_finalize_is_left_alone(buf):
    """>= 9 digits is a real number in progress. Speech must not bin it."""
    assert len(buf) >= _DTMF_MIN_FINALIZE_DIGITS
    assert not _stray_dtmf_buffer_yields_to_speech(
        _reschedule_session(), buf, THE_ANSWER
    ), "a nearly-complete number was thrown away because the caller spoke"


@pytest.mark.parametrize("utt", ["hello", "you still there", "oh seven five oh"])
def test_short_or_numeric_speech_does_not_count_as_conversational(utt):
    """
    Reuses the existing >4-words/no-digit-run test rather than inventing a
    second one. "hello" and "you still there" were also binned on the live
    call; they stay suppressed, and that is fine - the 5-word answer ahead of
    them opens the gate and everything after it flows normally.
    """
    assert not _stray_dtmf_buffer_yields_to_speech(
        _reschedule_session(), "2", utt
    )


def test_an_explicitly_armed_keypad_is_never_yielded():
    """
    Armed because someone ASKED for a number - the CA9758ceab shape. Strict
    suppression is correct here and must not loosen.
    """
    s = _reschedule_session(
        v3_phone_dtmf_armed_speculatively=False,
        last_bot_prompt=THE_KEYPAD_ASK,
    )
    assert not _stray_dtmf_buffer_yields_to_speech(s, "079", THE_ANSWER)


def test_a_live_keypad_prompt_blocks_the_hatch_even_if_flagged_speculative():
    """
    Belt and braces: a speculative arm followed by a genuine ask. The stale
    flag must not be enough on its own.
    """
    s = _reschedule_session(last_bot_prompt=THE_KEYPAD_ASK)
    assert s["v3_phone_dtmf_armed_speculatively"] is True
    assert not _stray_dtmf_buffer_yields_to_speech(s, "079", THE_ANSWER)


def test_a_phone_question_on_the_table_blocks_the_hatch():
    """The verbal phone step, which is a different prompt shape again."""
    s = _reschedule_session(
        last_bot_prompt="I've got you on oh seven five oh two - "
                        "is that the best number for the booking?",
        last_question="is that the best number for the booking?",
    )
    assert not _stray_dtmf_buffer_yields_to_speech(s, "079", THE_ANSWER)


def test_an_empty_buffer_is_not_this_branchs_business():
    """
    The empty-buffer case has its own deliberate handling (it STAYS ARMED, per
    CA9758ceab). This hatch must never claim it.
    """
    assert not _stray_dtmf_buffer_yields_to_speech(
        _reschedule_session(), "", THE_ANSWER
    )


# -- the wiring ------------------------------------------------------------
# A predicate nobody calls fixes nothing. These pin it onto the actual path.

def _suppression_region() -> str:
    """handle_transcript, from the DTMF intercept to the suppression log."""
    # Anchored on CODE, not on the log wording: this file's own docstrings
    # quote the log lines, and inspect.getsource sees the whole module.
    src = inspect.getsource(c)
    start = src.index('if self.session.get("v3_phone_dtmf_active"):')
    end = src.index('"phone DTMF active: %r"', start)
    return src[start:end]


def test_the_hatch_is_checked_before_the_suppression():
    region = _suppression_region()
    assert "_stray_dtmf_buffer_yields_to_speech(" in region, (
        "the escape hatch is not on the path a suppressed transcript takes - "
        "the predicate is dead code and the call still goes deaf"
    )


def test_the_hatch_clears_the_buffer_and_leaves_dtmf_mode():
    region = _suppression_region()
    assert 'self.session["phone_dtmf_buffer"] = ""' in region, (
        "the stray buffer is not discarded, so the next transcript is "
        "suppressed all over again"
    )
    assert 'self.session["v3_phone_dtmf_active"] = False' in region, (
        "DTMF mode is not exited, so suppression stays armed"
    )


def test_the_discarded_buffer_is_counted():
    region = _suppression_region()
    assert '_note_utterance_lost(' in region and "dtmf_stray_buffer_discarded" in region, (
        "the discarded digits are not tallied - the call that found this "
        "reported lost_total=0 while binning three utterances"
    )


def test_the_speculative_arm_is_recorded_where_it_happens():
    """The hatch's real discriminator is set at the auto-activation site."""
    src = inspect.getsource(c)
    i = src.index("auto-activating v3_phone_dtmf_active")
    j = src.index("v3_phone_dtmf_armed_speculatively", i)
    k = src.index("buf = self.session.get(\"phone_dtmf_buffer\", \"\")", i)
    assert i < j < k, (
        "nothing records WHY phone DTMF was armed, so the hatch cannot tell a "
        "guess from a real request for a number"
    )
