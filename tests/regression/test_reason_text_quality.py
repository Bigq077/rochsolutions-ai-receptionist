"""B-129 - the reason on the call record was a clipped fragment.

Found while reviewing CAf984d8c5 (demo line, 2026-09-01), whose record read:

    pre-summary reason: "for my left shoulder it's been"

`_extract_reason` takes a fixed span around the body part, so it opens on the
run-up ("for my left shoulder") and closes mid-clause ("it's been").
The label half is B-130, in its own file.
"""
import pytest

from app.media_streams.first_turn_extractor import _extract_reason


# -- the reason phrase itself ------------------------------------------------

class TestReasonPhraseIsNotAFragment:
    def test_the_live_call_that_prompted_this(self):
        """CAf984d8c5 recorded "for my left shoulder it's been"."""
        got = _extract_reason(
            "um yeah i'd like to book an appointment for my left shoulder "
            "it's been really sore for a couple of weeks"
        )
        assert got == "left shoulder it's been really sore for a couple of weeks"

    @pytest.mark.parametrize("utterance,expected", [
        ("hi id like to book an appointment for my knee please", "knee"),
        ("hi i'd like to book for shoulder pain", "shoulder pain"),
        ("uh hi there i think i need to see someone about my knee", "knee"),
    ])
    def test_the_run_up_and_the_dangle_are_dropped(self, utterance, expected):
        assert _extract_reason(utterance) == expected

    def test_the_booking_clause_is_not_swallowed(self):
        """The forward window stops where the caller stops describing and
        starts transacting; without it this ended "...and i'd like"."""
        got = _extract_reason(
            "hi i've worked tight hamstring from running and i'd like to "
            "book a sports massage"
        )
        assert "i'd like" not in got
        assert "hamstring" in got

    @pytest.mark.parametrize("utterance", [
        "hi i'd like to book my knee and my shoulder are both sore",
        "hi it's not my knee it's my hip",
        "hi can you call me back later",
    ])
    def test_the_fail_open_guards_are_untouched(self, utterance):
        """Trimming must change the TEXT only. Which openings produce a reason
        at all is what suppresses the reason question, and that is verified on
        live calls - measured identical across all 556 stored openings."""
        assert _extract_reason(utterance) is None

    def test_an_opening_that_produced_a_reason_still_produces_one(self):
        for utterance in (
            "hi i'd like to book please it's for knee pain",
            "yeah i'd like to book an appointment my left ankle is sore",
            "hi my call's been very sore lately",
        ):
            assert _extract_reason(utterance), utterance
