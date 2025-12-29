from datetime import datetime, timedelta
from typing import List, Tuple
import pytz

TZ = pytz.timezone("Europe/London")


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def parse_busy(busy: list[dict]) -> list[tuple[datetime, datetime]]:
    """Convert Google busy blocks (RFC3339 strings) into timezone-aware datetimes."""
    out: list[tuple[datetime, datetime]] = []
    for b in busy or []:
        s = b.get("start")
        e = b.get("end")
        if not s or not e:
            continue
        # Google returns Z or offset. datetime.fromisoformat needs small normalization for Z.
        s = s.replace("Z", "+00:00")
        e = e.replace("Z", "+00:00")
        ds = datetime.fromisoformat(s).astimezone(TZ)
        de = datetime.fromisoformat(e).astimezone(TZ)
        out.append((ds, de))
    return out


def next_7_days_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(TZ)
    end = now + timedelta(days=7)
    return now, end


def generate_candidate_slots(
    window_start: datetime,
    window_end: datetime,
    duration_min: int = 30,
    day_start_h: int = 9,
    day_end_h: int = 18,
) -> list[tuple[datetime, datetime]]:
    """
    Generate 30-min slots Mon–Fri between 09:00 and 18:00 inside [window_start, window_end].
    """
    slots: list[tuple[datetime, datetime]] = []
    step = timedelta(minutes=duration_min)

    # Start from next 30-min boundary for neatness
    cursor = window_start
    cursor = cursor.replace(second=0, microsecond=0)
    minute_mod = cursor.minute % duration_min
    if minute_mod != 0:
        cursor += timedelta(minutes=(duration_min - minute_mod))

    while cursor < window_end:
        # skip weekends
        if cursor.weekday() < 5:  # 0=Mon ... 4=Fri
            day_start = cursor.replace(hour=day_start_h, minute=0, second=0, microsecond=0)
            day_end = cursor.replace(hour=day_end_h, minute=0, second=0, microsecond=0)

            slot_start = cursor
            slot_end = cursor + step

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
