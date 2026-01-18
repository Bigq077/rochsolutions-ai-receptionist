from datetime import datetime, timedelta
from typing import Optional
import pytz

DEFAULT_TZ = pytz.timezone("Europe/London")


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def _ensure_tz(dt: datetime, tz) -> datetime:
    """
    Make sure dt is timezone-aware in tz.
    """
    if dt.tzinfo is None:
        return tz.localize(dt)
    return dt.astimezone(tz)


def parse_busy(busy: list[dict], tz=DEFAULT_TZ) -> list[tuple[datetime, datetime]]:
    """
    Convert Google busy blocks (RFC3339 strings) into timezone-aware datetimes in tz.
    """
    out: list[tuple[datetime, datetime]] = []
    for b in busy or []:
        s = b.get("start")
        e = b.get("end")
        if not s or not e:
            continue

        # Google returns Z or offset. datetime.fromisoformat needs small normalization for Z.
        s = s.replace("Z", "+00:00")
        e = e.replace("Z", "+00:00")

        ds = datetime.fromisoformat(s)
        de = datetime.fromisoformat(e)

        ds = _ensure_tz(ds, tz)
        de = _ensure_tz(de, tz)

        out.append((ds, de))
    return out


def next_7_days_window(now: Optional[datetime] = None, tz=DEFAULT_TZ) -> tuple[datetime, datetime]:
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
    Generate slots inside [window_start, window_end].

    If clinic_working_hours is provided (e.g. from clinic_config["working_hours"]),
    it will use per-day hours and will NOT force Mon–Fri only.

    clinic_working_hours example:
      {
        "mon": (8, 19),
        "tue": (8, 19),
        ...
        "sat": (9, 14),
        "sun": None
      }

    If not provided, it falls back to the old behaviour with day_start_h/day_end_h
    and will include Mon–Fri by default.
    """
    window_start = _ensure_tz(window_start, tz)
    window_end = _ensure_tz(window_end, tz)

    slots: list[tuple[datetime, datetime]] = []
    step = timedelta(minutes=duration_min)

    # Start from next boundary for neatness
    cursor = window_start.replace(second=0, microsecond=0)
    minute_mod = cursor.minute % duration_min
    if minute_mod != 0:
        cursor += timedelta(minutes=(duration_min - minute_mod))

    # Helper to read per-day hours
    day_keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    while cursor < window_end:
        slot_start = cursor
        slot_end = cursor + step

        # Determine hours for this weekday
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

        day_start = slot_start.replace(hour=ds_h, minute=0, second=0, microsecond=0)
        day_end = slot_start.replace(hour=de_h, minute=0, second=0, microsecond=0)

        if slot_start >= day_start and slot_end <= day_end and slot_end <= window_end:
            slots.append((slot_start, slot_end))

        cursor += step

    return slots


def filter_free_slots(
    candidates: list[tuple[datetime, datetime]],
    busy_blocks: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    free: list[tuple[datetime, datetime]] = []
    for s, e in candidates:
        if any(overlaps(s, e, bs, be) for bs, be in busy_blocks):
            continue
        free.append((s, e))
    return free


def format_slot(slot: tuple[datetime, datetime]) -> str:
    s, _ = slot
    # Example: "Tue 30 Dec at 14:30"
    return s.strftime("%a %d %b at %H:%M")


def pick_first_n(slots: list[tuple[datetime, datetime]], n: int = 3) -> list[tuple[datetime, datetime]]:
    return slots[:n]
