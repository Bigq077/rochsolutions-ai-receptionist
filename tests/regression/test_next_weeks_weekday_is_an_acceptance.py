"""Accepting a slot NEXT week read as asking for a different day.

`CA403eb7e2e6e23467397c063b10b6b526`, 2026-08-31 10:19, Vital Edge on canonical.
The owner reported having to name the slot twice.

    caller: "um anytime next week"
            [ms_conn v3] day_preference captured: next week
    Susie:  "Here's what we've got coming up — Number 1, Monday 7th September.
             Number 2, Tuesday 8th September — nine in the morning."
    caller: "uh yeah 9 in the morning on a tuesday"
            'is a slot SELECTION, not a time preference (B-90)'   <- engine agreed
            [ms_llm] DIFFERENT DAY REQUESTED steer applied        <- and then this
    Susie:  "Tuesday 8th September — Number 1, nine in the morning. Number 2,
             two in the afternoon. Number 3, three in the afternoon…"
    caller: "yeah i said 9 in the morning works"

He named option 2 verbatim, with its own time, and got the whole day read back.

WHY. `_offered_weekday_is_within_reach` asks whether the offered date is within
7 days of today, because a weekday name only pins a date while no OTHER date of
that weekday sits nearer. Today was Monday 31 August; the offered Tuesday was
8 September. Delta 8. One day over the boundary, so the guard concluded he must
mean a Tuesday it had not offered — and the nearer Tuesday, 1 September, was
never offered precisely BECAUSE he had asked for next week.

The session already knew. `day_preference` was captured as "next week" ten
seconds earlier, and that is the caller's own words about scope: a date in the
current week is not a candidate, so the offered one is unambiguous after all.

⚠️ THIS GUARD MUST KEEP FAILING IN THE SAME DIRECTION. Its docstring prices the
two errors and they are not equal:

    False negative — silent when the caller DID want another day. CAb81fe651:
    Wednesday asked four times, Tuesday served every time, hung up unbooked.
    False positive — fires on an acceptance. One wasted lookup, ~3s, and the
    caller still gets the right answer.

So this file tests BOTH directions, and the two prior calls that shaped the
guard are pinned as controls: over-correcting here buys a cosmetic win and pays
for it with a lost patient.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.media_streams.llm_stream import (
    _caller_requests_different_day,
    _offered_weekday_is_within_reach,
)


def _sess(*iso_dates: str, day_preference: str | None = None) -> dict:
    s: dict = {"available_days": [{"date": d} for d in iso_dates]}
    if day_preference:
        s["day_preference"] = day_preference
    return s


# ── The defect ──────────────────────────────────────────────────────────────

def test_next_weeks_tuesday_is_within_reach_when_the_caller_said_next_week():
    """The call. Today Monday 31 Aug, offered Tuesday 8 Sep — delta 8.

    The nearer Tuesday (1 Sep) is in THIS week, which the caller has excluded by
    their own words, so it is not a candidate and the offered day is unambiguous.
    """
    assert _offered_weekday_is_within_reach(
        _sess("2026-09-07", "2026-09-08", day_preference="next week"),
        "tuesday",
        date(2026, 8, 31),
    ) is True


def test_the_whole_utterance_reads_as_an_acceptance():
    """End to end through the predicate the steer actually calls."""
    session = _sess("2026-09-07", "2026-09-08", day_preference="next week")
    msgs = [{"role": "user", "content": "uh yeah 9 in the morning on a tuesday"}]
    assert _caller_requests_different_day(msgs, session, today=date(2026, 8, 31)) is False


# ── The controls: both prior calls that shaped this guard ───────────────────

def test_ca166de2a9_still_fires_without_a_next_week_preference():
    """Theorem, 2026-08-10. Offered Wednesday 19 Aug, today the 10th — delta 9,
    and Wednesday the 12th sat unoffered in between. Reading that as an
    acceptance is what let the model speak three times that existed on no
    calendar, 400 out four bookings, and alert the owner four times.

    No day_preference, so nothing excludes the 12th. Must stay ambiguous.
    """
    assert _offered_weekday_is_within_reach(
        _sess("2026-08-19"), "wednesday", date(2026, 8, 10)
    ) is False


def test_cab297555c_still_suppresses_at_the_seven_day_boundary():
    """8 Aug 2026. Offered Saturday 15 Aug, today the 8th — delta exactly 7, no
    Saturday in between. The call this suppression was built for; seven stays
    inclusive."""
    assert _offered_weekday_is_within_reach(
        _sess("2026-08-15"), "saturday", date(2026, 8, 8)
    ) is True


def test_cab81fe651_a_real_request_still_fires():
    """The expensive failure. The caller genuinely wants a day that is not on
    the table; the steer MUST fire or he asks four times and hangs up."""
    session = _sess("2026-09-07", "2026-09-08", day_preference="next week")
    msgs = [{"role": "user", "content": "have you got anything on the wednesday"}]
    assert _caller_requests_different_day(msgs, session, today=date(2026, 8, 31)) is True


# ── The new rule must not become a blanket week-and-a-half window ──────────

def test_a_later_weekday_of_the_same_name_is_still_ambiguous():
    """"next week" licenses next week, not any week. Offered Tuesday 15 Sep with
    today 31 Aug: Tuesday 8 Sep is nearer, inside the caller's own stated scope,
    and unoffered — so the name does not pin the 15th."""
    assert _offered_weekday_is_within_reach(
        _sess("2026-09-15", day_preference="next week"),
        "tuesday",
        date(2026, 8, 31),
    ) is False


def test_a_shift_word_still_beats_the_suppression():
    """"next tuesday" / "the tuesday after" are a move away from the day on the
    table even when the weekday word matches. Handled above this test in
    `_caller_requests_different_day`, pinned here so the new rule cannot be read
    as overriding it."""
    session = _sess("2026-09-07", "2026-09-08", day_preference="next week")
    for utt in ("what about the tuesday after",
                "can we do a different tuesday",
                "have you got another tuesday"):
        assert _caller_requests_different_day(
            [{"role": "user", "content": utt}], session, today=date(2026, 8, 31)
        ) is True, utt


def test_without_the_preference_the_old_boundary_is_unchanged():
    """The suppression is EARNED by the caller's own words. With no
    day_preference the delta-8 case stays ambiguous exactly as before, so a
    session that never captured one is not silently loosened."""
    assert _offered_weekday_is_within_reach(
        _sess("2026-09-07", "2026-09-08"), "tuesday", date(2026, 8, 31)
    ) is False


@pytest.mark.parametrize("pref", ["this week", "tomorrow", "as soon as possible",
                                  "today", "tonight", "whenever"])
def test_only_next_week_widens_it(pref):
    """The other captured preferences all point at or before the current week,
    so none of them can explain away a nearer same-weekday date."""
    assert _offered_weekday_is_within_reach(
        _sess("2026-09-08", day_preference=pref), "tuesday", date(2026, 8, 31)
    ) is False


def test_a_past_day_is_never_within_reach():
    """Unchanged. A day that has been and gone cannot be the one being accepted,
    and treating it as reachable would suppress the steer on the stalest state
    in the session."""
    assert _offered_weekday_is_within_reach(
        _sess("2026-08-25", day_preference="next week"), "tuesday", date(2026, 8, 31)
    ) is False
