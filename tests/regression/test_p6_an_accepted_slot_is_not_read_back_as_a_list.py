"""
P6: a caller who ACCEPTS a slot in words must not be read the list again.

CA82b240ccad48ed219371c3f2fddfffb8, 1 Sep 2026, vital_edge, build f875126e.
`outcome=abandoned`, judge `score=1`. The stored transcript, in full:

    assi  Number 1, Monday 7th September    - nine in the morning, or six in the evening.
    assi  Number 2, Tuesday 8th September   - nine in the morning, or six in the evening.
    assi  Number 3, Wednesday 9th September - nine in the morning, or six in the evening.
    user  um yeah the last day at 6 in the evening works
    assi  Let me see what I've got in the evening -
    assi  Number 1, Monday 7th September    - TEN in the morning, or FIVE in the evening.
    assi  Number 2, Tuesday 8th September   - ten in the morning, or five in the evening.
    assi  Number 3, Wednesday 9th September - ten in the morning, or five in the evening.
    <caller hangs up>

The pick was not recognised, so the model re-queried. On the second pass it
RECOVERED -- the Render log has the sentence it wrote:

    21:48:19 [ms_gate5] deterministic offer in force - 3 chunk(s); the model's
             1 buffered chunk(s) are discarded ('PRE_SLOTWednesday 9th
             September at six in the evening works. Can I just get yo')

...and `_flush_slot_buf` discarded it, because a prebuilt offer won
unconditionally and nothing tested whether the model's text was an offer or an
acceptance.

WHY THE SECOND READOUT IS DESTRUCTIVE AND NOT MERELY REDUNDANT, which is the
part worth keeping: `choose_presented_indices` prefers times this caller has
NOT heard (B-116), so the slot they just accepted is the one slot GUARANTEED
to be absent from the re-read. 09:00/18:00 became 10:00/17:00 on all three
days. That is the design working, and it is exactly why the re-read must not
happen -- not a cache fault to be chased.

Scope check before widening this: over the 807-call corpus, 106 calls read the
numbered offer more than once and 28 of those followed a reply that named a
slot or a time (14 abandoned). 27 of the 28 predate the deterministic path,
which landed 31 Aug 22:15 UTC -- on those the model's own text was spoken, so
they are the RECOGNITION defect (step 1), not this one. This file covers step
4 only: once the model has recovered, the engine must not undo it.
"""
from __future__ import annotations

import asyncio

import pytest


from app.media_streams.llm_stream import LLMStream


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


# What the SECOND lookup returned -- B-116 has already removed the times he
# heard, which is how the six o'clock he accepted vanished.
RE_READ = [
    _day("2026-09-07", "Monday 7th September", ["10:00", "17:00"],
         ["ten in the morning", "five in the evening"]),
    _day("2026-09-08", "Tuesday 8th September", ["10:00", "17:00"],
         ["ten in the morning", "five in the evening"]),
    _day("2026-09-09", "Wednesday 9th September", ["10:00", "17:00"],
         ["ten in the morning", "five in the evening"]),
]

# The offer already on the table when he spoke -- nine and six on each day.
STANDING = [
    {"start": "2026-09-07T09:00:00+01:00", "end": ""},
    {"start": "2026-09-08T09:00:00+01:00", "end": ""},
    {"start": "2026-09-09T09:00:00+01:00", "end": ""},
]
STANDING_LABELS = ["Monday 7th September", "Tuesday 8th September",
                   "Wednesday 9th September"]

ACCEPTANCE = ("Wednesday 9th September at six in the evening works. "
              "Can I just get your name and a contact number?")


def _prebuilt(days=None):
    from app.tools.slot_offer import build_slot_offer

    offer = build_slot_offer(list(days if days is not None else RE_READ))
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


def _session_mid_offer():
    """A caller who has already been read a list and has just picked from it."""
    return {
        "_slot_offer_prebuilt": _prebuilt(),
        "available_days": RE_READ,
        "last_offered_slots": [dict(s) for s in STANDING],
        "slot_labels": list(STANDING_LABELS),
    }


async def _flush(session, model_said):
    buf, tts = asyncio.Queue(), asyncio.Queue()
    await buf.put(model_said)
    await LLMStream._flush_slot_buf(buf, tts, session)
    spoken = []
    while not tts.empty():
        spoken.append(tts.get_nowait())
    return spoken


@pytest.mark.asyncio
async def test_the_acceptance_is_spoken_and_the_list_is_not_read_again():
    """The defect, exactly as it fired. Fails before the fix."""
    session = _session_mid_offer()
    text = " ".join(await _flush(session, ACCEPTANCE))

    assert "six in the evening works" in text, (
        "the model's acceptance was discarded and the caller was read a list "
        "instead -- this is P6"
    )
    assert "Number 1" not in text and "Number 2" not in text, (
        "the numbered list was read a second time: {!r}".format(text)
    )
    assert session["_slotbuf_emitted"] is True


@pytest.mark.asyncio
async def test_the_re_read_would_have_withdrawn_the_slot_he_accepted():
    """Why this is a HIGH, not a tidiness fix. B-116 guarantees the loss."""
    det = " ".join(_prebuilt()["chunks"])
    assert "six in the evening" not in det, (
        "the payload the engine wanted to read out still contains the accepted "
        "slot -- if this ever becomes true, re-check the B-116 reasoning above"
    )
    assert "five in the evening" in det


@pytest.mark.asyncio
async def test_the_standing_offer_and_its_keypad_survive():
    """He is choosing FROM the offer on the table, so it must not be renumbered.

    Overwriting `last_offered_slots` here would repoint every ordinal and every
    keypad digit at slots nobody has heard, mid-sentence.
    """
    session = _session_mid_offer()
    await _flush(session, ACCEPTANCE)

    assert [s["start"] for s in session["last_offered_slots"]] == \
        [s["start"] for s in STANDING]
    assert session["slot_labels"] == STANDING_LABELS

    from app.tools.slot_followup import spoken_starts_for_offer
    recorded = {str(s)[:19] for s in spoken_starts_for_offer(session)}
    assert "2026-09-07T10:00:00" not in recorded, (
        "times nobody spoke were recorded as heard, which would make B-116 "
        "withhold them from a caller who asks what else there is"
    )


# -- The two conditions that keep this narrow -------------------------------

@pytest.mark.asyncio
async def test_a_first_lookup_still_speaks_the_payload():
    """No standing offer means the model is opening the batting, not answering.

    This is the condition that makes the fix safe: a contentless model turn can
    never cost a caller the only offer they were going to get.
    """
    session = {"_slot_offer_prebuilt": _prebuilt(), "available_days": RE_READ}
    text = " ".join(await _flush(session, "Let me have a look for you -"))

    assert "Number 1" in text and "Monday 7th September" in text
    assert "Let me have a look" not in text


@pytest.mark.asyncio
async def test_a_real_presentation_is_still_overridden_by_the_payload():
    """The case section 1b was written for is untouched: numbered => payload wins."""
    session = _session_mid_offer()
    text = " ".join(await _flush(
        session, "Number 1, Monday - half nine. Number 2, Friday - half four."
    ))

    assert "half nine" not in text and "Friday" not in text
    assert "Number 1" in text and "ten in the morning" in text
