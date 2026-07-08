"""Unit tests for app.obs.reports — pure aggregation."""
from __future__ import annotations

from app.obs import reports


def _c(sid, clinic, start, *, booked=False, score=None, tags=None):
    return {
        "call_sid": sid, "clinic_id": clinic, "start_utc": start,
        "booking_confirmed": booked, "quality_score": score, "failure_tags": tags or [],
    }


def test_iso_week():
    assert reports.iso_week("2026-07-06T10:00:00+00:00") == "2026-W28"
    assert reports.iso_week(None) == "unknown"
    assert reports.iso_week("not-a-date") == "unknown"


def test_summarise_volume_booking_mean_and_tags():
    calls = [
        _c("A", "theorem", "2026-07-06T10:00:00+00:00", booked=True, score=5),
        _c("B", "theorem", "2026-07-06T11:00:00+00:00", booked=False, score=1, tags=["dead_end", "wrong_info"]),
        _c("C", "theorem", "2026-07-06T12:00:00+00:00", booked=True, score=3, tags=["dead_end"]),
        _c("D", "theorem", "2026-07-06T13:00:00+00:00"),  # unscored, not booked
    ]
    s = reports.summarise(calls)
    assert s["volume"] == 4
    assert s["booked"] == 2
    assert s["booking_rate"] == 0.5
    assert s["scored"] == 3
    assert s["mean_quality_score"] == (5 + 1 + 3) / 3
    assert s["failure_tags"] == {"dead_end": 2, "wrong_info": 1}


def test_summarise_empty():
    s = reports.summarise([])
    assert s["volume"] == 0
    assert s["booking_rate"] == 0.0
    assert s["mean_quality_score"] is None


def test_by_clinic_week_groups_and_sorts():
    calls = [
        _c("A", "theorem", "2026-07-06T10:00:00+00:00", score=5),   # 2026-W28
        _c("B", "jv", "2026-07-06T10:00:00+00:00", score=2),        # 2026-W28
        _c("C", "theorem", "2026-06-29T10:00:00+00:00", score=4),   # 2026-W27
    ]
    rows = reports.by_clinic_week(calls)
    keys = [(r["clinic_id"], r["week"]) for r in rows]
    assert keys == [("jv", "2026-W28"), ("theorem", "2026-W27"), ("theorem", "2026-W28")]
    assert all("mean_quality_score" in r and "volume" in r for r in rows)


def test_bottom_decile_selects_worst_and_ties():
    # 10 judged calls scored 1..5; worst ~10% = ceil(10*0.1)=1 → but ties at score 1 included.
    calls = [_c(f"S{i}", "theorem", "2026-07-06T10:00:00+00:00", score=sc)
             for i, sc in enumerate([1, 1, 2, 3, 3, 4, 4, 5, 5, 5])]
    worst = reports.bottom_decile(calls)
    assert {c["quality_score"] for c in worst} == {1}
    assert len(worst) == 2  # both score-1 calls (tie inclusion)


def test_bottom_decile_empty_when_unscored():
    assert reports.bottom_decile([_c("A", "theorem", "2026-07-06T10:00:00+00:00")]) == []
