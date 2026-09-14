"""CAd2747d0689 (14 Sep 2026, demo, c16e1aa4) — booked a slot 13 minutes away.

    15:16 BST  call starts
    Susie      "Number 1, Monday 14th September — half past three …"
    Caller     "half past 3 on the monday works"      -> booked 15:30

The Acuity reader has dropped slots inside 2 h for months; the generated
readers (Google Calendar, diary) started the grid at `now`. JV runs the Google
reader with allow_same_day, so a live patient could be offered a slot minutes
away. Owner, 14 Sep 2026: "at least 2 hours".
"""
import inspect
import re
from datetime import datetime, timedelta

import pytz

from app.tools import receptionist_tools as rt
from app.tools.slots import generate_candidate_slots

L = pytz.timezone("Europe/London")
HOURS = {d: (8, 19) for d in ("mon", "tue", "wed", "thu", "fri")}


def _slots(now, not_before=None):
    return generate_candidate_slots(
        now, now + timedelta(days=1), duration_min=40, clinic_working_hours=HOURS,
        break_min=10, not_before=not_before,
    )


def test_the_call_at_1516_is_not_offered_1530():
    now = L.localize(datetime(2026, 9, 14, 15, 16))
    starts = [s[0] for s in _slots(now, rt._min_notice_start({}, now))]
    assert starts, "later days are still offered"
    assert all(s >= now + timedelta(hours=2) for s in starts)
    assert L.localize(datetime(2026, 9, 14, 15, 30)) not in starts


def test_without_the_notice_the_grid_is_unchanged():
    now = L.localize(datetime(2026, 9, 14, 15, 16))
    assert _slots(now) == generate_candidate_slots(
        now, now + timedelta(days=1), duration_min=40, clinic_working_hours=HOURS, break_min=10)


def test_the_grid_stays_anchored_on_the_opening_time():
    now = L.localize(datetime(2026, 9, 14, 15, 16))
    full = _slots(now)
    trimmed = _slots(now, rt._min_notice_start({}, now))
    assert set(trimmed) <= set(full)


def test_notice_is_never_less_than_two_hours():
    now = L.localize(datetime(2026, 9, 14, 9, 0))
    assert rt._min_notice_start({"min_notice_minutes": 30}, now) == now + timedelta(hours=2)
    assert rt._min_notice_start({"min_notice_minutes": 240}, now) == now + timedelta(hours=4)
    assert rt._min_notice_start(None, now) == now + timedelta(hours=2)


def test_every_generated_reader_passes_the_notice():
    src = inspect.getsource(rt)
    calls = re.findall(r"generate_candidate_slots\((.*?)\n\s*\)", src, re.DOTALL)
    assert len(calls) >= 5
    assert all("not_before=_min_notice_start(" in c for c in calls), calls


def test_clinic_config_defaults_to_two_hours():
    from app.clinic_config import get_clinic
    for cid in ("jv_v1", "northgate"):
        assert get_clinic(cid)["min_notice_minutes"] >= 120
