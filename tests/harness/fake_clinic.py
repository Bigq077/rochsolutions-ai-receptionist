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

`book_appointment` goes further still: it runs the REAL executor and fakes only
its I/O (the token store, create_event, and the owner SMS). The booking path
carries the service and modality reconciliations, `_resolve_duration_minutes`
and the past-slot guard - all of which decide WHAT LANDS IN A PRACTITIONER'S
CALENDAR. A stub that recorded `args["service"]` would be blind to every one of
them, and would report a fix to any of them as still broken.

The recorded Booking is therefore built from the create_event call arguments
and `session["collected"]`, i.e. from what the executor actually resolved -
never from what the model asked for.
"""
from __future__ import annotations

import dataclasses
from contextlib import ExitStack
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import patch


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

    @staticmethod
    def _digits(value: str) -> str:
        """Compare phone numbers by their last nine digits.

        The caller's number reaches the engine as +447700900141, the seeded
        booking holds "07700 900141", and the model passes back whatever it
        heard. Matching on the raw strings finds nothing, which is exactly how
        three personas came to ring about an appointment nobody could see.
        """
        return "".join(c for c in str(value or "") if c.isdigit())[-9:]

    def find_bookings(self, phone: str = "", name: str = "") -> List[Booking]:
        """Every booking matching this caller. Phone wins; name is the fallback."""
        wanted_phone = self._digits(phone)
        wanted_name = (name or "").strip().lower()
        out = []
        for booking in self.bookings:
            if wanted_phone and self._digits(booking.phone) == wanted_phone:
                out.append(booking)
            elif wanted_name and wanted_name in (booking.name or "").lower():
                out.append(booking)
        return out

    def cancel_booking(self, booking: Booking) -> bool:
        """Remove it, and give the slot back to availability.

        Returning the slot matters: a cancelled appointment that stays busy
        means the caller who rings straight back cannot rebook the time they
        just freed.
        """
        if booking not in self.bookings:
            return False
        self.bookings.remove(booking)
        try:
            dt = datetime.fromisoformat(booking.start.replace("Z", ""))
        except ValueError:
            return True
        day, hhmm = dt.date().isoformat(), dt.strftime("%H:%M")
        if day in self.slots and hhmm not in self.slots[day]:
            self.slots[day].append(hhmm)
            self.slots[day].sort()
        return True

    def seed_booking(self, name: str, phone: str, start: datetime,
                     service: str = "physiotherapy assessment",
                     duration_min: int = 60) -> Booking:
        """An appointment that already existed when the call started.

        You cannot cancel or move what was never booked, so the personas that
        ring to do either need one of these. It goes through `book()` rather
        than straight onto the list, so the slot is taken out of availability
        exactly as a real prior booking would be -- otherwise Susie can offer
        the caller the very slot they are ringing to cancel.
        """
        booking = Booking(
            start=start.isoformat(),
            end=(start + timedelta(minutes=duration_min)).isoformat(),
            name=name, phone=phone, service=service,
            duration_min=duration_min, raw_args={"seeded": True},
        )
        self.book(booking)
        return booking

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

    # Captured BEFORE the driver patches the table, so book_appointment can
    # still reach the genuine executor.
    real_executors = dict(getattr(rt, "TOOL_EXECUTORS", {}) or {})

    async def check_availability(args: Dict[str, Any],
                                 session: Dict[str, Any]) -> Dict[str, Any]:
        """Run the REAL `_exec_check_availability`, faking only the READER.

        `_exec_check_availability` is not a thin dispatcher - it is a gate
        stack (service validity, location confirmed, duration choice) plus the
        session pins that later turns depend on, and only then a dispatch to
        one of four readers.

        An earlier version of this stub replaced the whole executor and went
        straight to the payload builders. That skipped every gate and every
        pin - including `_checked_service`, which is what makes
        book_appointment book the service the caller was actually shown. So a
        fix to that pin was invisible here: the harness reported the defect as
        still live because it never ran the fixed line.

        Now only the reader is replaced, per clinic arm. The Google-Calendar
        body is inline in the executor and cannot be patched, so that arm is
        cut off at `freebusy` instead.
        """
        import app.tools.calendar_google as gcal

        async def _reader(a, s, clinic=None):
            return await _payload_from_diary(a, s)

        def _no_busy(*a, **kw):
            return {}

        async def _tokens(*a, **kw):
            return {"token": "fake", "migrated": True}

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(rt, "_check_availability_diary", _reader))
            stack.enter_context(
                patch.object(rt, "_check_availability_published", _reader))
            stack.enter_context(
                patch.object(rt, "_check_availability_acuity",
                             lambda a, s: _reader(a, s)))
            stack.enter_context(patch.object(rt, "_get_tokens", _tokens))
            stack.enter_context(patch.object(gcal, "freebusy", _no_busy))
            return await real_executors["check_availability"](args, session)

    async def _payload_from_diary(args: Dict[str, Any],
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
        """Run the REAL booking executor with only its I/O faked.

        An earlier version of this stub replaced the executor wholesale and
        recorded `args["service"]` as the booked service. That made the harness
        blind to the entire booking path - including the service and modality
        reconciliations, `_resolve_duration_minutes`, and the past-slot guard -
        and it would have reported a fix to any of them as still broken,
        because the stub never ran the fixed code.

        So the executor runs. Faked here, and only here:

          * `_get_tokens`      - the Google token store (Redis)
          * `create_event` / `update_event` / `patch_event_time` - the write
          * `notify_owner`     - the owner SMS

        The recorded Booking is derived from the create_event CALL ARGUMENTS,
        which is the truest possible record: it is literally what would have
        landed in the practitioner's calendar.

        The provisional path imports create_event INSIDE the function, so
        patching the module attribute is what takes effect.
        """
        import app.notifications.owner_notify as owner_notify
        import app.tools.calendar_google as gcal

        created: Dict[str, Any] = {}

        def _fake_create_event(stored_tokens, start_dt, end_dt, summary,
                               description="", calendar_id=None,
                               visibility="default"):
            created["start_dt"] = start_dt
            created["end_dt"] = end_dt
            created["summary"] = summary
            created["description"] = description
            created["calendar_id"] = calendar_id
            return {"id": f"fake-event-{len(diary.bookings) + 1}"}

        def _fake_update_event(*a, **kw):
            return {"id": created.get("id") or "fake-event"}

        def _fake_patch_event_time(*a, **kw):
            return {"id": created.get("id") or "fake-event"}

        async def _fake_get_tokens(*a, **kw):
            return {"token": "fake", "refresh_token": "fake", "migrated": True}

        async def _fake_notify_owner(clinic, message):
            return True

        with ExitStack() as stack:
            stack.enter_context(patch.object(gcal, "create_event", _fake_create_event))
            stack.enter_context(patch.object(gcal, "update_event", _fake_update_event))
            stack.enter_context(patch.object(gcal, "patch_event_time", _fake_patch_event_time))
            stack.enter_context(patch.object(rt, "_get_tokens", _fake_get_tokens))
            stack.enter_context(
                patch.object(owner_notify, "notify_owner", _fake_notify_owner))
            result = await real_executors["book_appointment"](args, session)

        if created:
            start_dt = created["start_dt"]
            end_dt = created["end_dt"]
            dur = int((end_dt - start_dt).total_seconds() // 60)
            diary.book(Booking(
                start=start_dt.isoformat(),
                end=end_dt.isoformat(),
                name=(args.get("patient_name") or "").strip(),
                phone=(args.get("phone") or "").strip(),
                # The SERVICE AS WRITTEN, resolved by the executor - NOT the
                # model's argument. This is the field the wrong-service defect
                # lives in, so reading args here would make the harness blind
                # to the very reconciliation that fixes it. `collected` is what
                # the SMS templates and the call record read too.
                service=(session.get("collected") or {}).get("service") or "",
                duration_min=dur,
                raw_args=dict(args),
            ))
        return result

    async def cancel_appointment(args: Dict[str, Any],
                                 session: Dict[str, Any]) -> Dict[str, Any]:
        """SUPERSEDED by _cancel_appointment below, which actually removes the
        booking. Kept only for `diary.cancelled`, which this recorded and which
        a caller may still read; this one returned success unconditionally, so
        a call that cancelled nothing reported that it had."""
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

        "confirm_appointment_found": {"success": True},

        "get_patient_history": {"found": False},
        "send_followup_sms": {"success": True, "sent": False},
        "transfer_to_human": {"success": True},
        "add_to_waitlist": {"success": True},
        "request_callback": {"success": True},
        "log_call_outcome": {"success": True},
        "collect_and_store": {"success": True},
        "get_clinic_info": {"success": True},
    }
    # ── The write flows, backed by the diary ────────────────────────────
    # These were inert defaults -- lookup_patient returned {"found": False}
    # unconditionally, so the cancel, reschedule and changed-mind personas
    # could never find the appointment they had rung about. The engine then
    # behaved correctly for a caller with no booking, the suite reported the
    # calls clean, and three of sixteen personas were testing nothing at all.
    # Same silent-vacuum shape as a hasattr guard over a method that does not
    # exist.

    async def _lookup_patient(args, session):
        found = diary.find_bookings(
            phone=args.get("phone") or session.get("twilio_from") or "",
            name=args.get("name") or "",
        )
        if not found:
            return {"found": False}
        booking = found[0]
        return {
            "found": True,
            "appointments": [{
                "start": booking.start,
                "end": booking.end,
                "name": booking.name,
                "phone": booking.phone,
                "service": booking.service,
            }],
            "patient_name": booking.name,
        }

    async def _cancel_appointment(args, session):
        diary.cancelled.append((args.get("appointment_id") or "").strip())
        found = diary.find_bookings(
            phone=args.get("phone") or session.get("twilio_from") or "",
            name=args.get("patient_name") or "",
        )
        if not found:
            return {"success": False, "error": "no matching appointment"}
        diary.cancel_booking(found[0])
        return {"success": True, "cancelled": found[0].start}

    async def _reschedule_appointment(args, session):
        """A move is a cancel and a book, and BOTH halves must land.

        Writing the new one without removing the old is the double-booking this
        system has already produced on a real calendar.
        """
        found = diary.find_bookings(
            phone=args.get("phone") or session.get("twilio_from") or "",
            name=args.get("patient_name") or "",
        )
        if not found:
            return {"success": False, "error": "no matching appointment"}
        old = found[0]
        new_start = args.get("new_start") or args.get("start") or args.get("new_time")
        if not new_start:
            return {"success": False, "error": "no new time given"}
        diary.cancel_booking(old)
        moved = Booking(
            start=str(new_start), end=old.end, name=old.name, phone=old.phone,
            service=old.service, duration_min=old.duration_min,
            raw_args=dict(args),
        )
        diary.book(moved)
        return {"success": True, "moved_to": moved.start}

    table["lookup_patient"] = _lookup_patient
    table["cancel_appointment"] = _cancel_appointment
    table["reschedule_appointment"] = _reschedule_appointment

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
