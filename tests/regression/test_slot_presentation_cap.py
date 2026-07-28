# tests/regression/test_slot_presentation_cap.py
"""
Cap the SPOKEN availability list at ~2 options — without amputating availability
from the tool result the model sees.

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

from app.tools.receptionist_tools import _cap_presented_slots


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
    """V5 shape: one Wed with three free times (19:00/20:30 already booked)."""
    return {
        "available_days": [
            _day("2026-08-05", ["17:30", "18:15", "19:45"]),
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


def test_nine_options_speak_two_but_keep_all_nine_bookable():
    out = _cap_presented_slots(_nine_option_result())
    assert len(_spoken_times(out)) == 2
    assert len(_bookable_times(out)) == 9


def test_v5_unspoken_quarter_to_eight_stays_in_available_days():
    """THE live regression: capped speech must not erase 19:45 from the data."""
    out = _cap_presented_slots(_v5_wednesday())
    assert out["presentation_mode"] == "single_day"
    assert _spoken_times(out) == ["17:30", "18:15"]
    assert "19:45" in _bookable_times(out)
    assert "19:45" not in _spoken_times(out)
    assert out["first_day"].get("more_times") is True


def test_more_times_false_when_nothing_was_trimmed():
    src = {"available_days": [_day("2026-08-03", ["16:30", "18:00"])], "total_days": 1}
    out = _cap_presented_slots(src)
    assert _spoken_times(out) == ["16:30", "18:00"]
    assert out["first_day"].get("more_times") is not True


def test_a_single_day_still_offers_two_times():
    src = {"available_days": [_day("2026-08-03", ["16:30", "18:00", "19:00"])], "total_days": 1}
    out = _cap_presented_slots(src)
    assert len(_spoken_times(out)) == 2
    assert out["presentation_mode"] == "single_day"


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

    assert len(_spoken_times(returned)) == 2
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


def test_sync_last_offered_matches_spoken_two_so_remaining_starts_after():
    """With the cap, last_offered must be the spoken two — not all six.

    Otherwise unspoken follow-up sees remaining=[] after a six-option readout
    (CA16e8c6) and cannot serve a true unspoken V5.
    """
    from app.tools.receptionist_tools import _sync_last_offered_to_spoken
    from app.tools.slot_followup import remaining_slots_after_offer

    days = [_day("2026-08-06", ["16:30", "17:15", "18:00", "18:45", "19:30", "20:15"])]
    out = _cap_presented_slots({"available_days": days, "total_days": 1})
    session: dict = {"available_days": days}
    _sync_last_offered_to_spoken(session, out)
    assert len(session["last_offered_slots"]) == 2
    assert session["last_offered_slots"][0]["start"].startswith("2026-08-06T16:30")
    rem = remaining_slots_after_offer(days, session["last_offered_slots"])
    assert [s["time"] for s in rem] == ["18:00", "18:45", "19:30", "20:15"]
