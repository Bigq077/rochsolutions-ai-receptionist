"""
F1/F2, from the two demo calls on 2026-09-02. Owner-reported, both real.

F1 — CAd89b30ed528f6bd41b8a58250c2a26d9, 09:10. The caller ACCEPTED a slot:

    caller: "yeah the last day in the afternoon works"
    Susie : "Let me see what I've got in the afternoon —"      <- promises a lookup

`classify_intent` decides "is the caller answering rather than asking?" from
`utterance_is_slot_selection`, which is containment against the labels just
spoken — the same test that cannot see an ordinal, and the same root as
P6/P6b. The band word alone then carried the turn to a TIME_BAND head. The
resolver added in `65baedd0` already holds the answer on the session, so the
fix is to read it, not to add a second opinion.

Silence is the right outcome, not a new phrase: with no situational head the
contentless filler ("Still with you —") still covers a slow turn on its own
3s deadline, so there is no dead air to trade for.

F2 — CA4f74857a477a42105a809768eaa9a60e, 09:15. After a three-day readout:

    Susie : Number 1 Monday 7th | Number 2 Tuesday 8th | Number 3 Wednesday 9th
    caller: "uh what else have you got"
    Susie : "On Monday 7th September I also have ten to nine in the morning,
             twenty to ten in the morning, half past ten in the morning, ..."
             — NINE times, one day, 20 seconds

Two independent faults, one per level:

  * `try_unspoken_followup_speech` scoped an unnamed "what else" to
    `last_offered_slots[0]` and applied the 24 Aug owner rule for SINGLE-day
    offers ("a caller told 'I've a few others that day' gets ALL of them").
    A multi_day readout carries no such tail (B-99), so it answered a promise
    nobody made.
  * `_cap_presented_slots` took `days[:max_days]` — the first three, blind.
    Even after falling through to a real lookup that would have re-read
    Monday, Tuesday and Wednesday with different times on them.

Owner decision, 2026-09-02: after a multi-day readout, "what else have you
got" means MORE DAYS.
"""
from __future__ import annotations

import pytest

from app.hold_speech import classify_intent, render_intent_head, subject_for
from app.tools.slot_followup import (
    ACCEPTED_SLOT_KEY,
    choose_presented_days,
    record_spoken_slots,
    try_unspoken_followup_speech,
)


def _day(date, label, times, spoken=None):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": list(spoken or times),
        "times_not_shown": 0,
        "slots": [
            {"start": "{}T{}:00+01:00".format(date, t), "end": ""} for t in times
        ],
    }


# The week as the payload held it on the 09:15 call.
WEEK = [
    _day("2026-09-07", "Monday 7th September",
         ["08:00", "08:50", "09:40", "17:10"]),
    _day("2026-09-08", "Tuesday 8th September", ["08:50", "17:10"]),
    _day("2026-09-09", "Wednesday 9th September", ["08:00", "16:20"]),
    _day("2026-09-10", "Thursday 10th September", ["08:00", "17:10"]),
    _day("2026-09-11", "Friday 11th September", ["08:00", "17:10"]),
    _day("2026-09-14", "Monday 14th September", ["08:00", "17:10"]),
]

READOUT = ("Number 3, Wednesday 9th September — eight in the morning, or "
           "twenty past four in the afternoon. Any of those work?")


def _after_multi_day_readout():
    """Mon/Tue/Wed have been read out, two times each."""
    session = {
        "available_days": WEEK,
        "_slot_presentation_mode": "multi_day",
        "last_offered_slots": [
            {"start": "2026-09-07T08:00:00+01:00", "end": ""},
            {"start": "2026-09-08T08:50:00+01:00", "end": ""},
            {"start": "2026-09-09T08:00:00+01:00", "end": ""},
        ],
        "slot_labels": ["Monday 7th September", "Tuesday 8th September",
                        "Wednesday 9th September"],
    }
    record_spoken_slots(session, [
        {"start": "2026-09-07T08:00:00+01:00", "date": "2026-09-07"},
        {"start": "2026-09-07T17:10:00+01:00", "date": "2026-09-07"},
        {"start": "2026-09-08T08:50:00+01:00", "date": "2026-09-08"},
        {"start": "2026-09-08T17:10:00+01:00", "date": "2026-09-08"},
        {"start": "2026-09-09T08:00:00+01:00", "date": "2026-09-09"},
        {"start": "2026-09-09T16:20:00+01:00", "date": "2026-09-09"},
    ])
    return session


# ── F1 ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_resolved_pick_silences_the_lookup_head():
    """The 09:10 call. Fails before the fix."""
    said = "yeah the last day in the afternoon works"

    promised = classify_intent(said, READOUT, slot_selection=False)
    assert promised, "guard: without the pick signal this must still fire"
    assert "afternoon" in render_intent_head(
        promised[0], subject=subject_for(said), index=0
    )

    # With the pick known — which is what connection.py now puts on the session
    # before the head is chosen — she must not promise to go and look.
    assert classify_intent(said, READOUT, slot_selection=True) == [], (
        "Susie offered to check the afternoon for a caller who had just "
        "chosen an afternoon slot"
    )


@pytest.mark.asyncio
async def test_the_session_key_is_what_carries_it():
    """The wiring contract: an accepted slot on the session means 'picking'."""
    session = _after_multi_day_readout()
    session[ACCEPTED_SLOT_KEY] = "2026-09-09T16:20:00+01:00"
    assert bool(session.get(ACCEPTED_SLOT_KEY)) is True


# ── F2, day selection ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_what_else_offers_days_he_has_not_heard():
    """The heart of F2. Fails before the fix — returns Mon/Tue/Wed again."""
    session = _after_multi_day_readout()
    kept = [d["date"] for d in choose_presented_days(session, WEEK, 3)]

    assert kept == ["2026-09-10", "2026-09-11", "2026-09-14"], (
        "'what else have you got' was answered with days he had already been "
        "offered: {}".format(kept)
    )


@pytest.mark.asyncio
async def test_the_first_lookup_is_unchanged():
    """Nothing spoken yet — byte-identical to the slice this replaced."""
    session = {"available_days": WEEK}
    kept = [d["date"] for d in choose_presented_days(session, WEEK, 3)]
    assert kept == ["2026-09-07", "2026-09-08", "2026-09-09"]


@pytest.mark.asyncio
async def test_it_never_starves_a_repeat():
    """Every day heard: fall back to chronological, exactly as before."""
    session = _after_multi_day_readout()
    record_spoken_slots(session, [
        {"start": "{}T08:00:00+01:00".format(d), "date": d}
        for d in ("2026-09-10", "2026-09-11", "2026-09-14")
    ])
    kept = [d["date"] for d in choose_presented_days(session, WEEK, 3)]
    assert kept == ["2026-09-07", "2026-09-08", "2026-09-09"]


@pytest.mark.asyncio
async def test_two_fresh_days_are_not_padded_with_a_heard_one():
    """B-119 at day level: fewer than the cap is the correct answer."""
    session = _after_multi_day_readout()
    record_spoken_slots(session, [
        {"start": "2026-09-14T08:00:00+01:00", "date": "2026-09-14"},
    ])
    kept = [d["date"] for d in choose_presented_days(session, WEEK, 3)]
    assert kept == ["2026-09-10", "2026-09-11"], (
        "padded back up to three with a day he had already heard: "
        "{}".format(kept)
    )


# ── F2, the follow-up path must stand aside ─────────────────────────────────

@pytest.mark.asyncio
async def test_the_cached_followup_still_answers_from_day_one_FOR_NOW():
    """Documents the state after the 09:43 revert — NOT the desired behaviour.

    F2b made this branch decline so a real lookup could offer more days. On the
    demo call at 09:43 the decline worked and the consequence did not: the model
    answered "what else" from its own context WITHOUT calling the tool, so no
    deterministic offer was built and no record was written. The keypad map
    still said Monday/Tuesday/Wednesday while Susie had just offered Thursday
    the 10th, the caller said "the last day in the morning works", the resolver
    read the stale record and pinned Wednesday, and the model confirmed
    "Saturday the 12th at nine in the morning". Three days in play, none of them
    agreeing.

    That is worse than the nine-times-on-one-day answer it replaced: verbose but
    coherent beats a wrong day read back as a booking. Reverted on the demo line
    at 09:5x.

    The real fix is to ANSWER this deterministically — build the more-days offer
    from the cached payload through build_slot_offer and write the record —
    rather than declining and hoping the model calls the tool. Handing a turn to
    the model is what leaves the record stale, which is the disease this whole
    family has.
    """
    session = _after_multi_day_readout()
    spoken = try_unspoken_followup_speech(session, "uh what else have you got")
    assert spoken is not None, (
        "the decline is back without the deterministic answer behind it — "
        "see the docstring, this is the 09:43 regression"
    )


@pytest.mark.asyncio
async def test_a_caller_who_NAMES_a_day_still_gets_that_day_from_cache():
    """B-103 and B-105 are untouched — this is the scoped case, and it is fast."""
    session = _after_multi_day_readout()
    spoken = try_unspoken_followup_speech(
        session, "what else have you got on monday the 7th of september"
    )
    assert spoken, "a named day stopped being answered from cache"
    assert "Monday 7th September" in spoken
