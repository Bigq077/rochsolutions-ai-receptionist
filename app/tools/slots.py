# app/tools/slots.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
import pytz

DEFAULT_TZ = pytz.timezone("Europe/London")


def _ensure_tz(dt: datetime, tz=DEFAULT_TZ) -> datetime:
    """
    Ensure dt is timezone-aware in tz.
    If dt is naive -> localize to tz.
    If dt is aware -> convert to tz.
    """
    if dt.tzinfo is None:
        return tz.localize(dt)
    return dt.astimezone(tz)


def _coerce_pair(a: datetime, b: datetime, tz=DEFAULT_TZ) -> tuple[datetime, datetime]:
    """
    Force both datetimes to be timezone-aware in the SAME timezone before comparisons.
    This prevents: TypeError: can't compare offset-naive and offset-aware datetimes
    """
    return _ensure_tz(a, tz), _ensure_tz(b, tz)


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime, tz=DEFAULT_TZ) -> bool:
    """
    Return True if [a_start, a_end) overlaps [b_start, b_end).
    Fully safe across naive/aware mixtures by coercing all inputs.
    """
    a_start, a_end = _coerce_pair(a_start, a_end, tz)
    b_start, b_end = _coerce_pair(b_start, b_end, tz)
    return max(a_start, b_start) < min(a_end, b_end)


def parse_busy(busy: list[dict], tz=DEFAULT_TZ) -> list[tuple[datetime, datetime]]:
    """
    Convert Google busy blocks (RFC3339 strings) into timezone-aware datetimes in tz.
    Google returns strings like:
      - 2026-02-06T08:30:00Z
      - 2026-02-06T08:30:00+00:00
    """
    out: list[tuple[datetime, datetime]] = []
    for b in busy or []:
        s = b.get("start")
        e = b.get("end")
        if not s or not e:
            continue

        # Normalize 'Z' to '+00:00' for fromisoformat
        s = s.replace("Z", "+00:00")
        e = e.replace("Z", "+00:00")

        ds = datetime.fromisoformat(s)
        de = datetime.fromisoformat(e)

        # Force into tz (aware)
        ds = _ensure_tz(ds, tz)
        de = _ensure_tz(de, tz)

        out.append((ds, de))
    return out


def next_7_days_window(now: Optional[datetime] = None, tz=DEFAULT_TZ) -> tuple[datetime, datetime]:
    """
    Returns an aware (tz) window [now, now+7days].
    """
    now = now or datetime.now(tz)
    now = _ensure_tz(now, tz)
    end = now + timedelta(days=7)
    return now, end


def generate_candidate_slots(
    window_start: datetime,
    window_end: datetime,
    duration_min: int = 30,
    day_start_h: int = 9,
    day_end_h: int = 18,
    tz=DEFAULT_TZ,
    clinic_working_hours: Optional[dict] = None,
) -> list[tuple[datetime, datetime]]:
    """
    Generate candidate slots inside [window_start, window_end], all timezone-aware in tz.
    """
    window_start = _ensure_tz(window_start, tz)
    window_end = _ensure_tz(window_end, tz)

    slots: list[tuple[datetime, datetime]] = []
    step = timedelta(minutes=duration_min)

    # Start from next boundary for neatness (preserves tzinfo)
    cursor = window_start.replace(second=0, microsecond=0)
    minute_mod = cursor.minute % duration_min
    if minute_mod != 0:
        cursor += timedelta(minutes=(duration_min - minute_mod))

    day_keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    while cursor < window_end:
        slot_start = _ensure_tz(cursor, tz)
        slot_end = _ensure_tz(cursor + step, tz)

        # Determine working hours for this weekday
        if clinic_working_hours:
            key = day_keys[slot_start.weekday()]  # 0=mon
            hours = clinic_working_hours.get(key)
            if not hours:
                cursor += step
                continue
            ds_h, de_h = int(hours[0]), int(hours[1])
        else:
            # Backward-compatible default (Mon–Fri only)
            if slot_start.weekday() >= 5:
                cursor += step
                continue
            ds_h, de_h = int(day_start_h), int(day_end_h)

        day_start = _ensure_tz(slot_start.replace(hour=ds_h, minute=0, second=0, microsecond=0), tz)
        day_end = _ensure_tz(slot_start.replace(hour=de_h, minute=0, second=0, microsecond=0), tz)

        if slot_start >= day_start and slot_end <= day_end and slot_end <= window_end:
            slots.append((slot_start, slot_end))

        cursor += step

    return slots


def filter_free_slots(
    candidates: list[tuple[datetime, datetime]],
    busy_blocks: list[tuple[datetime, datetime]],
    tz=DEFAULT_TZ,
) -> list[tuple[datetime, datetime]]:
    """
    Filter out candidate slots that overlap with busy blocks.
    Fully safe even if upstream accidentally passes a naive datetime somewhere.
    """
    free: list[tuple[datetime, datetime]] = []

    # Normalize busy blocks once
    norm_busy: list[tuple[datetime, datetime]] = [
        (_ensure_tz(bs, tz), _ensure_tz(be, tz)) for bs, be in (busy_blocks or [])
    ]

    for s, e in candidates or []:
        s = _ensure_tz(s, tz)
        e = _ensure_tz(e, tz)

        if any(overlaps(s, e, bs, be, tz) for bs, be in norm_busy):
            continue
        free.append((s, e))

    return free


def format_slot(slot: tuple[datetime, datetime]) -> str:
    s, _ = slot
    return s.strftime("%a %d %b at %H:%M")


def pick_first_n(slots: list[tuple[datetime, datetime]], n: int = 3) -> list[tuple[datetime, datetime]]:
    return slots[:n]

