# tests/regression/test_b79_slot_readout_cap_and_record.py
"""
B-79 — CA6b90c3a2c606a522b63839842f926760, 24 Aug 2026, jv_v1, build 91a8db26.

Tuesday 1 September held five times: 17:00, 17:45, 18:30, 19:15, 20:00.

  12:23:49  first offer, correctly capped     "Number 1, five in the evening.
                                               Number 2, quarter to six..."
  12:24:03  caller: "i didn't say anything go ahead"
            model re-calls check_availability -> already_retrieved, which hands
            it the FULL available_days and says "present the existing slots"
  12:24:08  ALL FIVE read out in one breath, five numbered options deep
  12:24:25  caller: "have you got anything else that day"
            -> offered 18:30 and 19:15, which they had just heard
  12:24:39  caller: "tell me the few others you have that day"
            -> offered 20:00, which they had also just heard
  12:24:48  caller hangs up. outcome=abandoned.

Two defects, and the quiet one is the worse:

  B-79a  Nothing capped the SPOKEN option count. The tool capped its own
         payload; the already_retrieved re-entry bypassed that entirely.

  B-79b  Nothing recorded what the model had just read out. The cumulative
         spoken record (B-78b) only ever learned from the deterministic
         follow-up path, so `last_offered_slots` still said "two" after the
         caller had heard five — and every subsequent "anything else?" served
         times they had already been given.

Owner rule (24 Aug): at most THREE times per day on an offer, then "I've a few
others that day"; on an explicit yes, EVERY remaining time on that day, minus
the ones already offered.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    CAPPED_READOUT_QUESTION,
    MAX_SPOKEN_OPTIONS,
    all_remaining_on_next_day,
    cap_spoken_options,
    extract_slot_options,
    format_next_batch_speech,
    remaining_unspoken,
    resolve_spoken_options,
    try_unspoken_followup_speech,
    unspoken_remain_on_day,
)

# The five-option readout, verbatim from the 12:24:08 TTS chunks.
LIVE_FIVE = (
    "Tuesday 1st September — Number 1, five in the evening. "
    "Number 2, quarter to six in the evening. "
    "Number 3, half past six in the evening. "
    "Number 4, quarter past seven in the evening. "
    "Number 5, eight in the evening. Any of those work?"
)

_SPOKEN = {
    "17:00": "five in the evening",
    "17:45": "quarter to six in the evening",
    "18:30": "half past six in the evening",
    "19:15": "quarter past seven in the evening",
    "20:00": "eight in the evening",
    "20:45": "quarter to nine in the evening",
}


def _day(date: str, times: list[str], label: str | None = None) -> dict:
    return {
        "date": date,
        "day_label": label or f"Tuesday {date}",
        "slot_times": list(times),
        "slot_times_spoken": [_SPOKEN.get(t, t) for t in times],
        "slots": [
            {"start": f"{date}T{t}:00", "end": f"{date}T{t}:59"} for t in times
        ],
    }


TUESDAY = _day(
    "2026-09-01", ["17:00", "17:45", "18:30", "19:15", "20:00"], "Tuesday 1st September"
)


def _offer(times: list[str], date: str = "2026-09-01") -> list[dict]:
    return [{"start": f"{date}T{t}:00", "end": f"{date}T{t}:59"} for t in times]


# ───────────────────────────────────────────────────────────────────────────
# B-79a — the readout is capped, and capping does not damage it
# ───────────────────────────────────────────────────────────────────────────

def test_the_live_five_option_readout_is_cut_to_three():
    text, before, after = cap_spoken_options(LIVE_FIVE)
    assert (before, after) == (5, 3)
    assert "half past six in the evening" in text          # option 3 survives
    assert "quarter past seven" not in text                # option 4 removed
    assert "eight in the evening" not in text              # option 5 removed


def test_the_closing_question_survives_the_cut():
    """It lived after the LAST option, so a naive cut takes it with them.

    A slot readout that ends on a statement is dead air the caller has to
    break — and the watchdog BACKSTOP exists precisely because that happens.
    """
    text, _, _ = cap_spoken_options(LIVE_FIVE)
    assert text.rstrip().endswith("Any of those work?")


def test_a_readout_that_never_asked_gets_a_question_anyway():
    text, before, after = cap_spoken_options(
        "Number 1, five. Number 2, six. Number 3, seven. Number 4, eight."
    )
    assert (before, after) == (4, 3)
    assert text.rstrip().endswith(CAPPED_READOUT_QUESTION)
    assert "eight" not in text


def test_three_or_fewer_options_are_returned_untouched():
    for text in (
        "Number 1, five in the evening. Number 2, quarter to six. Any of those work?",
        "Tuesday — Number 1, five in the evening. Does that work?",
        "I don't have anything on Tuesday, I'm afraid.",
        "",
    ):
        out, before, after = cap_spoken_options(text)
        assert out == text
        assert before == after


def test_the_keypad_map_matches_the_capped_speech():
    """The map and the trim must count options with the SAME pattern.

    A keypad pointing at an option the caller never heard is how a booking
    lands on the wrong time while the call sounds perfect.
    """
    full = extract_slot_options(LIVE_FIVE)
    text, _, _ = cap_spoken_options(LIVE_FIVE)
    capped = extract_slot_options(text)
    assert list(capped) == ["1", "2", "3"]
    assert all(capped[d] == full[d] for d in capped)


def test_the_cap_is_three():
    assert MAX_SPOKEN_OPTIONS == 3


# ───────────────────────────────────────────────────────────────────────────
# B-79b — what was read out is recorded, so the follow-up never repeats it
# ───────────────────────────────────────────────────────────────────────────

async def _run_flush(chunks, session):
    """Drive the real _flush_slot_buf and return the TTS chunks it emitted."""
    import asyncio

    from app.media_streams.llm_stream import LLMStream, PRE_SLOT_MARKER

    buf = asyncio.Queue()
    for c in chunks:
        await buf.put(PRE_SLOT_MARKER + c)
    tts = asyncio.Queue()
    await LLMStream._flush_slot_buf(buf, tts, session)

    out = []
    while not tts.empty():
        out.append(tts.get_nowait())
    return out


def _live_session() -> dict:
    """Session state as it stood at 12:24:08 — two times already offered."""
    return {
        "available_days": [TUESDAY],
        "last_offered_slots": _offer(["17:00", "17:45"]),
        # already_retrieved carries no first_day, so these are what the choke
        # point wrote: the wrong-by-default values the derivation must replace.
        "_slot_more_times": False,
        "_slot_n_offered": 2,
        "_slot_presentation_mode": None,
    }


@pytest.mark.asyncio
async def test_the_five_option_blurt_is_trimmed_end_to_end():
    session = _live_session()
    spoken = " ".join(await _run_flush([LIVE_FIVE], session))

    assert "quarter past seven" not in spoken
    assert "eight in the evening" not in spoken
    assert session["v3_dtmf_slot_map"] == {
        "1": "five in the evening",
        "2": "quarter to six in the evening",
        "3": "half past six in the evening",
    }
    assert session["v3_awaiting_slot_selection"] is True


@pytest.mark.asyncio
async def test_trimming_makes_the_more_times_tail_TRUE_not_false():
    """The session flag said "no further times". After the trim that is a lie.

    This is the direction that matters: we removed two real times, so the
    caller must be told they exist. Getting this backwards would leave them
    believing three was the whole day.
    """
    session = _live_session()
    spoken = " ".join(await _run_flush([LIVE_FIVE], session))
    assert "few others" in spoken.lower()
    assert spoken.lower().count("few others") == 1
    # Three options were read, so the grammar must agree with three.
    assert "neither suits" not in spoken.lower()


@pytest.mark.asyncio
async def test_what_was_read_out_becomes_the_offer_on_the_table():
    session = _live_session()
    await _run_flush([LIVE_FIVE], session)

    assert [s["start"] for s in session["last_offered_slots"]] == [
        "2026-09-01T17:00:00",
        "2026-09-01T17:45:00",
        "2026-09-01T18:30:00",
    ]
    assert session["slot_labels"] == [
        "five in the evening",
        "quarter to six in the evening",
        "half past six in the evening",
    ]


@pytest.mark.asyncio
async def test_the_followup_never_re_offers_a_time_already_read_out():
    """THE live regression. Before the fix this returned 18:30 and 19:15."""
    session = _live_session()
    await _run_flush([LIVE_FIVE], session)

    speech = try_unspoken_followup_speech(session, "have you got anything else that day")

    assert speech is not None
    assert "half past six" not in speech.lower()   # heard at 12:24:08
    assert "five in the evening" not in speech.lower()
    assert "quarter past seven in the evening" in speech.lower()
    assert "eight in the evening" in speech.lower()


@pytest.mark.asyncio
async def test_the_day_is_then_exhausted_and_says_so():
    session = _live_session()
    await _run_flush([LIVE_FIVE], session)
    try_unspoken_followup_speech(session, "have you got anything else that day")

    again = try_unspoken_followup_speech(session, "anything else that day?")
    assert again is not None
    assert "don't have any further times on that day" in again
    # And never the sentence the model produced on the live call.
    assert "available slots on that day" not in again


@pytest.mark.asyncio
async def test_a_readout_we_cannot_resolve_leaves_the_record_alone():
    """Deny by default: a partial resolution is worse than no resolution.

    A last_offered_slots that disagrees with the speech is how a caller books
    a time they were never offered.
    """
    session = _live_session()
    before = list(session["last_offered_slots"])
    await _run_flush(
        ["Number 1, ten in the morning. Number 2, eleven in the morning. OK?"],
        session,
    )
    assert session["last_offered_slots"] == before


# ───────────────────────────────────────────────────────────────────────────
# The owner rule — an explicit "yes" gets the WHOLE remainder of that day
# ───────────────────────────────────────────────────────────────────────────

def test_asking_for_the_others_gets_every_remaining_time_on_that_day():
    day = _day(
        "2026-09-01",
        ["17:00", "17:45", "18:30", "19:15", "20:00", "20:45"],
        "Tuesday 1st September",
    )
    session = {"available_days": [day], "last_offered_slots": _offer(
        ["17:00", "17:45", "18:30"]
    )}

    speech = try_unspoken_followup_speech(session, "yes, tell me the others")

    for label in (
        "quarter past seven in the evening",
        "eight in the evening",
        "quarter to nine in the evening",
    ):
        assert label in speech, label
    # Nothing already offered is repeated.
    assert "five in the evening" not in speech
    assert "half past six" not in speech
    # And nothing is held back, so there is no "a few others" tail left to make.
    assert "few others" not in speech.lower()


def test_the_whole_day_batch_never_straddles_a_day_boundary():
    """CA5c4fb14f: a slot announced under the wrong day's name is a real
    patient sent to the clinic on a day they have no appointment."""
    remaining = [
        {"start": "2026-09-01T19:15:00", "date": "2026-09-01",
         "day_label": "Tuesday 1st September", "spoken": "quarter past seven", "time": "19:15"},
        {"start": "2026-09-01T20:00:00", "date": "2026-09-01",
         "day_label": "Tuesday 1st September", "spoken": "eight", "time": "20:00"},
        {"start": "2026-09-02T17:00:00", "date": "2026-09-02",
         "day_label": "Wednesday 2nd September", "spoken": "five", "time": "17:00"},
    ]
    batch, more = all_remaining_on_next_day(remaining)
    assert [s["start"] for s in batch] == [
        "2026-09-01T19:15:00", "2026-09-01T20:00:00",
    ]
    assert more is False


def test_all_remaining_on_an_empty_list_is_not_an_error():
    assert all_remaining_on_next_day([]) == ([], False)


# ───────────────────────────────────────────────────────────────────────────
# The one- and two-slot follow-up wordings must not have drifted
# ───────────────────────────────────────────────────────────────────────────

def _slot(t: str, spoken: str) -> dict:
    return {
        "start": f"2026-09-01T{t}:00", "end": "", "time": t,
        "spoken": spoken, "date": "2026-09-01", "day_label": "Tuesday 1st September",
    }


def test_one_and_two_slot_wording_is_byte_identical_to_the_two_slot_form():
    one = format_next_batch_speech([_slot("19:15", "quarter past seven")], False)
    assert one == (
        "On Tuesday 1st September I also have quarter past seven. Does that work?"
    )
    two = format_next_batch_speech(
        [_slot("19:15", "quarter past seven"), _slot("20:00", "eight")], False
    )
    assert two == (
        "On Tuesday 1st September I also have quarter past seven, or eight. "
        "Either of those work?"
    )


def test_three_slots_read_as_a_spoken_list_with_a_matching_question():
    three = format_next_batch_speech(
        [_slot("18:30", "half six"), _slot("19:15", "quarter past seven"),
         _slot("20:00", "eight")],
        False,
    )
    assert three == (
        "On Tuesday 1st September I also have half six, quarter past seven, "
        "or eight. Any of those work?"
    )


def test_an_exhausted_day_still_answers_deterministically():
    assert "different day" in format_next_batch_speech([], False)


# ───────────────────────────────────────────────────────────────────────────
# resolve_spoken_options — all-or-nothing
# ───────────────────────────────────────────────────────────────────────────

def test_an_unknown_label_resolves_to_nothing_at_all():
    assert resolve_spoken_options([TUESDAY], ["five in the evening", "midnight"]) is None


def test_a_label_on_two_days_is_ambiguous_not_guessed():
    """Guessing here writes the wrong day into last_offered_slots."""
    both = [TUESDAY, _day("2026-09-02", ["17:00"], "Wednesday 2nd September")]
    assert resolve_spoken_options(both, ["five in the evening"]) is None


def test_labels_resolve_regardless_of_case_and_trailing_punctuation():
    got = resolve_spoken_options([TUESDAY], ["Five In The Evening", "quarter to six in the evening."])
    assert got is not None
    assert [s["time"] for s in got] == ["17:00", "17:45"]


def test_empty_inputs_resolve_to_nothing():
    assert resolve_spoken_options([], ["five in the evening"]) is None
    assert resolve_spoken_options([TUESDAY], []) is None


# ───────────────────────────────────────────────────────────────────────────
# unspoken_remain_on_day reads the CUMULATIVE record, not one turn's payload
# ───────────────────────────────────────────────────────────────────────────

def test_more_times_is_false_once_every_time_on_the_day_has_been_spoken():
    session = {"available_days": [TUESDAY], "last_offered_slots": _offer(
        ["17:00", "17:45", "18:30"]
    )}
    assert unspoken_remain_on_day(session, "2026-09-01") is True

    remaining_unspoken(session)                      # folds the current offer in
    try_unspoken_followup_speech(session, "anything else that day")
    assert unspoken_remain_on_day(session, "2026-09-01") is False


def test_a_day_nobody_has_been_offered_still_has_times():
    session = {"available_days": [TUESDAY], "last_offered_slots": []}
    assert unspoken_remain_on_day(session, "2026-09-01") is True
    assert unspoken_remain_on_day(session, "2026-09-09") is False


# ───────────────────────────────────────────────────────────────────────────
# The a74f60c8 multi_day gate must still hold
# ───────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_days_named_still_gets_no_that_day_tail():
    """"And I've a few others that day" after naming two days has no referent."""
    session = {
        "available_days": [
            _day("2026-09-01", ["17:00", "17:45"], "Tuesday 1st September"),
            _day("2026-09-02", ["18:30", "19:15"], "Wednesday 2nd September"),
        ],
        "_slot_more_times": True,
        "_slot_n_offered": 2,
        "_slot_presentation_mode": "multi_day",
    }
    spoken = " ".join(await _run_flush([
        "Number 1, five in the evening on Tuesday 1st September. "
        "Number 2, half past six in the evening on Wednesday 2nd September. "
        "Either of those work?"
    ], session))

    assert "that day" not in spoken.lower()
    # And a cross-day readout is not recorded as one day's offer.
    assert "last_offered_slots" not in session


def test_a_day_with_more_than_nine_unspoken_times_stops_at_the_keypad_limit():
    """Not taste — "Number 10" anchors as "Number 1" and mis-points the keypad."""
    from app.tools.slot_followup import MAX_KEYPAD_OPTIONS

    remaining = [
        {"start": "2026-09-01T%02d:00:00" % h, "date": "2026-09-01",
         "day_label": "Tuesday 1st September", "spoken": "%d o'clock" % h,
         "time": "%02d:00" % h}
        for h in range(9, 21)
    ]
    batch, more = all_remaining_on_next_day(remaining)
    assert len(batch) == MAX_KEYPAD_OPTIONS == 9
    # ...and the caller is TOLD the rest exist rather than losing them silently.
    assert more is True


@pytest.mark.asyncio
async def test_an_explicit_ask_for_the_rest_is_NOT_trimmed_back_to_three():
    """`next_unspoken_batch` raises the ceiling to the batch it built.

    Trimming there would withhold the very times the caller just asked for —
    the defect the batch exists to fix. The cap protects the OFFER, not the
    answer to "tell me the others".
    """
    day = _day(
        "2026-09-01",
        ["17:00", "17:45", "18:30", "19:15", "20:00", "20:45"],
        "Tuesday 1st September",
    )
    session = {
        "available_days": [day],
        "last_offered_slots": _offer(["17:00", "17:45", "18:30"]),
        "_slot_spoken_cap": 3,          # the offer ceiling…
        "_slot_more_times": True,
        "_slot_n_offered": 3,
        "_slot_presentation_mode": "single_day",
    }
    readout = (
        "On Tuesday 1st September I also have — Number 1, quarter past seven "
        "in the evening. Number 2, eight in the evening. "
        "Number 3, quarter to nine in the evening. Any of those work?"
    )

    # …with the offer ceiling in force, three options survive untouched.
    spoken = " ".join(await _run_flush([readout], dict(session)))
    assert "quarter to nine in the evening" in spoken

    # And when the batch is larger, the raised ceiling lets it through whole.
    session["_slot_spoken_cap"] = 6
    big = (
        "Number 1, five in the evening. Number 2, quarter to six in the evening. "
        "Number 3, half past six in the evening. "
        "Number 4, quarter past seven in the evening. Number 5, eight in the "
        "evening. Number 6, quarter to nine in the evening. Any of those work?"
    )
    s2 = dict(session)
    spoken2 = " ".join(await _run_flush([big], s2))
    assert "quarter to nine in the evening" in spoken2
    assert len(s2["v3_dtmf_slot_map"]) == 6
    # The day is now fully spoken, so there is nothing left to promise.
    assert "few others" not in spoken2.lower()


# ───────────────────────────────────────────────────────────────────────────
# A fixed evening rota repeats its spoken labels on every day of the week
# ───────────────────────────────────────────────────────────────────────────

_ROTA = [
    _day("2026-09-01", ["17:00", "17:45", "18:30", "19:15"], "Tuesday 1st September"),
    _day("2026-09-02", ["17:00", "17:45", "18:30", "19:15"], "Wednesday 2nd September"),
    _day("2026-09-03", ["17:00", "17:45", "18:30", "19:15"], "Thursday 3rd September"),
]


def test_a_repeated_rota_label_is_ambiguous_without_the_presented_day():
    assert resolve_spoken_options(_ROTA, ["five in the evening"]) is None


def test_the_presented_day_disambiguates_it():
    got = resolve_spoken_options(
        _ROTA, ["five in the evening", "quarter to six in the evening"],
        prefer_day="2026-09-02",
    )
    assert got is not None
    assert [s["start"] for s in got] == [
        "2026-09-02T17:00:00", "2026-09-02T17:45:00",
    ]


def test_a_wrong_presented_day_falls_back_rather_than_inventing():
    """Scoping must not manufacture a match that isn't on that day."""
    got = resolve_spoken_options(
        _ROTA, ["quarter to nine in the evening"], prefer_day="2026-09-02"
    )
    assert got is None


@pytest.mark.asyncio
async def test_the_record_still_works_on_a_clinic_with_a_repeating_rota():
    """Without prefer_day this fix would be inert on almost every JV call."""
    session = {
        "available_days": _ROTA,
        "last_offered_slots": _offer(["17:00", "17:45"], "2026-09-02"),
        "_slot_presented_day": "2026-09-02",
        "_slot_more_times": False,
        "_slot_n_offered": 2,
        "_slot_presentation_mode": None,
    }
    await _run_flush([
        "Wednesday 2nd September — Number 1, five in the evening. "
        "Number 2, quarter to six in the evening. "
        "Number 3, half past six in the evening. Any of those work?"
    ], session)

    assert [s["start"] for s in session["last_offered_slots"]] == [
        "2026-09-02T17:00:00", "2026-09-02T17:45:00", "2026-09-02T18:30:00",
    ]
    speech = try_unspoken_followup_speech(session, "anything else that day")
    assert "quarter past seven in the evening" in speech
    # ...and it must not wander into Tuesday or Thursday.
    assert "Wednesday 2nd September" in speech
