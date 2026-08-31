"""
Wiring: a single_day offer is spoken from the payload, not composed by a model.

Step 3 of docs/plan/DETERMINISTIC_SLOT_PRESENTATION.md. `_flush_slot_buf` takes
the deterministic branch and the ~400-line repair layer below it does not run.

WHAT THIS HAS TO PROVE, beyond "the words came out":

  * the RECORD matches the sentence. That is the whole point, and it is what
    was missing on CA44f1bdbe — where a guard, reading a projection instead of
    a record, told the caller nine in the morning while Acuity held 18:00.
  * the model's buffered text is DISCARDED, not blended. A half-model,
    half-code sentence is the one shape no record could describe.
  * `more_times` is the PAYLOAD's answer, not one recomputed from a list that
    choose_presented_indices deliberately trimmed (B-116). Getting this wrong
    silently stops a caller being told the rest of the day exists — the B-97
    family.
  * the keypad map is armed exactly as the regex path armed it, because
    connection.py derives the slot window from it every turn and an unarmed
    map strands a caller who presses a digit.
"""
from __future__ import annotations

import asyncio

import pytest

from app.media_streams.llm_stream import LLMStream
from app.tools.slot_followup import LOSSY_SPOKEN_DAYS_KEY


WED = {
    "date": "2026-09-09",
    "day_label": "Wednesday 9th September",
    "slot_times": ["09:00", "12:00", "17:00"],
    "slot_times_spoken": [
        "nine in the morning", "midday", "five in the evening",
    ],
    "slots": [
        {"start": "2026-09-09T09:00:00+01:00", "end": ""},
        {"start": "2026-09-09T12:00:00+01:00", "end": ""},
        {"start": "2026-09-09T17:00:00+01:00", "end": ""},
    ],
}


def _prebuilt(more_times=True, lead_in=""):
    """Build the offer the way llm_stream does when the tool result lands."""
    from app.tools.slot_offer import build_slot_offer

    offer = build_slot_offer([WED], lead_in=lead_in, more_times=more_times)
    return {
        "chunks": list(offer.chunks),
        "slots": [
            {"start": s["start"], "end": s.get("end") or "",
             "spoken": s.get("spoken"), "date": s.get("date")}
            for s in offer.slots
        ],
        "dtmf_map": dict(offer.dtmf_map),
        "more_times": bool(offer.more_times),
    }


async def _flush(session, model_said="Number 1, half past nine. Number 2, noon."):
    buf, tts = asyncio.Queue(), asyncio.Queue()
    await buf.put(model_said)
    await LLMStream._flush_slot_buf(buf, tts, session)
    spoken = []
    while not tts.empty():
        spoken.append(tts.get_nowait())
    return spoken


@pytest.mark.asyncio
async def test_the_payload_speaks_and_the_model_is_discarded():
    session = {"_slot_offer_prebuilt": _prebuilt(), "available_days": [WED]}
    spoken = await _flush(session)
    text = " ".join(spoken)
    assert "Number 1, nine in the morning." in text
    assert "half past nine" not in text          # the model's words, gone
    assert "noon" not in text
    assert session["_slotbuf_emitted"] is True


@pytest.mark.asyncio
async def test_the_record_is_every_time_the_sentence_named():
    session = {"_slot_offer_prebuilt": _prebuilt(), "available_days": [WED]}
    spoken = await _flush(session)
    text = " ".join(spoken)
    assert [s["start"] for s in session["last_offered_slots"]] == [
        "2026-09-09T09:00:00+01:00",
        "2026-09-09T12:00:00+01:00",
        "2026-09-09T17:00:00+01:00",
    ]
    for label in session["slot_labels"]:
        assert label in text
    # B-126: a transcript, so no day is marked unsafe to reason from.
    assert LOSSY_SPOKEN_DAYS_KEY not in session


@pytest.mark.asyncio
async def test_the_keypad_map_is_armed_as_the_regex_path_armed_it():
    session = {
        "_slot_offer_prebuilt": _prebuilt(), "available_days": [WED],
        "turn_count": 7, "v3_slot_map_superseded": True,
    }
    spoken = await _flush(session)
    assert session["v3_dtmf_slot_map"] == {
        "1": "nine in the morning", "2": "midday", "3": "five in the evening",
    }
    assert session["v3_awaiting_slot_selection"] is True
    assert session["v3_slot_map_armed_turn"] == 7
    assert "v3_slot_map_superseded" not in session
    assert session["_slot_chunks_sent"] == len(spoken)
    assert session["_slot_chunks_inhibited"] == 0
    assert session["v3_last_offered_day_iso"] == "2026-09-09"


@pytest.mark.asyncio
async def test_more_times_comes_from_the_payload_not_from_the_trimmed_list():
    """B-116/B-97. The day handed in IS the trimmed list; recomputing from it
    would see nothing held back and stop telling the caller the rest exists."""
    with_more = {"_slot_offer_prebuilt": _prebuilt(more_times=True),
                 "available_days": [WED]}
    without = {"_slot_offer_prebuilt": _prebuilt(more_times=False),
               "available_days": [WED]}
    assert "others that day" in " ".join(await _flush(with_more))
    assert "others that day" not in " ".join(await _flush(without))


@pytest.mark.asyncio
async def test_no_prebuilt_offer_leaves_the_model_path_untouched():
    """multi_day, and every fallback, must still reach the repair layer."""
    session = {"available_days": [WED]}
    spoken = await _flush(session, model_said="Number 1, half past nine.")
    assert spoken, "the model's presentation must still be spoken"
    assert "half past nine" in " ".join(spoken)


@pytest.mark.asyncio
async def test_the_prebuilt_offer_is_consumed_once():
    """It describes ONE readout. Left set, the next turn would re-speak it."""
    session = {"_slot_offer_prebuilt": _prebuilt(), "available_days": [WED]}
    await _flush(session)
    assert "_slot_offer_prebuilt" not in session
    again = await _flush(session, model_said="Number 1, half past nine.")
    assert "half past nine" in " ".join(again)
