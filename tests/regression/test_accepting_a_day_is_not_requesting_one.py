"""
Regression (CAb297555c, Vital Edge, 2026-08-08 09:10:41): the caller accepted
the day they were offered and the engine re-searched it.

    09:10:33  Susie: "the next day we have available is Saturday 15th August —
                      eleven in the morning or midday — would either of those work?"
    09:10:41  caller: "yeah 11 in the morning works for saturday"
    09:10:41  [ms_llm] DIFFERENT DAY REQUESTED steer applied iter=1
    09:10:46  tool: check_availability after_date=2026-08-15 day_window=1
    09:10:47  Susie: "Saturday the 15th of August at eleven in the morning works."

The turn took 5.3s against a ~2.3s baseline for a one-iteration turn, and the
extra round trip re-fetched the day it had just finished offering.

`_caller_requests_different_day` was bare word-set membership: "saturday" is a
weekday word, so it returned True. Accepting a day and requesting one are
word-identical utterances — the only thing separating them is what has already
been offered, which the predicate never looked at.

── The asymmetry this file exists to protect ───────────────────────────────
The two errors are not equally bad, so the suppression is deliberately hard to
earn:

    false negative (silent when the caller DID want another day)
        = CAb81fe651: Wednesday asked four times, Tuesday served every time,
          caller hung up unbooked.
    false positive (fires on an acceptance)
        = one wasted tool round trip, ~3s, and the caller still gets the right
          answer.

So most of the tests below assert that the predicate STILL FIRES. Those are the
important ones. If a future change makes the suppression cleverer, these are
what stop it becoming the worse bug.
"""
from __future__ import annotations

from datetime import date as _date

import pytest

from app.media_streams.llm_stream import (
    _caller_requests_different_day,
    _different_day_steer,
    _offered_day_vocabulary,
)


# The call ran on Saturday 8 August 2026. Every assertion below is about a
# session holding 15 August, and since CA166de2a9 the suppression asks how far
# away that is — so the clock is pinned to the real call date rather than left
# to whenever the suite happens to run. Without this the file passes today and
# starts failing on 16 August for no reason anyone would connect to this change.
CALL_DAY = _date(2026, 8, 8)


@pytest.fixture(autouse=True)
def _pin_clinic_today(monkeypatch):
    monkeypatch.setattr("app.date_context.clinic_today", lambda *a, **k: CALL_DAY)


# Session as it stood at 09:10:41 — Saturday 15 August 2026 on the table.
OFFERED = {
    "available_days": [{"date": "2026-08-15", "day_label": "Saturday 15th August"}],
    "last_offered_slots": [
        {"start": "2026-08-15T11:00:00"},
        {"start": "2026-08-15T12:00:00"},
    ],
}


def _msgs(text: str) -> list:
    return [{"role": "user", "content": text}]


# ── 1. The defect ───────────────────────────────────────────────────────────

def test_accepting_the_offered_day_is_not_a_change_request():
    """The exact utterance from the call."""
    assert _caller_requests_different_day(
        _msgs("yeah 11 in the morning works for saturday"), OFFERED
    ) is False


def test_the_steer_stays_silent_on_that_turn():
    """One level up: the steer is what pushed the model back to the tool."""
    assert _different_day_steer(OFFERED, _msgs("yeah 11 in the morning works for saturday")) == ""


@pytest.mark.parametrize(
    "utterance",
    [
        "saturday works",
        "yeah saturday is fine",
        "yeah that saturday",
        "the 15th of august works",
    ],
)
def test_other_ways_of_accepting_the_same_day(utterance):
    assert _caller_requests_different_day(_msgs(utterance), OFFERED) is False


# ── 2. What must STILL fire — the important half ────────────────────────────

@pytest.mark.parametrize(
    "utterance,why",
    [
        ("can we do sunday instead", "a different weekday"),
        ("how about wednesday", "a different weekday, no shift word"),
        ("next saturday please", "same weekday, different week — 'next'"),
        ("the saturday after", "same weekday, different week — 'after'"),
        ("is there another saturday", "same weekday — 'another'"),
        ("anything tomorrow", "relative word, not resolvable without a clock"),
        ("what about next week", "change phrase"),
        ("any other day", "change phrase"),
        ("could we do the weekend", "relative word"),
        ("saturday or maybe sunday", "one named day is not offered"),
    ],
)
def test_a_real_change_request_still_fires(utterance, why):
    """
    CAb81fe651 is the cost of getting this wrong: the caller asked for
    Wednesday four times, was served Tuesday every time, and hung up unbooked.
    Every row here is a case where the predicate must stay True.
    """
    assert _caller_requests_different_day(_msgs(utterance), OFFERED) is True, why


# ── 3. Failing safe when nothing is known ───────────────────────────────────

@pytest.mark.parametrize("session", [None, {}, {"available_days": []}])
def test_without_offered_days_the_old_behaviour_is_kept(session):
    """
    `session` defaults to None so any call site that has not been threaded
    keeps the conservative behaviour rather than silently gaining the
    suppression. With nothing offered the caller cannot be accepting anything.
    """
    assert _caller_requests_different_day(_msgs("saturday works"), session) is True


def test_an_utterance_with_no_day_word_is_not_a_change_request():
    """Unchanged from before — pinned so the new branches did not disturb it."""
    assert _caller_requests_different_day(_msgs("yeah that works"), OFFERED) is False


# ── 4. The vocabulary helper ────────────────────────────────────────────────

def test_the_offered_vocabulary_covers_weekday_and_month():
    assert _offered_day_vocabulary(OFFERED) == frozenset({"saturday", "august"})


@pytest.mark.parametrize(
    "session",
    [
        None,
        {},
        {"available_days": [{"date": "not-a-date"}]},
        {"available_days": [{"date": "2026-13-45"}]},  # parses shape, not calendar
        {"last_offered_slots": [{"start": ""}]},
    ],
)
def test_unusable_session_state_yields_an_empty_vocabulary(session):
    """
    An empty vocabulary is read by the caller as "cannot rule out a change
    request", so malformed state fails toward firing rather than toward the
    silent-when-it-mattered failure.
    """
    assert _offered_day_vocabulary(session) == frozenset()


def test_the_vocabulary_reads_every_source_of_an_offered_day():
    """Four independent keys carry an offered day depending on the path taken;
    missing one would silently reopen the misfire on that path."""
    assert "monday" in _offered_day_vocabulary({"last_spoken_slot_date": "2026-08-10"})
    assert "monday" in _offered_day_vocabulary({"v3_last_presented_date_hint": "2026-08-10"})
    assert "monday" in _offered_day_vocabulary({"last_offered_slots": [{"start": "2026-08-10T09:00"}]})
    assert "monday" in _offered_day_vocabulary({"available_days": [{"date": "2026-08-10"}]})
