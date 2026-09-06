"""The reason a clinic reads back must name the body part, and only that.

`collected["reason"]` is quoted verbatim into the call-summary row and the
owner SMS. On the demo line that is cosmetic; on JV it is what Marcus's team
reads. Three live calls on 2026-09-06 put three different kinds of rubbish in
it.

1. THE BODY PART WAS LOST. `CA9fbb1aee`, 23:28:

       caller  "can i book an appointment my achilles is stiff for the first
                few minutes every morning it eases as i walk"
       reason  'stiff for the first few minutes'

   A complaint with no anatomy in it. `_reason_window` BREAKS on a
   transactional word rather than skipping it, and the look-back opens three
   words before the anchor — so "book an appointment my achilles" put
   "appointment" INSIDE the look-back and the window died before it reached
   "achilles". Pass 1 returned "", Pass 2 anchored on the symptom instead.

   **A window built around an anchor must contain the anchor.** The two
   directions are different jobs: forward, a stop word ENDS the reason ("...my
   ankle, can i book"); backward, it only marks where the run-up stopped, so it
   moves the start rather than emptying the window.

2. THE CALLER'S DISCLAIMER WAS KEPT. `CA798c2701`, 23:26 —
   "ankle it's nothing serious" — and again as "left ankle it's nothing serious
   though". One complaint, one verdict on it; only the first half is the
   reason.

3. AN EXPLETIVE WENT IN. 'fucking ankle', 2026-09-06 22:07. It carries no
   clinical information and it would have gone out in an owner SMS.

WHAT IS DELIBERATELY NOT TRIMMED, because over-trimming costs more than the
noise does: "not bad" and "not much" are as often DESCRIPTION as dismissal
("not bad in the morning but terrible by the evening"), "nothing like" is not a
disclaimer at all, and "bloody" is a real clinical word before it is an
intensifier.
"""
from __future__ import annotations

import pytest

from app.media_streams.first_turn_extractor import _extract_reason


# ── 1. The body part survives the booking clause ────────────────────────────

@pytest.mark.parametrize("said,must_contain", [
    # the live one
    ("um yeah can i book an appointment my achilles is stiff for the first "
     "few minutes every morning it eases as i walk", "achilles"),
    ("can i book an appointment my knee has been clicking going down stairs",
     "knee"),
    ("i'd like to book an appointment my shoulder has been aching for weeks",
     "shoulder"),
    ("could i book an appointment my back went yesterday lifting a box",
     "back"),
])
def test_a_stop_word_between_the_run_up_and_the_anchor_does_not_lose_it(
    said, must_contain
):
    reason = _extract_reason(said) or ""
    assert must_contain in reason.lower(), (
        f"the reason names no anatomy: {reason!r}"
    )


def test_the_live_achilles_call():
    """Anatomy AND the hallmark feature — what the clinic actually wants."""
    reason = _extract_reason(
        "um yeah can i book an appointment my achilles is stiff for the first "
        "few minutes every morning it eases as i walk"
    )
    assert reason
    assert "achilles" in reason.lower()
    assert "stiff" in reason.lower()


def test_the_window_always_contains_its_anchor():
    """Stated as the property, so it cannot regress through a different word.

    Every transactional stop word is tried in the position that broke it.
    """
    from app.media_streams.first_turn_extractor import _REASON_STOP_WORDS

    for stop in sorted(_REASON_STOP_WORDS):
        said = f"i would like to {stop} my ankle"
        reason = (_extract_reason(said) or "").lower()
        assert "ankle" in reason, (
            f"{stop!r} between the run-up and the anchor lost it: {reason!r}"
        )


# ── 2. The caller's own verdict is not the reason ───────────────────────────

@pytest.mark.parametrize("said,expected", [
    ("hi there yeah i'd like to book an appointment for my ankle it's "
     "nothing serious", "ankle"),
    ("yeah i'd like to book an appointment for my left ankle it's nothing "
     "serious though", "left ankle"),
    ("can i book in for my knee nothing major", "knee"),
    ("book me in for my shoulder, no big deal", "shoulder"),
])
def test_a_disclaimer_is_dropped(said, expected):
    assert _extract_reason(said) == expected


@pytest.mark.parametrize("said,must_keep", [
    # "not bad" is description, not dismissal — the half after it is the point
    ("my knee is not bad in the morning but terrible by the evening",
     "terrible"),
    # "nothing like" is not a disclaimer
    ("my shoulder is nothing like it was after the operation", "operation"),
])
def test_a_description_that_merely_looks_like_a_disclaimer_survives(
    said, must_keep
):
    reason = (_extract_reason(said) or "").lower()
    assert must_keep in reason, reason


# ── 3. Expletives ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("said,expected", [
    ("um yeah i'd like to book an appointment for my um fucking ankle",
     "ankle"),
    ("can i book in for my effing shoulder", "shoulder"),
])
def test_an_expletive_is_not_quoted_back_to_the_clinic(said, expected):
    assert _extract_reason(said) == expected


def test_bloody_is_left_alone():
    """It is a clinical word before it is an intensifier, and a reason field
    that silently deletes it is worse than one that quotes a swear."""
    reason = (_extract_reason("my knee is bloody swollen") or "").lower()
    assert "bloody" in reason, reason


# ── What must not change ────────────────────────────────────────────────────

@pytest.mark.parametrize("said,expected", [
    ("um yeah i would like to book an appointment for my shoulder", "shoulder"),
    ("um yeah can i book in for my knee please", "knee"),
])
def test_the_simple_openings_are_unchanged(said, expected):
    assert _extract_reason(said) == expected


def test_a_described_complaint_keeps_its_detail():
    reason = (_extract_reason(
        "i twisted my ankle playing football on saturday") or "").lower()
    assert "twisted" in reason and "ankle" in reason and "football" in reason


def test_two_complaints_still_decline():
    """The fail-open guard: which one is THE reason is a coin toss, and this
    change must not make it guess."""
    assert _extract_reason("my knee and my elbow are both playing up") is None


def test_a_correction_still_declines():
    assert _extract_reason("not my knee, it's my hip") is None


def test_nothing_is_invented():
    for said in ("hello", "can i book an appointment please", ""):
        assert _extract_reason(said) in (None, ""), said
