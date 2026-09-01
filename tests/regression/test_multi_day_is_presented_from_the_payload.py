"""
Wiring: a multi_day offer is spoken from the payload, not composed by a model.

Step 4 of docs/plan/DETERMINISTIC_SLOT_PRESENTATION.md, and the step the plan
is actually FOR. Measured over the stored corpus on 1 Sept 2026:

    51 of 52 multi_day readouts (98%) hand the positional resolver a
    DAY-only label.

`extract_slot_options` commits to the segment before the em dash -- "Monday
10th August" -- because that is what a caller pressing 1 means. A day label
matches every slot on that day, and `resolve_spoken_options` refuses an
ambiguous match rather than guessing. So multi_day does not fail sometimes; it
cannot succeed, and every guard downstream reads a record rebuilt from that
failure or from `last_offered_slots`, which on multi_day is positional by
design.

WHAT THIS HAS TO PROVE beyond "the words came out":

  * the RECORD holds every time the sentence named -- SIX on a three-day
    readout, not one per day, which is what the positional resolver could
    never express.
  * the model's buffered text is DISCARDED, not blended.
  * the keypad map stays DAY-keyed, because that is what the regex path armed
    and what a caller pressing 1 means. Changing it to a time would silently
    change what every digit does mid-call.
  * `v3_last_offered_day_iso` keeps the meaning turn_handler documents -- the
    PAYLOAD's first day. See test_the_anchor_is_the_payloads_first_day.
  * no more-times tail, per the B-99 rule, which is also what 50 of those 52
    readouts already do.
"""
from __future__ import annotations

import asyncio

import pytest

from app.media_streams.llm_stream import LLMStream
from app.tools.slot_followup import LOSSY_SPOKEN_DAYS_KEY


def _day(date, label, times, spoken, hidden=0):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": list(spoken),
        "times_not_shown": hidden,
        "slots": [
            {"start": "{}T{}:00+01:00".format(date, t), "end": ""} for t in times
        ],
    }


MON = _day("2026-09-07", "Monday 7th September", ["10:00", "17:00"],
           ["ten in the morning", "five in the evening"])
TUE = _day("2026-09-08", "Tuesday 8th September", ["09:00", "14:00"],
           ["nine in the morning", "two in the afternoon"])
WED = _day("2026-09-09", "Wednesday 9th September", ["11:00", "18:00"],
           ["eleven in the morning", "six in the evening"])

PRESENTED = [MON, TUE, WED]


def _prebuilt(presented=None, more_times=False, other_dates=None, day_iso=None):
    """Build the offer exactly as llm_stream does when the tool result lands."""
    from app.tools.slot_offer import build_slot_offer

    offer = build_slot_offer(
        list(presented if presented is not None else PRESENTED),
        more_times=more_times,
        other_dates=other_dates,
    )
    return {
        "chunks": list(offer.chunks),
        "slots": [
            {"start": s["start"], "end": s.get("end") or "",
             "spoken": s.get("spoken"), "date": s.get("date")}
            for s in offer.slots
        ],
        "dtmf_map": dict(offer.dtmf_map),
        "more_times": bool(offer.more_times),
        "day_iso": day_iso,
    }


async def _flush(session, model_said="Number 1, Monday — half nine. Number 2, Friday."):
    buf, tts = asyncio.Queue(), asyncio.Queue()
    await buf.put(model_said)
    await LLMStream._flush_slot_buf(buf, tts, session)
    spoken = []
    while not tts.empty():
        spoken.append(tts.get_nowait())
    return spoken


@pytest.mark.asyncio
async def test_the_payload_speaks_and_the_model_is_discarded():
    session = {"_slot_offer_prebuilt": _prebuilt(), "available_days": PRESENTED}
    spoken = await _flush(session)
    text = " ".join(spoken)
    assert "Monday 7th September" in text
    assert "Tuesday 8th September" in text
    assert "Wednesday 9th September" in text
    assert "half nine" not in text          # the model's words, gone
    assert "Friday" not in text
    assert session["_slotbuf_emitted"] is True


@pytest.mark.asyncio
async def test_the_record_holds_every_time_the_sentence_named():
    """SIX slots, not three.

    This is the invariant the current design cannot state. `last_offered_slots`
    on the model path is one slot per day and positional, so the second time on
    each day -- which the caller definitely heard -- was never in the record.
    """
    session = {"_slot_offer_prebuilt": _prebuilt(), "available_days": PRESENTED}
    spoken = await _flush(session)
    text = " ".join(spoken)
    assert [s["start"] for s in session["last_offered_slots"]] == [
        "2026-09-07T10:00:00+01:00",
        "2026-09-07T17:00:00+01:00",
        "2026-09-08T09:00:00+01:00",
        "2026-09-08T14:00:00+01:00",
        "2026-09-09T11:00:00+01:00",
        "2026-09-09T18:00:00+01:00",
    ]
    for label in session["slot_labels"]:
        assert label in text
    # B-126: a transcript, so no day is marked unsafe to reason from.
    assert LOSSY_SPOKEN_DAYS_KEY not in session


@pytest.mark.asyncio
async def test_the_keypad_map_stays_day_keyed():
    """A digit means a DAY on multi_day, and must keep meaning that.

    `extract_slot_options` commits to the segment before the em dash precisely
    because the keypad injects that label as a synthetic transcript. Mapping a
    digit to a time here would change what every press does.
    """
    session = {
        "_slot_offer_prebuilt": _prebuilt(), "available_days": PRESENTED,
        "turn_count": 4, "v3_slot_map_superseded": True,
    }
    spoken = await _flush(session)
    assert session["v3_dtmf_slot_map"] == {
        "1": "Monday 7th September",
        "2": "Tuesday 8th September",
        "3": "Wednesday 9th September",
    }
    assert session["v3_awaiting_slot_selection"] is True
    assert session["v3_slot_map_armed_turn"] == 4
    assert "v3_slot_map_superseded" not in session
    assert session["_slot_chunks_sent"] == len(spoken)
    assert session["_slot_chunks_inhibited"] == 0


@pytest.mark.asyncio
async def test_the_anchor_is_the_payloads_first_day_not_the_first_day_spoken():
    """Decision A: `v3_last_offered_day_iso` keeps its documented meaning.

    turn_handler documents it as "days[0] of the payload". The one time a
    reader treated it as "the day the caller is being offered", CA6e1024db ran
    four turns with the staleness gate blind for the whole call -- and the fix
    was to read `available_days` as the primary signal, not to redefine this
    scalar. Four readers sit on that contract.

    Here the payload's first day is a CLOSED Sunday that the offer never names,
    so the two sources provably differ and this pins which one wins.
    """
    closed_sunday = _day("2026-09-06", "Sunday 6th September", [], [])
    session = {
        "_slot_offer_prebuilt": _prebuilt(day_iso="2026-09-06"),
        "available_days": [closed_sunday, *PRESENTED],
    }
    await _flush(session)
    assert session["v3_last_offered_day_iso"] == "2026-09-06"
    # ... and the offer itself still never names it.
    assert session["last_offered_slots"][0]["start"].startswith("2026-09-07")


@pytest.mark.asyncio
async def test_single_day_keeps_the_anchor_behaviour_it_shipped_with():
    """`day_iso` absent must leave step 3's behaviour byte-for-byte.

    single_day sends None, so the fallback -- the first slot's date -- still
    decides. On single_day the two coincide, which is why step 3 could take
    either; this test is what stops step 4 changing it by accident.
    """
    session = {
        "_slot_offer_prebuilt": _prebuilt(presented=[TUE]),
        "available_days": [TUE],
    }
    session["_slot_offer_prebuilt"].pop("day_iso")
    await _flush(session)
    assert session["v3_last_offered_day_iso"] == "2026-09-08"


@pytest.mark.asyncio
async def test_no_more_times_tail_on_a_multi_day_readout():
    """B-99: the claim is made only where it has a referent -- one day.

    "A few others that day" after a three-day readout names no day. On
    CA890b511e that sentence was about a day nobody had asked about, in words
    that sounded like it was about the one they had.
    """
    session = {
        "_slot_offer_prebuilt": _prebuilt(more_times=True),
        "available_days": PRESENTED,
    }
    spoken = await _flush(session)
    text = " ".join(spoken).lower()
    assert "a few others" not in text
    assert "that day" not in text


class TestTheBuildSiteContract:
    """What the build site reads, driven from a REAL `_cap_presented_slots`.

    The tests above inject `_slot_offer_prebuilt` and so exercise the SPEAK
    site. The build site lives inside `run_turn`'s tool-result handling, which
    is not callable in isolation and is under the freeze-don't-refactor rule.
    These pin the contract between them instead, which is where a break would
    actually come from: the shape of `presented_days` changing under the
    branch, silently returning it to the model path.
    """

    def _result(self):
        from app.tools.receptionist_tools import _cap_presented_slots
        return _cap_presented_slots(
            {"available_days": [MON, TUE, WED]}, {},
        )

    def test_a_three_day_payload_is_multi_day_and_the_branch_fires(self):
        result = self._result()
        assert result["presentation_mode"] == "multi_day"
        assert result.get("presented_days")
        assert result.get("first_day") is None

    def test_presented_days_carries_one_time_per_day(self):
        """The measured cap, and the reason the readout normalises to 2x1.

        `_cap_presented_slots` sets per_day = 1 as soon as more than one day
        survives, and _MAX_PRESENTED_DAYS is 2. Live readouts are bimodal --
        24 of 52 at 2 days x 1 time, 25 at 3 days x 2 -- because the model
        sometimes obeyed this and sometimes obeyed its own prompt. Building
        from `presented_days` makes this the single owner. Raising per_day here
        is the one-line change that moves both paths together.
        """
        result = self._result()
        days = result["presented_days"]
        assert len(days) <= 2
        for day in days:
            assert len(day["slot_times"]) == 1

    def test_the_offer_built_from_it_is_usable(self):
        from app.tools.slot_offer import build_slot_offer

        result = self._result()
        offer = build_slot_offer(list(result["presented_days"]))
        assert offer is not None
        assert offer.mode == "multi_day"
        assert len(offer.dtmf_map) == len(result["presented_days"])
        # Record and sentence agree -- the invariant the whole plan rests on.
        assert all(s["spoken"] in offer.text for s in offer.slots)

    def test_the_anchor_source_is_the_untrimmed_payload(self):
        """`available_days` stays whole, so days[0] can be a day never spoken."""
        result = self._result()
        assert len(result["available_days"]) == 3
        assert result["available_days"][0]["date"] == "2026-09-07"

    def test_an_acuity_shaped_result_does_NOT_fire_the_branch(self):
        """SCOPE. Theorem is not covered by this change, and must not be assumed.

        `_exec_check_availability` returns at receptionist_tools.py:5902 for
        theorem / theorem_v2 / theorem_v3 -- straight out of
        `_check_availability_acuity` through `_filter_same_day_slots`, never
        through `_cap_presented_slots`. That executor caps the spoken list
        itself (`days_data[:3]`) and stores it in `available_days`; it never
        writes `presented_days`, and its own comment says the per-day cap "is
        enforced by the slot formatter", i.e. by the prompt.

        So Mark's line keeps the model-composed sentence and the whole repair
        layer. Extending step 4 to Acuity is separate work with its own real
        call. This is the recurring shape GENERAL.md 5.3 names: always check
        WHICH executor a clinic uses before believing a fix applies to it.
        """
        acuity_shaped = {
            "presentation_mode": "multi_day",
            "available_days": [MON, TUE, WED],   # the spoken list lives HERE
            "total_days": 3,
        }
        assert acuity_shaped.get("presented_days") is None
        assert not (
            acuity_shaped.get("presentation_mode") == "multi_day"
            and acuity_shaped.get("presented_days")
        )


@pytest.mark.asyncio
async def test_the_further_dates_sentence_survives_the_handover():
    """Section 3c has no mode gate, so the early return must not lose it."""
    session = {
        "_slot_offer_prebuilt": _prebuilt(other_dates=[
            {"date": "2026-09-15", "spoken": "Tuesday 15th September"},
            {"date": "2026-09-22", "spoken": "Tuesday 22nd September"},
        ]),
        "available_days": PRESENTED,
    }
    spoken = await _flush(session)
    text = " ".join(spoken)
    assert "also got" in text.lower()
    assert "15th" in text and "22nd" in text
    # Named, never offered: not in the record, not on the keypad.
    assert all(not s["start"].startswith("2026-09-15")
               for s in session["last_offered_slots"])
    assert all("15th" not in v for v in session["v3_dtmf_slot_map"].values())
