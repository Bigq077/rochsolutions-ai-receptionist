"""Hold-head audit of 12-13 Sep 2026: the same-act echo after a head.

Ten demo calls, 56 heads, and the commonest wrong sound was the head saying
something and the model saying it again ~1.5 s later:

    head: "I'm here, yes —"     model: "yes, still here — could I take..."
    head: "I'm here, yes —"     model: "yes, I'm here — could I take..."
                                                        (CAe541a6, CAddd98c, CAdb28a1)
    head: "Thank you —"         model: "thanks Quentin — I've got you on..."
    head: "Thanks, got that —"  model: "thanks Sandra — I've got you on..."
                                                        (7 of 7 name turns)

`hold_speech.strip_head_echo` and `ACK_OPENER_RE` were written for this and
are called from nowhere; e669ef45 (H) added "still here" to a regex nothing
runs. The live seam is `join_after_head`, which strips a WORD-FOR-WORD echo
of the head and, conditional on the head, a repeated apology. These two
same-act echoes now join the apology rule there, with the same safety: the
strip fires only after a head that already performed that act, and the
caller's NAME survives -- "Thank you — Quentin, I've got you on".
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import join_after_head


@pytest.mark.parametrize("chunk", [
    "yes, still here — could I take your first name and surname?",
    "yes, I'm here — could I take your first name and surname?",
    "still here — could I take your first name and surname?",
    "I'm still here — could I take your first name and surname?",
])
def test_a_check_in_echo_after_a_check_in_head_is_dropped(chunk):
    out = join_after_head(chunk, "I'm here, yes —")
    assert out == "could I take your first name and surname?", out


@pytest.mark.parametrize("head", ["Thank you —", "Thanks, got that —", "Thanks for that —"])
def test_a_thanks_echo_after_a_thanks_head_keeps_the_name(head):
    out = join_after_head("thanks Quentin — I've got you on oh seven five oh two", head)
    assert out == "Quentin, I've got you on oh seven five oh two", out
    out = join_after_head("Thanks Giacomo — and your surname?", head)
    assert out == "Giacomo, and your surname?", out
    out = join_after_head("thank you — and your surname?", head)
    assert out == "and your surname?", out


def test_the_strip_is_conditional_on_the_head():
    """After a LOOKUP head the model's thanks is its first thanks: kept."""
    assert join_after_head("thanks Quentin — I've got you on", "Let me see what Tuesday looks like —") \
        == "thanks Quentin — I've got you on"
    assert join_after_head("I'm here, yes — could I take your name", "Thank you —") \
        == "I'm here, yes — could I take your name"


def test_a_chunk_that_is_nothing_but_the_echo_is_left_alone():
    """A head with nothing behind it is the dead end this family exists to
    remove; the pure-duplicate decision has one owner."""
    assert join_after_head("thanks", "Thank you —") == "thanks"
    assert join_after_head("thanks", "Thank you —", suppress_pure_duplicate=True) == ""
    assert join_after_head("yes, I'm here.", "I'm here, yes —", suppress_pure_duplicate=True) == ""


def test_an_ordinary_continuation_is_untouched():
    assert join_after_head("I've got you on oh seven", "Thank you —") == "I've got you on oh seven"
    assert join_after_head("Right — I've got you on oh seven", "Thank you —") == "right — I've got you on oh seven"
