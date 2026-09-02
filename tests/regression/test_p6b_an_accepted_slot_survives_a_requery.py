"""
P6b: the slot a caller ACCEPTED must survive the re-query it triggers.

Two live calls, both abandoned:

  * `CA82b240ccad48ed219371c3f2fddfffb8` — 1 Sep 2026, vital_edge, 21:46.
    "um yeah the last day at 6 in the evening works". Judge score 1.
  * `CA5a126fe4e6addcf812836220cdf7ea44` — 2 Sep 2026, northgate, 00:03.
    "yeah the last day in the afternoon works". Judge score 2. A deliberate
    reproduction, and it reproduced first time.

THE CHAIN, and each link has its own test below:

  1. The caller picks in WORDS. `utterance_is_slot_selection` is containment
     against the labels just spoken, so it answers "was this a pick?" and
     cannot answer "which one" — an ordinal matches no label. Nothing on the
     main path resolved it: `day_selected_by_position` does understand
     ordinals, and is only wired into the FOLLOW-UP path.
  2. The model therefore reads the words as a fresh time filter and calls
     `check_availability` again — `date_hint 'any' -> 'Wednesday afternoon'`.
  3. `choose_presented_indices` prefers times the caller has NOT heard
     (B-116), so the accepted slot is the one slot guaranteed to be dropped
     from the new readout. Northgate's fresh payload HELD 16:20 and the
     readout withheld it. He was offered 13:00 / 13:50 / 14:40 instead and
     hung up.

THE TRIGGER IS A PART-OF-DAY WORD, established across three calls in one
night: "the last day **at 6 in the evening**" re-queried, "the last day **in
the afternoon**" re-queried, and "**half past 3** on the last day" — same
shape, no band word — did not. A caller who picks a day plus a rough time of
day takes the broken path; one who names a clock time does not.

Step 3 is fixed here, deliberately, rather than only step 1: the re-query is
the model's decision and cannot be relied on not to happen. Pin the accepted
slot and the caller hears it again whatever the model does.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    ACCEPTED_SLOT_KEY,
    choose_presented_indices,
    record_spoken_slots,
    slot_accepted_by_caller,
)


def _day(date, label, times, spoken):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": list(spoken),
        "times_not_shown": 0,
        "slots": [
            {"start": "{}T{}:00+01:00".format(date, t), "end": ""} for t in times
        ],
    }


# ── The northgate call, exactly as it was offered ───────────────────────────
NG_OFFER = [
    _day("2026-09-07", "Monday 7th September", ["08:00", "17:10"],
         ["eight in the morning", "ten past five in the evening"]),
    _day("2026-09-08", "Tuesday 8th September", ["08:50", "17:10"],
         ["ten to nine in the morning", "ten past five in the evening"]),
    _day("2026-09-09", "Wednesday 9th September", ["08:00", "16:20"],
         ["eight in the morning", "twenty past four in the afternoon"]),
]

# ── The vital_edge call ─────────────────────────────────────────────────────
VE_OFFER = [
    _day("2026-09-07", "Monday 7th September", ["09:00", "18:00"],
         ["nine in the morning", "six in the evening"]),
    _day("2026-09-08", "Tuesday 8th September", ["09:00", "18:00"],
         ["nine in the morning", "six in the evening"]),
    _day("2026-09-09", "Wednesday 9th September", ["09:00", "18:00"],
         ["nine in the morning", "six in the evening"]),
]

# What the SECOND lookup returned for Wednesday on the northgate call. 16:20 is
# in it — the readout is what dropped the slot, not the retrieval.
NG_REQUERY = _day(
    "2026-09-09", "Wednesday 9th September",
    ["13:00", "13:50", "14:40", "15:30", "16:20"],
    ["one in the afternoon", "ten to two in the afternoon",
     "twenty to three in the afternoon", "half past three in the afternoon",
     "twenty past four in the afternoon"],
)


def _mid_offer(days):
    """A caller who has been read `days` as a multi_day offer.

    `last_offered_slots` is one entry per DAY and positional — that is what an
    ordinal indexes, and what a keypad digit means.
    """
    session = {
        "available_days": days,
        "last_offered_slots": [
            {"start": "{}T{}:00+01:00".format(d["date"], d["slot_times"][0]),
             "end": ""}
            for d in days
        ],
        "slot_labels": [d["day_label"] for d in days],
    }
    record_spoken_slots(session, [
        {"start": "{}T{}:00+01:00".format(d["date"], t),
         "spoken": spoken, "date": d["date"]}
        for d in days
        for t, spoken in zip(d["slot_times"], d["slot_times_spoken"])
    ])
    return session


# ── Step 1: the pick resolves ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_two_live_utterances_resolve():
    """Both calls, verbatim from the stored transcripts."""
    assert slot_accepted_by_caller(
        _mid_offer(VE_OFFER), "um yeah the last day at 6 in the evening works"
    ) == "2026-09-09T18:00:00+01:00"

    assert slot_accepted_by_caller(
        _mid_offer(NG_OFFER), "yeah the last day in the afternoon works"
    ) == "2026-09-09T16:20:00+01:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("said,want", [
    ("the first day in the morning works",              "2026-09-07T08:00:00+01:00"),
    ("number 3 in the afternoon",                       "2026-09-09T16:20:00+01:00"),
    ("the second one at ten past five please",          "2026-09-08T17:10:00+01:00"),
    ("wednesday 9th september in the afternoon",        "2026-09-09T16:20:00+01:00"),
    # Moved out of test_it_declines_rather_than_guess on 2026-09-02, when the
    # resolver learned to pin a day by a bare weekday that matches exactly one
    # day of the offer. It sat there as "a bare weekday is not a named day
    # here" — a true statement about `day_named_by_caller`, which needs the
    # payload's full day_label, but a description of the limitation rather
    # than a safety property. The row above wants this very slot from the
    # fuller phrasing, and this is not a guess: the offer holds one Wednesday,
    # and "twenty past four" matches exactly one time spoken on it. The
    # deny-by-default cases that ARE load-bearing kept their place below.
    ("wednesday at twenty past four",                   "2026-09-09T16:20:00+01:00"),
])
async def test_the_other_ways_a_caller_picks(said, want):
    assert slot_accepted_by_caller(_mid_offer(NG_OFFER), said) == want


@pytest.mark.asyncio
@pytest.mark.parametrize("said,why", [
    ("what else have you got",        "a more-times request has its own path (B-90)"),
    ("anything the week after",       "a different-day request has its own path"),
    ("the last day",                  "a day with two heard times is ambiguous"),
    ("the last one, number 2",        "two positions named — never guess"),
    ("yeah that sounds good",         "an acceptance naming nothing resolves nothing"),
    # The weekday door, kept shut where it should be. A weekday the offer does
    # not hold pins nothing, and one that names a day whose times the caller
    # did not narrow is ambiguous exactly as "the last day" is.
    ("thursday at twenty past four",  "no thursday in the offer"),
    ("wednesday works",               "one wednesday, but two heard times on it"),
    ("wednesday in the morning, or is monday better",
     "two weekdays named is a comparison, not a pick"),
])
async def test_it_declines_rather_than_guess(said, why):
    """Deny by default. A wrong pin is read back to the caller as their booking."""
    assert slot_accepted_by_caller(_mid_offer(NG_OFFER), said) is None, why


# ── Step 3: the pin. This is the one that fails before the fix ──────────────

@pytest.mark.asyncio
async def test_the_accepted_slot_is_not_withheld_from_the_requery():
    """The northgate readout, reproduced. Fails before the fix."""
    session = _mid_offer(NG_OFFER)
    session[ACCEPTED_SLOT_KEY] = "2026-09-09T16:20:00+01:00"
    session["available_days"] = [NG_REQUERY]

    idx = choose_presented_indices(session, NG_REQUERY, 3)
    times = [NG_REQUERY["slot_times"][i] for i in idx]

    assert "16:20" in times, (
        "the slot he accepted was withheld from the readout because he had "
        "heard it — this is P6b: {}".format(times)
    )
    assert len(times) == 3, "the presented cap must still hold: {}".format(times)
    assert times == sorted(times), (
        "chronological order lost — the keypad map is built from this order, "
        "so pressing 2 would no longer mean the second thing he heard"
    )


@pytest.mark.asyncio
async def test_without_an_accepted_slot_b116_is_untouched():
    """The wrapper must be inert on every other readout in the system."""
    session = _mid_offer(NG_OFFER)
    session["available_days"] = [NG_REQUERY]
    before = choose_presented_indices(session, NG_REQUERY, 3)

    assert "16:20" not in [NG_REQUERY["slot_times"][i] for i in before], (
        "B-116 stopped withholding a heard time when nobody accepted it — "
        "that is the defect B-116 exists to prevent, not this one"
    )


@pytest.mark.asyncio
async def test_a_day_with_room_for_everything_is_unchanged():
    session = _mid_offer(NG_OFFER)
    session[ACCEPTED_SLOT_KEY] = "2026-09-09T16:20:00+01:00"
    session["available_days"] = [NG_REQUERY]
    idx = choose_presented_indices(session, NG_REQUERY, 5)
    assert idx == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_an_accepted_slot_on_another_day_does_not_leak():
    """Pinning is per-day. A Monday acceptance must not touch Wednesday."""
    session = _mid_offer(NG_OFFER)
    session[ACCEPTED_SLOT_KEY] = "2026-09-07T17:10:00+01:00"   # Monday
    session["available_days"] = [NG_REQUERY]                    # Wednesday
    idx = choose_presented_indices(session, NG_REQUERY, 3)
    assert "16:20" not in [NG_REQUERY["slot_times"][i] for i in idx]
    assert len(idx) == 3


# ── The other half: the re-read must not happen when the model got it right ──
#
# The pin above makes the accepted slot SURVIVE a re-read. This stops the
# re-read when the model has already confirmed the pick — which is what it did
# on the northgate call, and the P6 stand-down could not help because the
# model's recovery carried a "Number 1".

def _prebuilt_for(day):
    from app.tools.slot_offer import build_slot_offer
    offer = build_slot_offer([day])
    return {
        "chunks": list(offer.chunks),
        "slots": [
            {"start": s["start"], "end": s.get("end") or "",
             "spoken": s.get("spoken"), "date": s.get("date")}
            for s in offer.slots
        ],
        "dtmf_map": dict(offer.dtmf_map),
        "more_times": bool(offer.more_times),
        "day_iso": None,
        "mode": offer.mode,
    }


def _post_requery_session():
    """Mid-call, straight after the re-query the caller's pick triggered."""
    session = {
        "available_days": [NG_REQUERY],
        "last_offered_slots": [{"start": "2026-09-09T08:00:00+01:00", "end": ""}],
        "slot_labels": ["Wednesday 9th September"],
        "_slot_offer_prebuilt": _prebuilt_for(NG_REQUERY),
    }
    record_spoken_slots(session, [{
        "start": "2026-09-09T16:20:00+01:00",
        "spoken": "twenty past four in the afternoon",
        "date": "2026-09-09",
    }])
    return session


async def _flush(session, model_said):
    import asyncio
    from app.media_streams.llm_stream import LLMStream

    buf, tts = asyncio.Queue(), asyncio.Queue()
    await buf.put(model_said)
    await LLMStream._flush_slot_buf(buf, tts, session)
    out = []
    while not tts.empty():
        out.append(tts.get_nowait())
    return " ".join(out)


# What the model actually wrote, from the Render log.
NG_MODEL_RECOVERY = ("Wednesday 9th September — Number 1, twenty past four in "
                     "the afternoon.")


@pytest.mark.asyncio
async def test_a_model_confirming_the_accepted_slot_is_spoken():
    """The northgate discard, reproduced. Fails before the fix."""
    session = _post_requery_session()
    session[ACCEPTED_SLOT_KEY] = "2026-09-09T16:20:00+01:00"

    text = await _flush(session, NG_MODEL_RECOVERY)

    assert "twenty past four" in text, (
        "the model named the accepted slot and was discarded anyway: "
        "{!r}".format(text)
    )
    assert "one in the afternoon" not in text, (
        "the three-option list was read over the confirmation: {!r}".format(text)
    )


@pytest.mark.asyncio
async def test_without_an_acceptance_the_payload_still_wins():
    """Inert on every ordinary readout — this is the safety condition."""
    session = _post_requery_session()          # no ACCEPTED_SLOT_KEY

    text = await _flush(session, NG_MODEL_RECOVERY)

    assert "one in the afternoon" in text, (
        "the payload offer stopped winning when nobody had accepted anything"
    )


@pytest.mark.asyncio
async def test_a_model_offering_OTHER_times_is_still_overridden():
    """Naming the accepted slot is the test — not merely having one on file."""
    session = _post_requery_session()
    session[ACCEPTED_SLOT_KEY] = "2026-09-09T16:20:00+01:00"

    text = await _flush(
        session, "Number 1, ten to two in the afternoon. Number 2, half past three."
    )

    assert "one in the afternoon" in text, (
        "a genuine fresh list was mistaken for a confirmation: {!r}".format(text)
    )
