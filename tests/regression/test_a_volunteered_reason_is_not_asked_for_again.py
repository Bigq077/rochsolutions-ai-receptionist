"""A caller who opens with their complaint is not asked what the appointment is for.

Live call, 2026-08-23, and reproduced in call-test scenario 2.2:

    CALLER: Shoulder pain
    susie : I'm sorry to hear that - a painful shoulder can really get in the
            way of day-to-day life. Would you like to book...?
    CALLER: Yes
    susie : Right - What's the appointment for?      <-- already answered

`_next_question_after_booking_ack` guards this with `_reason_already_known`, and
the guard was correct in intent but read a key nothing writes. The seeded
soft_context schema in media_streams/session.py has eight keys and "reason" is
not among them; the Haiku extractor writes `condition_notes`, defined as "brief
description of the caller's complaint or condition" — the booking reason. So the
soft-context branch of the guard could never return True.

Why this is worth a regression test rather than a one-line fix and move on: on a
real call it costs one turn. In the scripted call-test suite it is fatal. The
unexpected question consumes the next scripted response, so every answer after it
lands on the wrong question and the script runs out before the booking completes
— which is the mechanism behind the `booking_confirmed` failures, and the reason
the suite's pass rate was never a measure of Susie.
"""
import pytest

from app.media_streams.connection import _reason_already_known


class TestTheReasonIsRecognisedWhereverItWasRecorded:
    def test_a_volunteered_complaint_counts(self):
        """The path that was broken: caller's opening words, via soft_context."""
        session = {"soft_context": {"condition_notes": "shoulder pain"}}
        assert _reason_already_known(session) is True

    def test_an_answer_to_the_explicit_question_counts(self):
        assert _reason_already_known({"collected": {"reason": "left ankle pain"}}) is True

    def test_a_reason_on_the_session_counts(self):
        assert _reason_already_known({"reason": "lower back pain"}) is True

    @pytest.mark.parametrize("session", [
        {},
        {"soft_context": {}},
        {"soft_context": {"condition_notes": None}},
        {"soft_context": {"condition_notes": "   "}},
        {"collected": {"reason": ""}},
        {"reason": None},
    ])
    def test_nothing_recorded_means_ask(self, session):
        """Fail towards ASKING.

        A wrongly-suppressed question books an appointment with no reason on it,
        which reaches the calendar entry and the confirmation SMS. A wrongly-kept
        question costs one turn. The asymmetry decides the default.
        """
        assert _reason_already_known(session) is False


class TestTheSeededSchemaAndTheGuardAgree:
    def test_condition_notes_is_a_real_seeded_key(self):
        """Pins the fix to the schema rather than to a string I chose.

        If someone renames the soft_context key, this fails here instead of
        silently restoring the redundant question on live calls.
        """
        from app.media_streams.session import DEFAULT_MS_SESSION
        soft = DEFAULT_MS_SESSION["soft_context"]
        assert "condition_notes" in soft
        assert "reason" not in soft, (
            "if a 'reason' key has been added to soft_context, the guard's "
            "belt-and-braces branch is now live and this test should say so"
        )
