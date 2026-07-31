# tests/regression/test_different_day_steers_to_the_tool.py
"""
A different-day request pushes the model to check_availability instead of
letting it answer from the previous day's slots.

CAb81fe651 (30 Jul 2026, build ad09f3e). The caller asked for Wednesday four
times. Every reply served Tuesday, and the fourth said "Wednesday the 5th is what
we've got — seven in the evening" about a slot on Tuesday the 4th. He hung up at
231 s, unbooked. Name, number, slot and reason all collected.

5b0c9c2 released the two guards that used to block the re-check, and they do
release: `_caller_requests_different_day` returns True on all four of his real
utterances (pinned below, STT mangling and all). The model simply never called
check_availability. It answered from the Tuesday slots still in its message
history — to it, perfectly good data. Releasing a block cannot fix that, because
there was no block left. The steer pushes toward the tool instead.

Two failure modes to stay away from, both worse than the defect:

  * firing when there is nothing to answer from — noise on every ordinary turn;
  * surviving into the presentation pass, where it would tell the model to ignore
    the very slots it just fetched.

Both are pinned here.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import (
    _caller_requests_different_day,
    _different_day_steer,
)

# Verbatim from the transcript, STT damage included ("by any charts", "by
# neutrons", "no jeff" for "no there"). The fix must survive real ASR output, not
# the sentence the caller meant to say.
JULES_UTTERANCES = [
    "actually do you have any availability on wednesday by any charts",
    "now do you have any available on wednesday by any chance",
    "no jeff any availability on wednesday by neutrons",
    "no jeff any bits on wednesday",
    "no i like to have a slot on wednesday please",
]

# The state the call was in: Tuesday slots offered and held in context.
TUESDAY_IN_CONTEXT = {
    "last_offered_slots": [
        {"iso": "2026-08-04T18:30:00"},
        {"iso": "2026-08-04T19:00:00"},
    ],
    "available_days": {"2026-08-04": ["18:30", "19:00"]},
}


def _msgs(text: str):
    return [{"role": "user", "content": text}]


class TestTheCallThatWasLost:
    @pytest.mark.parametrize("utterance", JULES_UTTERANCES)
    def test_the_predicate_was_never_the_problem(self, utterance):
        """Pinned so a future 'fix' to the predicate is not chased again — it
        already returns True on every one of these."""
        assert _caller_requests_different_day(_msgs(utterance)) is True

    @pytest.mark.parametrize("utterance", JULES_UTTERANCES)
    def test_every_wednesday_ask_now_steers_to_the_tool(self, utterance):
        steer = _different_day_steer(dict(TUESDAY_IN_CONTEXT), _msgs(utterance))
        assert "check_availability" in steer, (
            f"{utterance!r} must push the model to the tool — answering from "
            "context is how this call was lost"
        )

    def test_the_steer_forbids_the_exact_sentence_that_was_said(self):
        """"Wednesday the 5th is what we've got" attached the new day's name to
        the old day's time — the spoken form of C1."""
        steer = _different_day_steer(dict(TUESDAY_IN_CONTEXT), _msgs(JULES_UTTERANCES[0]))
        assert "NEVER attach the new day's name to a time that came from the old one" in steer
        assert "already got" in steer, (
            "\"We've already got Tuesday…\" was the second refusal — the steer "
            "must name that phrasing"
        )


class TestItStaysQuiet:
    @pytest.mark.parametrize("utterance", [
        "yes please",
        "half past six works for me",
        "that's the best number",
        "sarah jenkling",
        "my left shoulder, nothing serious",
        "yes go ahead and book me in",
    ])
    def test_ordinary_turns_get_no_steer(self, utterance):
        assert _different_day_steer(dict(TUESDAY_IN_CONTEXT), _msgs(utterance)) == ""

    def test_no_slots_in_context_means_no_steer(self):
        """Nothing to answer from — the model reaches for the tool unprompted and
        the steer would be noise on the first availability question of a call."""
        assert _different_day_steer({}, _msgs("do you have anything on wednesday")) == ""

    def test_suppressed_once_the_tool_has_run_this_turn(self):
        """The presentation pass must never be told to ignore the slots it just
        fetched — that would re-create the defect from the other direction."""
        session = dict(TUESDAY_IN_CONTEXT, _check_av_ran_turn=True)
        assert _different_day_steer(session, _msgs(JULES_UTTERANCES[0])) == ""

    @pytest.mark.parametrize("utterance", [
        "anything later that day",
        "do you have anything in the morning",
        "something a bit earlier",
    ])
    def test_same_day_time_changes_are_not_a_day_change(self, utterance):
        """These belong to the V5 deterministic follow-up. Re-fetching leads with
        the earliest times again — the exact problem 368b4e0 exists to prevent."""
        assert _different_day_steer(dict(TUESDAY_IN_CONTEXT), _msgs(utterance)) == ""

    def test_empty_and_missing_input_never_raises(self):
        """This runs on every turn of every call. An exception here kills the
        turn, which is worse than any wording defect it could prevent."""
        assert _different_day_steer({}, []) == ""
        assert _different_day_steer({}, None) == ""
        assert _different_day_steer(dict(TUESDAY_IN_CONTEXT), []) == ""
