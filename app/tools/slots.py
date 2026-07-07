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
    increment_min: Optional[int] = None,
    break_min: int = 0,
) -> list[tuple[datetime, datetime]]:
    """
    Generate candidate slots inside [window_start, window_end], all tz-aware.

    Produced day by day, anchored to each day's OPENING time and spaced
    `increment_min` apart. When no explicit `increment_min` override is given,
    the stride defaults to `duration_min + break_min` — i.e. the appointment
    length plus a gap between appointments (e.g. a 40-min service with a 5-min
    break steps 45 min: 5:00, 5:45, 6:30 …). The break is spacing only; each
    slot still lasts exactly `duration_min`, so the calendar event stays the
    true appointment length. Each slot lasts duration_min.

    Working-hours bounds support FRACTIONAL hours (16.5 = 16:30, 21.1667 ≈
    21:10), so half-hour openings/closings are respected exactly — a slot is
    only emitted when slot_start >= opening AND slot_start + duration <= closing.
    Integer hours (e.g. demo's 8–19) behave exactly as before.
    """
    window_start = _ensure_tz(window_start, tz)
    window_end = _ensure_tz(window_end, tz)

    increment = int(increment_min or (duration_min + break_min))
    dur = timedelta(minutes=duration_min)
    step = timedelta(minutes=increment)
    day_keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    def _hm(val: float) -> tuple[int, int]:
        h = int(val)
        m = int(round((val - h) * 60))
        if m >= 60:  # rounding guard
            h += 1
            m -= 60
        return h, m

    slots: list[tuple[datetime, datetime]] = []

    # Iterate by calendar date so DST transitions never drift the boundaries.
    d = window_start.date()
    end_d = window_end.date()
    while d <= end_d:
        wd = d.weekday()  # 0=mon
        if clinic_working_hours:
            hours = clinic_working_hours.get(day_keys[wd])
            if not hours:
                d += timedelta(days=1)
                continue
            ds_v, de_v = float(hours[0]), float(hours[1])
        else:
            if wd >= 5:  # backward-compatible default: Mon–Fri only
                d += timedelta(days=1)
                continue
            ds_v, de_v = float(day_start_h), float(day_end_h)

        ds_h, ds_m = _hm(ds_v)
        de_h, de_m = _hm(de_v)
        day_open = _ensure_tz(datetime(d.year, d.month, d.day, ds_h, ds_m), tz)
        day_close = _ensure_tz(datetime(d.year, d.month, d.day, de_h, de_m), tz)

        cursor = day_open
        while cursor + dur <= day_close and cursor < window_end:
            if cursor >= window_start:
                slots.append((cursor, _ensure_tz(cursor + dur, tz)))
            cursor = _ensure_tz(cursor + step, tz)
        d += timedelta(days=1)

    return slots


def filter_free_slots(
    candidates: list[tuple[datetime, datetime]],
    busy_blocks: list[tuple[datetime, datetime]],
    tz=DEFAULT_TZ,
    break_min: int = 0,
) -> list[tuple[datetime, datetime]]:
    """
    Filter out candidate slots that overlap with busy blocks.
    Fully safe even if upstream accidentally passes a naive datetime somewhere.

    `break_min` pads every busy block by that many minutes on BOTH sides before
    the overlap test, so a candidate is rejected if it starts or ends within
    `break_min` of an existing appointment. This enforces the inter-appointment
    break against bookings already on the calendar — including ones added
    manually or of a different length than the service being offered (e.g. a
    40-min booking 5:00–5:40 padded to 4:55–5:45 leaves 5:45 bookable, exactly
    the 5-min break). The busy block itself is not widened on the calendar; the
    padding lives only in this availability test.
    """
    free: list[tuple[datetime, datetime]] = []

    # Normalize busy blocks once, padding by the break on both sides.
    pad = timedelta(minutes=int(break_min or 0))
    norm_busy: list[tuple[datetime, datetime]] = [
        (_ensure_tz(bs, tz) - pad, _ensure_tz(be, tz) + pad) for bs, be in (busy_blocks or [])
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

