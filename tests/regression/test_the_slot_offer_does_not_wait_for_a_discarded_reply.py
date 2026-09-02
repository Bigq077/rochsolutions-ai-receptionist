"""The post-check_availability model call is skipped when it can only be binned.

Measured on two consecutive live demo-line calls, 2 Sep 2026, build fff61547:

    22:57:10.372  [ms_gate5] deterministic multi_day offer built: 3 chunk(s)
    22:57:10.375  [ms_llm]   slot buffer active - switched to HAIKU
    22:57:11.012  httpx      POST /v1/messages 200 OK
    22:57:11.725  [ms_gate5] deterministic offer in force - the model's 2
                             buffered chunk(s) are discarded
    22:57:11.727  [ms_tts]   synthesise_chunk: "Here's what we've got coming up..."

1,355ms on that call and 1,333ms on the next, spent generating a sentence
nobody ever hears: the words the caller gets are `_slot_offer_prebuilt`, which
was written during tool-result handling BEFORE that call was dispatched.

The call is not always waste, which is the whole difficulty. `_flush_slot_buf`
reads the model's text for the P6/P6b stand-down guards, and discarding a
recovery once cost a real caller the slot they had just accepted
(CA82b240cc..., 1 Sep, abandoned, judge score 1) -- see
test_p6_an_accepted_slot_is_not_read_back_as_a_list.py.

So the skip is gated on the two guards' own PRECONDITIONS, not on a judgement
about wording. This file pins both halves: that a first lookup skips, and that
anything a guard could act on does not.
"""
from __future__ import annotations

import asyncio

import pytest

from app.media_streams.llm_stream import LLMStream
from app.tools.slot_followup import (
    ACCEPTED_SLOT_KEY,
    slot_llm_reply_can_only_be_discarded,
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


# The payload from CA3029b7be, the second of the two calls above.
DAYS = [
    _day("2026-09-07", "Monday 7th September", ["08:00", "17:10"],
         ["eight in the morning", "ten past five in the evening"]),
    _day("2026-09-08", "Tuesday 8th September", ["08:50", "17:10"],
         ["ten to nine in the morning", "ten past five in the evening"]),
    _day("2026-09-09", "Wednesday 9th September", ["08:00", "16:20"],
         ["eight in the morning", "twenty past four in the afternoon"]),
]


def _prebuilt():
    from app.tools.slot_offer import build_slot_offer

    offer = build_slot_offer(list(DAYS))
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


def _first_lookup():
    """Nothing has been offered yet -- the state both calls above were in."""
    return {"_slot_offer_prebuilt": _prebuilt(), "available_days": DAYS}


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

def test_a_first_lookup_does_not_need_the_model():
    assert slot_llm_reply_can_only_be_discarded(_first_lookup()) is True


def test_a_standing_offer_keeps_the_call_because_p6_reads_it():
    session = _first_lookup()
    session["last_offered_slots"] = [{"start": "2026-09-07T09:00:00+01:00", "end": ""}]
    assert slot_llm_reply_can_only_be_discarded(session) is False


def test_an_accepted_slot_keeps_the_call_because_p6b_reads_it():
    session = _first_lookup()
    session[ACCEPTED_SLOT_KEY] = "2026-09-09T16:20:00"
    assert slot_llm_reply_can_only_be_discarded(session) is False


def test_no_deterministic_offer_means_the_model_is_the_only_source():
    assert slot_llm_reply_can_only_be_discarded({}) is False
    assert slot_llm_reply_can_only_be_discarded(
        {"_slot_offer_prebuilt": {"chunks": []}}
    ) is False
    assert slot_llm_reply_can_only_be_discarded(None) is False


# ---------------------------------------------------------------------------
# The receiving side: skipping the call means an EMPTY buffer reaches the flush
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_empty_buffer_still_speaks_the_whole_offer():
    """What the caller hears must not depend on the call having been made.

    The stand-down block is itself gated on `raw_chunks` being truthy, so with
    no reply it is skipped rather than evaluated against absent text, and
    section 1b emits the deterministic chunks as usual.
    """
    session = _first_lookup()
    tts: asyncio.Queue = asyncio.Queue()
    await LLMStream._flush_slot_buf(asyncio.Queue(), tts, session)

    spoken = []
    while not tts.empty():
        spoken.append(tts.get_nowait())
    text = " ".join(spoken)

    assert session["_slotbuf_emitted"] is True
    for n in ("Number 1", "Number 2", "Number 3"):
        assert n in text, "the caller lost an option: {!r}".format(text)
    assert "Monday 7th September" in text
    assert "twenty past four in the afternoon" in text


@pytest.mark.asyncio
async def test_the_keypad_map_is_still_written_without_a_reply():
    """`apply_offer_to_session` is what makes the DTMF digits mean anything.

    It runs inside the deterministic branch, so it must not have been depending
    on the model's text arriving first.
    """
    session = _first_lookup()
    await LLMStream._flush_slot_buf(asyncio.Queue(), asyncio.Queue(), session)

    assert session.get("v3_dtmf_slot_map"), "the keypad map was never written"
    assert len(session["v3_dtmf_slot_map"]) == 3
    assert session.get("last_offered_slots"), "the offer left no record"
