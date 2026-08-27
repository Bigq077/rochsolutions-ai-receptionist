"""
Regression: "what about the 22nd" was answered "that day is fully booked" by a
system that had never looked at it.

B-106 — CA8c4efedbc7fbc2cc74ab47fcc834ecdf, jv_v1, build e449791c, JV go-live
rehearsal, 27 August 2026.

    13:54:16  Susie:  "The available slots for Wednesday 2nd September are —
                       Number 1, half past five in the evening. Number 2,
                       quarter past six in the evening."
    13:54:31  caller: "uh what about the 22nd"
    13:54:35  Susie:  "Wednesday 22nd September is fully booked, I'm afraid —
                       the available slots for Wednesday 2nd September are …"

The 22nd was never looked at. No lookup ran, no error was raised, no alert
fired. `_caller_requests_different_day` matched the caller's words against
`_DIFFERENT_DAY_WORDS` — weekday names, month names and the relative tokens,
and nothing else. "the 22nd" is none of those, so the predicate returned False,
the different-day stand-down never happened, and check_availability was refused
as `already_retrieved`. That refusal hands the model the day it had ALREADY
offered together with a message reading "present the existing slots", and the
model reconciled the contradiction the only way it could: by declaring the day
it could not see to be full.

WHY THIS ONE IS THE QUIET ONE. A caller told a day is fully booked does not ask
about it again. The call sounds completely normal — this one carried on and
offered other days — and nothing anywhere records that a patient asked for a
date the clinic very probably had free. There is no failed booking to count.

── The asymmetry (inherited from test_accepting_a_day_is_not_requesting_one) ──
    false negative (silent when the caller DID want another day) = this call.
    false positive (fires on an acceptance) = one wasted tool round trip, ~3s,
        and the caller still gets the right answer.
So the tests that matter most are the ones asserting the predicate FIRES; the
rest pin the two shapes that must NOT be read as dates — a list position and an
incidental number.
"""
from __future__ import annotations

from datetime import date as _date

from app.media_streams.llm_stream import (
    _caller_requests_different_day,
    _dates_of_month_the_caller_named,
)

# Thursday 27 August 2026 — the day of the call.
CALL_DAY = _date(2026, 8, 27)

WED2 = "2026-09-02"

# The session as it stood at 13:54:31: Wednesday 2nd September on the table,
# two times read out as a numbered list. The 22nd is also a Wednesday, three
# weeks further on, and was never offered.
OFFERED_2ND = {
    "available_days": [{"date": WED2, "day_label": "Wednesday 2nd September"}],
    "last_offered_slots": [
        {"start": WED2 + "T17:30:00+01:00", "spoken": "half past five in the evening"},
        {"start": WED2 + "T18:15:00+01:00", "spoken": "quarter past six in the evening"},
    ],
    "v3_last_offered_day_iso": WED2,
}


def _asks(utterance: str, session=OFFERED_2ND) -> bool:
    return _caller_requests_different_day(
        [{"role": "user", "content": utterance}], session, today=CALL_DAY
    )


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_utterance_that_was_answered_fully_booked():
    """Verbatim from CA8c4efedb at 13:54:31."""
    assert _asks("uh what about the 22nd") is True


def test_the_same_request_spoken_as_words():
    """B-104 is the standing evidence that this ASR also writes ordinals out."""
    assert _asks("what about the twenty second") is True


def test_a_named_weekday_with_an_unoffered_date_also_fires():
    """The same-weekday shape of CA166de2a9, now carrying a date.

    "wednesday" alone names the day already on the table and reads as an
    acceptance. The 22nd is what makes it a request, and before B-106 the date
    was the half nothing could see.
    """
    assert _asks("have you got anything on wednesday the 22nd") is True


def test_with_nothing_offered_yet_a_date_is_a_request():
    """Nothing on the table means the caller cannot be accepting anything."""
    assert _asks("can i come in on the 22nd", session={}) is True


# ---------------------------------------------------------------------------
# The guards. Each of these, loosened, spends a lookup on an acceptance.
# ---------------------------------------------------------------------------
def test_the_offered_date_itself_is_not_a_change_request():
    assert _asks("yeah the 2nd of september is good") is False


def test_a_list_position_is_not_a_date():
    """"the second one" folds to the same "2" that "the 2nd" does.

    A readout is capped at three options, so a WORD ordinal is only read as a
    date above that cap — which is what keeps a caller picking option 2 off a
    numbered list from being sent back to the diary.
    """
    assert _asks("the second one") is False
    assert _asks("number 2") is False
    assert _dates_of_month_the_caller_named("the second one") == set()


def test_an_incidental_number_is_not_a_date():
    """Accepting a time carries a digit and must not spend a lookup."""
    assert _asks("yeah quarter past 6 works") is False
    assert _dates_of_month_the_caller_named("quarter past 6 works") == set()


def test_a_same_day_follow_up_is_untouched():
    """The V5 deterministic follow-up owns this one — re-fetching would lead
    with the earliest times again, which is what 368b4e0 exists to prevent."""
    assert _asks("do you have any other slots on that day") is False


# ---------------------------------------------------------------------------
# The extractor, directly
# ---------------------------------------------------------------------------
def test_digit_ordinals_are_read_at_any_value():
    """A position is spoken "number two", never "the 2nd", so the digit form
    carries no ambiguity to protect against."""
    assert _dates_of_month_the_caller_named("the 2nd") == {2}
    assert _dates_of_month_the_caller_named("the 22nd") == {22}
    assert _dates_of_month_the_caller_named("the 1st or the 3rd") == {1, 3}


def test_word_ordinals_are_read_only_above_the_option_cap():
    assert _dates_of_month_the_caller_named("the twenty second") == {22}
    assert _dates_of_month_the_caller_named("the fifth") == {5}
    # 1-3 are left to the day-word tests rather than guessed at.
    assert _dates_of_month_the_caller_named("the third") == set()


def test_an_impossible_day_of_month_is_dropped():
    assert _dates_of_month_the_caller_named("the 40th") == set()
