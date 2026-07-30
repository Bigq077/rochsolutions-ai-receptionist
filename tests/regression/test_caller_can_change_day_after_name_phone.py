# tests/regression/test_caller_can_change_day_after_name_phone.py
"""
Bug B — the caller cannot change the day once name and phone are collected.

CAc6b971ad (30 Jul 2026). The caller gave his name and number, then asked for a
Wednesday. Seven turns, verbatim from obs:

  [14] caller: "you have a different slot open for example on wednesday"
  [17] Susie : "So that's Sarah, Tuesday the 4th ... shall I go ahead and book?"
  [19] Susie : "Let me have a look at what's available on Wednesday."
               ...then repeated the Tuesday confirmation.
  [21] Susie : "Let me check Wednesday for you." ...then offered Tuesday times.
  [23] Susie : "I've just checked — on Tuesday the 4th of August ..."
  [25] Susie : "Yes — Tuesday the 4th of August ..."
  [27] Susie : "That's exactly what I've got — Tuesday the 4th of August"
  caller hung up. No booking, no calendar event.

CAUSE — two guards, neither of which had an escape for this case:

  1. The post-collect guard blocks check_availability once name AND phone are
     confirmed, and injects "Do NOT call check_availability and do NOT ask for
     the day or time again. Say EXACTLY this, then stop: <the Tuesday
     confirmation>". Turns 17 and 19 are that instruction, obeyed. She was not
     ignoring him; she was told to say that sentence.
  2. Falling through it is not enough: the V5 follow-up branch then answers from
     THIS day's remaining times, and its else-branch returns already_retrieved
     ("present the existing slots"). That produced turns 21-27.

She also twice announced "let me check Wednesday" without checking — an artefact
of blocking a tool the model had already announced in the same turn. Releasing
the guard removes that too, because the check then really happens.

WHY A PURPOSE-BUILT PREDICATE
The neighbouring guard already stands down on _caller_wants_new_slot, so the
escape existed and was simply never wired in. But that helper also matches ANY
digit and "no"/"not"/"slot"/"when" — a caller correcting a phone number says
digits — and the guard it would release exists to stop the model spuriously
re-searching after collection (BUG-14). Reusing it trades one defect for
another, so the tests below pin BOTH directions: the real utterances must
release, and ordinary collection turns must not.

Same-day time requests ("anything later?") must release the post-collect guard
but must NOT reach a re-fetch — re-fetching leads with the earliest times again,
which is what 368b4e0 (V5) exists to prevent.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import (
    _caller_requests_different_day,
    _caller_requests_new_day_or_time,
)


def _msgs(text: str):
    return [{"role": "user", "content": text}]


# ── the real call: every one of these must be recognised ───────────────────
CAc6b971ad_UTTERANCES = [
    "you have a different slot open for example on wednesday",
    "could you offer me this slot do you have on wednesday",
    "i was asking do you have a different slot for example on a wednesday",
    # STT mangled this one ("comment" = come in, "scots" = slots) and even put
    # "tuesday" in his mouth at the end — it must still read as a day change.
    "i want to comment on a wednesday not on a tuesday what scots do you have on tuesday",
    "anything on wednesday by any chance",
    "i asked for wednesday",
    "asked for a wednesday",
]


@pytest.mark.parametrize("text", CAc6b971ad_UTTERANCES)
def test_real_day_change_requests_are_recognised(text):
    assert _caller_requests_different_day(_msgs(text)) is True
    assert _caller_requests_new_day_or_time(_msgs(text)) is True


@pytest.mark.parametrize("text", [
    "can i do wednesday instead",
    "have you got anything thursday",
    "what about tomorrow",
    "anything next week",
    "do you have another day",
    "is there a different day",
    "how about the weekend",
    "anything in august",
])
def test_other_day_change_phrasings(text):
    assert _caller_requests_different_day(_msgs(text)) is True


# ── BUG-14 protection: ordinary collection turns must NOT release the guard ─
# The post-collect guard exists because the model spuriously re-runs availability
# after name+phone and dead-ends. If these release it, that defect returns.
@pytest.mark.parametrize("text", [
    "yes it is",
    "yes please",
    "please",
    "that's right",
    "no none of those",
    "tom green",
    "sarah bell",
    "no it's 07596 897492",      # digits — _caller_wants_new_slot WOULD fire here
    "0 7 5 9 6 8 9 7 4 9 2",    # DTMF-style readback
    "yes that's the one",
    "sorry can you repeat that",
])
def test_ordinary_turns_do_not_release_the_post_collect_guard(text):
    assert _caller_requests_new_day_or_time(_msgs(text)) is False, (
        f"{text!r} would release the guard that stops the model re-searching "
        "availability after name+phone (BUG-14)"
    )
    assert _caller_requests_different_day(_msgs(text)) is False


# ── V5 boundary: same-day time change releases the guard, but never re-fetches ─
@pytest.mark.parametrize("text", [
    "anything later please",
    "do you have anything in the morning",
    "something earlier",
    "anything in the afternoon",
])
def test_same_day_time_change_releases_guard_but_is_not_a_day_change(text):
    assert _caller_requests_new_day_or_time(_msgs(text)) is True, (
        "a same-day time request must still get past the post-collect guard"
    )
    assert _caller_requests_different_day(_msgs(text)) is False, (
        "a same-day time request must NOT reach a re-fetch — V5 (368b4e0) serves "
        "it from available_days, because re-fetching leads with the earliest "
        "times again"
    )


# ── edge cases ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("messages", [[], [{"role": "user", "content": ""}]])
def test_no_utterance_is_not_a_day_change(messages):
    assert _caller_requests_different_day(messages) is False
    assert _caller_requests_new_day_or_time(messages) is False


def test_may_is_not_treated_as_a_month():
    """"may" is a common auxiliary verb — the same omission the existing
    _NEW_SLOT_INTENT_WORDS makes deliberately."""
    assert _caller_requests_different_day(_msgs("may i book that please")) is False


def test_weekday_inside_a_longer_word_does_not_match():
    """Word-boundary tokenising, not substring matching."""
    assert _caller_requests_different_day(_msgs("i work sundays at the weekend")) is True
    assert _caller_requests_different_day(_msgs("my monday-ish schedule")) is True
    assert _caller_requests_different_day(_msgs("i am attuned to it")) is False
