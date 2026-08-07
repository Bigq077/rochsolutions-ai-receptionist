"""
Regression: the operator CALL BACK SMS must report the call, not summarise it.

The text was a fixed header plus the judge's `evidence` field — one model-written
sentence, requested by the prompt as "1-2 sentences quoting the turns". That made
the entire operator-facing account of a call a single act of interpretation, and
on CAe2120b (theorem_v3, 2026-08-06) the interpretation was wrong twice in one
sentence: it named a redundant step Susie had not taken (the quoted line was
suppressed before TTS and never spoken) and an ending that had not happened (the
caller went quiet; Susie closed the line).

Those two inputs are fixed separately — see test_obs_transcript_hears_the_whole_call
and test_no_audio_close_is_not_caller_hung_up. This file covers the third part:
the facts an operator needs in order to decide whether to ring someone back are
facts we hold in the record, and they should be read from it rather than
recovered from a paraphrase.
"""
from __future__ import annotations

from app.obs.judge import build_callback_sms


# The CAe2120b record, as it is stored once the two upstream fixes are in.
CALL = {
    "call_sid": "CAe2120b037d56f4ab94e8abf0a402fdd4",
    "clinic_id": "theorem_v3",
    "caller_number": "+447502211207",
    "reason": "no_audio_close",
    "booking_confirmed": None,
    "duration_s": 123,
    "transcript": [
        {"role": "assistant", "text": "Hi there, I'm Susie, Theorem Health's AI receptionist."},
        {"role": "user", "text": "uh yeah i'd like to book an appointment"},
        {"role": "user", "text": "you're yes sir"},
        {"role": "assistant", "text": "So that's John Smith, Monday the 10th of August at "
                                      "three in the afternoon — shall I go ahead and book that in?"},
        {"role": "assistant", "text": "I'm not able to hear you at the moment — feel free to "
                                      "call back and we'll get that sorted for you."},
    ],
}
JUDGEMENT = {
    "quality_score": 2,
    "failure_tags": ["booking_error"],
    "action_needed": "callback",
    "evidence": "The caller gave a slot and a name but the booking was never confirmed.",
}


def test_the_sms_says_how_the_call_ended():
    sms = build_callback_sms(CALL, JUDGEMENT)
    assert "dead air" in sms
    assert "hung up" not in sms, (
        "the operator is being told the caller hung up on a call Susie ended"
    )


def test_the_sms_quotes_both_sides_last_words():
    sms = build_callback_sms(CALL, JUDGEMENT)
    assert "you're yes sir" in sms
    assert "I'm not able to hear you at the moment" in sms


def test_the_sms_states_the_booking_state_without_reading_the_evidence():
    """"NOT booked" is the single fact that decides whether to ring back."""
    sms = build_callback_sms(CALL, JUDGEMENT)
    assert "NOT booked" in sms

    booked = build_callback_sms({**CALL, "booking_confirmed": True}, JUDGEMENT)
    assert "NOT booked" not in booked


def test_the_judge_opinion_is_labelled_and_last():
    """Keep it — it is useful colour. Just stop presenting it as the record."""
    sms = build_callback_sms(CALL, JUDGEMENT)
    lines = sms.splitlines()
    assert lines[-1].startswith("Judge (booking_error):")
    assert JUDGEMENT["evidence"] in lines[-1]


def test_the_header_still_carries_who_and_how_bad():
    sms = build_callback_sms(CALL, JUDGEMENT)
    head = sms.splitlines()[0]
    assert "theorem_v3" in head and "+447502211207" in head and "2/5" in head


def test_a_missing_transcript_does_not_break_the_sms():
    """Judging can run on a call captured with turns=0. Degrade, never raise."""
    sms = build_callback_sms(
        {"clinic_id": "theorem_v3", "caller_number": None,
         "reason": None, "transcript": None},
        {"quality_score": 1, "failure_tags": [], "evidence": ""},
    )
    assert "unknown number" in sms
    assert "Judge: unresolved" in sms.splitlines()[-1]


def test_long_quotes_are_capped():
    """One rambling turn must not push the ending off the end of a text."""
    long_turn = "so basically " * 60
    sms = build_callback_sms(
        {**CALL, "transcript": [{"role": "user", "text": long_turn}]}, JUDGEMENT
    )
    quoted = [ln for ln in sms.splitlines() if ln.startswith("Caller last said:")][0]
    assert len(quoted) < 200
    assert "Ended:" in sms


def test_an_unmapped_reason_is_passed_through_rather_than_dropped():
    """A new reason string must not silently produce a blank ending line."""
    sms = build_callback_sms({**CALL, "reason": "some_new_reason"}, JUDGEMENT)
    assert "Ended: some_new_reason." in sms
