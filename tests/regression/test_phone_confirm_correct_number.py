# tests/regression/test_phone_confirm_correct_number.py
"""
"that's the correct number" confirms the read-back, like it always has on the
deterministic path.

CA3145c15f (30 Jul 2026), the same call as
tests/regression/test_phone_step_markers_agree.py. Susie asked "is that the best
number for the booking?" and the caller answered **"yes that's the correct
number"**. `_is_use_this_number` matched nothing: "correct number" was not a
signal, and the five-word utterance was too long for the short-affirmative
fallback. The confirm did not fire.

The question is a yes/no about whether the number is RIGHT, so "correct" and
"right" are the words callers reach for. `flow._HG_YES` has accepted them on the
deterministic gate for months — this is the LLM path catching up, exactly as
3bbe4f0 did for bare "yes".

Negatives must stay negative: "that's not the correct number" is the same
sentence with the meaning inverted, and confirming it would write a booking to a
number the caller had just rejected.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import _is_use_this_number


@pytest.mark.parametrize("answer", [
    "yes that's the correct number",      # the exact utterance from CA3145c15f
    "that's the correct number",
    "thats the correct number",
    "that is the correct number",
    "yeah that's the right number",
    "that's the right number",
])
def test_correct_and_right_confirm_the_number(answer):
    assert _is_use_this_number(answer) is True, (
        f"{answer!r} confirms the read-back number"
    )


@pytest.mark.parametrize("answer", [
    "no that's not the correct number",
    "that's not the correct number",
    "that's the wrong number",
    "no that's not right",
    "that's a different number",
    "no, I'll give you another number",
])
def test_rejections_never_confirm(answer):
    assert _is_use_this_number(answer) is False, (
        f"{answer!r} rejects the number — confirming it would book to a number "
        "the caller just refused"
    )


@pytest.mark.parametrize("answer", [
    "it is",
    "that's the best number",
    "yes",
    "use this number",
])
def test_previously_accepted_answers_still_confirm(answer):
    """The widening must not disturb what already worked."""
    assert _is_use_this_number(answer) is True, f"{answer!r} regressed"
