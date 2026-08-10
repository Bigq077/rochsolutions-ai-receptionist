"""
Regression (CA166de2a99b97322f3d7ead3645b97d86, Theorem, 2026-08-10 14:58): the
caller asked for an earlier Wednesday and was read back times that existed on no
calendar.

    14:58:28  check_availability → ONE day: Wednesday 19 August, 14:00/15:00/16:00.
              (The 12th had 1 raw slot and none of it in the afternoon, so the
              tool skipped straight past it.)
    14:58:44  caller: "um are you three earlier earlier date on a wednesday"
              [no tool call — iteration 1 goes straight to speech]
    14:58:47  Susie:  "Wednesday the 12th of August — I've got two in the
                       afternoon, three, four."
    15:00:12  Acuity 400: 2026-08-12T16:00 "is not an available time slot"
    …×4, four manual-followup alerts to the owner, ~4 minutes of call.

The times were the 19th's, re-badged onto the 12th. `_caller_requests_different_day`
compared the caller's "wednesday" against a vocabulary of weekday NAMES — and the
day on offer was also a Wednesday — so it read him as ACCEPTING the day already on
the table. `_different_day_steer` stayed silent, nothing pushed the model back to
the tool, and it answered from the slots still in its message history. That is the
CAb81fe651 failure mode reopened for one specific shape: a caller moving to a
different date with the same weekday name.

He was only rescued by his own suggestion of "thursday the 13th" — a different
weekday WORD, the one thing the name test could see.

── The asymmetry (inherited from test_accepting_a_day_is_not_requesting_one) ───
    false negative (silent when the caller DID want another day) = this call.
    false positive (fires on an acceptance)                      = one wasted
        tool round trip, ~3s, and the caller still gets the right answer.
So the tests that matter most here are the ones asserting the predicate FIRES.
"""
from __future__ import annotations

from datetime import date as _date

import pytest

from app.media_streams.llm_stream import (
    _caller_requests_different_day,
    _different_day_steer,
    _offered_weekday_is_within_reach,
)

# Monday 10 August 2026 — the day of the call.
CALL_DAY = _date(2026, 8, 10)

# Session as it stood at 14:58:44: Wednesday the 19th on the table, nine days out.
# The 12th is also a Wednesday and was never offered.
OFFERED_19TH = {
    "available_days": [{"date": "2026-08-19", "day_label": "Wednesday 19th August"}],
    "last_offered_slots": [
        {"start": "2026-08-19T14:00:00"},
        {"start": "2026-08-19T15:00:00"},
        {"start": "2026-08-19T16:00:00"},
    ],
    "v3_last_offered_day_iso": "2026-08-19",
}


@pytest.fixture(autouse=True)
def _pin_clinic_today(monkeypatch):
    """The suppression asks how far away the offered day is, so the answer must
    not depend on when the suite runs."""
    monkeypatch.setattr("app.date_context.clinic_today", lambda *a, **k: CALL_DAY)


# ── 1. The defect ───────────────────────────────────────────────────────────

def test_an_earlier_wednesday_is_a_change_request():
    """The exact transcript, STT mangling and all ('free' came through as
    'three'). Before the fix this returned False."""
    assert _caller_requests_different_day(
        [{"role": "user", "content": "um are you three earlier earlier date on a wednesday"}],
        OFFERED_19TH,
    ) is True


def test_the_steer_fires_on_that_turn():
    """One level up: the steer going silent is what let the model answer from
    the 19th's slots. It is the thing that had to change."""
    assert _different_day_steer(
        OFFERED_19TH,
        [{"role": "user", "content": "um are you three earlier earlier date on a wednesday"}],
    ) != ""


@pytest.mark.parametrize(
    "utterance",
    [
        "can you do the wednesday before that",
        "have you got anything on wednesday the 12th",
        "wednesday would be better",
        "is there a wednesday sooner",
    ],
)
def test_other_ways_of_naming_a_nearer_wednesday(utterance):
    assert _caller_requests_different_day(
        [{"role": "user", "content": utterance}], OFFERED_19TH
    ) is True


def test_the_recovery_utterance_still_fires():
    """"thursday the 13th" is how the caller rescued the call himself — a
    different weekday word, which even the old name test could see. Pinned so a
    future narrowing cannot take the working path with it."""
    assert _caller_requests_different_day(
        [{"role": "user", "content": "can you do thursday the 4th uh thursday the 13th at all"}],
        OFFERED_19TH,
    ) is True


# ── 2. The suppression that must SURVIVE ────────────────────────────────────

def test_accepting_a_wednesday_that_is_days_away_is_still_acceptance():
    """Offered the 12th, two days out: no other Wednesday sits between today and
    it, so "wednesday" can only mean that one. Firing here would be the
    CAb297555c misfire this suppression exists to prevent."""
    session = {"available_days": [{"date": "2026-08-12"}]}
    assert _caller_requests_different_day(
        [{"role": "user", "content": "yeah wednesday works"}], session
    ) is False


def test_seven_days_out_is_still_acceptance():
    """The CAb297555c boundary, exactly: offered Saturday 15 August on Saturday
    8 August. Seven days is the last distance at which a weekday name is
    unambiguous — inclusive. If this flips, that call regresses."""
    session = {"available_days": [{"date": "2026-08-15"}]}
    assert _caller_requests_different_day(
        [{"role": "user", "content": "yeah 11 in the morning works for saturday"}],
        session,
        today=_date(2026, 8, 8),
    ) is False


def test_eight_days_out_is_a_change_request():
    """One day past the boundary an intervening same-weekday date exists, so the
    name no longer identifies the offered day."""
    session = {"available_days": [{"date": "2026-08-16"}]}
    assert _caller_requests_different_day(
        [{"role": "user", "content": "sunday works"}],
        session,
        today=_date(2026, 8, 8),
    ) is True


def test_a_month_word_keeps_the_name_test():
    """Only weekday names repeat. "august" cannot mean the wrong August the way
    "wednesday" can mean the wrong Wednesday, so the month path is untouched and
    an acceptance phrased by date is still an acceptance."""
    assert _caller_requests_different_day(
        [{"role": "user", "content": "the 19th of august works"}], OFFERED_19TH
    ) is False


def test_an_utterance_with_no_day_word_is_unaffected():
    assert _caller_requests_different_day(
        [{"role": "user", "content": "yes please"}], OFFERED_19TH
    ) is False


# ── 3. The proximity helper ─────────────────────────────────────────────────

def test_a_past_day_is_never_within_reach():
    """A day that has been and gone cannot be the one being accepted. Reading it
    as reachable would suppress the steer on the stalest state in the session."""
    session = {"available_days": [{"date": "2026-08-05"}]}
    assert _offered_weekday_is_within_reach(session, "wednesday", CALL_DAY) is False


def test_today_itself_is_within_reach():
    """Zero days out is the inclusive lower bound — a caller naming today's
    weekday when today is what was offered is accepting it."""
    session = {"available_days": [{"date": "2026-08-10"}]}
    assert _offered_weekday_is_within_reach(session, "monday", CALL_DAY) is True


def test_the_helper_reads_every_source_of_an_offered_day():
    """Four independent keys carry an offered day depending on the path taken.
    The name vocabulary reads all four; if this drifted from it, the suppression
    and the proximity test would disagree about what was offered."""
    for session in (
        {"available_days": [{"date": "2026-08-12"}]},
        {"last_offered_slots": [{"start": "2026-08-12T14:00:00"}]},
        {"last_spoken_slot_date": "2026-08-12"},
        {"v3_last_presented_date_hint": "2026-08-12"},
    ):
        assert _offered_weekday_is_within_reach(session, "wednesday", CALL_DAY) is True


@pytest.mark.parametrize("session", [None, {}, {"available_days": [{"date": "nope"}]}])
def test_unusable_state_is_never_within_reach(session):
    """Fails toward firing, like every other unknown in this predicate."""
    assert _offered_weekday_is_within_reach(session, "wednesday", CALL_DAY) is False
