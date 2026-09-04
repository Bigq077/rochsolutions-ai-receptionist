"""
Regression: she offered two times, recorded neither, and the caller's pick died.

B-134 — CA9c39d09fe12bfc1e971a7c79571e6139, northgate, build 12c5af8bb1ef,
4 September 2026.

The caller asked for "around midday, 11 o'clock". The P6 stand-down spoke the
model's sentence:

    "Monday 7th September — twenty past eleven in the morning, or ten past
     twelve in the afternoon. Either of those work?"

Both times were REAL — 11:20 and 12:10 were in `available_days` — but the
record still described the offer before it, Monday at 08:00 and 17:10. So:

    07:49:41.846  read-back time NOT in the offer and not safely correctable:
                  names Monday 7th September but not one of the times offered
                  on it ['eight in the morning', 'ten past five in the evening']
    07:49:41.857  deterministic offer STOOD DOWN — the model numbered no
                  options … the standing offer and its record are left untouched
    07:49:51.569  FINAL: 'oh yeah 10 past 12 works'
                  <- NO `caller ACCEPTED` line follows

`slot_accepted_by_caller` declined exactly as its own contract requires — it
takes a time "only among times the caller was actually READ", and 12:10 had
been read but never recorded. The read-back guard then warned twice more, the
final confirmation named a slot nothing had resolved, and the caller hung up.

── WHY THE BRANCH WAS WRONG, AND WHAT IT STILL GETS RIGHT ─────────────────────
P6 stands down when "the model numbered no options and an offer is already on
the table", reading that as: this is an ANSWER to a pick, not a presentation.
Here the model was presenting two new times and simply not numbering them.

The discriminator was already to hand and needs no new judgement: a sentence
that CONFIRMS a pick names a slot the record already holds; this one named two
it did not. So the branch keeps standing down — the caller still hears the
model, and the unspoken payload is still not written over the record, because
that would renumber the keypad under someone mid-sentence. What changes is
that the slots it SPEAKS are now recorded.

`apply_offer_to_session` does the writing, because its docstring is the rule
that was broken: "Anything that speaks an offer calls this. That is the whole
rule, and it is the one the slot layer keeps breaking."

── SAME SHAPE AS THE REST OF THIS FAMILY ──────────────────────────────────────
B-120, B-132, D-A and B-133 were all guards whose safety rested on a premise
that was false in one reachable case. This is the fifth, and its premise is
"no numbers means no presentation".
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    payload_slots_named_in,
    slot_accepted_by_caller,
)
from app.tools.slot_offer import apply_offer_to_session


# The live call's Monday payload, all twelve bookable times.
TIMES = [
    "08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
    "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
]
SPOKEN = [
    "eight in the morning", "ten to nine in the morning",
    "twenty to ten in the morning", "half past ten in the morning",
    "twenty past eleven in the morning", "ten past twelve in the afternoon",
    "one in the afternoon", "ten to two in the afternoon",
    "twenty to three in the afternoon", "half past three in the afternoon",
    "twenty past four in the afternoon", "ten past five in the evening",
]
MONDAY = {
    "date": "2026-09-07",
    "day_label": "Monday 7th September",
    "slot_times": list(TIMES),
    "slot_times_spoken": list(SPOKEN),
    "slots": [
        {"start": "2026-09-07T%s:00+01:00" % t, "end": ""} for t in TIMES
    ],
}
# A second day carrying the SAME spoken labels — the reason the matcher has to
# require a day as well as a time.
TUESDAY = {
    "date": "2026-09-08",
    "day_label": "Tuesday 8th September",
    "slot_times": list(TIMES),
    "slot_times_spoken": list(SPOKEN),
    "slots": [
        {"start": "2026-09-08T%s:00+01:00" % t, "end": ""} for t in TIMES
    ],
}

# What the model actually said, and what the caller actually replied.
MODEL_SENTENCE = (
    "Monday 7th September — twenty past eleven in the morning, or ten past "
    "twelve in the afternoon. Either of those work?"
)
CALLER_PICK = "oh yeah 10 past 12 works"


def _session():
    """The session as it stood when the stand-down fired: the three-day offer
    is the record, and Monday's two original times are all it holds."""
    return {
        "available_days": [MONDAY, TUESDAY],
        "last_offered_slots": [
            {"start": "2026-09-07T08:00:00+01:00", "end": ""},
            {"start": "2026-09-07T17:10:00+01:00", "end": ""},
        ],
        "slot_labels": ["eight in the morning", "ten past five in the evening"],
        "slot_starts_spoken": [
            "2026-09-07T08:00:00", "2026-09-07T17:10:00",
        ],
        "v3_dtmf_slot_map": {
            "1": "Monday 7th September",
            "2": "Tuesday 8th September",
            "3": "Wednesday 9th September",
        },
        "v3_awaiting_slot_selection": True,
    }


def _stand_down(session, sentence=MODEL_SENTENCE):
    """What the P6 branch now does before speaking."""
    named = payload_slots_named_in(session, sentence)
    already = {
        str((s or {}).get("start") or "")[:19]
        for s in (session.get("last_offered_slots") or [])
        if isinstance(s, dict)
    }
    fresh = [s for s in named if str(s.get("start") or "")[:19] not in already]
    if fresh:
        apply_offer_to_session(
            session,
            {"slots": named, "dtmf_map": {}, "mode": "single_day"},
            [sentence],
        )
    return fresh


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_pick_died_before_this_fix():
    """Pins the DEFECT, so the test cannot silently stop testing anything."""
    assert slot_accepted_by_caller(_session(), CALLER_PICK) is None


def test_the_caller_pick_now_resolves():
    """The whole point. He said "10 past 12 works" and nothing happened."""
    session = _session()
    _stand_down(session)

    assert slot_accepted_by_caller(session, CALLER_PICK) == (
        "2026-09-07T12:10:00+01:00"
    )


def test_both_spoken_times_reach_the_record():
    session = _session()
    _stand_down(session)

    starts = [str(s["start"])[:16] for s in session["last_offered_slots"]]
    assert starts == ["2026-09-07T11:20", "2026-09-07T12:10"]


def test_the_read_back_guard_now_has_something_true_to_check():
    """The guard warned three times on the live call because the record named
    times the caller had not been offered. It is right; the record was wrong."""
    session = _session()
    _stand_down(session)

    labels = list(session.get("slot_labels") or [])
    assert "ten past twelve in the afternoon" in labels


# ---------------------------------------------------------------------------
# The guards. Each is a way to turn this into a worse defect.
# ---------------------------------------------------------------------------
def test_a_sentence_confirming_an_existing_slot_changes_nothing():
    """THE guard. P6's real case — the model CONFIRMING a pick — must still
    leave the record alone, or every confirmation renumbers the offer."""
    session = _session()
    before = list(session["last_offered_slots"])

    fresh = _stand_down(
        session,
        "So that's Monday 7th September at eight in the morning — shall I book that in?",
    )

    assert fresh == []
    assert session["last_offered_slots"] == before


def test_a_sentence_naming_no_times_changes_nothing():
    session = _session()
    before = list(session["last_offered_slots"])

    assert _stand_down(session, "Let me just check that for you.") == []
    assert session["last_offered_slots"] == before


def test_a_time_without_its_day_is_not_recorded():
    """THE safety of the matcher. Every spoken label repeats across days, so
    matching on time alone would record slots on days never mentioned — and
    both MONDAY and TUESDAY here carry identical labels."""
    session = _session()
    before = list(session["last_offered_slots"])

    fresh = _stand_down(
        session,
        "twenty past eleven in the morning, or ten past twelve in the afternoon",
    )

    assert fresh == []
    assert session["last_offered_slots"] == before


def test_only_the_named_day_is_recorded():
    """Tuesday holds 11:20 and 12:10 too. Naming Monday must not drag them in."""
    session = _session()
    _stand_down(session)

    dates = {str(s["start"])[:10] for s in session["last_offered_slots"]}
    assert dates == {"2026-09-07"}


def test_an_invented_time_is_never_recorded():
    """The matcher reads the PAYLOAD, so a time the diary does not hold cannot
    enter the record however confidently the model says it."""
    session = _session()
    before = list(session["last_offered_slots"])

    fresh = _stand_down(
        session,
        "Monday 7th September — twenty past three in the morning. Does that work?",
    )

    assert fresh == []
    assert session["last_offered_slots"] == before


def test_the_stale_day_keypad_stops_resolving_digits():
    """She has narrowed to Monday TIMES while the map still said 1=Monday,
    2=Tuesday, 3=Wednesday. Pressing 2 would have booked a different day.

    Superseded, not popped: apply_offer_to_session marks the map so digits stop
    resolving while the voice window stays open, because popping
    `v3_dtmf_slot_map` hands the next turn permission to wipe
    `last_offered_slots` (B-78/B-80).
    """
    session = _session()
    _stand_down(session)

    assert session.get("v3_slot_map_superseded") is True
    assert session.get("v3_dtmf_slot_map"), "the voice window must stay open"


def test_the_day_anchor_follows_the_spoken_day():
    session = _session()
    _stand_down(session)

    assert session.get("v3_last_offered_day_iso") == "2026-09-07"


@pytest.mark.parametrize("bad", [None, "", "   ", 17])
def test_the_matcher_declines_junk(bad):
    assert payload_slots_named_in(_session(), bad) == []


def test_the_matcher_declines_an_empty_payload():
    assert payload_slots_named_in({"available_days": []}, MODEL_SENTENCE) == []
    assert payload_slots_named_in({}, MODEL_SENTENCE) == []


# ---------------------------------------------------------------------------
# Wired, not merely composable
# ---------------------------------------------------------------------------
# The tests above drive the helper and apply_offer_to_session directly, which
# is readable but proves only that the pieces FIT. Neutering the branch in
# llm_stream left every one of them green -- the fix could have shipped dead
# and this file would not have noticed. So the last test drives the real
# function, the way test_b120_* drives it.
import asyncio

from app.media_streams.llm_stream import LLMStream


async def test_the_stand_down_itself_records_what_it_speaks():
    """End to end through `_flush_slot_buf`, in the state the live call was in.

    THE test in this file. If the branch stops adopting, this goes red; the
    composition tests above do not.
    """
    session = _session()
    # A prebuilt offer exists (the Thu/Fri/Sat one the cached path built), the
    # model numbered nothing, and a map is standing -- the exact P6 shape.
    session["_slot_offer_prebuilt"] = {
        "chunks": ["Here's what we've got coming up — Number 1, Thursday 10th September."],
        "slots": [{"start": "2026-09-10T08:00:00+01:00", "end": "",
                   "spoken": "eight in the morning", "date": "2026-09-10"}],
        "dtmf_map": {"1": "Thursday 10th September", "2": "Friday 11th September"},
        "more_times": False,
        "mode": "multi_day",
    }

    buf, tts = asyncio.Queue(), asyncio.Queue()
    await buf.put(MODEL_SENTENCE)
    await LLMStream._flush_slot_buf(buf, tts, session)

    spoken = []
    while not tts.empty():
        spoken.append(tts.get_nowait())

    # The caller still hears the model, not the prebuilt Thursday offer.
    assert any("twenty past eleven" in s for s in spoken), spoken
    assert not any("Thursday" in s for s in spoken), spoken

    # …and the record now matches what he heard, so his pick can land.
    starts = [str(s["start"])[:16] for s in session["last_offered_slots"]]
    assert starts == ["2026-09-07T11:20", "2026-09-07T12:10"], starts
    assert slot_accepted_by_caller(session, CALLER_PICK) == (
        "2026-09-07T12:10:00+01:00"
    )
