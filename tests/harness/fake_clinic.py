"""An in-memory diary and a fake tool table, shaped exactly like the real ones.

WHY IT MIRRORS THE REAL READER SO CLOSELY
-----------------------------------------
The defect family that has dominated this repo lives in the seam between what
Susie SAYS and what the session RECORDS she said. `_flush_slot_buf` parses the
model's own sentence back out and overwrites `last_offered_slots`,
`slot_labels`, `slot_starts_spoken` and `v3_dtmf_slot_map` from that parse.

A tool stub that returned a hand-written dict would run none of that. It would
be green while the pipeline that actually breaks was never entered.

So `check_availability` here does what `_check_availability_diary` does, in the
same order, calling the SAME functions:

    _spoken_starts_for -> _select_presented_tuples -> _build_days_data
      -> session writes -> _filter_same_day_slots -> _cap_presented_slots
      -> _sync_last_offered_to_spoken

Only the calendar read is replaced. Everything downstream of the payload is
production code, so an offer-record defect reproduces here.

`book_appointment` does NOT mirror its executor - it records the write and
returns. Booking's failure modes are provider-shaped (Acuity 400s, admin flags,
SMS latches) and belong in their own tests; what this harness needs from it is
a truthful record of WHAT WOULD HAVE BEEN WRITTEN, so a test can assert the
diary entry matches what the caller was told.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclasses.dataclass
class ToolCall:
    """One tool invocation, as the model asked for it."""
    name: str
    args: Dict[str, Any]
    result: Any = None


@dataclasses.dataclass
class Booking:
    """A write the engine believes it made."""
    start: str
    end: str
    name: str
    phone: str
    service: str
    duration_min: Optional[int]
    raw_args: Dict[str, Any]


class FakeDiary:
    """Free slots in memory, keyed by date.

    Times are Europe/London-AWARE datetimes, built with `LONDON_TZ.localize()`
    exactly as `_check_availability_acuity` does for its own slots.

    This is not cosmetic. `_filter_tuples_by_preference` - which both
    `_select_presented_tuples` and `_build_days_data` run through - compares
    every slot against `datetime.now(LONDON_TZ)` to drop past times. Naive
    tuples raise TypeError there, and the executor's broad `except` turns that
    into "I'm having trouble pulling up availability" with no traceback: the
    call sounds like a provider outage and nothing says otherwise.

    pytz requires `.localize()`; `replace(tzinfo=LONDON_TZ)` silently yields
    the LMT offset (-00:01) and would shift every slot by a minute.
    """

    def __init__(self, slots: Optional[Dict[str, List[str]]] = None,
                 default_duration_min: int = 60) -> None:
        # {"2026-09-01": ["09:00", "14:00"]}
        self.slots: Dict[str, List[str]] = {k: list(v) for k, v in (slots or {}).items()}
        self.default_duration_min = default_duration_min
        self.bookings: List[Booking] = []
        self.cancelled: List[str] = []

    # -- construction helpers ------------------------------------------------

    @classmethod
    def weekly(cls, start: datetime, days: int, times: List[str],
               weekdays: Optional[List[int]] = None,
               default_duration_min: int = 60) -> "FakeDiary":
        """A diary with the same `times` free on each matching weekday."""
        slots: Dict[str, List[str]] = {}
        for n in range(days):
            d = (start + timedelta(days=n)).date()
            if weekdays is not None and d.weekday() not in weekdays:
                continue
            slots[d.isoformat()] = list(times)
        return cls(slots, default_duration_min=default_duration_min)

    # -- reads ---------------------------------------------------------------

    def free_tuples(self, after: Optional[datetime] = None,
                    window_days: Optional[int] = None,
                    duration_min: Optional[int] = None
                    ) -> List[Tuple[datetime, datetime]]:
        """(start, end) pairs, London-aware, the shape `_build_days_data` eats."""
        from app.tools.receptionist_tools import LONDON_TZ

        dur = timedelta(minutes=duration_min or self.default_duration_min)
        out: List[Tuple[datetime, datetime]] = []
        for date_iso in sorted(self.slots):
            day = datetime.fromisoformat(date_iso).date()
            if after is not None and day < after.date():
                continue
            if after is not None and window_days is not None:
                if day > (after + timedelta(days=window_days)).date():
                    continue
            for hhmm in sorted(self.slots[date_iso]):
                hour, minute = (int(x) for x in hhmm.split(":"))
                naive = datetime.combine(
                    day, datetime.min.time()
                ).replace(hour=hour, minute=minute)
                start = LONDON_TZ.localize(naive)
                out.append((start, start + dur))
        return out

    def is_free(self, start_iso: str) -> bool:
        try:
            dt = datetime.fromisoformat(start_iso.replace("Z", ""))
        except ValueError:
            return False
        return dt.strftime("%H:%M") in self.slots.get(dt.date().isoformat(), [])

    # -- writes --------------------------------------------------------------

    def book(self, booking: Booking) -> None:
        self.bookings.append(booking)
        try:
            dt = datetime.fromisoformat(booking.start.replace("Z", ""))
        except ValueError:
            return
        day = dt.date().isoformat()
        hhmm = dt.strftime("%H:%M")
        if hhmm in self.slots.get(day, []):
            self.slots[day].remove(hhmm)


def build_tool_executors(diary: FakeDiary, calls: List[ToolCall],
                         now: Optional[datetime] = None) -> Dict[str, Callable]:
    """A TOOL_EXECUTORS-shaped table backed by `diary`, recording into `calls`.

    Only names the REAL table already has are exposed, so this file cannot
    invent a tool the engine does not know, and the driver's parity check
    against the real key set stays meaningful.
    """
    from app.tools import receptionist_tools as rt
    from app.tools.slots import format_slot

    async def check_availability(args: Dict[str, Any],
                                 session: Dict[str, Any]) -> Dict[str, Any]:
        anchor = now or datetime.now()
        after_raw = (args.get("after_date") or "").strip()
        if after_raw:
            try:
                anchor = datetime.fromisoformat(after_raw)
            except ValueError:
                pass

        window = args.get("day_window")
        try:
            window = int(window) if window is not None else None
        except (TypeError, ValueError):
            window = None

        duration = args.get("duration") or args.get("duration_minutes")
        try:
            duration = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None

        free_slots = diary.free_tuples(after=anchor, window_days=window,
                                       duration_min=duration)
        if not free_slots:
            return {
                "error": "no_availability",
                "message": (
                    "There is nothing free in that window. Say so plainly, offer "
                    "to look at a different period, or take their details with "
                    "add_to_waitlist. Do NOT invent a time."
                ),
                "slots": [], "available_days": [], "total_days": 0,
            }

        # --- from here down, this is the real reader's own sequence ---
        pref = (args.get("date_hint") or args.get("preference") or "").strip()
        spoken = rt._spoken_starts_for(session)

        presented = rt._select_presented_tuples(
            free_slots, preference=pref, spoken_starts=spoken)
        days_data = rt._build_days_data(
            free_slots, preference=pref, spoken_starts=spoken)

        session["last_offered_slots"] = [
            {"start": s[0].isoformat(), "end": s[1].isoformat()} for s in presented
        ]
        session["slot_labels"] = [format_slot(s) for s in presented]
        session["available_days"] = days_data

        payload = {"available_days": days_data, "total_days": len(days_data)}
        out = rt._cap_presented_slots(
            rt._filter_same_day_slots(payload, session), session)
        rt._sync_last_offered_to_spoken(session, out)
        return out

    async def book_appointment(args: Dict[str, Any],
                               session: Dict[str, Any]) -> Dict[str, Any]:
        """Record the write and mirror the session state the real path sets.

        The arg is `slot_iso` and the success key is `booked_slot` - both taken
        from the schema and the executor, not guessed. Getting either wrong
        makes the model apologise for a booking that "failed" while the stub
        happily recorded it, which is the harness telling a lie in the most
        expensive possible direction.

        The session writes below are not decoration: `booking_confirmed` is
        what connection.py reads at teardown to decide "booked" vs
        "caller_hung_up", and omitting it made every harness booking look like
        an abandoned call - the exact defect that hid 25 real bookings in July.
        """
        start = (args.get("slot_iso") or "").strip()
        if not start:
            return {"success": False, "error": "Invalid slot datetime: missing slot_iso"}

        dur = args.get("duration_minutes")
        try:
            dur = int(dur) if dur is not None else None
        except (TypeError, ValueError):
            dur = None

        try:
            sdt = datetime.fromisoformat(start)
        except ValueError as exc:
            return {"success": False, "error": f"Invalid slot datetime: {exc}"}
        edt = sdt + timedelta(minutes=dur or diary.default_duration_min)

        name = (args.get("patient_name") or "").strip()
        phone = (args.get("phone") or "").strip()
        service = (args.get("service") or "").strip()
        location = (args.get("location") or "").strip()

        booking = Booking(
            start=sdt.isoformat(), end=edt.isoformat(), name=name, phone=phone,
            service=service, duration_min=dur, raw_args=dict(args),
        )
        diary.book(booking)

        event_id = f"fake-event-{len(diary.bookings)}"
        rt._sync_booked_patient_name(session, name)
        collected = session.setdefault("collected", {})
        collected["phone"] = phone
        collected["service"] = service
        collected["location"] = location
        collected["slot"] = start
        session["selected_slot"] = start
        session["calendar_event_id"] = event_id
        session["calendar_status"] = "created"
        session["booking_confirmed"] = True

        return {
            "success": True,
            "event_id": event_id,
            "booked_slot": sdt.strftime("%A %d %B at %H:%M"),
            "location": location.title(),
        }

    async def cancel_appointment(args: Dict[str, Any],
                                 session: Dict[str, Any]) -> Dict[str, Any]:
        diary.cancelled.append((args.get("appointment_id") or "").strip())
        return {"success": True, "cancelled": True}

    def _inert(name: str, payload: Dict[str, Any]) -> Callable:
        async def _fn(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
            return dict(payload)
        _fn.__name__ = f"fake_{name}"
        return _fn

    table: Dict[str, Callable] = {
        "check_availability": check_availability,
        "book_appointment": book_appointment,
        "cancel_appointment": cancel_appointment,
    }

    # Everything else answers inertly but is still RECORDED, so a test can
    # assert on tool choice without any of them reaching a provider.
    inert_defaults = {
        "lookup_appointment": {"found": False},
        "lookup_recent_appointment": {"found": False},
        "lookup_patient": {"found": False},
        "confirm_appointment_found": {"success": True},
        "reschedule_appointment": {"success": True},
        "get_patient_history": {"found": False},
        "send_followup_sms": {"success": True, "sent": False},
        "transfer_to_human": {"success": True},
        "add_to_waitlist": {"success": True},
        "request_callback": {"success": True},
        "log_call_outcome": {"success": True},
        "collect_and_store": {"success": True},
        "get_clinic_info": {"success": True},
    }
    for name, payload in inert_defaults.items():
        table[name] = _inert(name, payload)

    real_names = set(getattr(rt, "TOOL_EXECUTORS", {}) or {})
    if real_names:
        table = {k: v for k, v in table.items() if k in real_names}

    def _record(name: str, fn: Callable) -> Callable:
        async def _wrapped(args: Dict[str, Any], session: Dict[str, Any]):
            entry = ToolCall(name=name, args=dict(args))
            calls.append(entry)
            entry.result = await fn(args, session)
            return entry.result
        return _wrapped

    return {name: _record(name, fn) for name, fn in table.items()}
