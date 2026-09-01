# tests/regression/test_slot_presentation_cap.py
"""
Cap the SPOKEN availability list — without amputating availability from the
tool result the model sees.

The single-day cap moved 2 -> 3 on 24 Aug 2026 (owner decision, B-79), which
aligns the generic/Google executor with _check_availability_acuity's long-
standing [:3]. Everything below still asserts the SHAPE — spoken subset small,
`available_days` complete — because that is the invariant. The number is a
policy input, so it is named once, in _MAX_PRESENTED_TIMES_SINGLE_DAY.

b9baf79 capped `available_days` in the check_availability return. Session kept
the full set (so _resolve_slot_iso was fine), but the persona prompt tells the
model to treat check_availability data as ground truth and NOT invent more
times. On V5 (CAaf76d3…, 2026-07-28) Wed 5 Aug had a free 19:45; she offered
17:30/18:15 and refused anything later — the model never tried to book because
the tool payload said only two times existed.

Correct shape (this fix):
  * `available_days` stays the FULL bookable set (POST-REJECTION / "anything
    later?" reads it).
  * Spoken subset is `first_day` (single_day) or `presented_days` (multi_day),
    with `more_times=True` when truncated — matching the Acuity formatter path.
  * session["available_days"] is still set by the caller and untouched here.
"""
from __future__ import annotations

import copy

import pytest

from app.tools.receptionist_tools import (
    _MAX_PRESENTED_TIMES_SINGLE_DAY,
    _cap_presented_slots,
)


def _day(date: str, times: list[str]) -> dict:
    return {
        "date": date,
        "day_label": f"day {date}",
        "slot_times": list(times),
        "slot_times_spoken": [f"spoken {t}" for t in times],
        "slots": [{"start": f"{date}T{t}:00", "end": f"{date}T{t}:59"} for t in times],
    }


def _nine_option_result() -> dict:
    """The CA2f12077c98 shape: 3 days, 3 + 4 + 2 times."""
    return {
        "available_days": [
            _day("2026-07-27", ["18:45", "19:30", "20:15"]),
            _day("2026-07-31", ["16:30", "17:15", "18:00", "18:45"]),
            _day("2026-08-03", ["16:30", "18:00"]),
        ],
        "total_days": 3,
    }


def _v5_wednesday() -> dict:
    """V5 shape: one Wed with FOUR free times (19:00 already booked).

    The live CAaf76d3 day held three, all of which the cap now speaks. The
    fixture keeps a fourth so the invariant under test — an unspoken time
    stays bookable in available_days — is still exercised rather than passing
    vacuously.
    """
    return {
        "available_days": [
            _day("2026-08-05", ["17:30", "18:15", "19:45", "20:30"]),
        ],
        "total_days": 1,
    }


def _spoken_times(result: dict) -> list[str]:
    if result.get("presentation_mode") == "single_day":
        return list((result.get("first_day") or {}).get("slot_times") or [])
    days = result.get("presented_days") or []
    out: list[str] = []
    for d in days:
        out.extend(d.get("slot_times") or [])
    return out


def _bookable_times(result: dict) -> list[str]:
    return [t for d in (result.get("available_days") or []) for t in (d.get("slot_times") or [])]


def test_nine_options_speak_six_but_keep_all_nine_bookable():
    """Three days in, six spoken (two times each) — the multi_day shape.

    The property under test is unchanged and is the one that matters: capping
    SPEECH must never remove a time from the bookable data. Only the spoken
    count moved, 2 -> 6, with the 1 Sept cap decision.
    """
    out = _cap_presented_slots(_nine_option_result())
    assert len(_spoken_times(out)) == 6
    assert len(_bookable_times(out)) == 9


def test_v5_unspoken_half_eight_stays_in_available_days():
    """THE live regression: capped speech must not erase a time from the data."""
    out = _cap_presented_slots(_v5_wednesday())
    assert out["presentation_mode"] == "single_day"
    assert _spoken_times(out) == ["17:30", "18:15", "19:45"]
    assert "20:30" in _bookable_times(out)
    assert "20:30" not in _spoken_times(out)
    assert out["first_day"].get("more_times") is True


def test_more_times_false_when_nothing_was_trimmed():
    src = {"available_days": [_day("2026-08-03", ["16:30", "18:00"])], "total_days": 1}
    out = _cap_presented_slots(src)
    assert _spoken_times(out) == ["16:30", "18:00"]
    assert out["first_day"].get("more_times") is not True


def test_a_single_day_offers_three_times():
    """Owner rule, 24 Aug 2026: three is the most a caller can hold at once."""
    src = {
        "available_days": [_day("2026-08-03", ["16:30", "18:00", "19:00", "20:00"])],
        "total_days": 1,
    }
    out = _cap_presented_slots(src)
    assert len(_spoken_times(out)) == _MAX_PRESENTED_TIMES_SINGLE_DAY == 3
    assert out["presentation_mode"] == "single_day"


def test_a_day_with_exactly_three_times_speaks_all_three_and_claims_no_more():
    src = {
        "available_days": [_day("2026-08-03", ["16:30", "18:00", "19:00"])],
        "total_days": 1,
    }
    out = _cap_presented_slots(src)
    assert _spoken_times(out) == ["16:30", "18:00", "19:00"]
    assert out["first_day"].get("more_times") is not True


def test_multi_day_speaks_TWO_times_per_day():
    """Owner decision 1 Sept 2026, REVERSING 24 Aug.

    The August rule was ONE time per day -- "two days named in a breath is
    already two things to hold, and three times each would be six". It was safe
    to leave wrong, because the model read `available_days` and offered more
    anyway on about half of calls (measured 1 Sept: 24 of 52 readouts at two
    days x one time, 25 at three days x two). Since step 4 of
    DETERMINISTIC_SLOT_PRESENTATION.md the sentence is built from
    `presented_days`, so these constants are now the only thing deciding what a
    caller hears, and the owner chose the richer readout deliberately.

    The caller still holds THREE choices, not six: each day is ONE numbered
    option carrying two times. `test_the_three_by_two_readout_stays_short`
    guards the length that reasoning depends on.
    """
    src = {
        "available_days": [
            _day("2026-08-03", ["16:30", "18:00", "19:00"]),
            _day("2026-08-04", ["17:00", "18:30", "20:00"]),
        ],
        "total_days": 2,
    }
    out = _cap_presented_slots(src)
    assert out["presentation_mode"] == "multi_day"
    assert [d["slot_times"] for d in out["presented_days"]] == [
        ["16:30", "18:00"], ["17:00", "18:30"],
    ]


def test_multi_day_presents_up_to_THREE_days():
    src = {
        "available_days": [
            _day("2026-08-03", ["16:30"]), _day("2026-08-04", ["17:00"]),
            _day("2026-08-05", ["18:00"]), _day("2026-08-06", ["19:00"]),
        ],
        "total_days": 4,
    }
    out = _cap_presented_slots(src)
    assert [d["date"] for d in out["presented_days"]] == [
        "2026-08-03", "2026-08-04", "2026-08-05",
    ]
    assert out.get("more_times") is True     # the fourth day was held back


def test_the_three_by_two_readout_stays_short():
    """The length the 1 Sept decision is betting on, measured not assumed.

    `clinic_template_prompt` warns that "reading out three days with two times
    each takes over twenty seconds, which is where callers hang up". That
    estimate was of a MODEL improvising around the list; the deterministic
    sentence is fixed and this pins it, so a future edit that pads the wording
    fails a test rather than a call.

    ~165 wpm is a conversational TTS rate. The bound is deliberately loose: it
    is a regression guard on the SENTENCE, not a latency measurement.
    """
    from app.tools.slot_offer import build_slot_offer

    src = {
        "available_days": [
            _day("2026-08-03", ["16:30", "18:00"]),
            _day("2026-08-04", ["17:00", "18:30"]),
            _day("2026-08-05", ["09:00", "14:00"]),
        ],
        "total_days": 3,
    }
    out = _cap_presented_slots(src)
    offer = build_slot_offer(list(out["presented_days"]))
    words = len(offer.text.split())
    assert len(offer.dtmf_map) == 3, "three numbered choices, not six"
    assert len(offer.slots) == 6, "six times named, all of them recorded"
    assert words <= 70, (
        f"the three-by-two readout grew to {words} words "
        f"(~{words / 165 * 60:.0f}s spoken): {offer.text!r}"
    )


def test_a_day_with_one_slot_is_left_alone():
    src = {"available_days": [_day("2026-08-03", ["16:30"])], "total_days": 1}
    out = _cap_presented_slots(src)
    assert _spoken_times(out) == ["16:30"]


def test_each_day_keeps_its_EARLIEST_time_in_speech():
    out = _cap_presented_slots(_nine_option_result())
    assert out["presentation_mode"] == "multi_day"
    assert out["presented_days"][0]["slot_times"][0] == "18:45"
    assert out["presented_days"][1]["slot_times"][0] == "16:30"


def test_spoken_labels_stay_aligned_on_presented_subset():
    for d in _cap_presented_slots(_nine_option_result())["presented_days"]:
        assert len(d["slot_times_spoken"]) == len(d["slot_times"])
        assert len(d["slots"]) == len(d["slot_times"])


def test_the_input_is_not_mutated():
    src = _nine_option_result()
    before = copy.deepcopy(src)
    _cap_presented_slots(src)
    assert src == before


def test_error_and_empty_results_pass_through_untouched():
    for src in (
        {"error": "No slots found in the next 7 days.", "slots": []},
        {"available_days": [], "total_days": 0},
        {"available_days": None},
        {},
    ):
        assert _cap_presented_slots(dict(src)) == src


def test_malformed_days_do_not_raise():
    src = {"available_days": [{"date": "2026-08-03"}, {"slot_times": None}], "total_days": 2}
    out = _cap_presented_slots(src)
    assert len(out["available_days"]) == 2


def test_session_keeps_full_availability_after_the_capped_return():
    from app.tools.receptionist_tools import _filter_same_day_slots

    days_data = _nine_option_result()["available_days"]
    session = {"clinic_id": "jv_v1", "available_days": days_data}

    returned = _cap_presented_slots(
        _filter_same_day_slots(
            {"available_days": days_data, "total_days": len(days_data)}, session
        )
    )

    assert len(_spoken_times(returned)) == 6      # 3 days x 2, from 1 Sept
    assert len(session["available_days"]) == 3
    assert sum(len(d["slot_times"]) for d in session["available_days"]) == 9
    # And the RETURN keeps them too — that is what the model reads next turn.
    assert len(_bookable_times(returned)) == 9
    assert "20:15" in _bookable_times(returned)
    assert "20:15" not in _spoken_times(returned)


def test_day_firsts_used_by_numbered_selection_are_preserved():
    src = _nine_option_result()
    day_firsts = [d["slot_times"][0] for d in src["available_days"]]
    out = _cap_presented_slots(src)
    for i, day in enumerate(out["presented_days"]):
        assert day["slot_times"][0] == day_firsts[i]


def test_sync_last_offered_matches_the_spoken_subset_so_remaining_starts_after():
    """last_offered must be what was SPOKEN — not all six.

    Otherwise unspoken follow-up sees remaining=[] after a six-option readout
    (CA16e8c6) and cannot serve a true unspoken V5.
    """
    from app.tools.receptionist_tools import _sync_last_offered_to_spoken
    from app.tools.slot_followup import remaining_slots_after_offer

    days = [_day("2026-08-06", ["16:30", "17:15", "18:00", "18:45", "19:30", "20:15"])]
    out = _cap_presented_slots({"available_days": days, "total_days": 1})
    session: dict = {"available_days": days}
    _sync_last_offered_to_spoken(session, out)
    assert len(session["last_offered_slots"]) == _MAX_PRESENTED_TIMES_SINGLE_DAY
    assert session["last_offered_slots"][0]["start"].startswith("2026-08-06T16:30")
    rem = remaining_slots_after_offer(days, session["last_offered_slots"])
    assert [s["time"] for s in rem] == ["18:45", "19:30", "20:15"]
