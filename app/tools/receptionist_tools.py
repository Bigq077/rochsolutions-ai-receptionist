# app/tools/receptionist_tools.py
"""
Tool definitions (Anthropic format) and async executor functions for the
Phase 3 tool-calling LLM receptionist.

Each executor has the signature:
    async def _exec_<name>(args: dict, session: dict) -> dict

Tools mutate `session` in-place where needed.
All blocking I/O (Google APIs, Sheets) is wrapped in asyncio.to_thread().
Every executor catches its own exceptions and returns {"error": "..."} rather
than raising, so a single tool failure never crashes the conversation loop.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time as _time
from datetime import datetime, timedelta, date as _date_type
from typing import Any, Dict, Optional, Tuple

import pytz

logger = logging.getLogger(__name__)

LONDON_TZ = pytz.timezone("Europe/London")

# Google tokens are stored globally in Redis under this key (same as legacy)
_TOKENS_KEY = "google_tokens"

# ---------------------------------------------------------------------------
# Acuity appointment type IDs
# ---------------------------------------------------------------------------
# Each clinic location has its own Acuity appointment type.
# Format: "acuity_<raw_id>" (the adapter strips the prefix before calling the API).
# Override via env vars; hardcoded values are the known production IDs.
import os as _os
DEFAULT_ACUITY_APPOINTMENT_TYPE_ID: str = (
    f"acuity_{_os.getenv('DEFAULT_APPOINTMENT_TYPE_ID', '15823699')}"
)
# Per-location appointment type IDs — Redditch uses a different type from Alcester.
_LOCATION_APPOINTMENT_TYPE_IDS: dict = {
    "alcester": f"acuity_{_os.getenv('ACUITY_APPOINTMENT_TYPE_ID_ALCESTER', '15823699')}",
    "redditch": f"acuity_{_os.getenv('ACUITY_APPOINTMENT_TYPE_ID_REDDITCH', '33801703')}",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _ordinal(n: int) -> str:
    """Return English ordinal string: 1→'1st', 2→'2nd', 26→'26th', etc."""
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


_HOUR_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
}


def _spoken_slot_time(hhmm: str) -> str:
    """Convert a 24-hour 'HH:MM' slot time into its natural spoken label.

    Deterministic source of truth for slot wording ("nine in the morning",
    "midday", "one in the afternoon", "five in the evening") so the Haiku slot
    formatter copies labels verbatim instead of converting times itself — which
    let it drop/invent slots (e.g. rendering [09,10,11,12,13] as 09,10,12,13,14
    and booking a non-existent 2pm).  Every on-grid minute uses natural UK
    clock-face phrasing ("half past six", "five past five", "twenty to six") —
    the same forms the model produces on its own — so first read, repeat read
    and readback all name a slot identically (Call-3 P3).  Off-grid minutes
    fall back to unambiguous digits.
    """
    try:
        h, m = map(int, hhmm.split(":"))
    except Exception:
        return hhmm
    if h == 12 and m == 0:
        return "midday"
    if h == 0 and m == 0:
        return "midnight"
    part = (
        "in the morning" if h < 12
        else "in the afternoon" if h < 17
        else "in the evening"
    )
    hour_word = _HOUR_WORDS[h % 12 or 12]
    next_word = _HOUR_WORDS[(h + 1) % 12 or 12]
    if m == 0:
        return f"{hour_word} {part}"
    if m == 15:
        return f"quarter past {hour_word} {part}"
    if m == 30:
        return f"half past {hour_word} {part}"
    if m == 45:
        # "quarter to" the NEXT hour, keeping the reading's time-of-day label.
        return f"quarter to {next_word} {part}"
    # Every other on-grid minute: natural clock-face phrasing ("five past
    # five", "twenty to six"), matching :00/:15/:30/:45 above. This replaces
    # the digital "[hour] [minutes]" forms ("five oh five", "five forty") —
    # Call-3 P3, 2026-07-19: the model kept drifting to clock-face on repeat
    # reads and in its own confirmation ("twenty to six"), so one slot got
    # three different names across the call. Converging the label on the
    # phrasing the model produces naturally makes every read consistent by
    # construction. The anti-slot-drop property is unchanged — Haiku still
    # copies these labels verbatim; only the label text differs.
    _PAST_MIN = {5: "five", 10: "ten", 20: "twenty", 25: "twenty-five"}
    _TO_MIN   = {35: "twenty-five", 40: "twenty", 50: "ten", 55: "five"}
    if m in _PAST_MIN:
        return f"{_PAST_MIN[m]} past {hour_word} {part}"
    if m in _TO_MIN:
        # "to" the NEXT hour, keeping the reading's time-of-day label
        # (same convention as the :45 "quarter to" case above).
        return f"{_TO_MIN[m]} to {next_word} {part}"
    # Off-grid minute (never on a 5-minute grid) — unambiguous digit fallback.
    return f"{hour_word} {m:02d} {part}"


# An explicit clock time in the caller's preference ("12 o'clock", "half past
# 12", "around 3", "17:00", "noon") must take precedence over the coarse
# morning/afternoon/evening band. When BOTH appear in one hint (e.g. "Saturday
# morning around 12"), banding to hour<12 silently drops the very slot the caller
# asked for (12:30) and makes Susie claim it's unavailable (Call 4, 2026-07-08).
# When an explicit time is present we skip the band filter — which only ever
# RETAINS more slots, never fewer — so the requested slot survives and
# _resolve_slot_iso / the model pick the nearest. Bare-band hints ("mornings",
# "Thursday afternoon") contain no clock token, so their behaviour is unchanged.
_EXPLICIT_CLOCK_RE = re.compile(
    r"\b\d{1,2}\s*[:.]\s*\d{2}\b"                        # 12:30, 9.30, 17:00
    r"|\b\d{1,2}\s*(?:o'?clock|am|pm|a\.m\.?|p\.m\.?)\b"  # 12 o'clock, 3pm
    r"|\b(?:half|quarter|twenty(?:[-\s]five)?|ten|five|\d{1,2})\s+(?:past|to)\s+"
    r"(?:noon|midday|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\b"
    r"|\b(?:around|about|at|by|near|just after|just before|before|after)\s+\d{1,2}\b"
    r"|\bnoon\b|\bmidday\b",
    re.IGNORECASE,
)


def _has_explicit_clock(pref: str) -> bool:
    """True when the caller's preference names a specific clock time (not just a
    coarse morning/afternoon/evening band)."""
    return bool(_EXPLICIT_CLOCK_RE.search(pref or ""))


def _filter_tuples_by_preference(slot_tuples: list, preference: str = "") -> list:
    """
    Filter (start_dt, end_dt) tuples to those matching the caller's stated
    day-of-week and/or time-of-day preference (e.g. 'Thursday afternoon').

    Past slots are dropped first.  The day-of-week filter and the time-of-day
    filter are each applied only when they leave at least one slot — a
    preference that matches nothing is ignored rather than emptying the list,
    so the caller still hears the nearest available options.  Returns all
    future slots unfiltered when the preference produces no matches at all.

    Shared by _select_presented_tuples (which builds slot_labels) and
    _build_days_data (which builds available_days) so BOTH presentation
    surfaces honour the same preference.  Previously only slot_labels was
    filtered while available_days returned every day — so a "Thursday
    afternoon" request was presented with non-Thursday days (bug C5-5).
    """
    now = datetime.now(LONDON_TZ)
    future_only = [(s, e) for s, e in slot_tuples if s > now]

    pref = preference.lower()
    filtered = future_only

    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    pref_days = [wd for name, wd in day_map.items() if name in pref]
    if pref_days:
        day_filtered = [(s, e) for s, e in filtered if s.weekday() in pref_days]
        if day_filtered:
            filtered = day_filtered

    # Band boundaries, non-overlapping.  Morning and evening match the spoken
    # labels (_spoken_slot_time: <12 morning, >=17 evening).  Midday (12:00) is
    # voiced as its own word — "midday", not "afternoon" — so it is DELIBERATELY
    # excluded from the afternoon band (#5, 2026-06-18): a caller asking for
    # "afternoon" should not be offered noon, and the slot formatter already
    # drops it, so the band must agree (previously the band INCLUDED 12 while the
    # formatter dropped it — Call 3 inconsistency).  Midday therefore matches no
    # band and surfaces only on unfiltered ("any") requests; morning is
    # unaffected (already hour<12), evening unaffected (hour>=17).
    #   morning   = hour < 12
    #   afternoon = 13 <= hour < 17
    #   evening   = hour >= 17
    # Skip the coarse band when the caller named an explicit clock time — the
    # exact time wins so a boundary slot (e.g. 12:30 for "around 12") is never
    # dropped. Band-only hints are unaffected. See _has_explicit_clock.
    if not _has_explicit_clock(pref):
        if "morning" in pref:
            time_filtered = [(s, e) for s, e in filtered if s.hour < 12]
            if time_filtered:
                filtered = time_filtered
        elif "afternoon" in pref:
            time_filtered = [(s, e) for s, e in filtered if 13 <= s.hour < 17]
            if time_filtered:
                filtered = time_filtered
        elif "evening" in pref:
            time_filtered = [(s, e) for s, e in filtered if s.hour >= 17]
            if time_filtered:
                filtered = time_filtered

    # Fall back to all future slots if preference produced no matches
    if not filtered:
        filtered = future_only
    return filtered


def _build_days_data(
    slot_tuples: list, max_days: int = 30, preference: str = "",
) -> list:
    """
    Group (start_dt, end_dt) tuples into per-day summaries for the day-first
    availability presentation flow.

    max_days matches the 30-day Acuity search window so that every date
    Acuity returns is stored in session["available_days"].  The old cap of 8
    was the root cause of "I don't have the 23rd of April available" failures:
    when 8+ April days existed, April 23 was silently excluded from
    available_days even though Acuity returned it.  Presentation still shows
    only 3 days at a time via _build_day_list_phrase(:3) and paging.

    When a preference is supplied (e.g. 'Thursday afternoon') the day/time
    filter is applied first so available_days contains ONLY days matching the
    request — keeping the day-first presentation consistent with slot_labels
    (bug C5-5).  Falls back to all days when nothing matches the preference.
    """
    slot_tuples = _filter_tuples_by_preference(slot_tuples, preference)
    from collections import defaultdict as _dd
    days_map: "_dd[Any, list]" = _dd(list)
    for start, end in slot_tuples:
        days_map[start.date()].append((start, end))

    days_data = []
    for day in sorted(days_map.keys())[:max_days]:   # default 30 = full search window
        day_slots = days_map[day]
        dt = day_slots[0][0]
        day_name  = dt.strftime("%A")                # "Thursday"
        day_label = f"{day_name} {_ordinal(dt.day)} {dt.strftime('%B')}"  # "Thursday 26th March"
        _times = [s[0].strftime("%H:%M") for s in day_slots]
        days_data.append({
            "date":              day.isoformat(),
            "day_label":         day_label,
            "slot_times":        _times,
            # Ready-made spoken labels, aligned 1:1 with slot_times. The slot
            # formatter must use these verbatim — never re-convert the 24h times
            # itself (it dropped/invented slots when it did, booking phantoms).
            "slot_times_spoken": [_spoken_slot_time(t) for t in _times],
            "slots":             [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in day_slots],
        })
    return days_data


def _select_presented_tuples(slot_tuples: list, preference: str = "") -> list:
    """
    Pick up to 3 (start_dt, end_dt) tuples to present to the caller.
    Prefer one slot per day for variety.  Fall back to first 3 chronological
    slots when fewer than 3 days are available (e.g. all slots on same day).
    Ensures slot_labels[0/1/2] match exactly the 1st/2nd/3rd slot presented.

    Past slots are filtered out first.  If a preference string is given
    (e.g. 'Wednesday morning'), slots are further filtered to match the
    stated day-of-week and/or time-of-day so that slot_labels contains ONLY
    what the LLM will verbally present — preventing ordinal mismatch when
    the LLM filters by preference but slot_labels has unfiltered Acuity results.
    Falls back to all future slots if no preference-matching slots found.
    """
    # Apply preference filtering so stored slot_labels match LLM verbal output.
    # Shared helper guarantees available_days (built by _build_days_data) uses
    # the identical day/time filter — see _filter_tuples_by_preference.
    filtered = _filter_tuples_by_preference(slot_tuples, preference)

    day_seen: set = set()
    day_firsts: list = []
    for start, end in sorted(filtered, key=lambda t: t[0]):
        day = start.date()
        if day not in day_seen:
            day_seen.add(day)
            day_firsts.append((start, end))
    if len(day_firsts) >= 3:
        return day_firsts[:3]
    # Fewer than 3 days — take first 3 slots chronologically
    return sorted(filtered, key=lambda t: t[0])[:3]


# ---------------------------------------------------------------------------
# Week-range extraction from date_hint
# ---------------------------------------------------------------------------

_MONTH_MAP: dict[str, int] = {
    "january": 1,  "jan": 1,
    "february": 2, "feb": 2,
    "march": 3,    "mar": 3,
    "april": 4,    "apr": 4,
    "may": 5,
    "june": 6,     "jun": 6,
    "july": 7,     "jul": 7,
    "august": 8,   "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# Matches: "18", "18th", "18th May", "18th May 2026", "18 May 2026"
_DATE_RE = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?"
    r"(?:\s+([a-z]+))?"       # optional month name
    r"(?:\s+(\d{4}))?",       # optional 4-digit year
    re.IGNORECASE,
)

# Matches day-of-week names
_DOW_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

_DOW_INDEX: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


# Two-date disjunction / range, each side optionally carrying its own month:
#   "8th or 9th", "18-19 July", "16th of July or 15th of July", "1st or 2nd".
# A trailing month/year applies to whichever side lacks one.
# GUARD (applied in _extract_multidate_range): only treated as DATES when at
# least one ordinal suffix or a valid month name is present — so time ranges
# like "2-3pm" or "between 2 and 3" are NOT misread as the 2nd/3rd.
_MULTIDATE_RE = re.compile(
    r"(?:the\s+)?(\d{1,2})(st|nd|rd|th)?(?:\s+of)?(?:\s+([a-z]+))?"
    r"\s*(?:\bor\b|\band\b|\bto\b|/|&|,|-)\s*"
    r"(?:the\s+)?(\d{1,2})(st|nd|rd|th)?(?:\s+of)?(?:\s+([a-z]+))?"
    r"(?:\s+(\d{4}))?",
    re.IGNORECASE,
)


def _extract_multidate_range(
    hint: str,
    today: "_date_type",
) -> "tuple[_date_type, _date_type] | None":
    """Parse a two-date disjunction/range ("8th or 9th", "18-19 July") into a
    (start, end) span. Returns None when the text is not a confident two-date
    reference (e.g. a time range like "2-3pm"). The first name is taken from
    each side's own month if present, otherwise it inherits the other side's
    month; bare days with no month resolve to the nearest future day-of-month.
    """
    if not hint:
        return None
    m = _MULTIDATE_RE.search(hint)
    if not m:
        return None
    day1 = int(m.group(1))
    ord1 = m.group(2)
    mon1 = (m.group(3) or "").lower()
    day2 = int(m.group(4))
    ord2 = m.group(5)
    mon2 = (m.group(6) or "").lower()
    year_s = m.group(7) or ""
    m1n = _MONTH_MAP.get(mon1, 0)
    m2n = _MONTH_MAP.get(mon2, 0)
    # Date-vs-time guard: require a real month or an ordinal suffix.
    if not (m1n or m2n or ord1 or ord2):
        return None
    year_n = int(year_s) if year_s else today.year

    def _resolve(day_n: int, month_n: int) -> "_date_type | None":
        if month_n:
            try:
                d = _date_type(year_n, month_n, day_n)
                if d < today and not year_s:
                    d = _date_type(year_n + 1, month_n, day_n)
                return d
            except ValueError:
                return None
        # No month → nearest future date with that day-of-month.
        for off in range(4):
            mm = (today.month + off - 1) % 12 + 1
            yy = today.year + (today.month + off - 1) // 12
            try:
                c = _date_type(yy, mm, day_n)
                if c >= today:
                    return c
            except ValueError:
                continue
        return None

    eff_m1 = m1n or m2n          # inherit the other side's month if missing
    eff_m2 = m2n or m1n
    dates = [d for d in (_resolve(day1, eff_m1), _resolve(day2, eff_m2)) if d]
    if not dates:
        return None
    return min(dates), max(dates)


def _extract_week_range(
    date_hint: str,
) -> "tuple[_date_type, _date_type] | None":
    """
    Parse date_hint and return (week_start, week_end) as Monday–Sunday.

    Recognised patterns (case-insensitive, extra words like "mornings" ignored):
      "next week"                  → next Mon–Sun
      "this week"                  → current Mon–Sun
      "week of 18 May 2026"        → Mon–Sun of the week containing 18 May 2026
      "week of the 18th"           → Mon–Sun of the nearest future 18th
      "week of 18th May"           → Mon–Sun of 18 May (current/next year)
      "from 18 May 2026"           → Mon–Sun of the week containing 18 May 2026
      "Thursday 21st May mornings" → just 2026-05-21 (single-day range)
      "21st May 2026"              → just that date

    Returns None if no week/date can be extracted — caller falls back to the
    default 30-day fetch without error.
    """
    if not date_hint:
        return None

    hint = date_hint.lower().strip()
    today = _date_type.today()

    # ── Pattern 0: ISO date "YYYY-MM-DD" ─────────────────────────────────────
    # e.g. "2026-06-23" — used when v3_last_offered_day_iso is set in CALL STATE.
    # Most unambiguous form: match before any other pattern.
    _iso_m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", date_hint.strip())
    if _iso_m:
        try:
            target = _date_type(
                int(_iso_m.group(1)), int(_iso_m.group(2)), int(_iso_m.group(3))
            )
            logger.info(
                "[ms_tools] week filter: ISO date match → single-day %s", target
            )
            return target, target
        except ValueError:
            pass

    # ── Pattern 0.5: relative "today" / "tomorrow" ──────────────────────────
    # Bare relative-date words carry no week/ordinal/month anchor, so without
    # explicit handling they fall through to None and the 30-day sweep is
    # returned unfiltered — the caller asks for "tomorrow" and is shown the
    # soonest slot on some *other* day as if it answered the question, and on a
    # closed day (e.g. tomorrow=Saturday) is never told the day is unavailable
    # (C8-4 relative-date failure: caller had to repeat 3× before the LLM
    # happened to spell out the ISO date).  Resolve against today's date and
    # return a single-day range so the filter narrows to exactly that day — a
    # closed day then correctly yields zero slots and triggers the
    # next_available path ("tomorrow's a Saturday, next is Thursday 18th").
    # Checked before the ordinal/month patterns below: a phrase like "tomorrow
    # afternoon around 3pm" has no month token, so those patterns return None
    # and the day intent would otherwise be lost.
    if re.search(r"\btomorrow\b", hint):
        target = today + timedelta(days=1)
        logger.info(
            "[ms_tools] week filter: relative 'tomorrow' → single-day %s", target
        )
        return target, target
    if re.search(r"\btoday\b", hint):
        logger.info(
            "[ms_tools] week filter: relative 'today' → single-day %s", today
        )
        return today, today

    def _week_of(d: _date_type) -> "tuple[_date_type, _date_type]":
        monday = d - timedelta(days=d.weekday())
        return monday, monday + timedelta(days=6)

    def _next_monday() -> _date_type:
        # Days until next Monday: Mon=7, Tue=6, Wed=5, Thu=4, Fri=3, Sat=2, Sun=1
        days_ahead = 7 - today.weekday()
        return today + timedelta(days=days_ahead)

    def _nearest_future_day_of_month(day_n: int) -> "_date_type | None":
        """Return the nearest future (or today) date with day-of-month == day_n."""
        for month_offset in range(4):
            m = (today.month + month_offset - 1) % 12 + 1
            y = today.year + (today.month + month_offset - 1) // 12
            try:
                candidate = _date_type(y, m, day_n)
                if candidate >= today:
                    return candidate
            except ValueError:
                continue
        return None

    # ── Pattern 1: "next week" ────────────────────────────────────────────────
    if "next week" in hint:
        nm = _next_monday()
        return nm, nm + timedelta(days=6)

    # ── Pattern 2: "this week" ────────────────────────────────────────────────
    if "this week" in hint:
        return _week_of(today)

    # ── Pattern 3: "week of …" ────────────────────────────────────────────────
    # e.g. "week of 18 May 2026", "week of the 18th", "week of 18th May"
    _wo_m = re.search(
        r"week of(?: the)?\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+([a-z]+))?(?:\s+(\d{4}))?",
        hint,
    )
    if _wo_m:
        day_n   = int(_wo_m.group(1))
        month_s = (_wo_m.group(2) or "").lower()
        year_s  = _wo_m.group(3) or ""
        month_n = _MONTH_MAP.get(month_s, 0)
        year_n  = int(year_s) if year_s else today.year

        if month_n:
            try:
                target = _date_type(year_n, month_n, day_n)
                if target < today and not year_s:
                    target = _date_type(year_n + 1, month_n, day_n)
            except ValueError:
                return None
        else:
            target = _nearest_future_day_of_month(day_n)
            if target is None:
                return None

        return _week_of(target)

    # ── Pattern 3.5: "from [day] [month] [year]" ─────────────────────────────
    # e.g. "mornings from 18 May 2026", "from 18 May 2026"
    # Treat the named date as an anchor; return the Mon–Sun week containing it.
    _from_m = re.search(
        r"\bfrom\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+([a-z]+)(?:\s+(\d{4}))?",
        hint,
    )
    if _from_m:
        day_n   = int(_from_m.group(1))
        month_s = _from_m.group(2).lower()
        year_s  = _from_m.group(3) or ""
        month_n = _MONTH_MAP.get(month_s, 0)
        if month_n:
            year_n = int(year_s) if year_s else today.year
            try:
                anchor = _date_type(year_n, month_n, day_n)
                if anchor < today and not year_s:
                    anchor = _date_type(year_n + 1, month_n, day_n)
            except ValueError:
                pass  # fall through to Pattern 4
            else:
                wk_start, wk_end = _week_of(anchor)
                logger.info(
                    "[ms_tools] week filter applied (from-anchor): %s to %s",
                    wk_start, wk_end,
                )
                return wk_start, wk_end

    # ── Pattern 3.7: two-date disjunction / range ───────────────────────────
    # e.g. "8th or 9th", "18-19 July", "16th of July or 15th of July".
    # Must run BEFORE Pattern 4 — the single-date matcher would grab only the
    # first day and (lacking a directly-following month) return None, which is
    # the original "X or Y wrongly refused" bug. Returns the span covering both.
    _md_range = _extract_multidate_range(hint, today)
    if _md_range is not None:
        logger.info(
            "[ms_tools] week filter: multi-date range %s to %s (from %r)",
            _md_range[0], _md_range[1], date_hint,
        )
        return _md_range

    # ── Pattern 4: specific date (optional day-of-week prefix) ───────────────
    # e.g. "Thursday 21st May mornings", "21st May 2026", "the 14th"
    _sd_m = re.search(
        r"(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?(?:\s+([a-z]+))?(?:\s+(\d{4}))?",
        hint,
    )
    if _sd_m:
        day_n   = int(_sd_m.group(1))
        word    = (_sd_m.group(2) or "").lower()
        year_s  = _sd_m.group(3) or ""
        month_n = _MONTH_MAP.get(word, 0)

        if month_n:
            year_n = int(year_s) if year_s else today.year
            try:
                target = _date_type(year_n, month_n, day_n)
                if target < today and not year_s:
                    target = _date_type(year_n + 1, month_n, day_n)
            except ValueError:
                return None
            return target, target  # single-day range

        # Plain ordinal like "the 14th" with no month — nearest future day.
        # Guard against time strings (e.g. "9am", "10:30") by requiring an
        # explicit ordinal suffix (st/nd/rd/th); a bare digit with no suffix
        # is too ambiguous to treat as a calendar date.
        _has_ordinal = bool(re.search(r"\d(?:st|nd|rd|th)", _sd_m.group(0), re.IGNORECASE))
        if 1 <= day_n <= 31 and not word and _has_ordinal:
            target = _nearest_future_day_of_month(day_n)
            if target:
                return target, target

    return None


async def _get_tokens() -> Optional[Dict[str, Any]]:
    """Fetch Google Calendar OAuth tokens from Redis."""
    from app.storage.redis_store import redis_get_json
    try:
        return await redis_get_json(_TOKENS_KEY)
    except Exception:
        return None


async def _save_gcal_tokens(tokens: Dict[str, Any]) -> None:
    """
    Persist refreshed GCal tokens back to Redis.
    _refresh_if_needed() updates the token dict in-place but never writes to
    Redis itself; calling this after each calendar API call ensures the new
    access token and expiry are saved so the next request skips a needless
    refresh round-trip.  Failures are non-fatal — worst case is an extra
    refresh on the next call.
    """
    from app.storage.redis_store import redis_set_json
    try:
        await redis_set_json(_TOKENS_KEY, tokens, ttl_seconds=60 * 60 * 24 * 365)
    except Exception as e:
        logger.warning("_save_gcal_tokens: failed to persist updated tokens: %r", e)


def _resolve_calendar_id(clinic: Dict[str, Any], location: str) -> str:
    """
    Return the Google Calendar ID to use for a given clinic + location.
    Falls back to DEFAULT_CALENDAR_ID env var, then 'primary'.
    """
    import os
    # Theorem: per-location calendar IDs come from env vars via THEOREM_LOCATIONS
    if clinic.get("clinic_id") == "theorem" and location:
        from app.clinic_config import THEOREM_LOCATIONS
        loc_cfg = THEOREM_LOCATIONS.get(location.lower(), {})
        cal_id = loc_cfg.get("acuity_calendar_id")
        if cal_id:
            return cal_id
    # Fallback: clinic-level calendar_id, then env, then 'primary'
    return (
        clinic.get("calendar_id")
        or os.getenv("DEFAULT_CALENDAR_ID", "primary")
        or "primary"
    )


def _find_service_def(clinic: Dict[str, Any], service: str) -> Optional[Dict[str, Any]]:
    """Resolve a service arg (service_id or caller-facing name) to its clinic.json
    definition. Returns None if not found."""
    svc = (service or "").strip().lower()
    if not svc:
        return None
    for s in (clinic.get("services") or []):
        if (s.get("service_id") or "").lower() == svc or (s.get("name") or "").lower() == svc:
            return s
    return None


# Number words a caller actually uses for a session length. Deliberately short —
# this feeds a WRITE, so an unrecognised phrase (no capture, ask again) costs
# less than a creative one (wrong length booked at the wrong price).
_DURATION_WORDS = {
    "thirty": 30, "half an hour": 30, "half hour": 30,
    "forty five": 45, "forty-five": 45,
    "sixty": 60, "an hour": 60, "one hour": 60, "a hour": 60,
    "ninety": 90, "hour and a half": 90, "an hour and a half": 90,
    "hour and a half long": 90,
    "one twenty": 120, "two hours": 120,
}
# "the 30th" is a DATE, not a thirty-minute session.
_ORDINAL_NUM_RE = re.compile(r"\b\d+(?:st|nd|rd|th)\b")
# pricing keys look like "60min_in_clinic_gbp" / "90min_in_clinic_gbp"
_PRICE_KEY_RE = re.compile(r"^(\d+)\s*min", re.IGNORECASE)


def _options_services(clinic: Dict[str, Any]) -> list:
    """Every service in the clinic that offers a CHOICE of session lengths.

    `services` is a list of DICTS for the template clinics and a list of plain
    STRINGS for `theorem` and `demo`. This runs on every turn of every call, so
    the isinstance guard is load-bearing rather than defensive tidiness — without
    it a Theorem call raises on the first utterance.
    """
    return [
        s for s in (clinic.get("services") or [])
        if isinstance(s, dict) and s.get("typical_duration_minutes_options")
    ]


def duration_choice_from_utterance(
    clinic: Dict[str, Any], utterance: str
) -> Optional[int]:
    """The session length a caller just chose, or None if they did not name one.

    **Why this exists.** `CA86c320ef` (4 Aug 2026, Vital Edge, live): asked
    "60-minute at £125, or 90-minute at £180?", the caller answered **"£180"**.
    Eight turns and two minutes later `book_appointment` fired with
    `duration_minutes: 60` — the silent `opts[0]` default in
    `_service_duration_minutes` below. The caller expected 90 minutes and £180;
    the owner was notified of 60. Nothing had gone wrong deterministically: the
    choice existed ONLY in the model's context, and it did not survive the trip.

    So the choice is captured in the engine, at the moment it is spoken, the same
    way `time_of_day_preference` already is — not inferred from a tool argument
    the model may or may not carry.

    **Service-agnostic on purpose.** At transcript time the engine does not know
    which service is being booked — the service only appears later, in tool args.
    So this scans every options-service in the clinic and returns a length only
    when they AGREE. For Vital Edge both options-services are [60, 90] with
    identical pricing, so "£180" is unambiguous. Where two services would imply
    different lengths, it returns None and the existing behaviour stands.

    **Answers by PRICE count**, because that is what the caller actually said and
    what the question invites ("…at £125, or …at £180?"). The map is derived from
    each service's own `pricing` block, so it cannot drift from the prices Susie
    quotes.

    Bias, stated because it runs the wrong way for a write: a false positive
    books the wrong length at the wrong price — the very defect this closes — so
    anything ambiguous returns None and the caller is simply asked again.
    """
    if not utterance:
        return None
    svcs = _options_services(clinic)
    if not svcs:
        return None

    low = " " + re.sub(r"[^a-z0-9£ ]+", " ", (utterance or "").lower()).strip() + " "
    # Strip ordinals so "the 30th of August" cannot read as a 30-minute session.
    low = _ORDINAL_NUM_RE.sub(" ", low)

    found: set = set()
    for svc in svcs:
        opts = [int(o) for o in (svc.get("typical_duration_minutes_options") or [])]
        if not opts:
            continue
        # 1. an explicit length: "90", "90 minutes", "ninety"
        for n in opts:
            if re.search(rf"\b{n}\s*(?:min|mins|minute|minutes)?\b", low):
                found.add(n)
        # Longest phrase first, CONSUMING each match. "an hour and a half"
        # contains "an hour", and "half an hour" contains it too — scored
        # independently both read as ambiguous 60-or-90 and captured nothing,
        # which is the safe failure but the wrong answer. The longer phrase is
        # always the more specific one, so it wins and is then removed.
        _rest = low
        for word, n in sorted(
            _DURATION_WORDS.items(), key=lambda kv: -len(kv[0])
        ):
            if not re.search(rf"\b{re.escape(word)}\b", _rest):
                continue
            _rest = re.sub(rf"\b{re.escape(word)}\b", " ", _rest)
            if n in opts:
                found.add(n)
        # 2. a PRICE that identifies one — "£180", "180", "a hundred and eighty"
        for key, price in (svc.get("pricing") or {}).items():
            m = _PRICE_KEY_RE.match(str(key))
            if not m:
                continue
            n = int(m.group(1))
            if n not in opts or not isinstance(price, (int, float)):
                continue
            if re.search(rf"£?\s*{int(price)}\b", low):
                found.add(n)

    if len(found) == 1:
        return found.pop()
    if len(found) > 1:
        logger.info(
            "[ms_tools] duration choice ambiguous in %r — candidates %s, not "
            "captured; the caller will be asked again", utterance, sorted(found),
        )
    return None


def _service_duration_minutes(
    clinic: Dict[str, Any], service: str, fallback: int, preferred=None
) -> int:
    """Per-service appointment length from clinic.json.

    Reads `typical_duration_minutes`. For services that offer a CHOICE of lengths
    (`typical_duration_minutes_options`, e.g. sports massage 30/60), honours the
    caller's captured choice (`preferred`) when it is one of the options, else
    defaults to the first (shortest). Falls back to `fallback` (the clinic
    slot_minutes) when the service is unknown or declares no length.

    THE single source of truth for BOTH the slot grid (check_availability) and
    the calendar-event length (book_appointment / provisional), so the slot
    offered and the event booked are always the same length — and an options
    service (which previously fell back to a flat slot_minutes on some paths and
    the first option on others) now resolves consistently everywhere."""
    svc = _find_service_def(clinic, service)
    if svc:
        d = svc.get("typical_duration_minutes")
        if d:
            return int(d)
        opts = svc.get("typical_duration_minutes_options")
        if opts:
            opts_i = [int(o) for o in opts]
            if preferred and int(preferred) in opts_i:
                return int(preferred)
            # No captured choice. Falling back to the SHORTEST option is what
            # booked 60 minutes for a caller who had said £180 on CA86c320ef,
            # and it did so without a trace. Keep the behaviour — re-asking from
            # inside a resolver is not this function's job — but make it loud,
            # because "quietly the cheapest" is how a price gap stays invisible.
            logger.warning(
                "[ms_tools] %r offers %s minutes and NO choice was captured — "
                "defaulting to %s. If the caller named a length or a price, it "
                "was not captured (see duration_choice_from_utterance).",
                service, opts_i, opts_i[0],
            )
            return opts_i[0]
    return int(fallback)


def _service_supports_location(svc_def: Dict[str, Any], location: str) -> bool:
    """True if a service is bookable under the given booking location/modality,
    tolerant of the varied `available_as` token families in clinic.json
    (e.g. 'home_visit', 'home_visit_bolton_and_greater_manchester',
    'outdoor_bolton'). Used to reject impossible service×modality bookings (P2)."""
    loc = (location or "").strip().lower()
    if not loc:
        return True  # no modality to validate against
    tokens = [str(t).strip().lower() for t in (svc_def.get("available_as") or [])]
    if not tokens:
        return True  # service doesn't declare modalities — don't block
    for tok in tokens:
        if tok == loc or tok.startswith(loc + "_") or loc.startswith(tok):
            return True
    return False


def _location_to_modality(clinic: Dict[str, Any], location: str) -> str:
    """Map a booking location to an `available_as` modality token.

    The model's location enum is the physical site id ('bolton') for in-clinic,
    plus 'remote'/'home_visit'; clinic.json declares availability as
    'in_clinic'/'remote'/'home_visit'. A physical site id (or the primary
    location) maps to 'in_clinic'; 'remote'/'home_visit' pass through. Shared by
    check_availability and book_appointment so both guard the same way."""
    loc = (location or "").lower().strip()
    if not loc:
        return loc
    _loc_ids = {(l.get("location_id") or "").lower() for l in (clinic.get("locations") or [])}
    if loc in _loc_ids or loc == (clinic.get("primary_location") or "").lower():
        return "in_clinic"
    return loc


def _resolve_slot_iso(slot_iso: str, session: dict) -> "datetime":
    """
    Parse slot_iso as an ISO 8601 datetime.

    If direct parsing fails (e.g. Claude passed a human-readable label like
    'Mon 02 Mar at 09:00', a slot number like '1', or a slightly wrong format),
    fall back to looking up the matching start time from the
    session["last_offered_slots"] list that was stored by check_availability.

    Always returns a timezone-aware datetime in Europe/London.
    Raises ValueError if nothing can be resolved.
    """

    def _to_london(dt: "datetime") -> "datetime":
        """
        FIX #8: Convert any datetime to Europe/London, handling both naive
        and aware inputs correctly.
        - Naive  → localize (treat as London wall-clock time)
        - Aware  → astimezone (convert from whatever tz it carries)
        Using localize() on an already-aware datetime would silently double
        the UTC offset, so we must branch on tzinfo presence.
        """
        if dt.tzinfo is None:
            return LONDON_TZ.localize(dt)
        return dt.astimezone(LONDON_TZ)

    # 1. Direct ISO parse — but only accept if it matches one of the offered slots.
    # Claude sometimes hallucinates a slot ISO that was never presented; the guard
    # below forces it back to the offered list so the wrong slot can never be booked.
    s = str(slot_iso or "").strip()
    offered_check = session.get("last_offered_slots") or []
    if s and offered_check:
        try:
            dt_candidate = _to_london(datetime.fromisoformat(s))
            # Accept only if it matches an offered slot (within 60 s tolerance)
            for offered_slot in offered_check:
                try:
                    offered_dt = _to_london(datetime.fromisoformat(offered_slot["start"]))
                    if abs((dt_candidate - offered_dt).total_seconds()) < 60:
                        logger.info("_resolve_slot_iso: direct ISO match verified against offered slot %s", offered_slot["start"])
                        return dt_candidate
                except Exception:
                    pass
            # Did not match last_offered_slots (first-slot-per-day only).
            # 1b. Check ALL slots in available_days — covers times selected at
            # PRESENT_TIMES which weren't in last_offered_slots (e.g. 12:00 when
            # last_offered_slots only stored the first slot 09:00 for that day).
            _avail_days = session.get("available_days") or []
            for _day in _avail_days:
                for _slot in (_day.get("slots") or []):
                    try:
                        _adx_dt = _to_london(datetime.fromisoformat(_slot["start"]))
                        if abs((dt_candidate - _adx_dt).total_seconds()) < 60:
                            logger.info("_resolve_slot_iso: available_days match → %s", _slot["start"])
                            return dt_candidate
                    except Exception:
                        pass
            logger.warning("_resolve_slot_iso: ISO %r not in offered slots %s — falling back to index/label matching", s, [o['start'] for o in offered_check])
        except (ValueError, TypeError):
            pass
    elif s and not offered_check:
        # No offered slots in session (e.g. direct calendar booking) — accept as-is
        try:
            dt = datetime.fromisoformat(s)
            return _to_london(dt)
        except (ValueError, TypeError):
            pass

    offered = session.get("last_offered_slots") or []
    labels  = session.get("slot_labels") or []
    s_lower = s.lower()

    # 2. Numeric / ordinal index ("1", "first", "the first one", etc.)
    idx_map = {
        "1": 0, "first": 0,  "slot 1": 0, "option 1": 0, "slot1": 0,
        "the first": 0, "the first one": 0, "that first one": 0, "first one": 0,
        "2": 1, "second": 1, "slot 2": 1, "option 2": 1, "slot2": 1,
        "the second": 1, "the second one": 1, "that second one": 1, "second one": 1,
        "3": 2, "third": 2,  "slot 3": 2, "option 3": 2, "slot3": 2,
        "the third": 2, "the third one": 2, "that third one": 2, "third one": 2,
    }
    if s_lower in idx_map:
        idx = idx_map[s_lower]
        if idx < len(offered):
            try:
                dt = _to_london(datetime.fromisoformat(offered[idx]["start"]))
                logger.info("_resolve_slot_iso: index match %r → slot[%d] %s", slot_iso, idx, offered[idx]["start"])
                return dt
            except Exception:
                pass

    # 2a. Word-level ordinal fallback — catches "I'll take the first one please" etc.
    import re as _re
    _ordinal_words = {"first": 0, "second": 1, "third": 2}
    _tokens = set(_re.findall(r"\b\w+\b", s_lower))
    for word, idx in _ordinal_words.items():
        if word in _tokens and idx < len(offered):
            try:
                dt = _to_london(datetime.fromisoformat(offered[idx]["start"]))
                logger.info("_resolve_slot_iso: ordinal-word match %r → slot[%d] %s", slot_iso, idx, offered[idx]["start"])
                return dt
            except Exception:
                pass

    # 2b. "Last" / "final" keywords — dynamic index to the final offered slot
    _last_keywords = {
        "last", "last one", "last slot", "the last", "the last one",
        "that last one", "that last slot", "final", "final one", "final slot",
        "the final", "the final one", "that final one",
    }
    if s_lower in _last_keywords and offered:
        idx = len(offered) - 1
        try:
            dt = _to_london(datetime.fromisoformat(offered[idx]["start"]))
            logger.info("_resolve_slot_iso: 'last' match %r → slot[%d] %s", slot_iso, idx, offered[idx]["start"])
            return dt
        except Exception:
            pass

    # 2c. Informal British time expressions — "the morning one", "half nine",
    # "the twelve o'clock", "the early one", "the late one", etc. (Bug #6)
    _morning_kw = {"morning", "morning one", "the morning one", "early", "early one", "the early one", "earlier"}
    _afternoon_kw = {"afternoon", "afternoon one", "the afternoon one", "late", "late one", "the late one", "later", "the later one"}
    if offered:
        if s_lower in _morning_kw:
            # Pick the earliest offered slot
            try:
                dt = _to_london(datetime.fromisoformat(offered[0]["start"]))
                logger.info("_resolve_slot_iso: morning/early match %r → slot[0] %s", slot_iso, offered[0]["start"])
                return dt
            except Exception:
                pass
        if s_lower in _afternoon_kw:
            # Pick the latest offered slot
            try:
                idx = len(offered) - 1
                dt = _to_london(datetime.fromisoformat(offered[idx]["start"]))
                logger.info("_resolve_slot_iso: afternoon/late match %r → slot[%d] %s", slot_iso, idx, offered[idx]["start"])
                return dt
            except Exception:
                pass
        # "half nine" → 9:30, "nine o'clock" → 9:00, "twelve" → 12:00
        _time_map = {
            "half eight": (8, 30), "half nine": (9, 30), "half ten": (10, 30),
            "half eleven": (11, 30), "half twelve": (12, 30), "half one": (13, 30),
            "half two": (14, 30), "half three": (15, 30), "half four": (16, 30),
        }
        for phrase, (h, m) in _time_map.items():
            if phrase in s_lower:
                for i, slot in enumerate(offered):
                    try:
                        sdt = _to_london(datetime.fromisoformat(slot["start"]))
                        if sdt.hour == h and sdt.minute == m:
                            logger.info("_resolve_slot_iso: British time match %r → slot[%d]", phrase, i)
                            return sdt
                    except Exception:
                        pass

    # 3. Fuzzy match against human-readable labels
    for i, label in enumerate(labels):
        if i < len(offered):
            words = [w for w in s_lower.split() if len(w) > 2]
            if words and any(w in label.lower() for w in words):
                try:
                    dt = _to_london(datetime.fromisoformat(offered[i]["start"]))
                    logger.info("_resolve_slot_iso: fuzzy match %r → label[%d] %r", slot_iso, i, label)
                    return dt
                except Exception:
                    pass

    raise ValueError(f"Cannot parse or resolve slot datetime: {slot_iso!r}")


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic format: input_schema, not OpenAI parameters)
# ---------------------------------------------------------------------------

TOOL_CHECK_AVAILABILITY = {
    "name": "check_availability",
    "description": (
        "Check available appointment slots at a clinic location. "
        "Call this BEFORE offering times. Returns `available_days` — a list of days, "
        "each with day_label, slot_times, and slots. Present available DAYS first "
        "(up to 4), then times for the chosen day (up to 4)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": (
                    "MUST be 'physiotherapy assessment'. "
                    "This is the only valid value — never pass a treatment name "
                    "(acupuncture, shockwave, sports massage, etc.). "
                    "Regardless of what treatment the patient enquired about, "
                    "always use 'physiotherapy assessment'."
                ),
            },
            "location": {
                "type": "string",
                "enum": ["alcester", "redditch"],
                "description": "Which clinic location to check availability for.",
            },
            "date_hint": {
                "type": "string",
                "description": (
                    "Optional time/date preference from the patient, "
                    "e.g. 'evenings', 'Thursday afternoon', 'next week', 'any'."
                ),
            },
        },
        "required": ["service"],
    },
}

TOOL_BOOK_APPOINTMENT = {
    "name": "book_appointment",
    "description": (
        "Create a calendar booking ONLY after the patient has verbally confirmed "
        "the slot, their full name, and phone number. "
        "Also sends confirmation SMS and logs to Google Sheets."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {"type": "string", "description": "Patient's full name."},
            "phone": {"type": "string", "description": "Patient's mobile number."},
            "location": {
                "type": "string",
                "enum": ["alcester", "redditch"],
                "description": "Clinic location.",
            },
            "service": {
                "type": "string",
                "description": "Service being booked e.g. 'physiotherapy assessment'.",
            },
            "slot_iso": {
                "type": "string",
                "description": "Start datetime in ISO 8601 format, taken from the raw slot list returned by check_availability.",
            },
            "duration_minutes": {
                "type": "integer",
                "description": "Appointment length in minutes. Defaults to 50.",
            },
            "insurer_name": {
                "type": "string",
                "description": (
                    "Name of the caller's private health insurer if they mentioned "
                    "one (e.g. 'Aviva', 'Bupa'). Pass the insurer NAME the caller "
                    "actually said. Do NOT pass any pre-authorisation, membership or "
                    "policy code — the clinic collects those later, never by phone."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "REQUIRED IN PRACTICE — what the appointment is for, in the "
                    "caller's own words (e.g. 'right shoulder pain since a fall'). "
                    "The reason decides the appointment type, its length and its "
                    "price, so this tool REFUSES the booking when no reason is on "
                    "record. Ask for it before checking availability; pass what the "
                    "caller actually said, never a guess."
                ),
            },
            "followup_note": {
                "type": "string",
                "description": (
                    "Optional. A short note for the practitioner to follow up on "
                    "AFTER this booking — set this whenever the caller raised "
                    "something that needs the practitioner's input but should NOT "
                    "delay the booking (e.g. a clinical question you couldn't "
                    "answer, a request for specific equipment, an insurance query). "
                    "The patient is booked in directly; the practitioner is pinged "
                    "with this note to follow up. Leave unset for routine bookings."
                ),
            },
        },
        "required": ["patient_name", "phone", "location", "service", "slot_iso"],
    },
}

TOOL_CANCEL_APPOINTMENT = {
    "name": "cancel_appointment",
    "description": (
        "Cancel an existing upcoming appointment. "
        "Searches for the appointment by patient name. "
        "Confirm the cancellation verbally with the patient before calling this."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {"type": "string", "description": "Patient's full name."},
            "phone": {"type": "string", "description": "Patient's phone number."},
            "location": {"type": "string", "description": "Clinic location (alcester or redditch)."},
        },
        "required": ["patient_name", "phone", "location"],
    },
}

TOOL_RESCHEDULE_APPOINTMENT = {
    "name": "reschedule_appointment",
    "description": (
        "Move an existing appointment to a new slot. "
        "Call check_availability first to get the new slot_iso. "
        "Confirm with the patient before calling this."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {"type": "string"},
            "phone": {"type": "string"},
            "location": {"type": "string"},
            "new_slot_iso": {
                "type": "string",
                "description": "New start datetime in ISO 8601, from check_availability raw list.",
            },
            "duration_minutes": {"type": "integer"},
        },
        "required": ["patient_name", "phone", "location", "new_slot_iso", "duration_minutes"],
    },
}

TOOL_GET_CLINIC_INFO = {
    "name": "get_clinic_info",
    "description": (
        "Get factual clinic information. Use for hours, address, transport, prices, insurance, "
        "services, parking, cancellation policy, what to bring, or any FAQ topic. "
        "Never guess — always call this tool for factual questions about the clinic."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": [
                    # Core operational topics
                    "hours", "address", "transport", "parking", "prices", "insurance",
                    "services", "cancellation_policy", "what_to_bring",
                    # FAQ topics — spoken answers for common caller questions
                    "what_is_assessment",       # what happens in the first session
                    "gp_referral",              # do I need a GP referral?
                    "how_many_sessions",        # how many sessions will I need?
                    "conditions_treated",       # what conditions do you treat?
                    "practitioners",            # who are your physiotherapists?
                    "location_comparison",      # Alcester vs Redditch — which is better?
                    "shockwave_description",    # what is shockwave therapy?
                    "laser_description",        # what is MLS laser therapy?
                    "acupuncture_description",  # what is acupuncture?
                    "psychotherapy_description",# what is psychotherapy?
                    "home_visits",              # do you do home visits?
                    "online_booking",           # can I book online?
                    "online_consultations",     # do you offer video/phone appointments?
                    "children_policy",          # do you see children?
                    "first_visit",              # what happens on my first visit?
                    "surcharge_explained",      # can you explain the £45 surcharge?
                    "waitlist",                 # is there a waiting list?
                    "website",                  # what is your website?
                    "prescribing_service",      # can you prescribe medication?
                    "rehabilitation",           # what are rehab sessions?
                    "between_sessions_support", # can I contact you between sessions?
                    "reports_letters",          # can you provide GP letters / reports?
                    "insurance_claim",          # can I claim from my insurer?
                    "accessibility",            # are you wheelchair accessible?
                    "contact_after_call",       # how do I contact you?
                    "payment_methods",          # how do I pay? card/cash/online?
                    "same_day_booking",         # can I book for today?
                    "physio_vs_rehab_difference", # what's the difference between physio and rehab?
                    "new_vs_returning",         # am I a new or returning patient?
                    "running_late",             # what if I'm running late?
                    "what_to_expect_after",     # what should I expect after my first session?
                    "qualifications",           # are your physios qualified?
                    "returning_after_discharge",# can I come back after discharge?
                    "work_injury",              # I have a work-related injury — can you help?
                    "can_bring_someone",        # can I bring someone with me?
                    "location_inside_greig",    # where do I go inside the Greig Leisure Centre?
                    "packages_discounts",       # do you offer packages or discounts?
                ],
                "description": "The topic to retrieve information about.",
            },
        },
        "required": ["topic"],
    },
}

TOOL_COLLECT_AND_STORE = {
    "name": "collect_and_store",
    "description": (
        "Store a piece of information the patient has provided. "
        "Always call this when you learn the patient's name, phone, reason, "
        "location, insurer, or other booking details. "
        "Do NOT ask for the same field twice if it is already stored."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "enum": [
                    "name", "full_name", "phone", "location", "reason", "insurer",
                    "policy_number", "time_preference", "patient_type", "service",
                    "referral_source", "email",
                ],
                "description": (
                    "Which field to store. Use 'full_name' when collecting the caller's name "
                    "(always collected as a single full name, never split into first/last). "
                    "'full_name' and 'name' are equivalent — both are stored together."
                ),
            },
            "value": {
                "type": "string",
                "description": "The value to store.",
            },
        },
        "required": ["field", "value"],
    },
}

TOOL_TRANSFER_TO_HUMAN = {
    "name": "transfer_to_human",
    "description": (
        "Initiate a live transfer to the clinic team. "
        "Call this when: the patient explicitly asks to speak to someone, "
        "after 2+ failed attempts to understand them, or for emergency situations. "
        "After calling this tool, say a brief warm handover message."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Brief reason for the transfer e.g. 'caller requested', 'repeated misunderstanding'.",
            },
        },
        "required": ["reason"],
    },
}

TOOL_SEND_FOLLOWUP_SMS = {
    "name": "send_followup_sms",
    "description": (
        "Send an SMS to the patient. Use sparingly — only for callback requests "
        "or when the patient asks for a text. Booking confirmations are handled "
        "automatically by book_appointment."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "phone": {"type": "string", "description": "Patient's mobile number."},
            "message_type": {
                "type": "string",
                "enum": ["callback_request", "general"],
                "description": "Type of SMS to send.",
            },
            "custom_message": {
                "type": "string",
                "description": "Custom message text — required for 'general' type.",
            },
        },
        "required": ["phone", "message_type"],
    },
}

TOOL_LOG_CALL_OUTCOME = {
    "name": "log_call_outcome",
    "description": (
        "Record the outcome of this call for reporting. "
        "Call this at natural end points: after a successful booking, "
        "after a FAQ-only call ends, after a transfer, or when the caller hangs up."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["booked", "cancelled", "rescheduled", "faq_only", "transferred", "abandoned"],
            },
            "notes": {
                "type": "string",
                "description": "Optional brief notes about the call.",
            },
        },
        "required": ["outcome"],
    },
}

TOOL_LOOKUP_RECENT_APPOINTMENT = {
    "name": "lookup_recent_appointment",
    "description": (
        "Phone-only lookup of a returning patient's most recent appointment within "
        "a 90-day window. Use this at the start of a new booking when the caller "
        "says they are on an active treatment plan — before collecting their name. "
        "Returns the canonical name and treatment type stored in the system so the "
        "booking can proceed without asking for name again."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "phone": {
                "type": "string",
                "description": "Caller's phone number (as dialled or confirmed).",
            },
            "location": {
                "type": "string",
                "enum": ["alcester", "redditch"],
                "description": "Clinic location the caller is booking at.",
            },
        },
        "required": ["phone", "location"],
    },
}

TOOL_GET_PATIENT_HISTORY = {
    "name": "get_patient_history",
    "description": (
        "Look up a returning patient's recent appointment history to identify "
        "their current treatment type. Use this when a patient says they are on "
        "a treatment plan so you can announce what they have been coming in for."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {
                "type": "string",
                "description": "Full name of the patient as given on the call.",
            },
            "phone": {
                "type": "string",
                "description": (
                    "Patient's phone number (optional). Helps disambiguate if "
                    "multiple patients share the same name."
                ),
            },
        },
        "required": ["patient_name"],
    },
}

TOOL_LOOKUP_PATIENT = {
    "name": "lookup_patient",
    "description": (
        "Look up a patient by name or phone number. "
        "Use before cancel or reschedule to confirm the appointment exists and share details with the patient. "
        "The result includes has_more=true when the number has more than one upcoming booking; "
        "if the caller says the appointment you read back is not the right one, call again with next=true to step to the next match. "
        "Use purpose='history' to retrieve their recent treatment history."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Patient's full name.",
            },
            "phone": {
                "type": "string",
                "description": "Patient's phone number (optional — helps disambiguate).",
            },
            "purpose": {
                "type": "string",
                "enum": ["cancel", "reschedule", "history"],
                "description": (
                    "'cancel' or 'reschedule' — look up an upcoming appointment. "
                    "'history' — retrieve the patient's recent treatment history."
                ),
            },
            "next": {
                "type": "boolean",
                "description": (
                    "Set true to advance to the NEXT matching appointment when the caller "
                    "says the one you read back is not the right one and has_more was true. "
                    "Re-uses the stored match list — do not resend name or phone."
                ),
            },
        },
        "required": ["purpose"],
    },
}

TOOL_ADD_TO_WAITLIST = {
    "name": "add_to_waitlist",
    "description": (
        "Add a caller to the clinic waitlist when no appointment slots are available. "
        "Stores their name, phone, preferred location, and any notes about preferred "
        "days/times. The clinic team will contact them when a slot opens up."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {
                "type": "string",
                "description": "Full name of the patient.",
            },
            "phone": {
                "type": "string",
                "description": "Patient's phone number.",
            },
            "location": {
                "type": "string",
                "description": "Preferred clinic location (e.g. 'alcester' or 'redditch').",
            },
            "service": {
                "type": "string",
                "description": "Requested service (e.g. 'physiotherapy assessment').",
            },
            "notes": {
                "type": "string",
                "description": "Any preferences — preferred days, times, urgency notes.",
            },
        },
        "required": ["patient_name", "phone"],
    },
}

TOOL_LOOKUP_APPOINTMENT = {
    "name": "lookup_appointment",
    "description": (
        "Find a patient's FUTURE appointment before cancelling or rescheduling. "
        "Only strictly future bookings are returned — past appointments are ignored. "
        "Stores the found appointment in session so confirm_appointment_found / "
        "cancel_appointment / reschedule_appointment can act on it directly."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "first_name": {"type": "string"},
            "last_name":  {"type": "string"},
            "phone":      {"type": "string", "description": "Phone number used at time of booking."},
            "location":   {"type": "string", "enum": ["alcester", "redditch"]},
        },
        "required": ["first_name", "last_name", "phone", "location"],
    },
}

TOOL_CONFIRM_APPOINTMENT_FOUND = {
    "name": "confirm_appointment_found",
    "description": (
        "Call this ONLY after the caller has verbally confirmed that the appointment "
        "found by lookup_appointment is theirs. This unlocks cancel_appointment and "
        "reschedule_appointment for this session."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

# Master list passed to the Anthropic API
TOOL_SCHEMAS = [
    TOOL_CHECK_AVAILABILITY,
    TOOL_BOOK_APPOINTMENT,
    TOOL_CANCEL_APPOINTMENT,
    TOOL_RESCHEDULE_APPOINTMENT,
    TOOL_LOOKUP_PATIENT,
    TOOL_TRANSFER_TO_HUMAN,
    TOOL_ADD_TO_WAITLIST,
]


# theorem_v3's replacement for TOOL_BOOK_APPOINTMENT's `reason` description.
#
# The stock description reads "REQUIRED IN PRACTICE ... this tool REFUSES the
# booking when no reason is on record. Ask for it before checking availability."
# That is a standing instruction to ask, it ships with every request, and it is
# not subject to the system prompt at all — which is why suppressing the
# question at the output could never hold. It is correct for JV and Vital Edge
# and wrong for Theorem, so it is rewritten here rather than changed at source.
_THEOREM_REASON_DESC = (
    "OPTIONAL on this clinic, and an empty reason is a correct outcome — the "
    "booking is never refused for the want of one. NEVER ask the caller what "
    "the appointment is for, what brings them in, or any variation: that "
    "question does not exist on this clinic. Pass this field ONLY when the "
    "caller volunteered their condition unprompted, in their own words (e.g. "
    "'right shoulder pain since a fall'). Otherwise omit it entirely. Never a "
    "guess, and never a placeholder."
)


def build_tool_schemas(clinic_id: Optional[str]) -> list:
    """
    Return the tool schemas for a clinic.

    The static schemas above are hardcoded for Theorem (location enum
    alcester/redditch, service 'physiotherapy assessment', 50-min default). For a
    single-site template clinic (e.g. jv_v1) that forces the LLM to pass the
    wrong location/service. Here we deep-copy and rewrite location/service/
    duration from the clinic's own config. Legacy / multi-site clinics get the
    static list unchanged (single_location_template returns None); Theorem gets
    it with one field rewritten — see _THEOREM_REASON_DESC.
    """
    import copy
    from app.clinic_config import get_clinic, single_location_template

    solo = single_location_template(clinic_id)
    if not solo:
        if clinic_id == "theorem_v3":
            # Deep-copied, never mutated in place: TOOL_SCHEMAS is a module-level
            # constant shared by every clinic in the process, and rewriting it
            # would silently give JV and Vital Edge the never-ask instruction too.
            out = copy.deepcopy(list(TOOL_SCHEMAS))
            for t in out:
                _r = ((t.get("input_schema") or {}).get("properties") or {}).get("reason")
                if isinstance(_r, dict) and "REQUIRED IN PRACTICE" in (_r.get("description") or ""):
                    _r["description"] = _THEOREM_REASON_DESC
            return out
        return list(TOOL_SCHEMAS)

    clinic = get_clinic(clinic_id)
    mods = clinic.get("modalities") or []
    has_remote = "remote" in mods
    # Home visits are bookable if any service lists 'home_visit' in available_as.
    # They book exactly like a normal slot (same calendar + clinic hours); the
    # only extras are a patient address-request SMS and a practitioner ping,
    # handled in _exec_book_appointment. Without 'home_visit' in the enum the LLM
    # has no valid location when a caller asks for one and the booking stalls.
    has_home_visit = any(
        "home_visit" in (s.get("available_as") or [])
        for s in (clinic.get("services") or [])
        if s.get("available") is not False
    )
    loc_enum = (
        [solo]
        + (["remote"] if has_remote else [])
        + (["home_visit"] if has_home_visit else [])
    )
    slot_min = int(clinic.get("slot_minutes", 40))
    svc_ids = [
        s.get("service_id")
        for s in (clinic.get("services") or [])
        if s.get("available") is not False and s.get("service_id")
    ]
    svc_desc = (
        "The service to book — use the matching service ID from SERVICE MAPPING"
        + (f" (e.g. {svc_ids[0]})." if svc_ids else ".")
    )
    loc_desc = (
        f"Appointment location: '{solo}' for in-clinic"
        + (", 'remote' for a video/phone appointment" if has_remote else "")
        + (", 'home_visit' for a visit to the patient's home" if has_home_visit else "")
        + "."
    )

    out = []
    for t in TOOL_SCHEMAS:
        t2 = copy.deepcopy(t)
        props = (t2.get("input_schema") or {}).get("properties") or {}
        loc = props.get("location")
        if isinstance(loc, dict) and loc.get("enum") == ["alcester", "redditch"]:
            loc["enum"] = loc_enum
            loc["description"] = loc_desc
        svc = props.get("service")
        if isinstance(svc, dict):
            svc["description"] = svc_desc
        dur = props.get("duration_minutes")
        if isinstance(dur, dict):
            dur["description"] = f"Appointment length in minutes. Defaults to {slot_min}."
        # Single-site template clinics use the Google-calendar availability path,
        # which honours after_date / day_window to shift and bound the search
        # window. The Acuity-era static schema only exposed date_hint, so the LLM
        # could not act on "next week" / "not this week" and always got the
        # earliest slots. Expose them here. jv-only: Theorem returns the static
        # schema above (this code only runs when single_location_template != None).
        if t2.get("name") == "check_availability":
            props["after_date"] = {
                "type": "string",
                "description": (
                    "Earliest date to offer, as YYYY-MM-DD. Pass this when the "
                    "caller cannot be seen before a certain date — e.g. for "
                    "'next week' or 'not this week', pass next Monday's date "
                    "(see DATE AWARENESS in the system prompt). Leave unset when "
                    "the caller is happy with the soonest available."
                ),
            }
            props["day_window"] = {
                "type": "integer",
                "description": (
                    "Number of days from the search start to include. Pass 7 to "
                    "bound results to a single week (e.g. 'next week')."
                ),
            }
        out.append(t2)
    return out


# ===========================================================================
# ACUITY SCHEDULING — helpers and executors (Theorem clinic only)
# ===========================================================================

# Module-level cache: Acuity type name (lowercase) → "acuity_12345" ID
# Populated on first call, reused for the lifetime of the worker process.
_acuity_type_id_cache: Dict[str, str] = {}

# Module-level cache: UK (England/Wales) bank holiday dates fetched from GOV.UK API.
# None = not yet fetched; populated set = successfully fetched.
_uk_bank_holidays_cache: Optional[set] = None

# Hardcoded England/Wales bank holidays 2025-2027.
# Used as the baseline — always applied even if the GOV.UK API is unreachable.
# GOV.UK API results are merged on top of this set when available.
_UK_BANK_HOLIDAYS_FALLBACK: frozenset = frozenset({
    # 2025
    _date_type(2025,  1,  1),  # New Year's Day
    _date_type(2025,  4, 18),  # Good Friday
    _date_type(2025,  4, 21),  # Easter Monday
    _date_type(2025,  5,  5),  # Early May bank holiday
    _date_type(2025,  5, 26),  # Spring bank holiday
    _date_type(2025,  8, 25),  # Summer bank holiday
    _date_type(2025, 12, 25),  # Christmas Day
    _date_type(2025, 12, 26),  # Boxing Day
    # 2026
    _date_type(2026,  1,  1),  # New Year's Day
    _date_type(2026,  4,  3),  # Good Friday
    _date_type(2026,  4,  6),  # Easter Monday
    _date_type(2026,  5,  4),  # Early May bank holiday
    _date_type(2026,  5, 25),  # Spring bank holiday
    _date_type(2026,  8, 31),  # Summer bank holiday
    _date_type(2026, 12, 25),  # Christmas Day
    _date_type(2026, 12, 28),  # Boxing Day (substitute)
    # 2027
    _date_type(2027,  1,  1),  # New Year's Day
    _date_type(2027,  3, 26),  # Good Friday
    _date_type(2027,  3, 29),  # Easter Monday
    _date_type(2027,  5,  3),  # Early May bank holiday
    _date_type(2027,  5, 31),  # Spring bank holiday
    _date_type(2027,  8, 30),  # Summer bank holiday
    _date_type(2027, 12, 27),  # Christmas Day (substitute)
    _date_type(2027, 12, 28),  # Boxing Day (substitute)
})


async def _fetch_uk_bank_holidays() -> frozenset:
    """
    Return a frozenset of England/Wales bank holiday dates (datetime.date objects).

    Always returns at least _UK_BANK_HOLIDAYS_FALLBACK (2025-2027 hardcoded).
    On first call, merges with live data from the GOV.UK API and caches the
    combined result for the lifetime of the worker process.
    """
    global _uk_bank_holidays_cache
    if _uk_bank_holidays_cache is not None:
        return _uk_bank_holidays_cache

    import httpx

    api_holidays: set = set()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://www.gov.uk/bank-holidays.json")
            resp.raise_for_status()
            data = resp.json()

        for event in data.get("england-and-wales", {}).get("events", []):
            try:
                api_holidays.add(_date_type.fromisoformat(event["date"]))
            except Exception:
                pass

        logger.info(
            "_fetch_uk_bank_holidays: GOV.UK API returned %d holidays; "
            "merged with %d hardcoded fallback dates",
            len(api_holidays), len(_UK_BANK_HOLIDAYS_FALLBACK),
        )
    except Exception as exc:
        logger.warning(
            "_fetch_uk_bank_holidays: GOV.UK API failed (%r) — "
            "using hardcoded fallback (%d dates) only",
            exc, len(_UK_BANK_HOLIDAYS_FALLBACK),
        )

    combined = frozenset(api_holidays | _UK_BANK_HOLIDAYS_FALLBACK)
    _uk_bank_holidays_cache = combined
    return combined


def _filter_slots_by_working_hours(slots: list, location: str, location_working_hours: dict) -> list:
    """
    Remove slots that fall outside the configured working hours for the given location.

    location_working_hours format (from clinic_config):
        {"mon": (8.5, 21.0), "tue": (8.5, 21.0), ..., "sat": None, "sun": None}
    A day mapped to None means the clinic is closed; all slots on that day are removed.
    Hours are expressed as fractional hours (8.5 = 08:30).
    """
    _DAY_KEY = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
    loc_hours = location_working_hours.get(location) or {}
    if not loc_hours:
        # No hours configured for this location — return slots unchanged
        return slots

    filtered = []
    skipped = 0
    for slot in slots:
        day_key = _DAY_KEY.get(slot.start_time.weekday())
        hours = loc_hours.get(day_key)
        if hours is None:
            # Closed on this weekday
            skipped += 1
            continue
        open_frac  = hours[0]                                     # e.g. 8.5 → 08:30
        close_frac = hours[1]                                     # e.g. 21.0 → 21:00
        slot_start = slot.start_time.hour + slot.start_time.minute / 60.0
        slot_end   = slot.end_time.hour   + slot.end_time.minute   / 60.0
        if slot_start >= open_frac and slot_end <= close_frac:
            filtered.append(slot)
        else:
            skipped += 1

    if skipped:
        logger.info(
            "_filter_slots_by_working_hours: removed %d/%d slots outside %r hours",
            skipped, len(slots), location,
        )
    return filtered

# ---------------------------------------------------------------------------
# Location normalisation — maps spoken/STT variants to canonical location IDs
# ---------------------------------------------------------------------------
_ALCESTER_VARIANTS = {
    "alcester", "alce", "alchester", "alcest",
    "allster", "alster", "all ster", "all chester", "all-ster",
    "awlster", "olster", "ulster", "alcester road",
}
_REDDITCH_VARIANTS = {
    "redditch", "reditch", "reddich", "redich",
    "reddich road", "bromsgrove road",
}
# Number-based location selection (Theorem: "say one for Alcester, two for Redditch")
_ALCESTER_NUMBERS = {"1", "one", "first", "option one", "option 1", "number one", "number 1"}
_REDDITCH_NUMBERS = {"2", "two", "second", "option two", "option 2", "number two", "number 2"}


def _normalize_location(value: str) -> str:
    """
    Map a spoken or STT-transcribed location string to a canonical location ID.
    Returns "alcester", "redditch", or the lowercased original (for single-location
    clinics or already-canonical values).
    """
    v = (value or "").lower().strip()
    # Number-based selection ("say one for Alcester, two for Redditch")
    if v in _ALCESTER_NUMBERS:
        return "alcester"
    if v in _REDDITCH_NUMBERS:
        return "redditch"
    if any(variant in v for variant in _ALCESTER_VARIANTS):
        return "alcester"
    if any(variant in v for variant in _REDDITCH_VARIANTS):
        return "redditch"
    return v


def location_from_appointment_type(appt_type: str) -> str:
    """The clinic an existing appointment belongs to, or "" if it names none.

    Theorem's Acuity appointment types carry the site in their name
    ("Theorem Clinics Alcester."), which is what the prompt's STRICT RULE means
    when it says a cancel or reschedule takes its location from the lookup
    result rather than from the session or the caller's preference.

    Returns "" for clinics whose types name no town, so callers can leave the
    session untouched rather than guessing.
    """
    loc = _normalize_location(appt_type or "")
    return loc if loc in ("alcester", "redditch") else ""


def _make_acuity_adapter():
    """
    Create a fresh AcuityAdapter using Theorem clinic credentials.
    Returns None (with a warning) if ACUITY_USER_ID / ACUITY_API_KEY are not set.
    """
    from app.booking.booking.providers.acuity import AcuityAdapter
    from app.clinic_config import get_acuity_config

    cfg = get_acuity_config("theorem")
    user_id = (cfg.get("user_id") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    if not user_id or not api_key:
        logger.warning(
            "Acuity credentials not configured — set ACUITY_USER_ID and ACUITY_API_KEY"
        )
        return None
    return AcuityAdapter(user_id=user_id, api_key=api_key, clinic_id="theorem")


# Module-level singleton — reuses the httpx.AsyncClient connection pool across
# every availability check and booking call.  Creating a new AcuityAdapter (and
# therefore a new httpx.AsyncClient) on every call forces a fresh TCP + TLS
# handshake to Acuity each time, adding hundreds of milliseconds per booking turn.
_acuity_adapter_singleton = None


def _get_acuity_adapter():
    """Return the shared AcuityAdapter singleton (lazy-initialised on first call)."""
    global _acuity_adapter_singleton
    if _acuity_adapter_singleton is None:
        _acuity_adapter_singleton = _make_acuity_adapter()
    return _acuity_adapter_singleton


async def _fetch_acuity_type_cache(adapter) -> Dict[str, str]:
    """
    Populate _acuity_type_id_cache from the Acuity API if not already cached.
    Returns {type_name_lower: "acuity_12345"} mapping.
    """
    global _acuity_type_id_cache
    if _acuity_type_id_cache:
        return _acuity_type_id_cache
    try:
        types = await adapter.get_appointment_types()
        for t in types:
            _acuity_type_id_cache[t.name.lower()] = t.id
        logger.info("Acuity appointment type cache: %s", list(_acuity_type_id_cache.keys()))
    except Exception as e:
        logger.error("Failed to fetch Acuity appointment types: %r", e)
    return _acuity_type_id_cache


# Types that should never be booked by the AI receptionist
_SKIP_TYPES = [
    "blocked", "training course", "home visit", "outreach",
    "gong bath", "sound therapy", "meditation", "breathe work",
    "nada gb cert", "package x",
]


def _match_service_to_acuity_id(
    service: str,
    type_cache: Dict[str, str],
    location: str = "",
) -> str:
    """
    Map a free-text service description to an Acuity appointment type ID.

    Theorem's Acuity types are named by location and practitioner
    (e.g. "theorem clinics alcester.", "theorem clinics redditch",
    "theorem clinics alcester. leanne "), not by service name.

    Priority:
      1. Exact match
      2. Location-first: if location is known, find the primary type for
         that location (prefers the "main" entry, avoids practitioner-specific
         or blocked entries).
      3. Service keyword fallback (for specialist types: acupuncture,
         rehab, psychotherapy, shockwave, prescribing).
      4. Absolute fallback: first non-blocked entry.
    """
    s = service.lower().strip()
    loc = location.lower().strip()

    # Helper: is this type name something we should skip?
    def _skippable(name: str) -> bool:
        n = name.lower()
        return any(skip in n for skip in _SKIP_TYPES)

    # 1. Exact match
    if s in type_cache:
        return type_cache[s]

    # 2. Location-first matching (handles Theorem's location-named types)
    #    When location is known, find the PRIMARY type for that location.
    #    "Primary" = contains the location name but NOT practitioner-specific
    #    suffixes like "leanne" (unless specifically requested).
    if loc:
        # 2a. Look for a main location type (location name, no practitioner suffix)
        #     Prefer the shortest/cleanest matching name.
        location_matches = [
            (name, tid) for name, tid in type_cache.items()
            if loc in name and not _skippable(name)
        ]
        # Sort: prefer entries that DON'T contain practitioner names
        # (i.e. the generic location-level type)
        practitioner_suffixes = ["leanne", "mark", "ins-", "insurance"]
        generic = [
            (n, t) for n, t in location_matches
            if not any(p in n for p in practitioner_suffixes)
        ]
        if generic:
            # Among generics, pick the shortest name (most likely to be the main type)
            best = min(generic, key=lambda x: len(x[0]))
            logger.info(
                "_match_service_to_acuity_id: location=%r → matched type %r (id=%s)",
                loc, best[0], best[1],
            )
            return best[1]
        # 2b. If only practitioner-specific types exist for this location, use first one
        if location_matches:
            best = location_matches[0]
            logger.info(
                "_match_service_to_acuity_id: location=%r → practitioner type %r (id=%s)",
                loc, best[0], best[1],
            )
            return best[1]

    # 3. Service keyword table (for specialist services or when no location given)
    _PRIORITY = [
        (["acupuncture", "needle", "needling"],
         ["acupuncture"]),
        (["psychotherapy", "therapy", "mental", "hypno", "spiritual"],
         ["psychotherapy"]),
        (["prescrib", "medication", "prescription"],
         ["prescribing"]),
        (["shockwave", "laser", "mls"],
         ["laser", "shockwave", "mls"]),
        (["rehab", "rehabilitation", "remedial", "yoga", "training"],
         ["rehab", "remedial", "yoga", "training"]),
        (["massage"],
         ["massage"]),
        (["follow-up", "follow up", "followup", "follow", "returning"],
         ["follow-up", "followup", "follow up"]),
        (["assessment", "initial", "first", "new", "physio", "consultation"],
         ["assessment", "physiotherapy", "clinics"]),
    ]
    for input_keywords, cache_keywords in _PRIORITY:
        if any(kw in s for kw in input_keywords):
            for cached_name, cached_id in type_cache.items():
                if _skippable(cached_name):
                    continue
                if any(kw in cached_name for kw in cache_keywords):
                    logger.info(
                        "_match_service_to_acuity_id: service keyword match %r → %r (id=%s)",
                        s, cached_name, cached_id,
                    )
                    return cached_id

    # 4. Absolute fallback: first non-blocked entry
    for name, tid in type_cache.items():
        if not _skippable(name):
            logger.warning(
                "_match_service_to_acuity_id: fallback to first non-blocked type %r (id=%s)",
                name, tid,
            )
            return tid

    if type_cache:
        return next(iter(type_cache.values()))

    return None


def _split_name(full_name: str):
    """Split 'John Smith' → ('John', 'Smith').
    Single word → (word, word) so Acuity's required lastName field is never empty."""
    parts = full_name.strip().split(None, 1)
    if len(parts) == 2:
        return (parts[0], parts[1])
    first = parts[0] if parts else "Patient"
    return (first, first)


# ---------------------------------------------------------------------------
# Manual slot generator (fallback when Acuity working hours aren't configured)
# ---------------------------------------------------------------------------

async def _generate_manual_slots(
    adapter,
    appointment_type_id: str,
    practitioner_id,
    start_date,
    end_date,
    slot_minutes: int = 50,
) -> list:
    """
    Build slots from Theorem's known working hours (Mon–Thu 08:30–21:00, 50-min)
    and subtract already-booked Acuity appointments.

    Used as a fallback when Acuity /availability/times returns 0 slots because
    working hours aren't configured inside the Acuity admin panel.
    The booking POST still works fine regardless of that config gap.
    """
    from app.booking.booking.models import Slot as _Slot

    # Theorem Alcester: Mon–Fri (0–4), 08:30 start, last slot starts at 20:10 so it ends at 21:00
    WORK_START_H, WORK_START_M = 8, 30
    WORKING_WEEKDAYS = {0, 1, 2, 3, 4}  # Mon=0 … Fri=4

    # ── fetch existing appointments to exclude conflicts ──────────────────
    booked_times: set = set()
    try:
        existing = await adapter.list_appointments(min_date=start_date, max_date=end_date)
        for appt in existing:
            dt_str = appt.get("datetime") or appt.get("time") or ""
            if dt_str:
                try:
                    dt = datetime.fromisoformat(dt_str)
                    if dt.tzinfo is None:
                        dt = LONDON_TZ.localize(dt)
                    else:
                        dt = dt.astimezone(LONDON_TZ)
                    booked_times.add((dt.date(), dt.hour, dt.minute))
                except Exception:
                    pass
    except Exception as fetch_err:
        logger.warning(
            "_generate_manual_slots: could not fetch existing appointments to check conflicts: %r",
            fetch_err,
        )

    # ── generate slots ────────────────────────────────────────────────────
    slots: list = []
    current = start_date
    # End of last allowed slot must be <= 21:00, so last START is 21:00 - slot_minutes
    work_end_minutes = 21 * 60  # 21:00 in minutes from midnight
    start_minutes = WORK_START_H * 60 + WORK_START_M

    while current <= end_date:
        if current.weekday() in WORKING_WEEKDAYS:
            t_min = start_minutes
            while t_min + slot_minutes <= work_end_minutes:
                h, m = divmod(t_min, 60)
                naive_start = datetime(current.year, current.month, current.day, h, m)
                slot_start = LONDON_TZ.localize(naive_start)
                slot_end = slot_start + timedelta(minutes=slot_minutes)

                # Skip slots that are already in the past
                now = datetime.now(LONDON_TZ)
                if slot_start <= now:
                    t_min += slot_minutes
                    continue

                if (current, h, m) not in booked_times:
                    slots.append(
                        _Slot(
                            start_time=slot_start,
                            end_time=slot_end,
                            appointment_type_id=appointment_type_id,
                            practitioner_id=practitioner_id,
                            provider_slot_id=slot_start.isoformat(),
                        )
                    )
                t_min += slot_minutes
        current += timedelta(days=1)

    logger.info(
        "_generate_manual_slots: generated %d slots between %s and %s",
        len(slots), start_date, end_date,
    )
    return slots


# ---------------------------------------------------------------------------
# Acuity executor: check_availability
# ---------------------------------------------------------------------------

async def _check_availability_acuity(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """
    check_availability via Acuity Scheduling (Theorem clinic).

    Uses a progressive search window: 14 → 30 → 60 days, expanding automatically
    until slots are found. This ensures the next available appointment is always
    returned regardless of how far ahead it is.
    """
    from datetime import date as _date
    from app.clinic_config import THEOREM_LOCATIONS

    location = _normalize_location(args.get("location") or session.get("selected_location", ""))

    if not location and session.get("twilio_to") in ("+447366530580", "+447380841468"):
        return {
            "error": "location_required",
            "error_detail": (
                "You must ask the caller which clinic they want before checking availability. "
                "Say: 'Which clinic would you like — say one for Alcester, or two for Redditch?' "
                "Then call collect_and_store(field='location', value='alcester' or 'redditch'), "
                "and only after that call check_availability again."
            ),
            "slots": [],
        }

    service = (args.get("service") or "physiotherapy assessment").strip()
    preference = (args.get("date_hint") or args.get("preference") or "").strip()

    # ── Availability cache (90s TTL) ──────────────────────────────────────
    # When the user selects a slot from already-presented options on the next
    # turn, the LLM fires check_availability again with identical parameters.
    # Skip the 30-call Acuity fetch and return the cached result instead.
    _cache = session.get("_availability_cache")
    if _cache:
        _cache_age = _time.monotonic() - _cache.get("_ts", 0)
        if (
            _cache.get("location") == location
            and (_cache.get("date_hint") or "").lower() == preference.lower()
            and _cache_age < 90
        ):
            logger.info(
                "_check_availability_acuity: CACHE HIT loc=%r hint=%r age=%.1fs — "
                "skipping Acuity fetch",
                location, preference, _cache_age,
            )
            # Restore session keys so slot resolution still works
            session["last_offered_slots"] = _cache["last_offered_slots"]
            session["slot_labels"]        = _cache["slot_labels"]
            session["available_days"]     = _cache["available_days"]
            return {
                "available_days": _cache["available_days"],
                "total_days":     _cache["total_days"],
                "_from_cache":    True,
            }

    # Explicit day_window from the LLM bypasses progressive search
    explicit_window = args.get("day_window")

    adapter = _get_acuity_adapter()
    if not adapter:
        return {"error": "Booking system not configured. Please call the clinic directly.", "slots": []}

    try:
        # Use per-location appointment type ID — each clinic has its own type in Acuity.
        # /availability/times MUST always receive appointmentTypeID — calling
        # without it returns an empty list regardless of actual availability.
        appointment_type_id = (
            _LOCATION_APPOINTMENT_TYPE_IDS.get(location)
            or DEFAULT_ACUITY_APPOINTMENT_TYPE_ID
        )
        logger.info(
            "_check_availability_acuity: location=%s using appointment_type_id=%s",
            location, appointment_type_id,
        )

        loc_cfg = THEOREM_LOCATIONS.get(location, {})
        raw_cal_id = (loc_cfg.get("acuity_calendar_id") or "").strip()
        practitioner_id = f"acuity_cal_{raw_cal_id}" if raw_cal_id else None

        if location and not raw_cal_id:
            logger.warning(
                "No Acuity calendar ID configured for location %r — "
                "fetching all-calendar availability. "
                "Set ACUITY_CALENDAR_ID_%s on Render.",
                location, location.upper(),
            )

        today = _date.today()

        # Resolve after_date: earliest allowed date for returned slots
        after_date_str = (args.get("after_date") or "").strip()
        after_date_cutoff: "_date | None" = None
        if after_date_str:
            try:
                after_date_cutoff = _date.fromisoformat(after_date_str)
                logger.info(
                    "_check_availability_acuity: after_date_cutoff=%s (from args)",
                    after_date_cutoff,
                )
            except Exception:
                logger.warning(
                    "_check_availability_acuity: could not parse after_date=%r — ignoring",
                    after_date_str,
                )

        # Use after_date as the search start if it is later than today
        search_start = max(today, after_date_cutoff) if after_date_cutoff else today

        # Always scan a full 30-day window so near-term scarcity (bank holidays,
        # blocked days) doesn't leave the caller with only 1–2 options.
        # Explicit day_window from the LLM overrides this (e.g. for "next week").
        used_window = int(explicit_window) if explicit_window else 30
        end_date = search_start + timedelta(days=used_window)

        try:
            slots = await adapter.get_available_slots(
                appointment_type_id=appointment_type_id,
                start_date=search_start,
                end_date=end_date,
                practitioner_id=practitioner_id,
            )
        except Exception as api_err:
            logger.error(
                "_check_availability_acuity: Acuity API error location=%r window=%d: %r",
                location, used_window, api_err,
            )
            return {
                "error": (
                    f"Could not fetch availability for {location.title()}: {api_err}. "
                    "There may be a configuration issue — please call the clinic directly."
                ),
                "slots": [],
            }

        # Per-date logging: show how many raw slots Acuity returned per day
        if slots:
            from collections import defaultdict as _dd
            _per_day: dict = _dd(int)
            for _s in slots:
                _per_day[_s.start_time.date()] += 1
            for _day in sorted(_per_day):
                logger.info(
                    "_check_availability_acuity: %s — %d raw slot(s) from Acuity",
                    _day, _per_day[_day],
                )
            logger.info(
                "_check_availability_acuity: %d total raw slot(s) for %s over %d days",
                len(slots), location, used_window,
            )
        else:
            logger.info(
                "_check_availability_acuity: no slots for %s in %d days",
                location, used_window,
            )

        # ── Post-fetch filters ─────────────────────────────────────────────
        # 1. Minimum lead-time filter: drop slots starting within 2 hours of now.
        #    Prevents offering a 8:30 slot when the caller rings at 8:21 and
        #    the conversation itself takes several minutes.
        raw_slot_count = len(slots)  # count BEFORE lead-time filter
        if slots:
            now_london = datetime.now(LONDON_TZ)
            min_start  = now_london + timedelta(hours=2)
            before_lt  = len(slots)
            slots = [s for s in slots if s.start_time >= min_start]
            removed_lt = before_lt - len(slots)
            if removed_lt:
                logger.info(
                    "_check_availability_acuity: removed %d slot(s) within 2h lead-time window",
                    removed_lt,
                )

        # 2. Working-hours filter: remove slots outside configured clinic hours
        #    (guards against Acuity returning slots before open / after close).
        from app.clinic_config import get_clinic
        clinic_cfg = get_clinic(session.get("clinic_id", "theorem")) or {}
        loc_wh = clinic_cfg.get("location_working_hours", {})
        if slots and loc_wh:
            slots = _filter_slots_by_working_hours(slots, location, loc_wh)

        # 3. Bank-holiday filter: remove slots on England/Wales bank holidays.
        #    Always applied — _fetch_uk_bank_holidays() always returns at least
        #    the hardcoded 2025-2027 fallback set even if the GOV.UK API is down.
        #    NOTE: do NOT guard with "if bank_holidays:" — bool(empty set) is False
        #    and would silently skip the filter. Always run the comprehension.
        bank_holidays = await _fetch_uk_bank_holidays()
        if slots:
            before_bh = len(slots)
            slots = [s for s in slots if s.start_time.date() not in bank_holidays]
            removed_bh = before_bh - len(slots)
            if removed_bh:
                logger.info(
                    "_check_availability_acuity: removed %d slot(s) on UK bank holidays "
                    "(bank_holidays set size=%d)",
                    removed_bh, len(bank_holidays),
                )

        if not slots:
            # Distinguish between "lead-time filtered everything" vs "genuinely no slots"
            # so the LLM can use the appropriate message.
            if raw_slot_count > 0:
                # Slots existed but were all too soon (within 2h lead time).
                logger.warning(
                    "_check_availability_acuity: %d raw slot(s) for %s all within 2h lead-time window — "
                    "availability is limited today.",
                    raw_slot_count, location,
                )
                return {
                    "error": "lead_time_limited",
                    "error_detail": (
                        f"There are {raw_slot_count} slot(s) available at {location.title()} today "
                        "but all start within 2 hours — too soon to book. "
                        "Suggest the next available day or take contact details."
                    ),
                    "slots": [],
                }
            # Real Acuity availability returned nothing — report honestly.
            # Do NOT fall back to fake/manual slot generation.
            logger.warning(
                "_check_availability_acuity: 0 slots from Acuity for %s in %d days — "
                "no availability to offer.",
                location, used_window,
            )
            return {
                "error": "no_availability",
                "error_detail": (
                    f"No appointments available at {location.title()} in the next "
                    f"{used_window} days. The clinic may be fully booked — "
                    "please try the other location, or let the caller know the team will be in touch."
                ),
                "slots": [],
            }

        # Post-fetch date filter: safety net in case the Acuity API ignores start_date
        if after_date_cutoff:
            pre_filter_count = len(slots)
            slots = [s for s in slots if s.start_time.date() >= after_date_cutoff]
            logger.info(
                "_check_availability_acuity: after_date post-filter %s → %d/%d slots remaining",
                after_date_cutoff, len(slots), pre_filter_count,
            )

        # 4. Week range filter: if date_hint encodes a specific week (or single
        #    date), strip every slot outside that Mon–Sun window before the LLM
        #    sees the result.  Falls back silently when the hint cannot be parsed.
        #
        #    Bypass: if no explicit week-anchor phrase is present the caller has
        #    not named a week — they want the best match across the full 30-day
        #    window (e.g. "evening slots", "any Monday", "as soon as possible").
        #    Applying Pattern 4 in that case would silently narrow a "9pm" hint
        #    to the nearest 9th of the month and return nothing.
        _WEEK_ANCHORS = ("next week", "this week", "week of", "week beginning", "week starting", "from ")
        _hint_lower = preference.lower() if preference else ""
        _has_week_anchor = any(anchor in _hint_lower for anchor in _WEEK_ANCHORS)
        # Also allow exact ISO dates ("2026-06-23") and specific ordinal dates
        # ("Monday 22 June 2026", "22nd June 2026") to reach _extract_week_range —
        # Pattern 0 / Pattern 4 return a single-day range for these.
        # Without this, "2026-06-23" would be bypassed and all 30 days returned.
        if not _has_week_anchor and preference:
            _has_week_anchor = bool(
                re.fullmatch(r"\d{4}-\d{2}-\d{2}", preference.strip())
            ) or bool(
                # Relative single-day anchors — resolved by Pattern 0.5 in
                # _extract_week_range.  Without this the bypass returns the full
                # 30-day sweep for "tomorrow afternoon around 3pm" and the day
                # intent is lost (C8-4 relative-date failure).
                re.search(r"\b(?:today|tomorrow)\b", _hint_lower)
            ) or bool(
                re.search(
                    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|"
                    r"mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
                    r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
                    r"\b",
                    _hint_lower,
                )
            ) or bool(
                # Two-date disjunction/range ("8th or 9th", "18-19 July") —
                # without this, no-month forms never reach _extract_week_range
                # and fall through to the global-earliest sweep (the "X or Y
                # wrongly refused" bug).
                _extract_multidate_range(_hint_lower, _date_type.today())
            )
        if not _has_week_anchor:
            logger.info(
                "[ms_tools] week filter bypassed — no week anchor in date_hint: %r",
                preference,
            )
        _week_range = _extract_week_range(preference) if _has_week_anchor else None
        if _week_range is not None:
            _wk_start, _wk_end = _week_range
            _all_slots_pre_wk = slots[:]  # save before week filter for next_available lookup
            _pre_wk_count = len(slots)
            slots = [s for s in slots if _wk_start <= s.start_time.date() <= _wk_end]
            _n_days_returned = len({s.start_time.date() for s in slots})
            logger.info(
                "[ms_tools] week filter applied: %s to %s — %d days returned",
                _wk_start, _wk_end, _n_days_returned,
            )
            if not slots:
                logger.info(
                    "[ms_tools] week filter: no slots in range %s to %s "
                    "(removed %d slot(s))",
                    _wk_start, _wk_end, _pre_wk_count,
                )
                # Build next_available from unfiltered data so Sonnet can answer
                # without a second check_availability call.
                _future_slots = sorted(
                    (s for s in _all_slots_pre_wk if s.start_time.date() > _wk_end),
                    key=lambda s: s.start_time,
                )
                _seen_days: set = set()
                _next_avail: list = []
                for _fs in _future_slots:
                    _fd = _fs.start_time.date()
                    if _fd not in _seen_days:
                        _seen_days.add(_fd)
                        _next_avail.append({
                            "date": _fd.isoformat(),
                            "day_label": _fs.start_time.strftime("%A %-d %B"),
                            "time": _fs.start_time.strftime("%H:%M"),
                        })
                    if len(_next_avail) >= 3:
                        break
                _na_hint = (
                    f" Next available: {_next_avail[0]['day_label']} at {_next_avail[0]['time']}."
                    if _next_avail else " No availability in the next 30 days."
                )
                return {
                    "error": "no_availability",
                    "error_detail": (
                        f"No appointments available at {location.title()} "
                        f"on {_wk_start.strftime('%-d %B %Y')} ({_wk_start} to {_wk_end})."
                        f"{_na_hint} "
                        "Use the next_available data to respond: "
                        "'I don't have anything on [date] — the next I have is [day] at [time]. "
                        "Does that work for you?' "
                        "Do NOT call check_availability again."
                    ),
                    "next_available": _next_avail,
                    "slots": [],
                }

        # Build day-grouped structure for the day-first presentation flow.
        # Present exactly 3 slots (one per day where possible) so that
        # slot_labels[0/1/2] map 1:1 to the 1st/2nd/3rd slot spoken by Susie.
        slot_tuples = [(s.start_time, s.end_time) for s in slots]
        presented   = _select_presented_tuples(slot_tuples, preference=preference)
        # Build days_data from preference-matching slots so each day shows all
        # its available times for the requested day/time, not just the one slot
        # selected for variety by _select_presented_tuples.  Passing preference
        # keeps available_days consistent with slot_labels (bug C5-5): a
        # "Thursday afternoon" request no longer yields non-Thursday days.
        days_data   = _build_days_data(slot_tuples, preference=preference)

        # Drop TODAY at the source — same-day bookings are never offered (min lead
        # = next working day).  Doing it here (not only in the post-return
        # _filter_same_day_slots) keeps days_data, first_day, available_days and
        # the per-day 3-cap all consistent.  Bug B (2026-06-17): the post-return
        # filter stripped today from available_days but left first_day = today, so
        # single_day mode spoke today's filtered 6pm slot ("Wednesday 17th — six
        # in the evening") while available_days[0] was correctly Monday 22nd.
        _today_iso = today.isoformat()
        _before_sd = len(days_data)
        days_data = [d for d in days_data if d.get("date") != _today_iso]
        if len(days_data) < _before_sd:
            logger.info("[ms_tools] same-day dropped at source: %s", _today_iso)

        pres_raw    = [{"start": s.isoformat(), "end": e.isoformat()} for s, e in presented]
        pres_labels = [s.strftime("%a %d %b at %H:%M") for s, e in presented]

        session["last_offered_slots"]          = pres_raw
        session["slot_labels"]                 = pres_labels
        session["available_days"]              = days_data
        session["_acuity_appointment_type_id"] = appointment_type_id
        session["_acuity_practitioner_id"]     = practitioner_id

        # ── Populate 90s availability cache ───────────────────────────────
        session["_availability_cache"] = {
            "location":          location,
            "date_hint":         preference.lower(),
            "_ts":               _time.monotonic(),
            "last_offered_slots": pres_raw,
            "slot_labels":       pres_labels,
            "available_days":    days_data,
            "total_days":        len(days_data),
        }

        # ── presentation_mode ────────────────────────────────────────────────
        # multi_day is the DEFAULT (breadth: ≤3 days × ≤2 times) so a vague or
        # part-of-day request shows a real spread instead of one lonely slot.
        # single_day (one day, all its times) is reserved for the two cases where
        # a single day is genuinely what the caller wants:
        #   (a) ASAP  — "soonest/earliest" → the one earliest day + warm lead-in,
        #               but it FALLS BACK to multi_day when that day is too thin
        #               so the caller still hears ≥2 options ("fill-forward").
        #   (b) a SPECIFIC named day — "Tuesday", "the 23rd", "tomorrow" → the
        #               week filter resolved a 1-day range; show that day in full.
        _ASAP_SIGNALS = ("soon", "asap", "earliest", "first avail", "as soon as")
        _is_asap         = any(_s in _hint_lower for _s in _ASAP_SIGNALS)
        # A specific named day → single_day (show that one day in full). Either the
        # week filter resolved a 1-day range (ISO / "23rd June" / today / tomorrow),
        # OR the caller named a bare weekday with no week phrase ("do you have
        # Tuesday?"): _build_days_data filters to that weekday so days_data[0] is
        # the NEXT occurrence — present that one day, not three upcoming weekdays.
        _WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        _has_weekday_name = any(_d in _hint_lower for _d in _WEEKDAYS)
        _is_specific_day = (
            (_week_range is not None and _week_range[0] == _week_range[1])
            or (_has_weekday_name and not _has_week_anchor)
        )

        if _is_asap:
            # Owner decision 2026-06-15: ASAP shows the ONE soonest day as-is
            # (NO fill-forward), then acts on the caller's response.  The single
            # take-it-or-leave-it case is acceptable now that BUG-12 lets the
            # caller ask "anything else?" and get alternatives.
            _presentation_mode = "single_day"
        elif _is_specific_day:
            _presentation_mode = "single_day"
        else:
            _presentation_mode = "multi_day"

        # Cap the multi_day SPOKEN list to the soonest 3 days.  days_data is
        # sorted soonest-first, so [:3] keeps the nearest options and leads with
        # the soonest.  The FULL set is still in session["available_days"] (set
        # above) — resolution (_resolve_slot_iso / DTMF) reads the session copy,
        # so the caller can still pick or ask about a day beyond the 3 presented;
        # only the spoken presentation is trimmed.  The ≤2-times-per-day cap is
        # enforced by the slot formatter (SLOT_FORMATTER_SYSTEM_PROMPT, multi_day).
        _present_days = days_data[:3] if _presentation_mode == "multi_day" else days_data
        _result = {"available_days": _present_days, "total_days": len(_present_days), "presentation_mode": _presentation_mode}
        if _presentation_mode == "single_day" and days_data:
            # Cap a single day's SPOKEN times to the soonest 3 so a busy day
            # (e.g. "the 29th" with 10 slots) isn't a wall of times.  When more
            # exist, flag more_times so the formatter adds the "…a few others
            # that day if neither suits" tail.  The FULL day stays in
            # session["available_days"] (set above), so _resolve_slot_iso / DTMF
            # still resolve a time beyond the 3 presented.
            _fd = days_data[0]
            _n = len(_fd.get("slot_times", []) or [])
            if _n > 3:
                _fd = {
                    **_fd,
                    "slot_times":        (_fd.get("slot_times") or [])[:3],
                    "slot_times_spoken": (_fd.get("slot_times_spoken") or [])[:3],
                    "slots":             (_fd.get("slots") or [])[:3],
                    "more_times":        True,
                }
            _result["first_day"] = _fd
            # Warm lead-in only on a single_day ASAP request ("soonest/earliest").
            # Never on a specific-day request like "do you have Tuesday?".
            if _is_asap:
                _result["lead_in"] = "earliest"

        return _result

    except Exception as e:
        logger.error("_check_availability_acuity unexpected error: %r", e, exc_info=True)
        return {"error": f"Availability check failed: {e}", "slots": []}


# ---------------------------------------------------------------------------
# Acuity executor: book_appointment
# ---------------------------------------------------------------------------

async def _alert_owner_acuity_book_failed(
    args: Dict[str, Any], session: Dict[str, Any], reason: str
) -> None:
    """
    Text the owner when an Acuity write fails, so a caller who wanted an
    appointment and could not get one reaches a human.

    Why this exists (2026-08-05): the `manual_followup` owner alert lives only in
    the Google-Calendar no-calendar branch of `_exec_book_appointment`. Theorem
    short-circuits to the Acuity executors before that branch is ever reached, so
    an Acuity failure alerted nobody — it only returned success=False to the
    model. Enabling `manual_followup` in clinic config does not fix that on its
    own; there was no call site to enable.

    Note the failure shape here is NOT a phantom booking: the tool returns an
    error, so the model tells the caller it could not book. Nobody is falsely
    confirmed. What was missing is the other half — the clinic learning that a
    patient tried and failed, while Susie was still on the line with them.

    SlotUnavailable is deliberately excluded: the slot was taken between the
    availability check and the write, the model offers another time, and the call
    continues normally. That is routine contention, not a system failure, and
    alerting on it would train the owner to ignore the alert.
    """
    from app.notifications.owner_alert import notify_owner

    _phone = (args.get("phone") or "").strip()
    _when = (args.get("slot_iso") or "").strip()
    await notify_owner(
        session,
        event="manual_followup",
        patient_name=args.get("patient_name") or "",
        when_label=_when,
        service=args.get("service") or "",
        location=args.get("location") or "",
        note=(
            f"Acuity write FAILED ({reason}) — caller was told we could not book. "
            f"Please call them back on {_phone or 'the number in the call log'}."
        ),
    )


async def _book_appointment_acuity(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """book_appointment via Acuity Scheduling (Theorem clinic)."""
    from app.booking.booking.models import BookingRequest, InsuranceInfo
    from app.booking.booking.exceptions import SlotUnavailable, ProviderAuthError
    from app.notifications.booking_sms import send_booking_confirmation
    from app.clinic_config import get_clinic, THEOREM_LOCATIONS

    adapter = _get_acuity_adapter()
    if not adapter:
        # No adapter means no Acuity credentials at all — every booking on this
        # deploy is failing, not just this one. That is the loudest thing the
        # owner can be told, so it alerts on the same path as a write failure.
        logger.error("[BOOKING FAILED] Acuity adapter unavailable — no credentials?")
        await _alert_owner_acuity_book_failed(args, session, "booking system not configured")
        return {"success": False, "error": "Booking system not configured."}

    try:
        clinic = get_clinic(session.get("clinic_id"))
        location = _normalize_location(args.get("location") or session.get("selected_location", ""))

        if not location and session.get("twilio_to") in ("+447366530580", "+447380841468"):
            return {
                "success": False,
                "error": (
                    "Location is required to complete this booking. "
                    "Ask: 'Which clinic would you like — say one for Alcester, or two for Redditch?' "
                    "Call collect_and_store(field='location', ...) first, then retry book_appointment."
                ),
            }

        service = (args.get("service") or "physiotherapy assessment").strip()
        patient_name = (args.get("patient_name") or "").strip()
        # ── Full-name guarantee ───────────────────────────────────────────
        # The booking flow reads back only the FIRST name, so the LLM may pass
        # patient_name as just the first name even though the caller gave a
        # surname (captured deterministically into session this call). If we
        # hold a richer first+surname for the SAME first name, prefer it so the
        # surname reaches Acuity. Never overrides a name the LLM already sent
        # with two+ tokens, and never swaps to an unrelated first name.
        _stored_name = (
            session.get("patient_name")
            or session.get("collected", {}).get("name")
            or ""
        ).strip()
        if (
            _stored_name
            and len(_stored_name.split()) >= 2
            and len(patient_name.split()) <= 1
            and (
                not patient_name
                or patient_name.lower() == _stored_name.split()[0].lower()
            )
        ):
            logger.info(
                "[ms_tools] book_appointment full-name guarantee: %r -> %r",
                patient_name, _stored_name,
            )
            patient_name = _stored_name
        phone = (args.get("phone") or "").strip()
        is_new = bool(args.get("is_new_patient", True))
        insurer = (args.get("insurer_name") or "").strip()
        policy = (args.get("policy_number") or "").strip()

        if not patient_name or not phone:
            return {"success": False, "error": "patient_name and phone are required."}

        try:
            start_dt = _resolve_slot_iso(args.get("slot_iso", ""), session)
        except Exception as e:
            return {"success": False, "error": f"Invalid slot datetime: {e}"}

        # FIX #9: Reject slots in the past — a hallucinated or stale datetime
        # would reach Acuity and fail with a confusing error.
        now_london = datetime.now(LONDON_TZ)
        if start_dt <= now_london:
            return {
                "success": False,
                "error": (
                    f"Cannot book a slot in the past "
                    f"({start_dt.strftime('%a %d %b at %H:%M')}). "
                    "Please check availability again for current options."
                ),
            }

        # Appointment type — prefer cached from check_availability, else use env default.
        # appointmentTypeID MUST always be passed to Acuity for booking to succeed.
        appointment_type_id = (
            session.get("_acuity_appointment_type_id") or DEFAULT_ACUITY_APPOINTMENT_TYPE_ID
        )
        logger.info(
            "_book_appointment_acuity: using appointment_type_id=%s",
            appointment_type_id,
        )

        # Practitioner / calendar ID — prefer cached from check_availability
        practitioner_id = session.get("_acuity_practitioner_id")
        if not practitioner_id:
            loc_cfg = THEOREM_LOCATIONS.get(location, {})
            raw_cal_id = (loc_cfg.get("acuity_calendar_id") or "").strip()
            practitioner_id = f"acuity_cal_{raw_cal_id}" if raw_cal_id else None

        first_name, last_name = _split_name(patient_name)

        notes_parts = ["New patient" if is_new else "Returning patient"]
        if insurer:
            notes_parts.append(f"Insurance: {insurer}")
            if policy:
                notes_parts.append(f"Policy: {policy}")

        insurance_info = None
        if insurer:
            insurance_info = InsuranceInfo(
                provider_name=insurer,
                policy_number=policy or None,
            )

        # Email: fixed address used for all bookings.
        _caller_email = "julienroch56@gmail.com"
        logger.info(
            "_book_appointment_acuity: using fixed booking email %r",
            _caller_email,
        )

        request = BookingRequest(
            appointment_type_id=appointment_type_id,
            slot_start=start_dt,
            location_id=location,
            patient_first_name=first_name,
            patient_last_name=last_name,
            patient_phone=phone,
            patient_email=_caller_email,
            notes=" | ".join(notes_parts),
            practitioner_id=practitioner_id,
            insurance_info=insurance_info,
            call_sid=session.get("call_sid", "unknown"),
            session_id=session.get("session_id", "unknown"),
        )

        booking = await adapter.create_booking(request)

        # Update session
        session.setdefault("collected", {})
        # Acuity path — same sync as the calendar path; see the helper.
        _sync_booked_patient_name(session, patient_name)
        session["collected"]["phone"] = phone
        session["collected"]["service"] = service
        session["collected"]["slot"] = args["slot_iso"]
        # Populate the top-level slot key the confirmation SMS builder reads.
        # build_sms() (app/sms_templates.py) resolves date/time from
        # session["selected_slot"] — a key only the legacy v2 flow.py set.
        # The v3 tool path never wrote it, so the SMS rendered blank 📅/⏰
        # lines (fell through to "—").  Set it from the just-booked ISO so
        # Path 1 (ISO parse) fills in the date and time.
        session["selected_slot"] = args["slot_iso"]
        if insurer:
            session["collected"]["insurer"] = insurer
        session["acuity_booking_id"] = booking.provider_booking_id
        session["calendar_status"] = "created"
        # The provider has accepted the booking — record it. See the note in
        # _exec_book_appointment; this flag is what every downstream outcome
        # reader keys off, and until 2026-07-26 the v3 tool path never set it.
        session["booking_confirmed"] = True
        # Booking confirmed — clear the last-presented date hint so it
        # doesn't resurface in CALL STATE after the appointment is made.
        session.pop("v3_last_presented_date_hint", None)
        session.pop("v3_last_offered_day_iso", None)

        # Stage 2: create pending name-confirmation record + 30-min nudge —
        # ONLY when we do NOT already hold the caller's surname. With first +
        # surname captured on the call there is nothing to chase, so skip both
        # the pending record and the reminder SMS. (If surname capture missed,
        # the name is a single token and we still chase it, which also keeps
        # the booking-confirmation SMS's full-name request in step.)
        if len(patient_name.split()) >= 2:
            logger.info(
                "[PENDING_NAME] skipped — full name already captured (%r)",
                patient_name,
            )
        else:
            try:
                from app.storage.redis_store import create_pending_name_confirmation
                from app.flows.triage_legacy import normalize_phone
                norm_phone = normalize_phone(phone)
                first = patient_name.split()[0] if patient_name else ""
                await create_pending_name_confirmation(
                    phone=norm_phone,
                    first_name=first,
                    appointment_id=booking.provider_booking_id,
                    location=location,
                )
                logger.info(
                    "[PENDING_NAME] record created: phone=%r appt_id=%r",
                    norm_phone,
                    booking.provider_booking_id,
                )
                # Schedule 30-min name-confirm nudge (non-fatal)
                try:
                    from app.notifications.scheduler import schedule_name_confirm_reminder
                    await schedule_name_confirm_reminder(phone=norm_phone, first_name=first)
                except Exception as _sched_err:
                    logger.warning("[PENDING_NAME] reminder schedule failed (non-fatal): %r", _sched_err)
            except Exception as _pn_err:
                logger.warning("[PENDING_NAME] create failed (non-fatal): %r", _pn_err)

        # Confirmation SMS — non-fatal.
        # Suppressed when called as part of a reschedule (caller gets a reschedule
        # confirmation instead, sent by _reschedule_appointment_acuity).
        if not args.get("_suppress_sms"):
            # Owner heads-up FIRST — before the patient confirmation.
            # No-op unless the clinic enables owner_alerts.
            from app.notifications.owner_alert import notify_owner
            await notify_owner(
                session,
                event="booking",
                patient_name=patient_name,
                when_label=booking.start_time.strftime("%A %d %B at %H:%M"),
                service=service,
                location=location,
                is_new_patient=is_new,
            )
            try:
                await send_booking_confirmation(
                    patient_phone=phone,
                    patient_name=patient_name,
                    appointment_time=booking.start_time,
                    location=location.title(),
                    service=service,
                    is_new_patient=is_new,
                    has_insurance=bool(insurer),
                    insurer=insurer or None,
                    clinic_name=clinic.get("sms_name") or clinic.get("display_name"),
                    clinic_phone=clinic.get("sms_phone") or clinic.get("phone"),
                    session=session,
                )
            except Exception as e:
                logger.warning("_book_appointment_acuity SMS failed (non-fatal): %r", e)

        # Schedule day-before (24hr) + 2-hour reminders — non-fatal.
        # Fires for reschedules too (new appointment time gets its own reminders).
        try:
            from app.notifications.scheduler import schedule_appointment_reminders
            await schedule_appointment_reminders(
                patient_phone=phone,
                patient_name=patient_name,
                appointment_time=booking.start_time,
                location=location.title(),
                is_new_patient=is_new,
                has_insurance=bool(insurer),
                insurer=insurer or None,
                clinic_name=clinic.get("sms_name") or clinic.get("display_name"),
                clinic_phone=clinic.get("sms_phone") or clinic.get("phone"),
            )
        except Exception as e:
            logger.warning("_book_appointment_acuity reminder scheduling failed (non-fatal): %r", e)

        # Sheets log — non-fatal
        try:
            from app.tools.handoff import send_to_sheet
            await asyncio.to_thread(
                send_to_sheet,
                patient_name, phone, "BOOK",
                (
                    f"Booked: {service} at {location.title()} "
                    f"on {booking.start_time.strftime('%d %b %Y %H:%M')} "
                    f"(Acuity #{booking.provider_booking_id})"
                ),
                session.get("call_sid", ""),
                "Phase3 AI Receptionist",
            )
        except Exception as e:
            logger.warning("_book_appointment_acuity Sheets log failed (non-fatal): %r", e)

        return {
            "success": True,
            "acuity_booking_id": booking.provider_booking_id,
            "booked_slot": booking.start_time.strftime("%A %d %B at %H:%M"),
            "location": location.title(),
            "practitioner": booking.practitioner_name or "your practitioner",
        }

    except SlotUnavailable as e:
        logger.error(
            "[BOOKING FAILED] SlotUnavailable: location=%r service=%r slot=%r err=%r",
            args.get("location"), args.get("service"), args.get("slot_iso"), e,
        )
        return {
            "success": False,
            "error": "That slot has just been taken. Please call check_availability again for alternative times.",
        }
    except ProviderAuthError as e:
        logger.error(
            "[BOOKING FAILED] ProviderAuthError (Acuity credentials wrong or expired): %r", e,
        )
        await _alert_owner_acuity_book_failed(args, session, "booking system auth error")
        return {
            "success": False,
            "error": "Booking system authentication error. Please ask the caller to call the clinic directly.",
        }
    except Exception as e:
        logger.error(
            "[BOOKING FAILED] Unexpected error: location=%r service=%r slot=%r "
            "patient=%r phone=%r appointment_type_id=%r practitioner_id=%r err=%r",
            args.get("location"), args.get("service"), args.get("slot_iso"),
            args.get("patient_name"), args.get("phone"),
            session.get("_acuity_appointment_type_id"),
            session.get("_acuity_practitioner_id"),
            e, exc_info=True,
        )
        await _alert_owner_acuity_book_failed(args, session, str(e))
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Acuity executor: lookup_appointment
# ---------------------------------------------------------------------------

async def _lookup_appointment_acuity(
    args: Dict[str, Any], session: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Find the nearest future Acuity appointment matching first+last name AND phone.
    Strict: both name fields AND phone must match (unless Acuity record has no phone).
    Stores results in session so subsequent tools can act without re-searching.
    """
    adapter = _get_acuity_adapter()
    if not adapter:
        return {"found": False, "error": "Booking system not configured."}

    try:
        location = _normalize_location(
            args.get("location") or session.get("selected_location", "")
        )
        first_name   = (args.get("first_name") or "").strip().lower()
        last_name    = (args.get("last_name")  or "").strip().lower()
        phone_digits = "".join(c for c in (args.get("phone") or "") if c.isdigit())

        logger.info(
            "_lookup_appointment_acuity: first=%r last=%r raw_phone=%r normalized_last10=%s",
            first_name, last_name,
            args.get("phone"),
            phone_digits[-10:] if phone_digits else "(none)",
        )

        # Code-level guard: phone number is always required.
        # Name fields are optional — when both are empty the lookup runs in
        # phone-only mode (reschedule phone-first path).
        if not phone_digits:
            return {
                "found": False,
                "error": (
                    "Phone number is required. "
                    "Collect the caller's phone number before calling this tool."
                ),
            }
        # Phone-only mode: both name fields empty → skip name matching entirely.
        _phone_only_mode = not first_name and not last_name

        now = datetime.now(LONDON_TZ)
        end = (now + timedelta(days=365)).date()

        from app.clinic_config import THEOREM_LOCATIONS as _TL
        cal_id = _TL.get(location, {}).get("acuity_calendar_id") if location else None

        appointments = await adapter.list_appointments(
            min_date=now.date(), max_date=end, calendar_id=cal_id
        )

        logger.info(
            "_lookup_appointment_acuity: raw Acuity response — %d appointment(s) "
            "(cal_id=%r, location=%r, date_range=%s..%s)",
            len(appointments), cal_id, location,
            now.date().isoformat(), end.isoformat(),
        )

        import difflib as _dl

        future_matches = []
        _near_match_used = False
        for appt in appointments:
            dt_str = appt.get("datetime", "")
            if not dt_str:
                continue
            try:
                dt = datetime.fromisoformat(
                    dt_str.replace("Z", "+00:00")
                ).astimezone(LONDON_TZ)
                if dt <= now:          # strictly future — skip any slot from earlier today
                    continue
            except Exception:
                continue

            appt_first   = (appt.get("firstName") or "").strip().lower()
            appt_last    = (appt.get("lastName")  or "").strip().lower()
            appt_phone_d = "".join(c for c in (appt.get("phone") or "") if c.isdigit())

            # Both first AND last name must match (substring, either direction).
            # In phone-only mode (both name fields empty) name_match is unconditionally
            # True — the phone gate below is the sole discriminator.
            fn_sub = bool(first_name) and bool(appt_first) and (
                first_name in appt_first or appt_first in first_name
            )
            ln_sub = bool(last_name) and bool(appt_last) and (
                last_name in appt_last or appt_last in last_name
            )
            name_match = _phone_only_mode or (fn_sub and ln_sub)

            # ── FIX 1: phone gate must fire even when Acuity has NO phone stored.
            # Previously, no-phone records bypassed the gate entirely on name alone,
            # which let other patients' appointments into the candidate pool.
            # Now: if the caller provided a phone, an Acuity record with no phone
            # is treated as an uncertain match and rejected on the strict pass.
            # (The near-match pass below uses a lower bar — see comment there.)
            phone_ok_strict: bool
            if appt_phone_d:
                phone_ok_strict = bool(phone_digits) and (
                    phone_digits[-10:] == appt_phone_d[-10:]
                )
            else:
                # No phone in Acuity: reject on strict pass when caller gave a phone.
                # This prevents name-only ghosts from polluting future_matches.
                phone_ok_strict = not bool(phone_digits)

            fn_ratio = _dl.SequenceMatcher(None, first_name, appt_first).ratio()
            ln_ratio = _dl.SequenceMatcher(None, last_name,  appt_last).ratio()

            logger.info(
                "_lookup_appt STRICT candidate: id=%s dt=%s cal=%r "
                "appt_name=%r %r appt_phone=%r | "
                "fn_sub=%s ln_sub=%s name_match=%s | "
                "caller_phone_last10=%s appt_phone_last10=%s phone_ok=%s | "
                "fn_ratio=%.2f ln_ratio=%.2f | verdict=%s",
                appt.get("id"), dt.strftime("%Y-%m-%d %H:%M"),
                appt.get("calendar", appt.get("calendarID", "?")),
                appt.get("firstName"), appt.get("lastName"),
                appt.get("phone", "(none)"),
                fn_sub, ln_sub, name_match,
                phone_digits[-10:] if phone_digits else "(none)",
                appt_phone_d[-10:] if appt_phone_d else "(none)",
                phone_ok_strict,
                fn_ratio, ln_ratio,
                "ACCEPTED" if (name_match and phone_ok_strict) else "rejected",
            )

            if not name_match:
                continue
            if not phone_ok_strict:
                continue

            future_matches.append((dt, appt))

        if not future_matches:
            _near: list = []
            for appt in appointments:
                dt_str = appt.get("datetime", "")
                if not dt_str:
                    continue
                try:
                    dt = datetime.fromisoformat(
                        dt_str.replace("Z", "+00:00")
                    ).astimezone(LONDON_TZ)
                    if dt <= now:
                        continue
                except Exception:
                    continue
                appt_first   = (appt.get("firstName") or "").strip().lower()
                appt_last    = (appt.get("lastName")  or "").strip().lower()
                appt_phone_d = "".join(c for c in (appt.get("phone") or "") if c.isdigit())

                # Near-match phone gate: same strict check on phone when Acuity
                # has one stored; if Acuity has no phone, only allow if the caller
                # ALSO provided no phone (prevents name-only ghosts here too).
                if appt_phone_d:
                    phone_ok_near = bool(phone_digits) and (
                        phone_digits[-10:] == appt_phone_d[-10:]
                    )
                else:
                    phone_ok_near = not bool(phone_digits)

                fn_ratio = _dl.SequenceMatcher(None, first_name, appt_first).ratio()
                ln_ratio = _dl.SequenceMatcher(None, last_name,  appt_last).ratio()
                combined  = fn_ratio + ln_ratio   # used for ranking (FIX 2)

                logger.info(
                    "_lookup_appt NEAR candidate: id=%s dt=%s cal=%r "
                    "appt_name=%r %r appt_phone=%r | "
                    "fn_ratio=%.2f ln_ratio=%.2f combined=%.2f | "
                    "phone_ok=%s | verdict=%s",
                    appt.get("id"), dt.strftime("%Y-%m-%d %H:%M"),
                    appt.get("calendar", appt.get("calendarID", "?")),
                    appt.get("firstName"), appt.get("lastName"),
                    appt.get("phone", "(none)"),
                    fn_ratio, ln_ratio, combined,
                    phone_ok_near,
                    "ACCEPTED" if (fn_ratio >= 0.65 and ln_ratio >= 0.65 and phone_ok_near)
                    else "rejected",
                )

                if not phone_ok_near:
                    continue
                if fn_ratio >= 0.65 and ln_ratio >= 0.65:
                    # FIX 2: store combined score alongside (dt, appt) for ranking
                    _near.append((combined, dt, appt))

            if not _near:
                logger.info(
                    "_lookup_appointment_acuity: no future match (incl. near-match) for %r %r",
                    first_name, last_name,
                )
                session["rc_lookup_failed"] = True
                return {"found": False, "error": "No future appointment found under those details."}

            # ── PART 1: STRONG candidate tier ────────────────────────────────────
            # When phone is exact AND first name is very strong (≥0.90) AND surname
            # is close enough for STT/spelling drift (≥0.70), treat as STRONG.
            # This catches Rock/Rook/Roch variants with exact phone + exact first name
            # without broadly loosening matching for everyone.
            _strong_list: list = []
            for _sc, _sdt, _sappt in _near:
                _sf = (_sappt.get("firstName") or "").strip().lower()
                _sl = (_sappt.get("lastName")  or "").strip().lower()
                _sfn = _dl.SequenceMatcher(None, first_name, _sf).ratio()
                _sln = _dl.SequenceMatcher(None, last_name,  _sl).ratio()
                if _sfn >= 0.90 and _sln >= 0.70:
                    _strong_list.append((_sc, _sfn, _sln, _sdt, _sappt))
                    logger.info(
                        "_lookup_appt STRONG candidate: id=%s dt=%s fn_ratio=%.2f ln_ratio=%.2f "
                        "combined=%.2f — qualifies for strong tier",
                        _sappt.get("id"), _sdt.strftime("%Y-%m-%d %H:%M"), _sfn, _sln, _sc,
                    )

            if _strong_list:
                _strong_list.sort(key=lambda x: (-x[0], x[3]))  # combined desc, nearest date as tiebreaker
                _top_score = _strong_list[0][0]
                _sec_score = _strong_list[1][0] if len(_strong_list) >= 2 else -1.0

                if len(_strong_list) >= 2 and (_top_score - _sec_score) < 0.05:
                    # ── Genuinely tied STRONG candidates: preserve ambiguity ────
                    # Do NOT commit a "best" guess — return structured ambiguity so
                    # the flow can ask a deterministic disambiguation question.
                    _cands = [
                        {
                            "id":         str(a["id"]),
                            "datetime":   d.isoformat(),
                            "day_label":  f"{d.strftime('%A')} {_ordinal(d.day)} {d.strftime('%B')}",
                            "time_label": d.strftime("%H:%M"),
                            "first_name": a.get("firstName", ""),
                            "last_name":  a.get("lastName", ""),
                            "type":       a.get("type", "appointment"),
                        }
                        for _, _, _, d, a in _strong_list[:3]
                    ]
                    session["lookup_candidates"] = _cands
                    session.pop("rc_stage", None)
                    session.pop("rc_lookup_failed", None)
                    session.pop("rc_lookup_just_succeeded", None)  # not a clean single match
                    logger.info(
                        "_lookup_appointment_acuity: STRONG ambiguous — %d tied candidates "
                        "(scores %.2f / %.2f) — returning ambiguous for deterministic disambiguation",
                        len(_cands), _top_score, _sec_score,
                    )
                    return {
                        "found":             True,
                        "ambiguous":         True,
                        "lookup_candidates": _cands,
                        "near_match":        True,
                    }
                else:
                    # ── Single clear STRONG winner ────────────────────────────────
                    _, _, _, best_dt, best_appt = _strong_list[0]
                    raw_type_id = best_appt.get("typeID")
                    if raw_type_id:
                        session["reschedule_original_type_id"] = f"acuity_{raw_type_id}"
                    session["reschedule_appt_id"]       = str(best_appt["id"])
                    session["reschedule_appt_datetime"] = best_dt.isoformat()
                    session["reschedule_appt_type"]     = best_appt.get("type", "appointment")
                    session["lookup_appt_first_name"]   = best_appt.get("firstName", "")
                    session["lookup_appt_last_name"]    = best_appt.get("lastName", "")
                    session["rc_stage"]                 = "lookup_done"
                    session.pop("rc_appointment_confirmed", None)
                    session.pop("rc_lookup_failed", None)
                    _alts = [
                        {
                            "id":         str(a["id"]),
                            "datetime":   d.isoformat(),
                            "day_label":  f"{d.strftime('%A')} {_ordinal(d.day)} {d.strftime('%B')}",
                            "time_label": d.strftime("%H:%M"),
                            "first_name": a.get("firstName", ""),
                            "last_name":  a.get("lastName", ""),
                            "type":       a.get("type", "appointment"),
                        }
                        for _, _, _, d, a in _strong_list[1:3]
                    ]
                    if _alts:
                        session["reschedule_appt_alternatives"] = _alts
                    _day_label  = f"{best_dt.strftime('%A')} {_ordinal(best_dt.day)} {best_dt.strftime('%B')}"
                    _time_label = best_dt.strftime("%H:%M")
                    # Store for deterministic post-LLM confirmation in flow.py
                    session["reschedule_appt_day_label"]  = _day_label
                    session["reschedule_appt_time_label"] = _time_label
                    session["rc_lookup_just_succeeded"]   = True
                    logger.info(
                        "_lookup_appointment_acuity: STRONG single winner — id=%s at %s",
                        best_appt["id"], best_dt.isoformat(),
                    )
                    return {
                        "found":            True,
                        "appointment_id":   str(best_appt["id"]),
                        "day_label":        _day_label,
                        "time_label":       _time_label,
                        "appointment_type": best_appt.get("type", "appointment"),
                        "multiple_found":   False,
                        "alternatives":     _alts,
                        "near_match":       True,
                    }

            # No STRONG candidates — fall through to existing near-multi/single logic
            if len(_near) >= 2:
                _near.sort(key=lambda x: (-x[0], x[1]))  # best score first, nearest date as tiebreaker
                _combined, best_dt, best_appt = _near[0]
                raw_type_id = best_appt.get("typeID")
                if raw_type_id:
                    session["reschedule_original_type_id"] = f"acuity_{raw_type_id}"
                session["reschedule_appt_id"]       = str(best_appt["id"])
                session["reschedule_appt_datetime"] = best_dt.isoformat()
                session["reschedule_appt_type"]     = best_appt.get("type", "appointment")
                session["lookup_appt_first_name"]   = best_appt.get("firstName", "")
                session["lookup_appt_last_name"]    = best_appt.get("lastName", "")
                session["rc_stage"]                 = "lookup_done"
                session.pop("rc_appointment_confirmed", None)
                session.pop("rc_lookup_failed", None)
                _alts = [
                    {
                        "id":         str(a["id"]),
                        "datetime":   d.isoformat(),
                        "day_label":  f"{d.strftime('%A')} {_ordinal(d.day)} {d.strftime('%B')}",
                        "time_label": d.strftime("%H:%M"),
                        "first_name": a.get("firstName", ""),
                        "last_name":  a.get("lastName", ""),
                        "type":       a.get("type", "appointment"),
                    }
                    for _, d, a in _near[1:3]
                ]
                session["reschedule_appt_alternatives"] = _alts
                day_label  = f"{best_dt.strftime('%A')} {_ordinal(best_dt.day)} {best_dt.strftime('%B')}"
                time_label = best_dt.strftime("%H:%M")
                session["reschedule_appt_day_label"]  = day_label
                session["reschedule_appt_time_label"] = time_label
                session["rc_lookup_just_succeeded"]   = True
                logger.info(
                    "_lookup_appointment_acuity: multiple near-matches, best id=%s at %s "
                    "(rc_stage=lookup_done set, %d alternatives stored)",
                    best_appt["id"], best_dt.isoformat(), len(_alts),
                )
                return {
                    "found":            True,
                    "appointment_id":   str(best_appt["id"]),
                    "day_label":        day_label,
                    "time_label":       time_label,
                    "appointment_type": best_appt.get("type", "appointment"),
                    "multiple_found":   True,
                    "alternatives":     _alts,
                    "near_match":       True,
                }

            # Exactly one near-match — convert back to (dt, appt) 2-tuples
            future_matches = [(dt, appt) for _, dt, appt in _near]
            _near_match_used = True
            logger.info(
                "_lookup_appointment_acuity: near-match found for %r %r", first_name, last_name
            )

        # Phone-only mode: when caller provided no name, all phone-matching
        # appointments pass the name gate unconditionally.  Filter out obvious
        # placeholder rows ("That That", "Test Test", single-token junk) so a
        # real booking always wins over a junk row with an earlier date.
        if _phone_only_mode:
            _JUNK_WORDS = {
                "test", "that", "this", "unknown", "none", "na", "tbd",
                "xxx", "dummy", "user", "patient", "booking",
            }

            def _is_junk(appt: dict) -> bool:
                _af = (appt.get("firstName") or "").strip().lower()
                _al = (appt.get("lastName")  or "").strip().lower()
                if not _af and not _al:
                    return True   # completely blank name record
                if _af == _al and _af:
                    return True   # same word repeated: "That That", "Test Test"
                if _af in _JUNK_WORDS or _al in _JUNK_WORDS:
                    return True
                # Single-character names are likely placeholders
                if len(_af) <= 1 or len(_al) <= 1:
                    return True
                return False

            _legit = [(dt, a) for dt, a in future_matches if not _is_junk(a)]
            if _legit:
                _before = len(future_matches)
                future_matches = _legit
                logger.info(
                    "_lookup_appt phone-only: junk filter reduced %d matches → %d legitimate",
                    _before, len(_legit),
                )
            else:
                # All matches are junk names — keep them (might be a real patient
                # with a badly-entered name) but flag so confirmation avoids the name.
                session["lookup_appt_name_unreliable"] = True
                logger.info(
                    "_lookup_appt phone-only: all %d matches have junk names — "
                    "keeping nearest, marking name unreliable",
                    len(future_matches),
                )

            # When multiple legitimate matches remain after junk filter, check
            # if they are genuinely different appointments or the same person with
            # multiple bookings.  In both cases pick nearest (the correct target for
            # reschedule), but store candidates so disambiguation can ask if needed.
            if len(future_matches) > 1:
                _lu_cands_po = [
                    {
                        "id":         str(a["id"]),
                        "datetime":   d.isoformat(),
                        "day_label":  f"{d.strftime('%A')} {_ordinal(d.day)} {d.strftime('%B')}",
                        "time_label": d.strftime("%H:%M"),
                        "first_name": a.get("firstName", ""),
                        "last_name":  a.get("lastName", ""),
                        "type":       a.get("type", "appointment"),
                    }
                    for d, a in future_matches[:3]
                ]
                session["lookup_phone_only_candidates"] = _lu_cands_po
                logger.info(
                    "_lookup_appt phone-only: %d matches after junk filter — "
                    "candidates stored for possible disambiguation",
                    len(future_matches),
                )

        # Nearest first
        future_matches.sort(key=lambda x: x[0])
        best_dt, best_appt = future_matches[0]

        # Preserve original appointment type for reschedule
        raw_type_id = best_appt.get("typeID")
        if raw_type_id:
            session["reschedule_original_type_id"] = f"acuity_{raw_type_id}"

        # Store for cancel/reschedule fast-path
        session["reschedule_appt_id"]       = str(best_appt["id"])
        session["reschedule_appt_datetime"] = best_dt.isoformat()
        session["reschedule_appt_type"]     = best_appt.get("type", "appointment")
        session["lookup_appt_first_name"]   = best_appt.get("firstName", "")
        session["lookup_appt_last_name"]    = best_appt.get("lastName", "")
        session["rc_stage"]                 = "lookup_done"
        # Clear any leftover confirmed/failure flags from a previous lookup in this session
        session.pop("rc_appointment_confirmed", None)
        session.pop("rc_lookup_failed", None)

        day_label  = f"{best_dt.strftime('%A')} {_ordinal(best_dt.day)} {best_dt.strftime('%B')}"
        time_label = best_dt.strftime("%H:%M")
        session["reschedule_appt_day_label"]  = day_label
        session["reschedule_appt_time_label"] = time_label
        session["rc_lookup_just_succeeded"]   = True

        # Up to 2 alternatives for disambiguation
        alternatives = []
        for alt_dt, alt_appt in future_matches[1:3]:
            alternatives.append({
                "id":         str(alt_appt["id"]),
                "datetime":   alt_dt.isoformat(),
                "day_label":  f"{alt_dt.strftime('%A')} {_ordinal(alt_dt.day)} {alt_dt.strftime('%B')}",
                "time_label": alt_dt.strftime("%H:%M"),
                "first_name": alt_appt.get("firstName", ""),
                "last_name":  alt_appt.get("lastName", ""),
                "type":       alt_appt.get("type", "appointment"),
            })
        if alternatives:
            session["reschedule_appt_alternatives"] = alternatives

        logger.info(
            "_lookup_appointment_acuity: found id=%s at %s (total matches=%d)",
            best_appt["id"], best_dt.isoformat(), len(future_matches),
        )
        return {
            "found":            True,
            "appointment_id":   str(best_appt["id"]),
            "day_label":        day_label,
            "time_label":       time_label,
            "appointment_type": best_appt.get("type", "appointment"),
            "multiple_found":   len(future_matches) > 1,
            "alternatives":     alternatives,
            "near_match":       _near_match_used,
        }

    except Exception as e:
        logger.error("_lookup_appointment_acuity error: %r", e)
        return {"found": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Name sanitisation helper (used by cancel + reschedule SMS paths)
# ---------------------------------------------------------------------------

_JUNK_NAMES = {
    "none", "unknown", "null", "n/a", "na", "undefined", "test",
    "that", "this", "tbd", "xxx", "dummy", "user", "patient", "booking",
}


def _safe_first_name(session: Dict[str, Any], fallback: str = "") -> str:
    """
    Derive a safe displayable first name for SMS/speech use.
    Priority: lookup_appt_first_name (from Acuity) → fallback (first token of full_name).
    Returns "" when no valid name can be found — callers should use "there" as fallback.
    Also returns "" when the name is flagged as unreliable (e.g. all-junk phone-only lookup).
    """
    if session.get("lookup_appt_name_unreliable"):
        return ""
    for raw in (session.get("lookup_appt_first_name"), fallback):
        if not raw:
            continue
        token = str(raw).strip().split()[0]
        if token and token.lower() not in _JUNK_NAMES and len(token) >= 2:
            return token
    return ""


# ---------------------------------------------------------------------------
# Acuity executor: cancel_appointment
# ---------------------------------------------------------------------------

async def _cancel_reminders_for(
    session: Dict[str, Any], args: Dict[str, Any], appt_time_str: str
) -> None:
    """
    Remove pending reminder SMS for a cancelled appointment instant. Non-fatal.
    Resolves the caller's phone from args/session and matches reminders by the
    appointment instant, so a reschedule's NEW reminders are never touched.
    """
    if not appt_time_str:
        return
    try:
        from app.notifications.scheduler import cancel_reminders_for_appointment
        phone = (
            args.get("phone")
            or (session.get("collected", {}) or {}).get("phone")
            or session.get("twilio_from_local")
            or session.get("twilio_from")
            or ""
        )
        when = datetime.fromisoformat(appt_time_str.replace("Z", "+00:00"))
        await cancel_reminders_for_appointment(patient_phone=phone, appointment_time=when)
    except Exception as e:
        logger.warning("reminder cancellation failed (non-fatal): %r", e)


async def _alert_owner_acuity_cancelled(
    args: Dict[str, Any], session: Dict[str, Any], appt_type: str, appt_time_str: str
) -> None:
    """
    Owner heads-up that an appointment was cancelled on the Acuity path.

    The equivalent alert existed only on the Google-Calendar path, whose own
    comment said "JV only: Theorem routes to the Acuity path above" — accurate,
    and the reason a Theorem cancellation reached nobody. A slot freeing up is
    something the clinic wants to know while it is still fillable.

    Suppressed during a reschedule (`_suppress_sms`) for the same reason the
    patient SMS is: the reschedule sends its own single alert, so the owner is
    not buzzed twice for one appointment moving.
    """
    if args.get("_suppress_sms"):
        return
    _when = appt_time_str or ""
    if _when:
        try:
            _when = datetime.fromisoformat(
                _when.replace("Z", "+00:00")
            ).strftime("%A %d %B at %H:%M")
        except (ValueError, TypeError):
            pass  # unparseable — send the raw string rather than nothing
    from app.notifications.owner_alert import notify_owner
    await notify_owner(
        session,
        event="cancellation",
        patient_name=(args.get("patient_name") or session.get("reschedule_appt_name") or ""),
        when_label=_when,
        service=appt_type or "",
    )


async def _cancel_appointment_acuity(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """cancel_appointment via Acuity Scheduling (Theorem clinic)."""
    from datetime import date as _date

    adapter = _get_acuity_adapter()
    if not adapter:
        return {"success": False, "error": "Booking system not configured."}

    try:
        _cancel_location = _normalize_location(
            args.get("location") or session.get("selected_location", "")
        )
        if session.get("twilio_to") in ("+447366530580", "+447380841468") and not _cancel_location:
            return {
                "success": False,
                "error": (
                    "Location is required before cancelling. "
                    "Ask: 'Which clinic is your appointment at — say one for Alcester or two for Redditch?' "
                    "Call collect_and_store(field='location', ...) first, then retry cancel_appointment."
                ),
            }

        # RC flow fast-path: if lookup_appointment already ran, use the cached appointment ID directly
        cached_appt_id = session.get("reschedule_appt_id")
        if cached_appt_id:
            # Confirmation gate — caller must have said yes before we act
            if not session.get("rc_appointment_confirmed"):
                return {
                    "success": False,
                    "error": (
                        "The appointment must be confirmed with the caller before cancelling. "
                        "Ask the caller to confirm the appointment, then call confirm_appointment_found()."
                    ),
                }
            appt_time_str = session.get("reschedule_appt_datetime", "")
            appt_type     = session.get("reschedule_appt_type", "appointment")
            # Clear RC session state
            session.pop("reschedule_appt_id", None)
            session.pop("reschedule_appt_alternatives", None)
            session.pop("rc_appointment_confirmed", None)
            session.pop("rc_stage", None)

            success = await adapter.cancel_booking(cached_appt_id)
            if not success:
                return {"success": False, "error": "Cancellation failed. Please ask the caller to call the clinic directly."}
            session["calendar_status"] = "cancelled"
            if not args.get("_suppress_sms"):
                try:
                    from app.notifications.booking_sms import send_cancellation_confirmation
                    if appt_time_str:
                        dt = datetime.fromisoformat(appt_time_str)
                        await send_cancellation_confirmation(
                            patient_phone=args.get("phone", ""),
                            patient_name=_safe_first_name(session, args.get("patient_name") or ""),
                            appointment_time=dt,
                        )
                except Exception as e:
                    logger.warning("_cancel_appointment_acuity SMS (cached path) failed (non-fatal): %r", e)
                session["confirmation_sms_sent"] = True
            session["cancellation_completed"] = True
            session["cancel_confirmed"] = True
            logger.info(
                "_cancel_appointment_acuity (fast-path): cancellation_completed=True"
                " cancelled=%r was_at=%r",
                appt_type, appt_time_str,
            )
            await _cancel_reminders_for(session, args, appt_time_str)
            await _alert_owner_acuity_cancelled(args, session, appt_type, appt_time_str)
            return {"success": True, "cancelled": appt_type, "was_at": appt_time_str}
        # End RC fast-path — fall through to legacy name-search below

        # Exact-ID path: cancel the EXACT appointment when its id is known —
        # passed explicitly by reschedule, or stored in session by a preceding
        # lookup_patient.  The name-search fallback below can match the WRONG
        # appointment: during reschedule the new booking is created first (under
        # the same/placeholder name), so the search matched that just-created
        # NEW booking and cancelled it instead of the original (2026-06-19
        # data-integrity bug).  Prefer the id whenever we have one.
        _explicit_appt_id = (
            str(args.get("appointment_id") or "").strip()
            or str(session.get("_lookup_appointment_id") or "").strip()
        )
        if _explicit_appt_id:
            _ok = await adapter.cancel_booking(_explicit_appt_id)
            if not _ok:
                return {"success": False, "error": "Cancellation failed. Please ask the caller to call the clinic directly."}
            _appt_time_str = (
                session.get("_lookup_appointment_datetime", "")
                or session.get("reschedule_appt_datetime", "")
            )
            _appt_type = session.get("_lookup_appointment_type", "") or "appointment"
            session["calendar_status"] = "cancelled"
            session.pop("_lookup_appointment_id", None)
            if not args.get("_suppress_sms"):
                try:
                    from app.notifications.booking_sms import send_cancellation_confirmation
                    if _appt_time_str:
                        _dt = datetime.fromisoformat(_appt_time_str.replace("Z", "+00:00"))
                        await send_cancellation_confirmation(
                            patient_phone=args.get("phone", ""),
                            patient_name=_safe_first_name(session, args.get("patient_name") or ""),
                            appointment_time=_dt,
                        )
                except Exception as e:
                    logger.warning("_cancel_appointment_acuity SMS (exact-id) failed (non-fatal): %r", e)
                session["confirmation_sms_sent"] = True
            session["cancellation_completed"] = True
            session["cancel_confirmed"] = True
            logger.info(
                "_cancel_appointment_acuity (exact-id): cancelled id=%r was_at=%r",
                _explicit_appt_id, _appt_time_str,
            )
            await _cancel_reminders_for(session, args, _appt_time_str)
            await _alert_owner_acuity_cancelled(args, session, _appt_type, _appt_time_str)
            return {"success": True, "cancelled": _appt_type, "was_at": _appt_time_str}

        patient_name_lower = (args.get("patient_name") or "").strip().lower()
        today = datetime.now(LONDON_TZ).date()
        end = today + timedelta(days=60)

        from app.clinic_config import THEOREM_LOCATIONS as _TL
        _cal_id = (
            _TL.get(_cancel_location, {}).get("acuity_calendar_id")
            if _cancel_location else None
        )
        appointments = await adapter.list_appointments(min_date=today, max_date=end, calendar_id=_cal_id)

        found = None
        for appt in appointments:
            full = f"{appt.get('firstName', '')} {appt.get('lastName', '')}".strip().lower()
            if patient_name_lower and patient_name_lower in full:
                found = appt
                break

        if not found:
            return {
                "success": False,
                "error": "No upcoming appointment found for that name. Please check the name and try again.",
            }

        provider_id = str(found["id"])
        appt_time_str = found.get("datetime", "")
        appt_type = found.get("type", "appointment")

        success = await adapter.cancel_booking(provider_id)

        if not success:
            return {"success": False, "error": "Cancellation failed. Please ask the caller to call the clinic directly."}

        session["calendar_status"] = "cancelled"

        # SMS confirmation — non-fatal.
        # Suppressed when called as part of a reschedule (to avoid sending a cancel
        # SMS alongside the reschedule confirmation that the caller will also receive).
        if not args.get("_suppress_sms"):
            try:
                from app.notifications.booking_sms import send_cancellation_confirmation
                if appt_time_str:
                    dt = datetime.fromisoformat(appt_time_str.replace("Z", "+00:00"))
                    await send_cancellation_confirmation(
                        patient_phone=args.get("phone", ""),
                        patient_name=_safe_first_name(session, args.get("patient_name") or ""),
                        appointment_time=dt,
                    )
            except Exception as e:
                logger.warning("_cancel_appointment_acuity SMS failed (non-fatal): %r", e)
            session["confirmation_sms_sent"] = True

        session["cancellation_completed"] = True
        session["cancel_confirmed"] = True
        logger.info(
            "_cancel_appointment_acuity: cancellation_completed=True"
            " cancelled=%r was_at=%r",
            appt_type, appt_time_str,
        )
        await _cancel_reminders_for(session, args, appt_time_str)
        await _alert_owner_acuity_cancelled(args, session, appt_type, appt_time_str)
        return {
            "success": True,
            "cancelled": appt_type,
            "was_at": appt_time_str,
        }

    except Exception as e:
        logger.error("_cancel_appointment_acuity error: %r", e)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Acuity executor: reschedule_appointment
# ---------------------------------------------------------------------------

_PLACEHOLDER_NAMES = {
    "", "unknown", "unknown unknown", "the caller", "caller", "patient",
    "the patient", "guest", "n/a", "na", "none",
}


def _phone_key(p: str) -> str:
    """Reduce a phone number to its UK 'core' (no +44 / leading 0 / spaces) so
    local and E.164 forms compare equal. '07502211207' and '+447502211207'
    both -> '7502211207'. Returns '' for empty/garbage."""
    import re as _re
    d = _re.sub(r"\D", "", p or "")
    if d.startswith("44"):
        d = d[2:]
    d = d.lstrip("0")
    return d


def _reconcile_booking_phone(
    args: Dict[str, Any], session: Dict[str, Any]
) -> Tuple[Optional[str], str]:
    """Compare the model's `phone` argument against the number actually
    confirmed on this call.

    Returns ``(corrected_number, reason)``. ``corrected_number`` is the
    confirmed number when it differs from the argument and the booking should
    use it instead; ``None`` when there is nothing to change. ``reason`` is a
    short tag for the caller to log ("match", "mismatch", "no_reference").

    Pure — no session or args mutation — so the A3 gate is testable without
    executing a booking.

    `phone` is the only identity field on a booking that is model-authored;
    every other one is machine-captured. On CA3590527b (1 Aug 2026) the caller
    typed nine digits, the keypad commit refused them as too short, and the
    model padded them with "00" into a plausible-looking UK mobile —
    07987124700, a number that appears in no DTMF frame and no transcript. The
    booking succeeded and two reminders were scheduled to it. The A1 gate
    cannot catch this: it asks whether *a* number was confirmed, not whether
    *this* number is that one.

    Every path that sets phone_confirmed also writes collected["phone"]
    (connection.py x3, flow.py x2) in either local or E.164 form; _phone_key
    folds both to the same core, so a formatting difference never registers as
    a mismatch.
    """
    _ref = (
        _gate_text((session.get("collected") or {}).get("phone"))
        or _gate_text(session.get("phone_number"))
    )
    _ref_key = _phone_key(_ref)
    if not _ref_key:
        return None, "no_reference"
    if _phone_key(_gate_text(args.get("phone"))) == _ref_key:
        return None, "match"
    return _ref, "mismatch"


def _sync_booked_patient_name(session: Dict[str, Any], patient_name: str) -> bool:
    """Write the booked name to BOTH records session keeps of it.

    The name lives in two places — `collected["name"]` and the top-level
    `patient_name` — and consumers disagree about which to read.
    `actionable_summary.py:229` checks `session["patient_name"]` FIRST and only
    falls back to `collected["name"]`, so once the two drift the call-summary
    row names the wrong patient.

    They drifted on `CA74b20e5dff8d3b4734e5a0be016b537f` (3 Aug 2026, build
    `914cda38cf9f`). STT heard "rook" for Roch; the A3 read-back said it aloud;
    the caller corrected it; `book_appointment` wrote **Quentin Roch** to the
    calendar. But the read-back upgrade at `connection.py:10376` had already
    latched `session["patient_name"] = "Quentin Rook"` and, by design, refuses
    to overwrite a name that already carries a surname. Calendar said Roch, the
    row said Rook.

    Aligning session with the name that actually reached the calendar closes
    that divergence for **every** cause, and does it downstream of all the write
    gates rather than adding another guess about which speaker to believe. It
    does NOT unlatch the read-back ratchet — see `A3` in `REGISTER_B_U.md` for
    why that guard is deliberately left alone.

    Every booking executor must route through this. There are four, and all
    four had the same bare assignment: `_exec_book_appointment` (two branches —
    Google Calendar and manual-followup), `_book_appointment_acuity`, and
    `_book_appointment_provisional`. The provisional path is Vital Edge's, where
    the appointment is not confirmed until the practitioner accepts, so the
    summary row is the only record of who asked.

    The asymmetry is deliberate. `collected["name"]` is assigned
    unconditionally because that is the pre-existing behaviour at all four call
    sites and this fix is not authorised to change it. `patient_name` is guarded
    on truthiness because writing an empty string there would be a NEW way to
    lose a name that every reader preferring that key would then miss.

    Returns True when the top-level key was written.
    """
    session.setdefault("collected", {})["name"] = patient_name
    if patient_name:
        session["patient_name"] = patient_name
        return True
    return False


def _is_placeholder_name(n: str) -> bool:
    """True when a name is a placeholder/non-name and must not reach Acuity."""
    n = (n or "").strip().lower()
    if n in _PLACEHOLDER_NAMES:
        return True
    parts = n.split()
    # "unknown unknown" / first==last placeholder forms.
    if len(parts) >= 2 and len(set(parts)) == 1:
        return True
    return False


async def _reschedule_appointment_acuity(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reschedule via Acuity: book new slot FIRST, then cancel old.
    Booking first ensures the original appointment is never destroyed if the new slot fails.
    """
    if session.get("twilio_to") in ("+447366530580", "+447380841468"):
        _early_location = _normalize_location(
            args.get("location") or session.get("selected_location", "")
        )
        if not _early_location:
            return {
                "success": False,
                "error": (
                    "Location is required before rescheduling. "
                    "Ask: 'Which clinic is your appointment at — Alcester or Redditch?' "
                    "Call collect_and_store(field='location', value='alcester' or 'redditch') first."
                ),
            }

    # Confirmation gate — if RC flow was used, require explicit caller confirmation
    if session.get("reschedule_appt_id") and not session.get("rc_appointment_confirmed"):
        return {
            "success": False,
            "error": (
                "The appointment must be confirmed with the caller before rescheduling. "
                "Call confirm_appointment_found() first."
            ),
        }

    # Inject original appointment type so _book_appointment_acuity uses the correct Acuity type
    original_type_id = session.get("reschedule_original_type_id")
    if original_type_id and not session.get("_acuity_appointment_type_id"):
        session["_acuity_appointment_type_id"] = original_type_id
        logger.info(
            "_reschedule_appointment_acuity: injecting original type_id=%s", original_type_id
        )

    # Capture the ORIGINAL appointment id BEFORE booking the new slot.  STEP 1
    # creates a new appointment and overwrites session["acuity_booking_id"], so
    # STEP 2 must cancel this captured id EXPLICITLY — never by name search,
    # which matched the just-created new booking and cancelled it instead of the
    # original (2026-06-19 data-integrity bug).
    _orig_appt_id = (
        str(session.get("reschedule_appt_id") or "").strip()
        or str(session.get("_lookup_appointment_id") or "").strip()
    )

    # STEP 1: Book the new slot FIRST — original appointment untouched until this succeeds.
    # Carry the looked-up patient name onto the new booking. The LLM sometimes
    # passes a placeholder ("unknown") several turns after the lookup, which would
    # otherwise create the rescheduled appointment with no real name AND trip the
    # name-chase SMS. Resolution order: looked-up name → a real arg name → cached
    # reschedule name → safe label. Never let a placeholder through.
    _lookup_nm = (session.get("_lookup_patient_name") or "").strip()
    _arg_nm    = (args.get("patient_name") or "").strip()
    if _lookup_nm and not _is_placeholder_name(_lookup_nm):
        _resched_name = _lookup_nm
    elif _arg_nm and not _is_placeholder_name(_arg_nm):
        _resched_name = _arg_nm
    else:
        _resched_name = (session.get("reschedule_appt_name") or "").strip() or "Patient"
        logger.warning(
            "_reschedule: no real patient name (lookup=%r arg=%r) — booked as %r",
            _lookup_nm, _arg_nm, _resched_name,
        )
    logger.info(
        "_reschedule: new-booking name=%r (lookup=%r arg=%r)",
        _resched_name, _lookup_nm, _arg_nm,
    )
    book_args = {**args, "patient_name": _resched_name, "slot_iso": args["new_slot_iso"], "_suppress_sms": True}
    book_result = await _book_appointment_acuity(book_args, session)

    if not book_result.get("success"):
        return {
            "success": False,
            "error": (
                f"Could not secure the new slot: {book_result.get('error')} "
                "Your original appointment is still active — please try a different time."
            ),
        }

    # STEP 2: Cancel the ORIGINAL appointment by its exact id (only now that the
    # new slot is confirmed).  Pass the captured id so the cancel can never
    # target the new booking.
    cancel_result = await _cancel_appointment_acuity(
        {**args, "_suppress_sms": True, "appointment_id": _orig_appt_id}, session
    )
    if not cancel_result.get("success"):
        # New booking succeeded but cancel failed — log for clinic to manually clean up
        logger.warning(
            "_reschedule_appointment_acuity: new booking succeeded (id=%s) but old cancel failed: %s "
            "— clinic must manually remove duplicate appointment",
            book_result.get("acuity_booking_id"), cancel_result.get("error"),
        )

    session["calendar_status"] = "rescheduled"

    # STEP 3: Single reschedule confirmation SMS
    try:
        from app.notifications.booking_sms import send_reschedule_confirmation
        location     = _normalize_location(args.get("location") or session.get("selected_location", ""))
        old_time_str = (
            cancel_result.get("was_at")
            if cancel_result.get("success")
            else session.get("reschedule_appt_datetime", "")
        )
        new_time = _resolve_slot_iso(args["new_slot_iso"], session)
        if old_time_str:
            old_time = datetime.fromisoformat(old_time_str.replace("Z", "+00:00"))
            await send_reschedule_confirmation(
                patient_phone=args.get("phone", ""),
                patient_name=_safe_first_name(session, _resched_name),
                old_time=old_time,
                new_time=new_time,
                location=location.title(),
            )
    except Exception as e:
        logger.warning("_reschedule_appointment_acuity SMS failed (non-fatal): %r", e)

    session["confirmation_sms_sent"] = True

    # STEP 4: single owner heads-up. The Google-Calendar path has had this since
    # JV; Theorem short-circuits to the Acuity executors above it, so a Theorem
    # reschedule alerted nobody. Exactly ONE alert fires for the whole move: the
    # inner book and cancel both carry `_suppress_sms`, which gates their own
    # owner alerts, so there is no double-buzz. Never fatal — notify_owner
    # swallows its own errors, and a failed alert must not fail the reschedule.
    from app.notifications.owner_alert import notify_owner as _notify_owner_alert
    await _notify_owner_alert(
        session,
        event="reschedule",
        patient_name=_resched_name,
        when_label=book_result.get("booked_slot") or "",
        service=session.get("reschedule_appt_type", "") or "",
        location=_normalize_location(
            args.get("location") or session.get("selected_location", "")
        ),
    )

    return {
        "success":           True,
        "rescheduled_to":    book_result.get("booked_slot"),
        "location":          book_result.get("location"),
        "acuity_booking_id": book_result.get("acuity_booking_id"),
    }


# ===========================================================================
# GOOGLE CALENDAR — original executors (demo clinic + fallback)
# ===========================================================================

# ---------------------------------------------------------------------------
# Executor: check_availability
# ---------------------------------------------------------------------------

def _gate_text(value: Any) -> str:
    """Stripped string, or "" for anything that is not usable text.

    Session slots are written by many paths and are not always strings (None,
    a bool, a dict from a partial capture). Gates must treat those as absent
    rather than raise.
    """
    return value.strip() if isinstance(value, str) else ""


def _resolve_clinic_id(session: Dict[str, Any]) -> str:
    """
    Return the clinic_id for this session, re-deriving it if missing.

    Defensive helper — clinic_id should always be set by connection.py but
    in case the resolution chain failed (Twilio customParameters unreliable,
    Redis miss, env var not set), attempt to recover from session["twilio_to"]
    before falling back to "demo".
    """
    cid = session.get("clinic_id")
    if cid:
        return cid
    twilio_to = session.get("twilio_to", "")
    if twilio_to:
        from app.clinic_config import clinic_id_from_twilio_to
        cid = clinic_id_from_twilio_to(twilio_to)
        session["clinic_id"] = cid
        logger.warning(
            "_resolve_clinic_id: re-derived clinic_id=%s from twilio_to=%s",
            cid, twilio_to,
        )
        return cid
    logger.error(
        "_resolve_clinic_id: clinic_id missing and twilio_to empty — "
        "defaulting to 'demo'. Tools will use Google Calendar.",
    )
    return "demo"


_VALID_SERVICES: frozenset[str] = frozenset({"physiotherapy assessment"})


_MAX_PRESENTED_DAYS = 2


def _cap_presented_slots(
    result: Dict[str, Any], max_days: int = _MAX_PRESENTED_DAYS
) -> Dict[str, Any]:
    """Trim the SPOKEN availability list to ~2 options. Never trim bookable data.

    Speak via first_day (single_day) or presented_days (multi_day), with
    more_times=True when truncated. available_days stays the FULL bookable set
    so unspoken follow-up (slot_followup) and _resolve_slot_iso still see every
    real time. Does not touch session["available_days"]. Input is copied.
    """
    days = result.get("available_days")
    if not isinstance(days, list) or not days:
        return result

    kept = days[:max_days]
    per_day = 1 if len(kept) > 1 else 2

    presented: List[Dict[str, Any]] = []
    truncated = False
    for day in kept:
        if not isinstance(day, dict):
            presented.append(day)
            continue
        trimmed = dict(day)
        for key in ("slot_times", "slot_times_spoken", "slots"):
            value = trimmed.get(key)
            if isinstance(value, list):
                if len(value) > per_day:
                    truncated = True
                trimmed[key] = value[:per_day]
        presented.append(trimmed)

    if len(days) > max_days:
        truncated = True

    out = dict(result)
    out["available_days"] = days
    out["total_days"] = len(days)

    if len(presented) == 1 and isinstance(presented[0], dict):
        first = dict(presented[0])
        if truncated:
            first["more_times"] = True
        out["presentation_mode"] = "single_day"
        out["first_day"] = first
        out.pop("presented_days", None)
    else:
        out["presentation_mode"] = "multi_day"
        out["presented_days"] = presented
        out.pop("first_day", None)
        if truncated:
            out["more_times"] = True

    return out


def _sync_last_offered_to_spoken(
    session: Dict[str, Any], result: Dict[str, Any]
) -> None:
    """Align last_offered_slots with what Haiku will actually speak.

    Unspoken follow-up computes remaining = available_days − last_offered.
    If last_offered still held _select_presented_tuples (up to 3) while speech
    only named two, "later" would skip a still-unspoken middle slot — or, with
    an uncapped six-option readout, remaining would be empty. Sync to the
    spoken subset from first_day / presented_days.
    """
    spoken_slots: List[Dict[str, Any]] = []
    spoken_labels: List[str] = []
    if result.get("presentation_mode") == "single_day":
        fd = result.get("first_day") or {}
        for s in (fd.get("slots") or []):
            if isinstance(s, dict) and s.get("start"):
                spoken_slots.append({"start": s["start"], "end": s.get("end") or ""})
        spoken_labels = list(fd.get("slot_times_spoken") or [])
    else:
        for day in (result.get("presented_days") or []):
            if not isinstance(day, dict):
                continue
            slots = day.get("slots") or []
            labels = day.get("slot_times_spoken") or []
            if slots and isinstance(slots[0], dict) and slots[0].get("start"):
                spoken_slots.append({
                    "start": slots[0]["start"],
                    "end": slots[0].get("end") or "",
                })
                if labels:
                    spoken_labels.append(labels[0])
    if spoken_slots:
        session["last_offered_slots"] = spoken_slots
        if spoken_labels:
            session["slot_labels"] = spoken_labels


def _filter_same_day_slots(result: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """Remove today's date from all availability results.

    Default: same-day bookings are not offered (minimum lead time is the next
    working day) — Theorem's policy. Clinics whose config sets
    operational.allow_same_day=true (e.g. jv_v1) SKIP this filter and may offer
    today's remaining slots. (Slots are generated from `now`, so already-passed
    times today are never included regardless.)
    """
    if "available_days" not in result:
        return result
    try:
        from app.clinic_config import get_clinic as _gc_sd
        if _gc_sd(session.get("clinic_id")).get("allow_same_day"):
            return result
    except Exception:
        pass
    from datetime import date as _date_cls
    today_str = _date_cls.today().isoformat()
    original = result["available_days"]
    filtered = [d for d in original if d.get("date") != today_str]
    if len(filtered) < len(original):
        logger.info("[ms_tools] same-day slots filtered out: %s", today_str)
    result = dict(result)
    result["available_days"] = filtered
    result["total_days"] = len(filtered)
    session["available_days"] = filtered
    return result


# A day_window at or below this is a deliberately narrow search (a named day, or
# "the next couple of days"), so an empty result is a MISS worth widening rather
# than a genuine absence of availability.
_NARROW_WINDOW_MAX_DAYS = 3
# How far ahead to look once a narrow search misses.
_WIDEN_WINDOW_DAYS = 14


def _spoken_day_label(date_iso: str) -> str:
    """"2026-08-04" → "Tuesday 4th August". Matches _build_days_data's day_label
    so the model never has to render a date itself."""
    try:
        from datetime import date as _sdl_date
        d = _sdl_date.fromisoformat(date_iso)
        return f"{d.strftime('%A')} {_ordinal(d.day)} {d.strftime('%B')}"
    except Exception:
        return ""


async def _exec_check_availability(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    # ── PROMPT L hard gate: reject any service name other than the valid set ──
    # This fires BEFORE any Acuity or Google Calendar request so invalid
    # service names can never reach the booking system.  The error message
    # instructs the LLM to retry immediately with the correct value; the
    # patient must never hear anything about this correction.
    # The single-service gate and the "which clinic" location gate below are
    # Theorem-specific (one bookable service, two physical sites). They must
    # NOT fire for data-driven template clinics (e.g. jv_v1), which have their
    # own multi-service catalogue and a single site selected by modality.
    _gate_cid = _resolve_clinic_id(session)
    _is_theorem_gate = _gate_cid in ("theorem", "theorem_v2", "theorem_v3")

    _raw_service = (args.get("service") or "").strip()
    if _is_theorem_gate and _raw_service.lower() not in _VALID_SERVICES:
        logger.warning(
            "[ms_tools] check_availability rejected: invalid service=%r — "
            "must be 'physiotherapy assessment'",
            _raw_service,
        )
        return {
            "error": "invalid_service",
            "message": (
                f"'{_raw_service}' is not a bookable service. "
                f"The ONLY valid service is 'physiotherapy assessment'. "
                f"Call check_availability again immediately with "
                f"service='physiotherapy assessment' and the same location/date_hint. "
                f"Do NOT mention this correction to the patient. "
                f"If the patient enquired about a specific treatment (acupuncture, "
                f"shockwave, sports massage, etc.), apply the Prompt L framing — "
                f"acknowledge their interest, explain that Mark recommends starting "
                f"with a physiotherapy assessment, then proceed with the slot search."
            ),
        }

    # ── Location-confirmed gate ────────────────────────────────────────────────
    # check_availability must never be called with a guessed or assumed location.
    # selected_location is only set once the caller has confirmed their clinic
    # (verbal intercept, biased-confirm, or DTMF).  Reject if not yet set.
    _confirmed_loc = session.get("selected_location") or session.get("location")
    if not _confirmed_loc:
        # Single-site template clinics (e.g. jv_v1) have no clinic-selection
        # step — auto-resolve to their one location so the gate passes.
        from app.clinic_config import get_clinic as _gc_loc
        _loc_clinic = _gc_loc(session.get("clinic_id"))
        _loc_list = _loc_clinic.get("locations") or []
        if _loc_clinic.get("prompt_engine") == "template_v1" and len(_loc_list) == 1:
            _confirmed_loc = (_loc_list[0].get("location_id") or "primary")
            session["selected_location"] = _confirmed_loc
            logger.info(
                "[ms_tools] single-site template clinic — auto-set "
                "selected_location=%s", _confirmed_loc,
            )
        else:
            logger.warning(
                "[ms_tools] check_availability rejected: location not yet confirmed"
                " (location arg=%r) — returning location_required error",
                args.get("location"),
            )
            return {
                "error": "location_required",
                "message": (
                    "The caller has not yet confirmed their clinic. "
                    "Do NOT call check_availability until the caller has stated "
                    "which clinic they want (Alcester or Redditch). "
                    "Ask: 'Which clinic would you like — Awlstuh or Redditch?' "
                    "and wait for their answer. Once they confirm, call "
                    "check_availability with their confirmed location."
                ),
            }

    # Theorem clinic (both numbers) uses Acuity Scheduling; demo clinic uses Google Calendar
    if _gate_cid in ("theorem", "theorem_v2", "theorem_v3"):
        _acuity_result = await _check_availability_acuity(args, session)
        return _filter_same_day_slots(_acuity_result, session)

    # Provisional clinics (e.g. vital_edge) read the slots the practitioner has
    # PUBLISHED to a dedicated Google calendar, rather than generating slots
    # from fixed working_hours. Susie offers only what genuinely exists.
    from app.clinic_config import get_clinic as _gc_prov
    _prov_clinic = _gc_prov(session.get("clinic_id"))
    if _prov_clinic.get("booking_system") == "google_calendar_provisional":
        return await _check_availability_published(args, session, _prov_clinic)

    from app.tools.slots import (
        generate_candidate_slots,
        filter_free_slots,
        format_slot,
        pick_first_n,
        next_7_days_window,
        parse_busy,
    )
    from app.tools.calendar_google import freebusy
    from app.clinic_config import get_clinic

    location = (args.get("location") or session.get("selected_location", "")).lower().strip()
    # Pin the service+modality the caller is actually being shown slots for, so
    # book_appointment books the SAME service. Without this the model defaulted
    # to msk_initial_assessment at booking even for a returning patient whose
    # availability was checked as msk_treatment_session (P2).
    if _raw_service:
        session["_checked_service"] = _raw_service
        session["_checked_location"] = location
    day_window_days = int(args.get("day_window") or 7)
    # Day/time preference (e.g. "Thursday afternoon") — passed to both
    # presentation builders so available_days honours it, mirroring the Acuity
    # path (bug C5-5).
    _pref = (args.get("date_hint") or args.get("preference") or "").strip()

    clinic = get_clinic(session.get("clinic_id"))
    working_hours = clinic.get("working_hours", {})

    # ── Service×modality guard (mirrors the book-path guard, but up front) ──
    # Reject before offering slots if the service can't be delivered in the
    # chosen modality (e.g. a neuro assessment requested "home_visit" when
    # clinic.json doesn't offer it), so Susie redirects to a modality it IS
    # available as instead of offering slots that only dead-end at book time.
    # Skips single-service Theorem (returned above) and services that declare
    # no available_as (the helper returns True → no block).
    _av_svc_def = _find_service_def(clinic, _raw_service)
    _av_modality = _location_to_modality(clinic, location)
    if _av_svc_def and _av_modality and not _service_supports_location(_av_svc_def, _av_modality):
        _avail = ", ".join(str(t) for t in (_av_svc_def.get("available_as") or [])) or "none"
        logger.warning(
            "[ms_tools] check_availability rejected: service %r not available as %r (only: %s)",
            _raw_service, _av_modality, _avail,
        )
        return {
            "error": "service_modality_unavailable",
            "message": (
                f"'{_raw_service}' is not offered as '{_av_modality}'. It is available "
                f"as: {_avail}. Do NOT search availability for this combination. Tell "
                f"the caller warmly that this service isn't available that way, offer "
                f"the modality(ies) it IS available as, and only call check_availability "
                f"again once they pick one. Do NOT invent a price for the unavailable combination."
            ),
        }

    _slot_minutes = int(clinic.get("slot_minutes") or 50)
    _break_min = int(clinic.get("slot_break_minutes") or 0)
    # Slot length: caller-passed override, else the length configured for THIS
    # service (services[].typical_duration_minutes), else the clinic slot_minutes.
    # Per-service so a 30-min treatment and a 60-min neuro assessment grid (and
    # book) at their true lengths rather than a flat slot_minutes.
    # Latch a caller-chosen length (e.g. 30 vs 60 min massage) so it survives from
    # this check to the later book_appointment turn, and resolve via the shared
    # _options-aware helper so the slot grid and the booked event always agree.
    if args.get("duration_minutes"):
        session["_service_duration_choice"] = int(args["duration_minutes"])
    duration_min = int(
        args.get("duration_minutes")
        or _service_duration_minutes(
            clinic, _raw_service, _slot_minutes,
            preferred=session.get("_service_duration_choice"),
        )
    )
    # Re-anchor each day's closing bound to the service length. working_hours end
    # was baked as last_appointment + slot_minutes (config default 40). Shift it by
    # (duration_min - slot_minutes) so the LAST offered start equals last_appointment
    # for ANY service duration — otherwise a 30-min service would over-offer past
    # last_appointment and a 60-min service would be truncated before it. Build a
    # NEW dict; never mutate the shared clinic config.
    if working_hours and duration_min != _slot_minutes:
        _delta_h = (duration_min - _slot_minutes) / 60.0
        working_hours = {
            _day: ((_hrs[0], _hrs[1] + _delta_h) if _hrs else None)
            for _day, _hrs in working_hours.items()
        }

    now = datetime.now(LONDON_TZ)
    w_start = now

    # Apply after_date: shift the search window start forward if caller is unavailable before that date
    after_date_str = (args.get("after_date") or "").strip()
    if after_date_str:
        try:
            from datetime import date as _gcal_date
            _after_naive = datetime.combine(
                _gcal_date.fromisoformat(after_date_str),
                datetime.min.time(),
            )
            _after_dt = LONDON_TZ.localize(_after_naive)
            if _after_dt > w_start:
                w_start = _after_dt
                logger.info(
                    "_exec_check_availability (gcal): after_date=%s applied, w_start shifted",
                    after_date_str,
                )
        except Exception as _ae:
            logger.warning(
                "_exec_check_availability (gcal): could not parse after_date=%r — ignoring: %r",
                after_date_str, _ae,
            )

    w_end = w_start + timedelta(days=day_window_days)

    candidates = generate_candidate_slots(
        w_start, w_end,
        duration_min=duration_min,
        clinic_working_hours=working_hours,
        increment_min=clinic.get("slot_increment_minutes"),
        break_min=_break_min,
    )

    tokens = await _get_tokens()
    if not tokens:
        if not candidates:
            return {"error": "No slots found in the next 7 days.", "slots": []}
        presented  = _select_presented_tuples(candidates, preference=_pref)
        # Build from preference-matching candidates so available_days honours
        # the requested day/time.  Mirrors the Acuity path fix (bug C5-5).
        days_data  = _build_days_data(candidates, preference=_pref)
        pres_raw   = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in presented]
        pres_labels = [format_slot(s) for s in presented]
        session["last_offered_slots"] = pres_raw
        session["slot_labels"]        = pres_labels
        session["available_days"]     = days_data
        _out = _cap_presented_slots(_filter_same_day_slots(
            {"available_days": days_data, "total_days": len(days_data), "note": "calendar_not_connected"},
            session,
        ))
        _sync_last_offered_to_spoken(session, _out)
        return _out

    calendar_id = _resolve_calendar_id(clinic, location)

    try:
        busy_raw = await asyncio.to_thread(freebusy, tokens, w_start, w_end, calendar_id)
        await _save_gcal_tokens(tokens)   # persist any token refresh that happened inside freebusy
        busy_blocks = parse_busy(busy_raw or [])
        free_slots = filter_free_slots(candidates, busy_blocks, break_min=_break_min)
    except Exception as e:
        # Calendar API failed — fall back to unfiltered candidate slots (same
        # behaviour as when calendar tokens are absent).  This keeps the
        # conversation alive so Susie can still offer times and the caller
        # can complete their booking.  Slots may overlap existing appointments
        # but that is far better than the conversation dying with an error.
        logger.error(
            "check_availability freebusy error: %r — falling back to unfiltered candidates", e
        )
        free_slots = candidates
        if not free_slots:
            return {"error": "No candidate slots found in the next 7 days.", "slots": []}
        presented  = _select_presented_tuples(free_slots, preference=_pref)
        # Build from preference-matching free_slots so available_days honours
        # the requested day/time.  Mirrors the Acuity path fix (bug C5-5).
        days_data  = _build_days_data(free_slots, preference=_pref)
        pres_raw   = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in presented]
        pres_labels = [format_slot(s) for s in presented]
        session["last_offered_slots"] = pres_raw
        session["slot_labels"]        = pres_labels
        session["available_days"]     = days_data
        _out = _cap_presented_slots(_filter_same_day_slots(
            {"available_days": days_data, "total_days": len(days_data), "note": "calendar_check_failed_unfiltered"},
            session,
        ))
        _sync_last_offered_to_spoken(session, _out)
        return _out

    if not free_slots:
        # ── Requested day is full: widen once, and offer REAL alternatives ────
        # The model passes day_window=1 for a named day (SPECIFIC DAY in the
        # template prompt), so an empty result here means "that one day is
        # full" — not "we have nothing".  This used to return a bare error with
        # no data, leaving Sonnet to invent an alternative day it had no
        # availability for: a whole extra round-trip, and a real chance of
        # offering a day that is also full.  Widen and return the miss TOGETHER
        # with the nearest genuine slots so the caller hears "Tuesday's full,
        # I've got Wednesday at seven" in a single turn.
        # Narrow windows only — an empty DEFAULT search really does mean empty.
        _explicit_window = args.get("day_window")
        if _explicit_window and int(_explicit_window) <= _NARROW_WINDOW_MAX_DAYS:
            _requested_iso = after_date_str or w_start.date().isoformat()
            _wide_end = w_start + timedelta(days=_WIDEN_WINDOW_DAYS)
            logger.info(
                "_exec_check_availability (gcal): requested day %s empty — "
                "widening %sd → %sd",
                _requested_iso, _explicit_window, _WIDEN_WINDOW_DAYS,
            )
            _wide_candidates = generate_candidate_slots(
                w_start, _wide_end,
                duration_min=duration_min,
                clinic_working_hours=working_hours,
                increment_min=clinic.get("slot_increment_minutes"),
                break_min=_break_min,
            )
            try:
                _wide_busy = await asyncio.to_thread(
                    freebusy, tokens, w_start, _wide_end, calendar_id
                )
                await _save_gcal_tokens(tokens)
                free_slots = filter_free_slots(
                    _wide_candidates, parse_busy(_wide_busy or []), break_min=_break_min
                )
            except Exception as _we:
                # Same posture as the primary freebusy failure above: unfiltered
                # candidates beat killing the conversation.
                logger.error(
                    "check_availability widen freebusy error: %r — "
                    "falling back to unfiltered candidates", _we
                )
                free_slots = _wide_candidates

            if free_slots:
                presented   = _select_presented_tuples(free_slots, preference=_pref)
                days_data   = _build_days_data(free_slots, preference=_pref)
                pres_raw    = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in presented]
                pres_labels = [format_slot(s) for s in presented]
                session["last_offered_slots"] = pres_raw
                session["slot_labels"]        = pres_labels
                session["available_days"]     = days_data
                _out = _cap_presented_slots(_filter_same_day_slots({
                    "available_days":      days_data,
                    "total_days":          len(days_data),
                    # Spoken label is pre-built here for the same reason
                    # slot_times_spoken is: the model must never render a date
                    # itself.  Empty when the caller gave a relative narrow
                    # window ("the next 2 days") rather than a named day.
                    "requested_day_empty": True,
                    "requested_date":      _requested_iso,
                    "requested_day_label": _spoken_day_label(_requested_iso) if after_date_str else "",
                    "note":                "requested_day_full_widened",
                }, session))
                # same-day filtering can empty a widened window whose only slots
                # were today; fall through to the honest "nothing" rather than
                # announcing a miss with no alternative attached.
                if _out.get("available_days"):
                    _sync_last_offered_to_spoken(session, _out)
                    return _out

        return {"error": "No available slots found. Try a different time preference or wider window.", "slots": []}

    presented  = _select_presented_tuples(free_slots, preference=_pref)
    # Build from preference-matching free_slots so available_days honours the
    # requested day/time.  Mirrors the Acuity path fix (bug C5-5).
    days_data  = _build_days_data(free_slots, preference=_pref)
    pres_raw   = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in presented]
    pres_labels = [format_slot(s) for s in presented]
    session["last_offered_slots"] = pres_raw
    session["slot_labels"]        = pres_labels
    session["available_days"]     = days_data
    # Cap SPEECH only — available_days stays full; last_offered synced to the
    # spoken two so unspoken follow-up (V5) computes remaining correctly.
    _out = _cap_presented_slots(_filter_same_day_slots(
        {"available_days": days_data, "total_days": len(days_data)},
        session,
    ))
    _sync_last_offered_to_spoken(session, _out)
    return _out


# ---------------------------------------------------------------------------
# Provisional clinics: availability is READ from a published Google calendar
# ---------------------------------------------------------------------------

# Booking-marker prefixes Susie writes onto a published slot when it is taken,
# so it drops out of the available set on subsequent reads.
_PROVISIONAL_TAKEN_PREFIXES = ("PENDING", "BOOKED", "CONFIRMED")


def _parse_event_dt(raw: str):
    """Parse a Google event dateTime string to an aware Europe/London datetime."""
    return datetime.fromisoformat((raw or "").replace("Z", "+00:00")).astimezone(LONDON_TZ)


async def _check_availability_published(
    args: Dict[str, Any], session: Dict[str, Any], clinic: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Availability for google_calendar_provisional clinics: every (timed) event on
    the dedicated availability calendar is a bookable slot, EXCEPT ones already
    flipped to PENDING/BOOKED. We never generate slots from working_hours, so
    Susie can only ever offer times the practitioner has actually published.
    """
    from app.tools.calendar_google import list_upcoming_events
    from app.tools.slots import format_slot

    location = (args.get("location") or session.get("selected_location", "")).lower().strip()
    _pref = (args.get("date_hint") or args.get("preference") or "").strip()
    calendar_id = _resolve_calendar_id(clinic, location)
    days_ahead = int(clinic.get("days_ahead") or 14)

    now = datetime.now(LONDON_TZ)
    # Hard booking horizon: the practitioner only releases availability ~2 weeks
    # ahead and will not take bookings further out. Enforce it here so a specific
    # far-future after_date can't slip past the window.
    horizon = now + timedelta(days=days_ahead)
    w_start = now
    after_date_str = (args.get("after_date") or "").strip()
    if after_date_str:
        try:
            from datetime import date as _d
            _ad = LONDON_TZ.localize(datetime.combine(_d.fromisoformat(after_date_str), datetime.min.time()))
            if _ad > w_start:
                w_start = _ad
        except Exception as e:
            logger.warning("_check_availability_published: bad after_date=%r — ignoring: %r", after_date_str, e)
    if w_start > horizon:
        return {
            "error": "beyond_booking_horizon",
            "message": (
                f"That date is more than {days_ahead} days away. Jonathan only "
                "releases his availability about two weeks ahead, so you can only "
                "offer bookings up to roughly a fortnight from today. Explain this "
                "to the caller, and offer to take their details (add_to_waitlist) "
                "so Jonathan can be in touch when he opens up later dates. Do NOT "
                "offer any slot beyond the two-week horizon."
            ),
            "slots": [],
        }
    day_window = args.get("day_window")
    w_end = (w_start + timedelta(days=int(day_window))) if day_window else horizon
    # Never let an explicit day_window push the window past the 2-week horizon.
    if w_end > horizon:
        w_end = horizon

    tokens = await _get_tokens()
    if not tokens:
        return {"error": "Calendar not connected — unable to read availability.", "slots": []}

    try:
        events = await asyncio.to_thread(
            list_upcoming_events, tokens, days_ahead, 75, calendar_id
        )
        await _save_gcal_tokens(tokens)
    except Exception as e:
        logger.error("_check_availability_published: list events error: %r", e)
        return {"error": "Couldn't read availability just now — please try again.", "slots": []}

    candidates = []
    slot_event_map: Dict[str, str] = {}
    seen_starts: set = set()
    for ev in events or []:
        start_raw = (ev.get("start") or {}).get("dateTime")
        if not start_raw:
            continue  # all-day / date-only blocks are not bookable slots
        summary = (ev.get("summary") or "").strip().upper()
        if summary.startswith(_PROVISIONAL_TAKEN_PREFIXES):
            continue  # already requested/booked — not offerable
        try:
            s_dt = _parse_event_dt(start_raw)
            end_raw = (ev.get("end") or {}).get("dateTime")
            e_dt = _parse_event_dt(end_raw) if end_raw else (s_dt + timedelta(minutes=int(clinic.get("slot_minutes") or 60)))
        except Exception:
            continue
        if s_dt <= now or s_dt < w_start or s_dt > w_end:
            continue
        # Collapse multiple published events that share the same start time into
        # ONE offerable slot. Two identical starts made Susie invent a fake
        # "30-minute vs an hour" distinction — the duration is the caller's
        # 60/90 choice, not a property of the published slot.
        start_iso = s_dt.isoformat()
        if start_iso in seen_starts:
            continue
        seen_starts.add(start_iso)
        candidates.append((s_dt, e_dt))
        if ev.get("id"):
            slot_event_map[start_iso] = ev["id"]

    candidates.sort(key=lambda t: t[0])
    # Remember which published event backs each offered slot, so book_appointment
    # can flip that exact event to PENDING.
    session["_provisional_slot_events"] = slot_event_map

    if not candidates:
        return {
            "error": "No published slots in that window — take the caller's name, "
                     "number and preferred days and add them to the waitlist.",
            "slots": [],
        }

    presented = _select_presented_tuples(candidates, preference=_pref)
    days_data = _build_days_data(candidates, preference=_pref)
    # Provisional model: each published slot is a START-TIME marker only. The
    # caller chooses 60 or 90 minutes and the practitioner confirms, so a slot's
    # own published window length is NOT the session length (same reason as the
    # duplicate-start collapse above). Exposing each slot's `end` to the model
    # made it read a published 60-minute window as a fixed session length and
    # refuse 90-minute requests ("that's only a 60-minute session") — the
    # 2026-07-24 Vital Edge abandoned booking. Nothing downstream reads this
    # `end` (_resolve_slot_iso keys on `start`, _filter_same_day_slots on
    # `date`), so drop it from the model-facing payload. last_offered_slots
    # below keeps start+end for internal slot resolution.
    for _ved_day in days_data:
        for _ved_slot in _ved_day.get("slots", []):
            _ved_slot.pop("end", None)
    session["last_offered_slots"] = [{"start": s[0].isoformat(), "end": s[1].isoformat()} for s in presented]
    session["slot_labels"] = [format_slot(s) for s in presented]
    session["available_days"] = days_data
    return _filter_same_day_slots(
        {"available_days": days_data, "total_days": len(days_data)},
        session,
    )


def _uk_when_label(dt) -> str:
    """British, SMS-friendly datetime label, e.g. 'Wednesday 15 July at 4pm' or
    'Wednesday 15 July at 10:30am'. Platform-safe (avoids %-d / %-I, which
    Windows strftime rejects)."""
    parts = dt.strftime("%A %d %B").split()
    if len(parts) == 3 and parts[1].startswith("0"):
        parts[1] = parts[1][1:]  # "05" -> "5"
    day = " ".join(parts)
    hour = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    label = f"{hour}:{dt.minute:02d}{ampm}" if dt.minute else f"{hour}{ampm}"
    return f"{day} at {label}"


def _provisional_practitioner(clinic: Dict[str, Any]) -> str:
    return (
        clinic.get("practitioner")
        or (clinic.get("prompt_facts") or {}).get("practitioner")
        or "the practitioner"
    )


async def _book_appointment_provisional(
    args: Dict[str, Any], session: Dict[str, Any], clinic: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Provisional booking: write the chosen PUBLISHED slot to the calendar as a
    PENDING event and notify the owner. Never tells the caller it's confirmed,
    and never sends a caller confirmation SMS — the practitioner confirms
    directly. Returns success so Susie speaks the pending message.
    """
    from app.tools.calendar_google import create_event, update_event, patch_event_time
    from app.notifications.owner_notify import notify_owner, build_booking_request_message

    patient_name = (args.get("patient_name") or "").strip()
    phone = (args.get("phone") or "").strip()
    service = args.get("service") or "Deep Tissue Massage"
    # The book_appointment tool schema exposes `followup_note`, NOT `notes` — the
    # prompt tells Susie to put the caller's context there. Reading only `notes`
    # meant it was always empty, so the caller's reason for booking reached
    # neither the calendar event nor the owner's SMS. On the provisional model
    # the owner rings the client back, so that context is the whole point.
    # `notes` is kept first for any internal caller that does pass it.
    notes = (args.get("notes") or args.get("followup_note") or "").strip()

    # Resolve the friendly service name AND a per-service default duration from
    # clinic.json, so a named massage (e.g. sports_massage → 90 min) lands on the
    # calendar with the right length even when the LLM doesn't pass
    # duration_minutes. Falls back to the clinic slot length.
    _svc_def_prov = _find_service_def(clinic, service)
    svc_name = (_svc_def_prov or {}).get("name") or service
    if args.get("duration_minutes"):
        session["_service_duration_choice"] = int(args["duration_minutes"])
    duration = int(
        args.get("duration_minutes")
        or _service_duration_minutes(
            clinic, service, clinic.get("slot_minutes") or 60,
            preferred=session.get("_service_duration_choice"),
        )
    )

    if not patient_name or not phone:
        return {"success": False, "error": "patient_name and phone are required."}

    try:
        start_dt = _resolve_slot_iso(args.get("slot_iso", ""), session)
        end_dt = start_dt + timedelta(minutes=duration)
    except Exception as e:
        return {"success": False, "error": f"Invalid slot datetime: {e}"}

    now_london = datetime.now(LONDON_TZ)
    if start_dt <= now_london:
        return {
            "success": False,
            "error": (
                f"Cannot book a slot in the past "
                f"({start_dt.strftime('%a %d %b at %H:%M')}). "
                "Please check availability again for current options."
            ),
        }

    location = (
        args.get("location") or session.get("selected_location")
        or clinic.get("primary_location") or ""
    ).lower().strip()
    calendar_id = _resolve_calendar_id(clinic, location)

    # GUARANTEED home-visit flag. VE books home visits as a normal appointment
    # with no address taken on the call, so the ONLY thing telling the
    # practitioner to travel is this flag. Derive it from THREE signals so it can
    # never silently go missing: (1) a durable session latch set the moment the
    # caller asked for a home visit (independent of the LLM — see connection.py
    # _utterance_is_home_visit), (2) the location arg, (3) home-visit keywords in
    # the note. If any fires, force it into the calendar event, Sheets and owner
    # SMS regardless of what the model passed in followup_note.
    _hv_kw = ("home visit", "home-visit", "house visit", "come to me",
              "at my home", "at my house", "at my place")
    is_home_visit = (
        location == "home_visit"
        or session.get("home_visit_requested") is True
        or any(k in notes.lower() for k in _hv_kw)
    )
    _prac = _provisional_practitioner(clinic)
    if is_home_visit:
        _hv_banner = (
            f"HOME VISIT REQUEST — patient would like {_prac} to travel to them; "
            "confirm feasibility and get their address on the callback."
        )
        owner_notes = f"\U0001F3E0 {_hv_banner}" + (f" {notes}" if notes else "")
    else:
        owner_notes = notes

    summary = (
        f"PENDING CONFIRMATION{' — HOME VISIT' if is_home_visit else ''} — "
        f"{patient_name} — {svc_name} ({duration} min)"
    )
    description = "\n".join(
        [
            "PROVISIONAL booking requested via Susie (AI receptionist) — NOT yet confirmed.",
            f"Patient: {patient_name}",
            f"Phone: {phone}",
            f"Service: {svc_name} ({duration} min)",
            f"Requested: {start_dt.strftime('%A %d %B %Y at %H:%M')}",
        ]
        + (["HOME VISIT — patient wants the practitioner to travel to them; "
            "confirm feasibility + address on the callback."] if is_home_visit else [])
        + ([f"Notes: {notes}"] if notes else [])
        + ["Confirm directly with the client (WhatsApp/phone)."]
    )

    tokens = await _get_tokens()
    event_id = None
    calendar_written = False
    if tokens:
        slot_map = session.get("_provisional_slot_events") or {}
        src_event_id = slot_map.get(start_dt.isoformat())
        try:
            if src_event_id:
                # Flip the published availability slot into a PENDING booking so
                # it both drops out of availability and shows the request.
                ev = await asyncio.to_thread(
                    update_event, tokens, src_event_id, summary, description, calendar_id
                )
                event_id = (ev or {}).get("id") or src_event_id
                # update_event only patches summary/description, so the flipped
                # event keeps the published window's length (often 60 min). Set
                # the block to the actually-booked duration (start_dt..end_dt) so
                # a 90-minute booking reads as 90 on the calendar, not 60. The
                # published slot is a start-time marker; its own length carries
                # no booking meaning. Non-fatal — the booking already stands via
                # the flip above, Sheets and the owner/caller notifications.
                try:
                    await asyncio.to_thread(
                        patch_event_time, tokens, event_id, start_dt, end_dt, calendar_id
                    )
                except Exception as _pe:
                    logger.warning(
                        "provisional flip: block-length patch failed (non-fatal): %r",
                        _pe,
                    )
            else:
                ev = await asyncio.to_thread(
                    create_event, tokens, start_dt, end_dt, summary, description, calendar_id, "default"
                )
                event_id = (ev or {}).get("id", "")
            await _save_gcal_tokens(tokens)
            calendar_written = True
        except Exception as e:
            logger.error("provisional booking calendar write failed: %r", e)

    # Always log to Sheets so the request is never lost, even if the write failed.
    booked_label = start_dt.strftime("%A %d %B at %H:%M")
    try:
        from app.tools.handoff import send_to_sheet
        await asyncio.to_thread(
            send_to_sheet,
            patient_name, phone, "BOOK_PROVISIONAL",
            f"PROVISIONAL{' [HOME VISIT]' if is_home_visit else ''}: "
            f"{svc_name} ({duration} min) on {booked_label}"
            + (f" | Notes: {owner_notes}" if owner_notes else "")
            + ("" if calendar_written else " | (calendar write FAILED — action manually)"),
            session.get("call_sid", ""),
            "Vital Edge AI Receptionist",
        )
    except Exception as e:
        logger.warning("provisional booking Sheets log failed (non-fatal): %r", e)

    # Session state — mark provisional and SUPPRESS the caller confirmation SMS.
    session.setdefault("collected", {})
    # Provisional (Vital Edge): the practitioner has not accepted yet, so the
    # summary row is the ONLY record of who asked — a stale name loses them.
    _sync_booked_patient_name(session, patient_name)
    session["collected"]["phone"] = phone
    session["collected"]["service"] = service
    session["collected"]["slot"] = start_dt.isoformat()
    session["selected_slot"] = start_dt.isoformat()
    session["calendar_event_id"] = event_id or ""
    session["calendar_status"] = "provisional"
    session["provisional_booking"] = True
    session["confirmation_sms_sent"] = True  # never send the caller a "confirmed" text

    # Notify the owner immediately (SMS now; WhatsApp later). Non-fatal.
    try:
        await notify_owner(
            clinic,
            build_booking_request_message(
                clinic=clinic, patient_name=patient_name, phone=phone,
                when=start_dt, duration_minutes=duration, service=svc_name, notes=owner_notes,
            ),
        )
    except Exception as e:
        logger.warning("provisional owner notify failed (non-fatal): %r", e)

    # Caller acknowledgement — a provisional-worded text (NEVER "confirmed").
    # confirmation_sms_sent above already suppresses the smart-SMS follow-up, so
    # this is the single caller text and it must not imply the slot is booked.
    try:
        from app.notifications.sms import send_sms as _send_sms_caller
        _prac = _provisional_practitioner(clinic)
        _cn = clinic.get("sms_name") or clinic.get("display_name") or "the clinic"
        _first = patient_name.split()[0] if patient_name.strip() else "there"
        _line = clinic.get("phone") or ""
        _msg = (
            f"Hi {_first}, thanks for your request with {_cn} — a {svc_name} on "
            f"{_uk_when_label(start_dt)}. This is a request, not yet confirmed: "
            f"{_prac} will be in touch directly to confirm and arrange payment "
            f"beforehand, so there's nothing to pay now."
            + (f" {_line}" if _line else "")
        )
        if phone:
            await _send_sms_caller(to=phone, message=_msg)
    except Exception as e:
        logger.warning("provisional caller ack SMS failed (non-fatal): %r", e)

    return {
        "success": True,
        "provisional": True,
        "booked_slot": booked_label,
        "note": (
            "PROVISIONAL request logged and owner notified. Tell the caller it is "
            "NOT confirmed — the practitioner will confirm with them directly and "
            "may offer a similar time."
        ),
    }


async def _send_practitioner_followup_ping(
    clinic: Dict[str, Any],
    patient_name: str,
    phone: str,
    service: str,
    location: str,
    booked_label: str,
    followup_note: str = "",
) -> None:
    """
    Ping the practitioner (clinic.transfer_phone, e.g. Marcus) AFTER a booking
    when the appointment needs human follow-up — a home visit (Marcus must
    coordinate travel and the patient will text their address), or any query the
    caller raised that Susie flagged via book_appointment(followup_note=...).

    The patient is ALWAYS booked in directly first — Susie never gatekeeps a
    booking behind the practitioner. This is just the heads-up so the
    practitioner follows up after. Fire-and-forget, non-fatal.
    """
    # When the clinic uses real-time owner_alerts for bookings, the consolidated
    # notify_owner(event="booking") call already texted the owner (carrying any
    # follow-up note), so skip this to avoid a duplicate SMS to the same number.
    from app.notifications.owner_alert import owner_alerts_enabled
    if owner_alerts_enabled(clinic, "booking"):
        return
    is_home_visit = (location or "").lower() == "home_visit"
    note = (followup_note or "").strip()
    if not is_home_visit and not note:
        return
    try:
        from app.notifications.sms import send_sms
        transfer_phone = clinic.get("transfer_phone", "")
        if not transfer_phone:
            return
        reasons = []
        if is_home_visit:
            reasons.append("HOME VISIT — confirm travel; patient will text their address")
        if note:
            reasons.append(note)
        body = (
            f"🔔 {patient_name or 'A patient'} ({phone or 'no number'}) just booked "
            f"{service or 'an appointment'} for {booked_label}. "
            f"Needs follow-up: {' | '.join(reasons)}."
        )
        asyncio.create_task(send_sms(to=transfer_phone, message=body))
        logger.info(
            "practitioner follow-up ping queued to ***%s (home_visit=%s, note=%s)",
            transfer_phone[-4:] if transfer_phone else "????",
            is_home_visit, bool(note),
        )
    except Exception as e:
        logger.warning("practitioner follow-up ping failed (non-fatal): %r", e)


# ---------------------------------------------------------------------------
# Executor: book_appointment
# ---------------------------------------------------------------------------

async def _ping_owner_insurance(
    clinic: Dict[str, Any],
    patient_name: str,
    phone: str,
    start_dt: "datetime",
    service: str,
    insurer: str,
) -> None:
    """
    SMS-ping the clinic owner (Marcus) when an INSURANCE booking is made, so they
    can follow up to collect the pre-auth / membership details and confirm cover.
    Non-fatal; no-op if no owner_notification_sms is configured. We deliberately
    do NOT include any code (Susie never takes one by phone) — the owner collects
    it directly. (Option B insurance handling.)
    """
    try:
        from app.notifications.owner_notify import notify_owner
        svc_name = service
        for _s in (clinic.get("services") or []):
            if _s.get("service_id") == service or (_s.get("name") or "").lower() == (service or "").lower():
                svc_name = _s.get("name") or service
                break
        name = clinic.get("sms_name") or clinic.get("display_name") or "Clinic"
        when_str = start_dt.strftime("%a %d %b at %H:%M")
        msg = (
            f"\U0001F3E5 {name} — INSURANCE booking, please follow up\n"
            f"Name: {patient_name or '—'}\n"
            f"Phone: {phone or '—'}\n"
            f"Insurer: {insurer or '—'}\n"
            f"Appointment: {when_str} — {svc_name}\n"
            "Action: collect the pre-authorisation / membership details and confirm "
            "cover before the appointment. (Susie did NOT take any code by phone.)"
        )
        await notify_owner(clinic, msg)
    except Exception as e:
        logger.warning("insurance owner ping failed (non-fatal): %r", e)


async def _exec_book_appointment(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    # ── Clinical-screening backstop (Layer 3) ─────────────────────────────
    # A caller must never be booked over an un-run (or positive) red-flag
    # screen, even if the model tries. clinical_screening.py arms
    # session['pending_screen'] deterministically; this gate holds the
    # tool boundary. No-op for clinics without clinical_screening enabled.
    try:
        from app.clinic_config import get_clinic as _scr_get_clinic
        from app.media_streams.clinical_screening import booking_blocked_reason
        _scr_reason = booking_blocked_reason(
            session, _scr_get_clinic(session.get("clinic_id"))
        )
        if _scr_reason:
            logger.info(
                "[book] blocked by clinical screening backstop: %s",
                (session.get("pending_screen") or session.get("screen_red_flag")),
            )
            return {"success": False, "error": _scr_reason}
    except Exception:
        # Fail CLOSED on the safety-relevant signal, not blindly. The block
        # condition — a pending or positive red-flag screen — lives in the
        # SESSION (keys mirror clinical_screening.PENDING_SCREEN_KEY /
        # SCREEN_RED_FLAG_KEY), so we can honour it even when the clinic lookup
        # or the import is exactly what threw. Referencing the literals rather
        # than re-importing keeps this handler working when that import is the
        # failure. If no screen was ever armed there is nothing to guard, so we
        # proceed — blocking every booking on any transient error, including for
        # clinics that have no screening at all, would be a worse bug.
        logger.exception("[book] clinical screening backstop errored")
        if session.get("pending_screen") or session.get("screen_red_flag"):
            logger.error(
                "[book] backstop errored while a screen was pending/positive "
                "(%s) — failing CLOSED",
                (session.get("pending_screen") or session.get("screen_red_flag")),
            )
            return {
                "success": False,
                "error": (
                    "Booking is paused for a safety check that has not been "
                    "completed. Please ask the outstanding screening question and "
                    "only book once the caller has answered it."
                ),
            }

    # ── Collection gate (A1 / A2) ─────────────────────────────────────────
    # Same shape as the screening backstop above: hold the tool boundary for
    # the two collection steps that are otherwise enforced by the prompt alone.
    #
    # Evidence — docs/plan/FIX_QUEUE_PRE_DEMO.md A1/A2, call
    # CA4969580082db5e757c3b1d04dd38e7ae: the booking completed with
    # collected.reason=None (the reason was asked AFTER the slots and never
    # answered), and a turn was spent asking for a number we already had from
    # caller ID, followed by "I already have your number confirmed."
    #
    # llm_stream.py has backstops for the phone step, but they stop blocking the
    # moment the model has *asked* the question — asked is not answered, which is
    # exactly how that call booked. This gate takes the authoritative signals
    # only: phone_confirmed is True (set by every path that actually confirms a
    # number — caller-ID accept, spoken number, keypad entry) and a reason on
    # record. Reschedule/cancel do not come through here.
    _cg_collected = session.get("collected")
    if not isinstance(_cg_collected, dict):
        _cg_collected = {}
        session["collected"] = _cg_collected

    # args["reason"] is what the model states it knows; session["reason"] is the
    # canonical slot; collected["reason"] is the mirror obs and the SMS router
    # read. Any of the three satisfies the gate.
    _cg_reason = (
        _gate_text(args.get("reason"))
        or _gate_text(session.get("reason"))
        or _gate_text(_cg_collected.get("reason"))
    )
    # THEOREM ONLY — the reason is never asked here (owner decision 2026-08-07,
    # scope reconfirmed 2026-08-09), so it can only ever arrive volunteered, and
    # `_extract_reason` cannot supply it either: that runs from flow.py, and the
    # FlowEngine is bypassed on every live clinic.
    #
    # Which makes this gate a deadlock on theorem_v3, not a safety net. On a
    # caller who never mentions their injury ("um yeah i'd like to book an
    # appointment", CAb1592daa) the reason is unreachable, so the booking is
    # refused — and the refusal message below tells the model to ask "What's the
    # appointment for?", which Gate 5b-r then deletes on its way to TTS. The
    # model asks, gets deleted, asks again. That loop IS the reported pestering,
    # and underneath it no such caller could be booked at all.
    #
    # So on Theorem an empty reason is a correct outcome and must not block. The
    # gate stands unchanged everywhere else — asking is right on JV and Vital
    # Edge, and only Theorem carries the never-ask rule.
    _reason_required = session.get("clinic_id") != "theorem_v3"
    if _reason_required and not _cg_reason:
        logger.warning(
            "[book] BLOCKED — no reason on record (A2) clinic=%s",
            session.get("clinic_id"),
        )
        return {
            "success": False,
            "error": (
                "book_appointment cannot fire yet — there is no reason on record "
                "for this appointment. The reason decides the appointment type, "
                "its length and its price, so a booking without one gives the "
                "clinic an appointment it cannot prepare for. Do NOT book. Ask "
                "what the appointment is for as its own turn (\"What's the "
                "appointment for?\"), wait for the caller's answer, then call "
                "book_appointment again passing their own words as `reason`."
            ),
        }
    if not _cg_reason:
        # Only reachable on Theorem. Logged because "the booking has no reason"
        # must stay legible in the call record — it is now an expected outcome,
        # not a silent one.
        logger.info(
            "[book] no reason on record — not required on clinic=%s, proceeding",
            session.get("clinic_id"),
        )
    # Commit the reason both ways, so a booking that passes this gate always
    # carries the reason it was made for into the call record and the SMS.
    if not _gate_text(session.get("reason")):
        session["reason"] = _cg_reason
    if not _gate_text(_cg_collected.get("reason")):
        _cg_collected["reason"] = _cg_reason

    if session.get("phone_confirmed") is not True:
        logger.warning(
            "[book] BLOCKED — phone not confirmed (A1) phone_confirmed=%r clinic=%s",
            session.get("phone_confirmed"), session.get("clinic_id"),
        )
        return {
            "success": False,
            "error": (
                "book_appointment cannot fire yet — the caller has not confirmed "
                "a number for this booking. Do NOT book and do NOT treat the "
                "calling number as confirmed. Read the number you already have "
                "back to them and ask for a plain yes or no — \"I've got you on "
                "<number>, is that the best number for the booking?\" — or take a "
                "different number if they give one. Then call book_appointment "
                "again."
            ),
        }

    # ── A3: book the number that was confirmed, not the one the model typed ──
    # Placed above the backend branch so Acuity, Google Calendar and provisional
    # bookings are all covered by one gate.
    #
    # Correct rather than block. The confirmed number is machine-captured and
    # reachable; the argument is not. Blocking would add a conversational dead
    # end to the one flow that already loops, and would lose a booking the
    # caller believes they have — a missed patient to prevent a wrong digit.
    # A caller reached on a number they did not nominate is recoverable; a
    # caller reached on a number that does not exist is not.
    #
    # phone_confirmed=True with nothing on record should be unreachable, so
    # fail open there but say so loudly — that combination means one of the
    # five confirm sites has stopped mirroring and A3 is blind until it is fixed.
    _a3_fix, _a3_reason = _reconcile_booking_phone(args, session)
    if _a3_reason == "no_reference":
        logger.error(
            "[book] A3 SKIPPED — phone_confirmed=True but no number on record "
            "(collected.phone=%r phone_number=%r) clinic=%s",
            _cg_collected.get("phone"), session.get("phone_number"),
            session.get("clinic_id"),
        )
    elif _a3_fix:
        logger.error(
            "[book] A3 — booking phone corrected: model passed %r, confirmed "
            "number is %r; booking on the confirmed number. clinic=%s",
            args.get("phone"), _a3_fix, session.get("clinic_id"),
        )
        session["phone_arg_corrected"] = True
        args["phone"] = _a3_fix

    # Theorem clinic (both numbers) uses Acuity Scheduling; demo clinic uses Google Calendar
    if _resolve_clinic_id(session) in ("theorem", "theorem_v2", "theorem_v3"):
        return await _book_appointment_acuity(args, session)

    from app.clinic_config import get_clinic

    clinic = get_clinic(session.get("clinic_id"))
    # Provisional clinics never auto-confirm — book the published slot as PENDING
    # and notify the owner, who confirms with the caller directly.
    if clinic.get("booking_system") == "google_calendar_provisional":
        return await _book_appointment_provisional(args, session, clinic)

    from app.tools.calendar_google import create_event
    from app.notifications.booking_sms import send_booking_confirmation

    tokens = await _get_tokens()
    location = (args.get("location") or "").lower().strip()
    # BUG-5: carry the confirmed modality into the booking. The model sometimes
    # reverts to the in-clinic default (a physical site) at book time even after
    # the caller chose remote/video/phone or a home visit — availability was
    # already checked for that modality (_checked_location, pinned by
    # check_availability). If the checked modality is remote/home but the book
    # location is an in-clinic site, trust the checked modality so the calendar
    # event, price and SMS template all match what the caller actually booked.
    _REMOTE_HOME = ("remote", "video", "phone", "online", "virtual", "home_visit", "home visit")
    _checked_mod = (session.get("_checked_location") or "").lower().strip()
    if _checked_mod in _REMOTE_HOME and location not in _REMOTE_HOME:
        logger.info(
            "[book] modality reconciled: book location %r → %r (matches checked availability)",
            location, _checked_mod,
        )
        location = _checked_mod
    patient_name = args.get("patient_name", "")
    phone = args.get("phone", "")
    service = args.get("service", "physiotherapy")
    is_new = bool(args.get("is_new_patient", False))
    insurer = (args.get("insurer_name") or "").strip()
    policy = (args.get("policy_number") or "").strip()
    followup_note = (args.get("followup_note") or "").strip()

    # ── Service×modality guard (P2) ────────────────────────────────────────
    # Reject impossible combinations (e.g. msk_initial_assessment booked
    # "remote", which clinic.json does not allow) and correct the common
    # failure where the model defaults to the wrong service at booking time
    # despite checking availability for a different one. If the requested
    # service can't be booked under this modality but the service we actually
    # showed slots for (pinned at check_availability) can, substitute it;
    # otherwise ask the model to re-resolve. Never surfaced to the caller.
    # Normalise the booking location to an available_as modality token. The
    # location enum the model sees is the physical site id ('bolton') for
    # in-clinic, plus 'remote'/'home_visit'; clinic.json declares availability
    # as 'in_clinic'/'remote'/'home_visit'. Map any physical site id (or the
    # primary location) to 'in_clinic' so the guard doesn't reject the normal
    # in-clinic path.
    _loc_for_check = _location_to_modality(clinic, location)

    # ── Service reconciliation (Call 3, 2026-07-08) ────────────────────────
    # Symmetric with the modality reconciliation above. The slot the caller
    # picked was generated for the service pinned at check_availability
    # (_checked_service) — its length and time grid belong to THAT service.
    # If the model books a DIFFERENT but modality-valid service (e.g. the call
    # opened on acupuncture, pivoted to a neuro assessment, and the model
    # reverted to acupuncture at book time), the P2 guard below cannot catch it
    # because the wrong service is itself bookable under this modality. Trust
    # the checked service so the event type, duration, price and SMS all match
    # the slot the caller actually chose. Only reconcile when the checked
    # service exists and is valid for this modality; otherwise leave it to the
    # P2 guard (which rejects/re-resolves impossible service×modality combos).
    _checked_svc = (session.get("_checked_service") or "").strip()
    if _checked_svc and _checked_svc.lower() != (service or "").lower():
        _checked_svc_def = _find_service_def(clinic, _checked_svc)
        if _checked_svc_def and _service_supports_location(
            _checked_svc_def, _loc_for_check
        ):
            logger.warning(
                "[book] service reconciled: book service %r → %r "
                "(matches checked availability)",
                service, _checked_svc,
            )
            service = _checked_svc

    _svc_def = _find_service_def(clinic, service)
    if _svc_def and _loc_for_check and not _service_supports_location(_svc_def, _loc_for_check):
        _pinned = (session.get("_checked_service") or "").strip()
        _pinned_def = _find_service_def(clinic, _pinned) if _pinned else None
        if (
            _pinned_def
            and _pinned.lower() != (service or "").lower()
            and _service_supports_location(_pinned_def, _loc_for_check)
        ):
            logger.warning(
                "[ms_tools] book_appointment service×modality fix: %r not available "
                "as %r — substituting checked service %r",
                service, _loc_for_check, _pinned,
            )
            service = _pinned
            _svc_def = _pinned_def
        else:
            logger.warning(
                "[ms_tools] book_appointment rejected: service %r not available as %r",
                service, _loc_for_check,
            )
            _avail = ", ".join(str(t) for t in (_svc_def.get("available_as") or [])) or "none"
            return {
                "success": False,
                "error": (
                    f"'{service}' cannot be booked as '{location}' "
                    f"(it is only available as: {_avail}). "
                    f"Re-resolve the correct service for this modality and call "
                    f"book_appointment again with that service. Do NOT mention this "
                    f"correction to the caller."
                ),
            }

    # Resolve slot_iso — handles ISO strings, labels, and slot indices
    try:
        start_dt = _resolve_slot_iso(args.get("slot_iso", ""), session)
        # Event length = the service's own duration (services[].typical_duration_minutes),
        # matching the slot length offered in check_availability — so a 30-min treatment
        # books 30 min and a 60-min neuro assessment books 60, not a flat default.
        if args.get("duration_minutes"):
            session["_service_duration_choice"] = int(args["duration_minutes"])
        _bk_dur = int(
            args.get("duration_minutes")
            or _service_duration_minutes(
                clinic, service, clinic.get("slot_minutes") or 30,
                preferred=session.get("_service_duration_choice"),
            )
        )
        end_dt = start_dt + timedelta(minutes=_bk_dur)
    except Exception as e:
        return {"success": False, "error": f"Invalid slot datetime: {e}"}

    # FIX #9: Reject slots in the past
    now_london = datetime.now(LONDON_TZ)
    if start_dt <= now_london:
        return {
            "success": False,
            "error": (
                f"Cannot book a slot in the past "
                f"({start_dt.strftime('%a %d %b at %H:%M')}). "
                "Please check availability again for current options."
            ),
        }

    # Human-readable slot label — defined once here so EVERY branch and return
    # can use it safely. Previously it was only assigned inside the no-tokens
    # branch, so a calendar-connected booking that referenced it raised
    # UnboundLocalError ("cannot access local variable 'booked_label'").
    booked_label = start_dt.strftime("%A %d %B at %H:%M")

    if not tokens:
        # Calendar not connected — log intent to Sheets so the clinic can follow up,
        # then tell Claude the booking succeeded so it doesn't loop with "slot unavailable".
        try:
            from app.tools.handoff import send_to_sheet
            await asyncio.to_thread(
                send_to_sheet,
                patient_name, phone, "BOOK_MANUAL",
                (
                    f"MANUAL BOOKING NEEDED: {service} at {location.title()} on {booked_label} "
                    f"({'new' if is_new else 'returning'} patient)"
                    + (f" | Insurer: {insurer}" if insurer else "")
                    + (f" | Policy: {policy}" if policy else "")
                ),
                session.get("call_sid", ""),
                "Phase3 AI Receptionist",
            )
        except Exception as e:
            logger.warning("book_appointment (no calendar) Sheets log failed (non-fatal): %r", e)

        session.setdefault("collected", {})
        # Manual-followup branch: no calendar event exists, so the summary row
        # IS the record and a stale name here is worse, not better.
        _sync_booked_patient_name(session, patient_name)
        session["collected"]["phone"] = phone
        session["collected"]["service"] = service
        session["collected"]["slot"] = start_dt.isoformat()
        session["selected_slot"] = start_dt.isoformat()
        session["calendar_status"] = "manual_followup"

        # Owner heads-up FIRST — Marcus is alerted (needs manual entry) before the
        # patient confirmation. No-op unless the clinic enables owner_alerts.
        from app.notifications.owner_alert import notify_owner
        await notify_owner(
            session,
            event="manual_followup",
            patient_name=patient_name,
            when_label=booked_label,
            service=service,
            location=location,
            is_new_patient=is_new,
            note=followup_note,
        )

        # Still send the confirmation SMS — the caller was told "all booked" and a
        # human enters it into the booking system, so they get the same confirmation
        # text as a calendar-backed booking. Non-fatal; build_sms uses the session.
        try:
            from app.notifications.booking_sms import send_booking_confirmation
            await send_booking_confirmation(
                patient_phone=phone,
                patient_name=patient_name,
                appointment_time=start_dt,
                location=location.title(),
                service=service,
                is_new_patient=is_new,
                has_insurance=bool(insurer),
                insurer=insurer or None,
                clinic_name=clinic.get("sms_name") or clinic.get("display_name"),
                clinic_phone=clinic.get("sms_phone") or clinic.get("phone"),
                session=session,
            )
            session["confirmation_sms_sent"] = True
        except Exception as e:
            logger.warning("book_appointment (no calendar) SMS failed (non-fatal): %r", e)

        # Home visit — schedule the 30-min address-request nudge too. Gated on
        # location too (a home_visit of any service, not just the home_visit svc).
        if location == "home_visit" or "home_visit" in (service or "").lower() or "home visit" in (service or "").lower():
            try:
                from app.notifications.scheduler import schedule_address_reminder
                from app.clinic_config import twilio_number_for_clinic
                _cid = clinic.get("clinic_id") or session.get("clinic_id") or ""
                await schedule_address_reminder(
                    phone=phone,
                    first_name=(patient_name.split()[0] if patient_name else "there"),
                    from_number=twilio_number_for_clinic(_cid),
                    clinic_id=_cid,
                )
            except Exception as e:
                logger.warning("book_appointment (no calendar) address reminder failed (non-fatal): %r", e)

        # Insurance booking — ping the owner so they can collect pre-auth/cover details (Option B).
        if insurer:
            await _ping_owner_insurance(clinic, patient_name, phone, start_dt, service, insurer)

        # Ping the practitioner if this booking needs follow-up (home visit or a
        # flagged note) — patient is booked in directly regardless.
        await _send_practitioner_followup_ping(
            clinic, patient_name, phone, service, location, booked_label,
            args.get("followup_note", ""),
        )

        return {
            "success": True,
            "booked_slot": booked_label,
            "location": location.title(),
            "note": "Calendar not connected — logged for manual confirmation by clinic team.",
        }

    # Resolve the internal service_id (e.g. "msk_initial_assessment") to the
    # caller-facing name (e.g. "Initial Assessment (MSK)") for a readable event.
    _svc_name = service
    for _s in (clinic.get("services") or []):
        if _s.get("service_id") == service or (_s.get("name") or "").lower() == (service or "").lower():
            _svc_name = _s.get("name") or service
            break

    # Event title leads with the service + practitioner so the calendar/Carepatron
    # entry reads e.g. "Acupuncture for Marcus — Quentin Rock" (not a generic
    # assessment). Practitioner comes from clinic config; omitted if unset.
    _prac = (clinic.get("prompt_facts") or {}).get("practitioner") or ""
    summary = (
        f"{_svc_name} for {_prac} — {patient_name}" if _prac
        else f"{patient_name} — {_svc_name}"
    )
    description_parts = [
        f"Patient: {patient_name}",
        f"Phone: {phone}",
        f"Service: {_svc_name}",
        f"Location: {location.title()}",
    ]
    if is_new:
        description_parts.append("New patient")
    if insurer:
        description_parts.append(f"Insurer: {insurer}")
    if policy:
        description_parts.append(f"Policy: {policy}")
    # Surface the caller's concern in the calendar event itself (P3/#9) so Marcus
    # sees it in Carepatron, not only in the practitioner-ping SMS (which can be
    # missed). The same note still drives the ping via _send_practitioner_followup_ping.
    if followup_note:
        description_parts.append(f"Follow-up: {followup_note}")
    description_parts.append("— Booked via Susie (AI receptionist); enter into Carepatron.")
    description = "\n".join(description_parts)

    calendar_id = _resolve_calendar_id(clinic, location)

    try:
        event = await asyncio.to_thread(
            create_event, tokens, start_dt, end_dt, summary, description, calendar_id, "public"
        )
        await _save_gcal_tokens(tokens)   # persist any token refresh that happened inside create_event
        event_id = event.get("id", "")
    except Exception as e:
        logger.error("book_appointment create_event error: %r", e)
        return {"success": False, "error": str(e)}

    # Update session
    session.setdefault("collected", {})
    # Both records of the name, written together so they cannot drift.
    _sync_booked_patient_name(session, patient_name)
    session["collected"]["phone"] = phone
    session["collected"]["service"] = service
    # Record the booking modality ("bolton"/"remote"/"home_visit") so build_sms()
    # picks the right confirmation template (remote → no clinic address/Maps).
    session["collected"]["location"] = location
    session["collected"]["slot"] = args["slot_iso"]
    # Populate the top-level slot key build_sms() reads for the 📅/⏰ lines —
    # mirrors the Acuity path (see _book_appointment_acuity); without it the
    # confirmation SMS renders blank date/time ("—").
    session["selected_slot"] = args["slot_iso"]
    if insurer:
        session["collected"]["insurer"] = insurer
    if policy:
        session["collected"]["policy_number"] = policy
    session["calendar_event_id"] = event_id
    session["calendar_status"] = "created"
    # ── The calendar has accepted the booking — record that it happened ──────
    # `booking_confirmed` is the flag every downstream outcome reader keys off:
    # connection.py derives `reason` from it at teardown ("booked" vs
    # "caller_hung_up"), sets session["call_outcome"], and uses it to suppress
    # the dropped-call owner alert.
    #
    # Until 2026-07-26 it was written ONLY in flow.py (6 sites), never on this
    # tool path — so every booking made through the v3 media-streams flow landed
    # in the calendar and was recorded as an abandoned call. Jules's sweep made
    # it visible: 25 captured calls, real events in the demo calendar, and every
    # single row reading `reason='caller_hung_up'`, `booking_confirmed=None`.
    #
    # The giveaway was one record holding `success=True` AND
    # `reason='caller_hung_up'` at once: `success` reads `confirmation_sms_sent`
    # (which this path does set, just below), `reason` reads `booking_confirmed`
    # (which it did not). None rather than False because session.py seeds the key
    # as None and nothing here ever overwrote it.
    session["booking_confirmed"] = True

    # Owner heads-up FIRST — Marcus is alerted before the patient confirmation.
    # No-op unless the clinic enables owner_alerts (Theorem etc. unaffected).
    from app.notifications.owner_alert import notify_owner
    await notify_owner(
        session,
        event="booking",
        patient_name=patient_name,
        when_label=booked_label,
        service=_svc_name,
        location=location,
        is_new_patient=is_new,
        note=followup_note,
    )

    # Persist the FINAL booked service (post-reconciliation) so downstream
    # consumers see what was actually booked. build_sms() uses it to infer
    # new-vs-returning when the model never stored patient_type (Call-3 P3:
    # a returning msk_treatment_session booking got the new-patient SMS
    # flavour because the is_new tool arg is ignored on the session path and
    # collected.patient_type is never written in the media-streams flow).
    session["_booked_service"] = service

    # Confirmation SMS — failure must never fail the booking
    try:
        await send_booking_confirmation(
            patient_phone=phone,
            patient_name=patient_name,
            appointment_time=start_dt,
            location=location.title(),
            service=service,
            is_new_patient=is_new,
            has_insurance=bool(insurer),
            insurer=insurer or None,
            clinic_name=clinic.get("sms_name") or clinic.get("display_name"),
            clinic_phone=clinic.get("sms_phone") or clinic.get("phone"),
            session=session,
        )
        # Tell the smart SMS router at call end that a confirmation was already sent
        session["confirmation_sms_sent"] = True
    except Exception as e:
        logger.warning("book_appointment SMS failed (non-fatal): %r", e)

    # Schedule day-before (24hr) + 2-hour reminders — non-fatal.
    try:
        from app.notifications.scheduler import schedule_appointment_reminders
        from app.clinic_config import twilio_number_for_clinic
        await schedule_appointment_reminders(
            patient_phone=phone,
            patient_name=patient_name,
            appointment_time=start_dt,
            location=location.title(),
            is_new_patient=is_new,
            has_insurance=bool(insurer),
            insurer=insurer or None,
            clinic_name=clinic.get("sms_name") or clinic.get("display_name"),
            clinic_phone=clinic.get("sms_phone") or clinic.get("phone"),
            from_number=twilio_number_for_clinic(
                clinic.get("clinic_id") or session.get("clinic_id") or ""
            ),
        )
    except Exception as e:
        logger.warning("book_appointment reminder scheduling failed (non-fatal): %r", e)

    # Home visits need the patient's address — schedule a 30-min nudge to text
    # it. Gated on location too (a home_visit of any service, not just the
    # dedicated home_visit service).
    if location == "home_visit" or "home_visit" in (service or "").lower() or "home visit" in (service or "").lower():
        try:
            from app.notifications.scheduler import schedule_address_reminder
            from app.clinic_config import twilio_number_for_clinic
            _cid = clinic.get("clinic_id") or session.get("clinic_id") or ""
            await schedule_address_reminder(
                phone=phone,
                first_name=(patient_name.split()[0] if patient_name else "there"),
                from_number=twilio_number_for_clinic(_cid),
                clinic_id=_cid,
            )
        except Exception as e:
            logger.warning("book_appointment address reminder scheduling failed (non-fatal): %r", e)

    # Insurance booking — ping the owner so they can collect pre-auth/cover details (Option B).
    if insurer:
        await _ping_owner_insurance(clinic, patient_name, phone, start_dt, service, insurer)

    # Remember this booking (any type) keyed by the caller's phone, so that if
    # the patient later TEXTS the clinic, the inbound-SMS handler can label the
    # forward with their appointment — and, for a home visit, write a texted
    # address back onto this calendar event. Non-fatal.
    try:
        from app.flows.triage_legacy import normalize_phone as _np_ctx
        from app.storage.redis_store import set_recent_booking_context
        _is_home_ctx = (
            location == "home_visit"
            or "home_visit" in (service or "").lower()
            or "home visit" in (service or "").lower()
        )
        await set_recent_booking_context(_np_ctx(phone), {
            "name": patient_name,
            "service": _svc_name or service,
            "location": location,
            "slot": booked_label,
            "event_id": event_id,
            "calendar_id": calendar_id,
            "description": description,
            "is_home_visit": _is_home_ctx,
            "clinic_id": session.get("clinic_id") or "",
        })
    except Exception as e:
        logger.warning("book_appointment recent-booking context store failed (non-fatal): %r", e)

    # Ping the practitioner if this booking needs follow-up (home visit or a
    # flagged note) — patient is booked in directly regardless.
    await _send_practitioner_followup_ping(
        clinic, patient_name, phone, service, location, booked_label,
        args.get("followup_note", ""),
    )

    # Sheets log — non-blocking
    try:
        from app.tools.handoff import send_to_sheet
        await asyncio.to_thread(
            send_to_sheet,
            patient_name, phone, "BOOK",
            f"Booked: {service} at {location.title()} on {start_dt.strftime('%d %b %Y %H:%M')}",
            session.get("call_sid", ""),
            "Phase3 AI Receptionist",
        )
    except Exception as e:
        logger.warning("book_appointment Sheets log failed (non-fatal): %r", e)

    return {
        "success": True,
        "event_id": event_id,
        "booked_slot": start_dt.strftime("%A %d %B at %H:%M"),
        "location": location.title(),
    }


# ---------------------------------------------------------------------------
# Executor: lookup_appointment + confirm_appointment_found
# ---------------------------------------------------------------------------

async def _exec_lookup_appointment(
    args: Dict[str, Any], session: Dict[str, Any]
) -> Dict[str, Any]:
    if _resolve_clinic_id(session) in ("theorem", "theorem_v2", "theorem_v3"):
        return await _lookup_appointment_acuity(args, session)
    return {"found": False, "error": "Appointment lookup not supported for this clinic type."}


async def _exec_confirm_appointment_found(
    args: Dict[str, Any], session: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Stage gate: caller has verbally confirmed the found appointment is theirs.
    Requires rc_stage == 'lookup_done' — enforces that lookup_appointment ran first.
    Advances rc_stage to 'confirmed' so cancel/reschedule tools are unlocked.
    """
    if session.get("rc_stage") != "lookup_done":
        return {
            "error": (
                "No pending appointment lookup. Call lookup_appointment first, "
                "present the result to the caller, then call this tool when they confirm."
            )
        }
    session["rc_appointment_confirmed"] = True
    session["rc_stage"] = "confirmed"
    return {
        "confirmed":        True,
        "appointment_id":   session.get("reschedule_appt_id"),
        "appointment_type": session.get("reschedule_appt_type", "appointment"),
    }


# ---------------------------------------------------------------------------
# Executor: cancel_appointment
# ---------------------------------------------------------------------------

async def _exec_cancel_appointment(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    # Theorem clinic (both numbers) uses Acuity Scheduling; demo clinic uses Google Calendar
    if _resolve_clinic_id(session) in ("theorem", "theorem_v2", "theorem_v3"):
        return await _cancel_appointment_acuity(args, session)

    from app.tools.calendar_google import list_upcoming_events, delete_event
    from app.clinic_config import get_clinic

    tokens = await _get_tokens()
    if not tokens:
        return {"success": False, "error": "Calendar not connected."}

    clinic = get_clinic(session.get("clinic_id"))
    location = (args.get("location") or "").lower().strip()
    calendar_id = _resolve_calendar_id(clinic, location)

    try:
        events = await asyncio.to_thread(
            list_upcoming_events, tokens, 60, 50, calendar_id
        )
    except Exception as e:
        return {"success": False, "error": str(e)}

    # Target the exact event the caller confirmed at lookup: prefer the
    # appointment_id from lookup_patient, then the phone, then the name.
    found = _match_gcal_event(events, args, session)

    if not found:
        return {"success": False, "error": "No upcoming appointment found."}

    event_id = found["id"]
    event_summary = found.get("summary", "")
    event_start = (found.get("start") or {}).get("dateTime", "")

    # Patient name for notifications must come from the appointment itself, never
    # the model's arg — it sometimes passes a placeholder ("the caller", "lookup
    # result name") which would render as "Hi lookup"/"Hi the". Fall back to "" so
    # the template greeting becomes "there", never a placeholder.
    _appt_name = _gcal_event_patient_name(found) or session.get("_lookup_patient_name") or ""

    try:
        if clinic.get("booking_system") == "google_calendar_provisional":
            # Give the freed slot back to availability instead of deleting it, so
            # the time Jonathan published becomes bookable again. Falls back to a
            # delete if the restore write fails, so the pending booking is at least
            # cleared either way.
            from app.tools.calendar_google import update_event as _upd
            _avail = clinic.get("provisional_available_label") or "Available"
            try:
                await asyncio.to_thread(_upd, tokens, event_id, _avail, "", calendar_id)
            except Exception as _e:
                logger.warning("provisional cancel: restore-to-available failed, deleting instead: %r", _e)
                await asyncio.to_thread(delete_event, tokens, event_id, calendar_id)
        else:
            await asyncio.to_thread(delete_event, tokens, event_id, calendar_id)
    except Exception as e:
        return {"success": False, "error": str(e)}

    session["calendar_status"] = "cancelled"
    # Mark the cancellation on the session so infer_call_outcome() labels the
    # CallSummaries row "cancelled" (not "abandoned"). The Acuity cancel path
    # (_cancel_appointment_acuity) already sets these; the Google-Calendar path
    # did not, so a caller who hung up right after "your appointment has been
    # cancelled" was mis-logged as abandoned even though the delete succeeded.
    session["cancellation_completed"] = True
    session["cancel_confirmed"] = True

    # SMS notification — non-fatal
    try:
        from app.notifications.booking_sms import send_cancellation_confirmation
        from app.clinic_config import get_clinic as _get_clinic_sms
        from datetime import datetime as _dt
        _c_sms = _get_clinic_sms(session.get("clinic_id"))
        appt_time = _dt.fromisoformat(event_start.replace("Z", "+00:00")) if event_start else None
        if appt_time:
            await send_cancellation_confirmation(
                patient_phone=args.get("phone", ""),
                patient_name=_appt_name,
                appointment_time=appt_time,
                clinic_name=_c_sms.get("sms_name") or _c_sms.get("display_name"),
                clinic_phone=_c_sms.get("sms_phone") or _c_sms.get("phone"),
            )
    except Exception as e:
        logger.warning("cancel_appointment SMS failed (non-fatal): %r", e)

    # Prevent smart router from sending a duplicate follow-up SMS
    session["confirmation_sms_sent"] = True

    # Provisional clinics: keep the owner informed of the cancellation.
    if clinic.get("booking_system") == "google_calendar_provisional":
        try:
            from app.notifications.owner_notify import notify_owner
            await notify_owner(
                clinic,
                f"❌ {clinic.get('sms_name') or 'Clinic'} — booking CANCELLED via Susie. "
                f"{_appt_name} ({args.get('phone','')}) — was {event_start or event_summary}.",
            )
        except Exception as e:
            logger.warning("provisional cancel owner notify failed (non-fatal): %r", e)

    # Owner heads-up on cancellation — no-op unless the clinic enables
    # owner_alerts for 'cancellation' (JV only: Theorem routes to the Acuity
    # path above; demo / Vital Edge have no owner_alerts block). Never fatal.
    try:
        from app.notifications.owner_alert import notify_owner as _notify_owner_alert
        from datetime import datetime as _dt_oa
        _when_oa = ""
        if event_start:
            try:
                _when_oa = _dt_oa.fromisoformat(
                    event_start.replace("Z", "+00:00")
                ).strftime("%A %d %B at %H:%M")
            except (ValueError, TypeError):
                _when_oa = event_start
        await _notify_owner_alert(
            session,
            event="cancellation",
            patient_name=_appt_name,
            when_label=_when_oa,
            service=event_summary,
        )
    except Exception as e:
        logger.warning("cancel owner_alert failed (non-fatal): %r", e)

    return {
        "success": True,
        "cancelled_event": event_summary,
        "was_at": event_start,
    }


# ---------------------------------------------------------------------------
# Executor: reschedule_appointment
# ---------------------------------------------------------------------------

async def _exec_reschedule_appointment(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    # Theorem clinic (both numbers) uses Acuity Scheduling; demo clinic uses Google Calendar
    if _resolve_clinic_id(session) in ("theorem", "theorem_v2", "theorem_v3"):
        return await _reschedule_appointment_acuity(args, session)

    from app.tools.calendar_google import list_upcoming_events, patch_event_time
    from app.clinic_config import get_clinic

    tokens = await _get_tokens()
    if not tokens:
        return {"success": False, "error": "Calendar not connected."}

    clinic = get_clinic(session.get("clinic_id"))
    location = (args.get("location") or "").lower().strip()
    calendar_id = _resolve_calendar_id(clinic, location)

    try:
        events = await asyncio.to_thread(
            list_upcoming_events, tokens, 60, 50, calendar_id
        )
    except Exception as e:
        return {"success": False, "error": str(e)}

    # Same exact-event targeting as cancel: appointment_id → phone → name.
    found = _match_gcal_event(events, args, session)

    if not found:
        return {"success": False, "error": "No upcoming appointment found."}

    event_id = found["id"]

    # Patient name for notifications must come from the appointment itself, never
    # the model's arg — it sometimes passes a placeholder ("the caller", "lookup
    # result name") which would render as "Hi the"/"Hi lookup". Fall back to "" so
    # the template greeting becomes "there", never a placeholder.
    _appt_name = _gcal_event_patient_name(found) or session.get("_lookup_patient_name") or ""

    try:
        new_start = _resolve_slot_iso(args.get("new_slot_iso", ""), session)
        # P2: preserve the ORIGINAL appointment's duration. The model's
        # duration_minutes arg defaults to the initial-assessment length (40) and
        # would silently lengthen e.g. a 30-min treatment session to 40 min on
        # reschedule. Derive the true length from the existing event; fall back to
        # the arg only if the original end time is unavailable.
        _dur_minutes = None
        _orig_start_s = (found.get("start") or {}).get("dateTime", "")
        _orig_end_s = (found.get("end") or {}).get("dateTime", "")
        if _orig_start_s and _orig_end_s:
            try:
                _os = datetime.fromisoformat(_orig_start_s.replace("Z", "+00:00"))
                _oe = datetime.fromisoformat(_orig_end_s.replace("Z", "+00:00"))
                _delta_min = int(round((_oe - _os).total_seconds() / 60))
                if _delta_min > 0:
                    _dur_minutes = _delta_min
            except (ValueError, TypeError):
                _dur_minutes = None
        if _dur_minutes is None:
            _dur_minutes = int(args["duration_minutes"])
        new_end = new_start + timedelta(minutes=_dur_minutes)
    except Exception as e:
        return {"success": False, "error": f"Invalid new slot datetime: {e}"}

    if clinic.get("booking_system") == "google_calendar_provisional":
        # Clean provisional move: CONSUME the newly-chosen published slot (flip it
        # to PENDING, carrying the patient's details) and GIVE THE OLD SLOT BACK to
        # availability — rather than dragging the event onto a time that is still
        # published as an open slot (which would leave a duplicate at the new time
        # and a hole at the old one). Mirrors _book_appointment_provisional.
        from app.tools.calendar_google import update_event as _upd, create_event as _crt
        _p_name = _gcal_event_patient_name(found) or session.get("_lookup_patient_name") or ""
        _p_phone = _gcal_event_phone(found) or (args.get("phone") or "")
        _p_service = _gcal_event_service(found) or "appointment"
        _was_home = "HOME VISIT" in (
            (found.get("summary") or "") + " " + (found.get("description") or "")
        ).upper()
        _new_summary = (
            f"PENDING CONFIRMATION{' — HOME VISIT' if _was_home else ''} — "
            f"{_p_name} — {_p_service}"
        )
        _new_desc = "\n".join(
            [
                "PROVISIONAL booking (rescheduled) via Susie — NOT yet confirmed.",
                f"Patient: {_p_name}",
                f"Phone: {_p_phone}",
                f"Service: {_p_service}",
                f"Requested: {new_start.strftime('%A %d %B %Y at %H:%M')}",
            ]
            + (["HOME VISIT — patient wants the practitioner to travel to them; "
                "confirm feasibility + address on the callback."] if _was_home else [])
            + ["Confirm directly with the client (WhatsApp/phone)."]
        )
        _avail = clinic.get("provisional_available_label") or "Available"
        _slot_map = session.get("_provisional_slot_events") or {}
        _new_src = _slot_map.get(new_start.isoformat())
        try:
            if _new_src and _new_src != event_id:
                # Flip the target published slot to PENDING (consume it)…
                await asyncio.to_thread(_upd, tokens, _new_src, _new_summary, _new_desc, calendar_id)
                _new_event_id = _new_src
                # …then hand the caller's old slot back to availability.
                try:
                    await asyncio.to_thread(_upd, tokens, event_id, _avail, "", calendar_id)
                except Exception as _e:
                    logger.warning("provisional reschedule: old-slot restore failed (non-fatal): %r", _e)
            elif _new_src == event_id:
                # Caller re-picked their own slot — just refresh the labels in place.
                await asyncio.to_thread(_upd, tokens, event_id, _new_summary, _new_desc, calendar_id)
                _new_event_id = event_id
            else:
                # New time isn't a published slot — move the existing event there
                # (no open slot to consume, so nothing to duplicate) and leave the
                # old time freed.
                await asyncio.to_thread(patch_event_time, tokens, event_id, new_start, new_end, calendar_id)
                await asyncio.to_thread(_upd, tokens, event_id, _new_summary, _new_desc, calendar_id)
                _new_event_id = event_id
            await _save_gcal_tokens(tokens)
            session["calendar_event_id"] = _new_event_id
        except Exception as e:
            return {"success": False, "error": str(e)}
    else:
        try:
            await asyncio.to_thread(
                patch_event_time, tokens, event_id, new_start, new_end, calendar_id
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    session["calendar_status"] = "patched"

    # SMS notification — non-fatal
    try:
        from app.clinic_config import get_clinic as _get_clinic_sms
        _c_sms = _get_clinic_sms(session.get("clinic_id"))
        if _c_sms.get("booking_system") == "google_calendar_provisional":
            # Provisional model: the new time is a REQUEST, not confirmed. The
            # caller text must never say "moved / see you then" (the owner also
            # gets a "please confirm" ping below) — Jonathan confirms directly.
            from app.notifications.sms import send_sms as _send_sms_caller
            _prac = _provisional_practitioner(_c_sms)
            _cn = _c_sms.get("sms_name") or _c_sms.get("display_name") or "the clinic"
            _first = _appt_name.split()[0] if (_appt_name or "").strip() else "there"
            _line = _c_sms.get("phone") or ""
            _pmsg = (
                f"Hi {_first}, your reschedule request with {_cn} — "
                f"{_uk_when_label(new_start)} — is with {_prac} to confirm. It "
                f"isn't finalised until he confirms with you directly."
                + (f" {_line}" if _line else "")
            )
            if args.get("phone"):
                await _send_sms_caller(to=args.get("phone", ""), message=_pmsg)
        else:
            from app.notifications.booking_sms import send_reschedule_confirmation
            old_start_str = (found.get("start") or {}).get("dateTime", "")
            if old_start_str:
                old_time = datetime.fromisoformat(old_start_str.replace("Z", "+00:00"))
                # Location clause must reflect the appointment's TRUE modality (read
                # from the event), not the model's arg — a remote/home appointment
                # must never be labelled "at our Bolton clinic". For remote/home (or
                # an unknown legacy event) pass "" so the clause is omitted.
                _evt_loc = _gcal_event_location(found)
                _sms_location = (
                    "" if _evt_loc.lower() in (
                        "remote", "video", "phone", "online", "virtual",
                        "home_visit", "home visit",
                    ) else _evt_loc.title()
                )
                await send_reschedule_confirmation(
                    patient_phone=args.get("phone", ""),
                    patient_name=_appt_name,
                    old_time=old_time,
                    new_time=new_start,
                    location=_sms_location,
                    clinic_name=_c_sms.get("sms_name") or _c_sms.get("display_name"),
                    clinic_phone=_c_sms.get("sms_phone") or _c_sms.get("phone"),
                )
    except Exception as e:
        logger.warning("reschedule_appointment SMS failed (non-fatal): %r", e)

    # Prevent smart router from sending a duplicate follow-up SMS
    session["confirmation_sms_sent"] = True

    # Provisional clinics: the new time is still subject to the owner's
    # confirmation — notify them of the change.
    if clinic.get("booking_system") == "google_calendar_provisional":
        try:
            from app.notifications.owner_notify import notify_owner
            await notify_owner(
                clinic,
                f"🔄 {clinic.get('sms_name') or 'Clinic'} — booking RESCHEDULE requested via Susie. "
                f"{_appt_name} ({args.get('phone','')}) → "
                f"{new_start.strftime('%a %d %b at %H:%M')}. Please confirm with the client.",
            )
        except Exception as e:
            logger.warning("provisional reschedule owner notify failed (non-fatal): %r", e)

    # Owner heads-up on reschedule — no-op unless the clinic enables owner_alerts
    # for 'reschedule' (JV only: Theorem routes to the Acuity path above; demo /
    # Vital Edge have no owner_alerts block). This is an in-place event move
    # (patch_event_time), NOT an internal book+cancel, so exactly ONE alert
    # fires — no double-buzz. Never fatal.
    try:
        from app.notifications.owner_alert import notify_owner as _notify_owner_alert
        await _notify_owner_alert(
            session,
            event="reschedule",
            patient_name=_appt_name,
            when_label=new_start.strftime("%A %d %B at %H:%M"),
            service=found.get("summary", ""),
            location=_gcal_event_location(found),
        )
    except Exception as e:
        logger.warning("reschedule owner_alert failed (non-fatal): %r", e)

    return {
        "success": True,
        "rescheduled_to": new_start.strftime("%A %d %B at %H:%M"),
    }


# ---------------------------------------------------------------------------
# Executor: get_clinic_info
# ---------------------------------------------------------------------------

async def _exec_get_clinic_info(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    from app.clinic_config import get_clinic

    clinic = get_clinic(session.get("clinic_id"))
    location_id = (session.get("selected_location") or "").lower()
    topic = args.get("topic", "")

    # Try location-specific data first (for Theorem's two locations)
    locations = clinic.get("locations", [])
    loc_cfg = next((loc for loc in locations if loc.get("id") == location_id), None)

    if topic == "hours":
        text = (loc_cfg.get("hours_summary") if loc_cfg else None) or clinic.get("hours_summary", "")
    elif topic == "address":
        text = (loc_cfg.get("address") if loc_cfg else None) or clinic.get("address", "")
    elif topic == "parking":
        text = (loc_cfg.get("parking") if loc_cfg else None) or clinic.get("parking", "")
    elif topic == "transport":
        text = (loc_cfg.get("transport") if loc_cfg else None) or clinic.get("transport", "")
    elif topic == "prices":
        text = clinic.get("pricing_summary", "")
    elif topic == "insurance":
        text = clinic.get("insurance_note", "")
    elif topic == "services":
        svcs = clinic.get("services", [])
        _svc_descs = clinic.get("service_descriptions", {})
        if _svc_descs:
            _lines = ["Services offered:"]
            for _name, _desc in _svc_descs.items():
                _lines.append(f"- {_name}: {_desc}")
            text = "\n".join(_lines)
        else:
            text = "Services include: " + ", ".join(svcs) if svcs else ""
    elif topic == "cancellation_policy":
        text = clinic.get("cancellation_policy", "")
    elif topic == "what_to_bring":
        text = clinic.get("what_to_bring", "")
    else:
        # Fall through to the faq dict for any FAQ topic
        text = clinic.get("faq", {}).get(topic, "")

    return {"topic": topic, "info": text or "I don't have that specific information to hand."}


# ---------------------------------------------------------------------------
# Executor: collect_and_store
# ---------------------------------------------------------------------------

_WORD_DIGIT_MAP: Dict[str, str] = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "won": "1",
    "two": "2", "to": "2", "too": "2",
    "three": "3",
    "four": "4", "for": "4", "fore": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8", "ate": "8",
    "nine": "9", "niner": "9",
}


def _spoken_to_digits(text: str) -> str:
    """
    Convert a spoken/transcribed UK phone number to a digit string.

    Handles every format ASR may produce:
    - Pure digit string: "07870166861" → "07870166861"
    - E.164 / with prefix: "+447870166861" → returned as-is (normalize_phone handles it)
    - Spoken words: "zero seven eight seven oh one six six eight six one" → "07870166861"
    - Space-separated single digits: "0 7 8 7 0 1 6 6 8 6 1" → "07870166861"
    - Grouped digits: "07870 166861" → "07870166861"
    - Mixed: "07870 one six six eight six one" → "07870166861"
    - Letter O for zero: "O 7 8 7 0" → "07870"
    - double/treble shorthand: "double six" → "66"
    - Digits with punctuation: "078-701-66-861" → "07870166861"
    """
    import re as _re

    stripped = text.strip()

    # Fast path: already looks like a formatted phone number (only digits, spaces,
    # hyphens, dots, parens, and optional leading +)
    # Let normalize_phone handle E.164 / already-formatted strings directly.
    _clean = _re.sub(r'[\s\-\.\(\)\+]', '', stripped)
    if _clean.isdigit() and len(_clean) >= 7:
        return stripped  # pass through unchanged; normalize_phone strips non-digits

    # Expand "double X" → "X X" and "treble X" → "X X X"
    text = _re.sub(
        r'\bdouble\s+(\w+)',
        lambda m: f"{m.group(1)} {m.group(1)}",
        stripped, flags=_re.IGNORECASE,
    )
    text = _re.sub(
        r'\btreble\s+(\w+)',
        lambda m: f"{m.group(1)} {m.group(1)} {m.group(1)}",
        text, flags=_re.IGNORECASE,
    )

    tokens = _re.split(r'[\s\-,\.\;\(\)]+', text.lower())
    digits: list[str] = []
    for token in tokens:
        if not token:
            continue
        # Pure digit chunk (e.g. "07870", "7", "166")
        if token.isdigit():
            digits.append(token)
            continue
        # Exact word match (e.g. "zero", "oh", "seven")
        if token in _WORD_DIGIT_MAP:
            digits.append(_WORD_DIGIT_MAP[token])
            continue
        # Mixed token like "o7870" or "oh7" — scan char by char
        # Try longest-matching word first, then single char
        i = 0
        local: list[str] = []
        while i < len(token):
            if token[i].isdigit():
                local.append(token[i])
                i += 1
                continue
            # Try to match a known word at this position (longest match first)
            matched = False
            for word in sorted(_WORD_DIGIT_MAP.keys(), key=len, reverse=True):
                if token[i:i + len(word)] == word:
                    local.append(_WORD_DIGIT_MAP[word])
                    i += len(word)
                    matched = True
                    break
            if not matched:
                i += 1  # skip unrecognised character
        if local:
            digits.append("".join(local))
        # non-digit, non-word tokens ("my", "number", "is") → ignored

    return "".join(digits)


async def _exec_collect_and_store(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    field = args.get("field", "")
    value = (args.get("value") or "").strip()

    if not field or not value:
        return {"error": "field and value are required"}

    # Guard: reject confirmation words stored as phone numbers.
    # Catches the common LLM mistake of collect_and_store(field="phone", value="yes")
    # when the caller confirmed their caller_number — the actual digits must be stored.
    if field == "phone":
        _CONFIRM_WORDS = {
            "yes", "yeah", "yep", "yup", "correct", "that's right", "that's it",
            "right", "sure", "ok", "okay", "confirmed", "affirmative",
        }
        if value.lower() in _CONFIRM_WORDS:
            return {
                "error": (
                    f"'{value}' is not a valid phone number — you stored a confirmation word. "
                    "Store the actual phone number digits, not the caller's spoken confirmation. "
                    "Check caller_number in the known context and call collect_and_store again "
                    "with those exact digits as the value."
                )
            }

    session.setdefault("collected", {})

    # Normalise phone: convert spoken words to digits, then to E.164
    if field == "phone":
        # First convert any word-based digits ("zero seven eight..." → "0780...")
        converted = _spoken_to_digits(value)
        if converted:
            value = converted
        try:
            from app.flows.triage_legacy import normalize_phone
            value = normalize_phone(value)
        except Exception:
            pass

        # Guard: reject partial phone numbers — UK mobiles are 11 digits (07xxx xxxxxxx).
        # Catching 5-digit partials here prevents the "first five digits" mid-collection
        # from being silently stored and causing the booking to proceed with bad data.
        import re as _re
        _digit_count = len(_re.sub(r"\D", "", value))
        if _digit_count < 10:
            return {
                "error": (
                    f"Partial phone number — only {_digit_count} digit(s) received. "
                    "Do NOT store phone after the first five digits alone. "
                    "Ask for the remaining digits (Part 2), combine both parts into the "
                    "full number, confirm it with the caller, THEN call collect_and_store."
                )
            }

    # Keep session location keys in sync (normalise STT variants → canonical ID)
    if field == "location":
        session["selected_location"] = _normalize_location(value)
        session["location_selected"] = True

    # full_name is the preferred field for collecting the caller's name as a
    # single utterance.  Store under both "full_name" and "name" so all
    # downstream code (booking, context display, call summary) continues to
    # work regardless of which key it reads from.
    if field == "full_name":
        session["collected"]["full_name"] = value
        session["collected"]["name"] = value
        return {"ok": True}

    session["collected"][field] = value
    return {"ok": True}


# ---------------------------------------------------------------------------
# Executor: transfer_to_human
# ---------------------------------------------------------------------------

async def _exec_transfer_to_human(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    reason = (args.get("reason") or "caller requested").strip()

    # Critical: this flag is checked by twilio.py after handle_turn returns
    session["request_transfer"] = True
    session["human_requested"] = True
    session["manual_followup_reason"] = reason

    collected = session.get("collected") or {}
    caller_name  = collected.get("name", "")
    caller_phone = session.get("twilio_from", "") or collected.get("phone", "")
    call_reason  = collected.get("reason", "") or reason

    # Fire-and-forget heads-up SMS — don't block the tool return waiting for Twilio
    try:
        from app.clinic_config import get_clinic
        from app.notifications.sms import send_sms
        from app.config import TRANSFER_DISABLED
        from app.routes.realtime import resolve_transfer_target
        clinic = get_clinic(session.get("clinic_id"))
        transfer_phone = clinic.get("transfer_phone", "")
        caller_snippet = (
            f" from {caller_phone}"
            if (caller_phone and not caller_phone.startswith("client:"))
            else ""
        )
        # TRANSFER_DISABLED (test-sweep kill-switch) suppresses ALL outbound SMS
        # here so a run doesn't text a real staff number. No-op in production,
        # and it must stay fully silent — the recovery branch below is not an
        # exception to it.
        if TRANSFER_DISABLED:
            pass
        elif transfer_phone and resolve_transfer_target(session):
            asyncio.create_task(send_sms(
                to=transfer_phone,
                message=f"📞 Susie is transferring a patient{caller_snippet} — call coming through now.",
            ))
        elif transfer_phone:
            # No dial target, so _handle_transfer will place no leg. Telling the
            # practitioner a call is "coming through now" when none is coming is
            # a false alarm that also buries the real one: they wait for a ring
            # instead of ringing back. Say what actually happened.
            asyncio.create_task(send_sms(
                to=transfer_phone,
                message=(
                    f"📞 CALLBACK NEEDED — {caller_name or 'a caller'} asked to be put "
                    f"through but the transfer could not be placed. Please call them "
                    f"back on {caller_phone or 'the number they called from'}. "
                    f"Reason: {reason}."
                ),
            ))
            logger.error(
                "transfer_to_human: no dial target — sent CALLBACK NEEDED instead "
                "of a transfer heads-up (clinic=%r)", session.get("clinic_id"),
            )
    except Exception as e:
        logger.warning("transfer_to_human SMS alert failed (non-fatal): %r", e)

    # Fire-and-forget Sheets log — don't block the tool return waiting for Sheets
    try:
        from app.tools.handoff import send_to_sheet
        asyncio.create_task(asyncio.to_thread(
            send_to_sheet,
            caller_name or "Unknown",
            caller_phone or collected.get("phone", ""),
            "TRANSFER",
            f"Transfer requested: {reason}",
            session.get("call_sid", ""),
            "Phase3 AI Receptionist",
        ))
    except Exception as e:
        logger.warning("transfer_to_human Sheets log failed (non-fatal): %r", e)

    return {"transfer_initiated": True, "reason": reason}


# ---------------------------------------------------------------------------
# Executor: send_followup_sms
# ---------------------------------------------------------------------------

async def _exec_send_followup_sms(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    phone = (args.get("phone") or "").strip()
    msg_type = args.get("message_type", "general")
    custom_msg = (args.get("custom_message") or "").strip()

    if not phone:
        return {"sent": False, "error": "Phone number is required."}

    try:
        if msg_type == "callback_request":
            from app.notifications.booking_sms import send_callback_confirmation
            collected = session.get("collected") or {}
            name = collected.get("name", "")
            await send_callback_confirmation(patient_phone=phone, patient_name=name)
            return {"sent": True, "type": msg_type}

        if msg_type == "general" and custom_msg:
            from app.notifications.sms import send_sms
            await send_sms(to=phone, message=custom_msg)
            return {"sent": True, "type": msg_type}

        return {"sent": False, "error": "No message sent — check message_type and custom_message."}

    except Exception as e:
        logger.error("send_followup_sms failed: %r", e)
        return {"sent": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Executor: log_call_outcome
# ---------------------------------------------------------------------------

async def _exec_log_call_outcome(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    outcome = args.get("outcome", "abandoned")
    notes = (args.get("notes") or "").strip()

    session["call_outcome_logged"] = outcome
    session["call_outcome_notes"] = notes
    session["intent"] = outcome.upper()
    session["call_ended"] = True  # Signal to pipeline that call should wind down

    # Fire-and-forget: log to Google Sheets if configured
    try:
        from app.tools.handoff import fire_and_forget_append_summary_row
        fire_and_forget_append_summary_row(session)
    except Exception:
        pass

    return {"logged": True, "outcome": outcome}


# ---------------------------------------------------------------------------
# Tool executor registry
# ---------------------------------------------------------------------------

async def _exec_get_patient_history(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """Look up a patient's recent appointment history in Acuity to identify their treatment."""
    if session.get("clinic_id") not in ("theorem", "theorem_v2", "theorem_v3"):
        return {"found": False, "message": "Patient history lookup only available for Theorem clinic"}

    adapter = _get_acuity_adapter()
    if not adapter:
        return {"found": False, "message": "Scheduling system not configured"}

    patient_name = (args.get("patient_name") or "").strip()
    if not patient_name:
        return {"found": False, "message": "Patient name required"}

    caller_phone_digits = "".join(c for c in (args.get("phone") or "") if c.isdigit())
    caller_phone_last10 = caller_phone_digits[-10:] if len(caller_phone_digits) >= 10 else ""

    today = datetime.now(LONDON_TZ).date()
    min_date = today - timedelta(days=120)   # look back 4 months
    max_date = today + timedelta(days=30)    # include upcoming sessions on the plan

    try:
        appointments = await adapter.list_appointments(min_date=min_date, max_date=max_date)
    except Exception as exc:
        logger.warning("get_patient_history: list_appointments failed: %r", exc)
        return {"found": False, "message": "Could not retrieve appointment history"}

    # Match by name — fuzzy matching with rapidfuzz (handles typos, accents)
    try:
        from rapidfuzz import fuzz
    except ImportError:
        fuzz = None

    name_lower = patient_name.strip().lower()
    matching = []
    for appt in appointments:
        first = (appt.get("firstName") or "").lower()
        last  = (appt.get("lastName") or "").lower()
        full  = f"{first} {last}".strip()
        if not full:
            continue
        if fuzz:
            score = fuzz.token_sort_ratio(name_lower, full)
            if score >= 75:
                matching.append(appt)
        else:
            # Fallback: substring match (original behaviour)
            name_parts = [p.lower() for p in patient_name.split() if p]
            if any(part in full for part in name_parts):
                matching.append(appt)

    if not matching:
        return {"found": False, "message": f"No appointments found for {patient_name}"}

    # Sort most-recent first
    def _dt(a: dict) -> datetime:
        try:
            raw = a.get("datetime") or a.get("time") or ""
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=pytz.utc)

    matching.sort(key=_dt, reverse=True)

    # If caller's phone was provided, narrow to phone-matching appointments.
    # Collapses same-name multi-patient ambiguity without an LLM disambiguation turn.
    if caller_phone_last10:
        _before_count = len(matching)
        _phone_filtered = [
            _a for _a in matching
            if "".join(
                c for c in (_a.get("phone") or _a.get("smsReminderNumber") or "") if c.isdigit()
            )[-10:] == caller_phone_last10
        ]
        if _phone_filtered:
            matching = _phone_filtered
            logger.info(
                "get_patient_history: phone pre-filter narrowed %d → %d appointments",
                _before_count, len(_phone_filtered),
            )

    # Detect same-name ambiguity: group appointments by phone number
    _phone_groups: dict = {}
    for _appt in matching:
        _ph = (_appt.get("phone") or _appt.get("smsReminderNumber") or "").strip()
        _ph_key = _ph or "__unknown__"
        _phone_groups.setdefault(_ph_key, []).append(_appt)

    if len(_phone_groups) > 1:
        # Multiple distinct patients share this name — return all for disambiguation
        _multi = []
        for _ph_key, _appts in _phone_groups.items():
            _appts_sorted = sorted(_appts, key=_dt, reverse=True)
            _t = next(
                (a.get("type", "").strip() for a in _appts_sorted if a.get("type")),
                "physiotherapy",
            )
            _last4 = _ph_key[-4:] if _ph_key != "__unknown__" else "????"
            _name_appt = _appts_sorted[0]
            _full = f"{_name_appt.get('firstName', '')} {_name_appt.get('lastName', '')}".strip()
            _multi.append({"phone_last4": _last4, "name": _full, "most_recent_type": _t})
        return {"found": "multiple", "matches": _multi}

    # Collect unique treatment types from the 5 most recent appointments
    seen: list = []
    for appt in matching[:5]:
        t = (appt.get("type") or "").strip()
        if t and t not in seen:
            seen.append(t)

    most_recent_type = seen[0] if seen else "physiotherapy"
    return {
        "found": True,
        "most_recent_type": most_recent_type,
        "recent_types": seen,
        "appointment_count": len(matching),
    }


async def _exec_lookup_recent_appointment(
    args: Dict[str, Any], session: Dict[str, Any]
) -> Dict[str, Any]:
    """Phone-only lookup of a patient's most recent appointment within 90 days.

    Used by the RETURNING_PLAN_LOOKUP booking step to retrieve the canonical
    name and treatment type before asking any further questions.

    Returns:
        found=True  → first_name, last_name, full_name, last_appointment_type, phone
        found=False → message explaining why
    """
    if _resolve_clinic_id(session) not in ("theorem", "theorem_v2", "theorem_v3"):
        return {"found": False, "message": "Recent appointment lookup only available for Theorem clinic"}

    adapter = _get_acuity_adapter()
    if not adapter:
        return {"found": False, "message": "Scheduling system not configured"}

    phone_raw    = (args.get("phone") or "").strip()
    phone_digits = "".join(c for c in phone_raw if c.isdigit())
    if not phone_digits:
        return {"found": False, "message": "Phone number is required"}
    phone_last10 = phone_digits[-10:] if len(phone_digits) >= 10 else phone_digits

    location = _normalize_location(
        args.get("location") or session.get("selected_location", "")
    )

    today    = datetime.now(LONDON_TZ).date()
    min_date = today - timedelta(days=90)
    max_date = today

    from app.clinic_config import THEOREM_LOCATIONS as _TL
    cal_id = _TL.get(location, {}).get("acuity_calendar_id") if location else None

    try:
        appointments = await adapter.list_appointments(
            min_date=min_date, max_date=max_date, calendar_id=cal_id
        )
    except Exception as exc:
        logger.warning("_exec_lookup_recent_appointment: list_appointments failed: %r", exc)
        return {"found": False, "message": "Could not retrieve recent appointments"}

    logger.info(
        "_exec_lookup_recent_appointment: %d appointment(s) in past 90 days "
        "(loc=%r cal_id=%r range=%s..%s)",
        len(appointments), location, cal_id,
        min_date.isoformat(), max_date.isoformat(),
    )

    # Match strictly by phone (last 10 digits)
    matches = []
    for appt in appointments:
        appt_phone_d = "".join(
            c for c in (appt.get("phone") or appt.get("smsReminderNumber") or "") if c.isdigit()
        )
        if not appt_phone_d:
            continue
        if appt_phone_d[-10:] == phone_last10:
            matches.append(appt)

    if not matches:
        logger.info(
            "_exec_lookup_recent_appointment: no match for phone ***%s", phone_last10[-4:]
        )
        return {"found": False, "message": "No recent appointment found for this number"}

    # Sort most-recent first
    def _appt_dt(a: dict) -> datetime:
        try:
            raw = a.get("datetime") or a.get("time") or ""
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=pytz.utc)

    matches.sort(key=_appt_dt, reverse=True)
    most_recent = matches[0]

    first_name = (most_recent.get("firstName") or "").strip()
    last_name  = (most_recent.get("lastName")  or "").strip()
    full_name  = f"{first_name} {last_name}".strip()
    appt_type  = (most_recent.get("type") or "physiotherapy").strip()

    # Collect all unique types from the last 5 appointments for richer context
    seen_types: list = []
    for a in matches[:5]:
        t = (a.get("type") or "").strip()
        if t and t not in seen_types:
            seen_types.append(t)
    most_recent_type = seen_types[0] if seen_types else appt_type

    # Store in session so the booking step can use canonical values without
    # asking the caller for their name or phone again.
    session["full_name"]     = full_name
    session["returning_plan_lookup_name"] = full_name
    session["returning_plan_lookup_type"] = most_recent_type
    session.setdefault("collected", {})["full_name"] = full_name

    # Never overwrite a number the caller confirmed on THIS call with the one
    # on file at the booking provider.
    #
    # collected["phone"] is the reference _reconcile_booking_phone (the A3 gate)
    # compares the model's `phone` argument against. Overwriting it here means a
    # caller who typed a new number and then hit a lookup would have the gate
    # "correct" their booking back to the stale number — the gate that exists to
    # protect the confirmed number would be the thing discarding it.
    #
    # Mirrors the same refusal on the caller-ID path (connection.py, "verbal
    # phone confirm SKIPPED — keypad number already on record"). Gated on
    # phone_confirmed rather than phone_entered_by_keypad so a verbally
    # confirmed caller-ID number is protected too — A1/A3 already treat that
    # flag as the authority on which number is real.
    #
    # Reachability was measured, not assumed: 0 of 155 obs calls had both a
    # caller-supplied number and the lookup path (10 had one, 4 the other,
    # none both). This is hardening against a mechanism, not a reproduced
    # defect — see B-47 in docs/plan/REGISTER_B_U.md.
    if session.get("phone_confirmed") is True:
        logger.info(
            "lookup_recent_appointment: keeping the number confirmed on this "
            "call (***%s) — NOT overwriting with the number on file (***%s)",
            str((session.get("collected") or {}).get("phone") or "")[-4:],
            str(phone_raw or "")[-4:],
        )
    else:
        session["phone_number"] = phone_raw
        session.setdefault("collected", {})["phone"] = phone_raw

    logger.info(
        "_exec_lookup_recent_appointment: found %r type=%r (phone ***%s)",
        full_name, most_recent_type, phone_last10[-4:],
    )
    return {
        "found":               True,
        "first_name":          first_name,
        "last_name":           last_name,
        "full_name":           full_name,
        "last_appointment_type": most_recent_type,
        "phone":               phone_raw,
    }


async def _exec_add_to_waitlist(args: dict, session: dict) -> dict:
    """Add a caller to the clinic waitlist in Redis."""
    patient_name = (args.get("patient_name") or "").strip()
    phone = (args.get("phone") or "").strip()
    location = (args.get("location") or "").strip()
    service = (args.get("service") or "").strip()
    notes = (args.get("notes") or "").strip()

    if not patient_name or not phone:
        return {"error": "Name and phone are required for the waitlist."}

    # Heads-up SMS to the clinic (transfer_phone, e.g. Marcus) so a waitlist /
    # coming-soon enquiry actually reaches a human — without this the entry just
    # sits in Redis and nobody follows up, so "the team will be in touch" is an
    # empty promise. Fire-and-forget, non-fatal, deduped to one per call.
    if not session.get("_waitlist_pinged"):
        try:
            from app.clinic_config import get_clinic
            from app.notifications.sms import send_sms
            _clinic = get_clinic(session.get("clinic_id"))
            _tp = _clinic.get("transfer_phone", "")
            if _tp:
                _what = (notes or service or "an enquiry").strip()
                asyncio.create_task(send_sms(
                    to=_tp,
                    message=(
                        f"📞 CALLBACK NEEDED — please call {patient_name} back on "
                        f"{phone}. They asked about: {_what}."
                    ),
                ))
                session["_waitlist_pinged"] = True
                logger.info(
                    "waitlist practitioner ping queued to ***%s",
                    _tp[-4:] if _tp else "????",
                )
        except Exception as e:
            logger.warning("waitlist practitioner ping failed (non-fatal): %r", e)

    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            import json as _json
            waitlist_entry = {
                "patient_name": patient_name,
                "phone": phone,
                "location": location,
                "service": service,
                "notes": notes,
                "added_at": _iso_now(),
                "call_sid": session.get("call_sid", ""),
            }
            key = f"waitlist:{phone}:{patient_name.lower().replace(' ', '_')}"
            await redis_client.setex(key, 60 * 60 * 24 * 30, _json.dumps(waitlist_entry))  # 30 days
            logger.info("Waitlist entry added for ***%s", phone[-4:] if phone else "????")
            return {"success": True, "message": f"{patient_name} has been added to the waitlist."}
        else:
            # No Redis — store in session for later pickup
            session.setdefault("waitlist_request", {
                "patient_name": patient_name,
                "phone": phone,
                "location": location,
                "service": service,
                "notes": notes,
            })
            return {"success": True, "message": f"{patient_name} has been added to the waitlist."}
    except Exception as exc:
        logger.error("Waitlist add failed: %r", exc)
        return {"error": "I wasn't able to add you to the waitlist — the team will follow up."}


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Executor: lookup_patient
# Consolidates: lookup_appointment + lookup_recent_appointment + get_patient_history
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Google-Calendar appointment parsing + lookup (template clinics, e.g. jv_v1)
# ---------------------------------------------------------------------------
# Susie's bookings are calendar events: summary "Name — Service",
# description "Patient: …\nPhone: …\nService: …\nLocation: …".

def _gcal_event_phone(ev: Dict[str, Any]) -> str:
    for line in (ev.get("description") or "").splitlines():
        if line.strip().lower().startswith("phone:"):
            return line.split(":", 1)[1].strip()
    return ""


def _gcal_event_location(ev: Dict[str, Any]) -> str:
    """Read the modality from the event description's "Location:" line
    (book_appointment writes "Location: Bolton/Remote/Home_Visit"). Empty when
    absent (legacy/external events)."""
    for line in (ev.get("description") or "").splitlines():
        if line.strip().lower().startswith("location:"):
            return line.split(":", 1)[1].strip()
    return ""


# Leading segments of a calendar title that describe the BOOKING'S STATE, not a
# person: the provisional writer titles events
# "PENDING CONFIRMATION [— HOME VISIT] — <name> — <service>". Treating the first
# segment as the name therefore returned "PENDING CONFIRMATION", and the SMS
# greeting (first token only) reached a real caller as "Hi PENDING" after a
# cancellation on Vital Edge, 2026-08-08. Matched on the first word so
# "PENDING", "PENDING CONFIRMATION" and "BOOKED — …" all strip.
_SUMMARY_STATUS_WORDS = frozenset({
    "pending", "booked", "confirmed", "provisional", "cancelled", "canceled",
    "available", "home",   # "HOME VISIT"
})


def _gcal_summary_segments(ev: Dict[str, Any]) -> List[str]:
    """Em-dash segments of an event title with any leading status markers
    dropped, so segment 0 is the patient and segment 1 the service under both
    the provisional ("PENDING CONFIRMATION — Name — Service") and the legacy
    ("Name — Service") titles."""
    parts = [p.strip() for p in (ev.get("summary") or "").split("—")]
    while parts and (
        not parts[0]
        or parts[0].split()[0].strip(":,").lower() in _SUMMARY_STATUS_WORDS
    ):
        parts.pop(0)
    return parts


def _gcal_event_patient_name(ev: Dict[str, Any]) -> str:
    # Read the patient name from the structured `description` ("Patient: …"),
    # which book_appointment always writes. Splitting the summary is unreliable
    # because the title ordering depends on whether a practitioner is set
    # ("Service for Prac — Patient" vs "Patient — Service"), which previously
    # made cancel/reschedule lookups return the SERVICE as the name (P14).
    for line in (ev.get("description") or "").splitlines():
        if line.strip().lower().startswith("patient:"):
            return line.split(":", 1)[1].strip()
    # Fallback for legacy/externally-created events with no structured
    # description: assume the historical "Patient — Service" ordering, after
    # dropping any status marker. Returns "" rather than a marker word, so the
    # callers fall through to _lookup_patient_name / the "there" greeting.
    segments = _gcal_summary_segments(ev)
    return segments[0] if segments else ""


def _gcal_event_service(ev: Dict[str, Any]) -> str:
    # Read the service from the structured `description` ("Service: …") for the
    # same reason as the patient name above (P14).
    for line in (ev.get("description") or "").splitlines():
        if line.strip().lower().startswith("service:"):
            return line.split(":", 1)[1].strip()
    segments = _gcal_summary_segments(ev)
    return segments[1] if len(segments) > 1 else ""


# Placeholder values the model emits into patient_name when it has not carried
# the real name through from lookup_patient. Observed live: patient_name=
# "Unknown" on CA1fc9cb13337ccc7eb936e0dbf5c8fc3d (2026-08-02) — harmless there
# only because the appointment_id branch matched first. None of these may ever
# be allowed to select an appointment to move or cancel.
_PLACEHOLDER_PATIENT_NAMES = frozenset({
    "unknown", "unknown caller", "unknown patient", "unnamed",
    "caller", "the caller", "patient", "the patient", "client", "the client",
    "name", "patient name", "first name", "full name", "lookup result name",
    "n/a", "na", "none", "null", "tbc", "tbd", "-",
})


def _match_gcal_event(events: List[Dict[str, Any]], args: Dict[str, Any],
                      session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pick the calendar event a cancel/reschedule targets: prefer the
    appointment_id confirmed at lookup, then the booked-under phone, then the
    patient name. Returns the event dict or None."""
    event_id = (args.get("appointment_id") or session.get("_lookup_appointment_id") or "").strip()
    if event_id:
        ev = next((e for e in events if e.get("id") == event_id), None)
        if ev:
            return ev
    pk = _phone_key(args.get("phone") or "")
    if pk:
        ev = next((e for e in events if pk == _phone_key(_gcal_event_phone(e))), None)
        if ev:
            return ev
    # Name is the LAST resort, and the only branch that can select an event the
    # caller never confirmed at lookup. Both callers are destructive (cancel and
    # reschedule), so a wrong match here moves or cancels a stranger's
    # appointment. Two hazards, both reachable:
    #
    #   1. The model passes a placeholder instead of the looked-up name.
    #   2. The comparison is a SUBSTRING, so a short fragment ("jo") silently
    #      matches any summary containing it ("Jonathan Smith").
    #
    # So this branch now refuses anything it cannot resolve to exactly one
    # event: no placeholders, nothing under 3 characters, and no guessing when
    # several appointments match. Returning None is safe — both callers surface
    # "No upcoming appointment found", and the prompt's not-found path offers to
    # check another number rather than dead-ending on a transfer.
    #
    # The length floor can reject a genuine two-letter first name. That is
    # deliberate: this is the third fallback, reached only when the confirmed
    # appointment_id and the booked-under phone have BOTH already missed.
    name_norm = (args.get("patient_name") or "").strip().lower()
    if len(name_norm) < 3 or name_norm in _PLACEHOLDER_PATIENT_NAMES:
        if name_norm:
            logger.warning(
                "[ms_tools] _match_gcal_event: refusing name fallback for "
                "unusable patient_name=%r — no appointment targeted", name_norm,
            )
        return None
    matches = [e for e in events if name_norm in (e.get("summary") or "").lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning(
            "[ms_tools] _match_gcal_event: patient_name=%r matched %d events — "
            "ambiguous, refusing to guess", name_norm, len(matches),
        )
    return None


# ── B-42 — identity ambiguity on a phone-only lookup ────────────────────────
# CAe74ceae7 (3 Aug 2026): the caller rang from +447502211207, the lookup
# returned "match 1/13 name='Sarah Jenkins'", Susie read back only a day and a
# time — "is that the right one?" — the caller said yes, and Sarah Jenkins's
# appointment was cancelled. The caller confirmed a DATE, never a PERSON.
#
# On that call it was test data. The mechanism is not: a shared phone number is
# ordinary in physiotherapy (a couple, a parent booking for a child, a carer),
# and the same path cancels the wrong family member's appointment with nobody
# aware. The lookup already reports `match_count` and `has_more` and supports
# `next=true`; nothing was forcing them to be used.
#
# These two keys are the deterministic half of the fix. `_ambiguous` is set
# whenever more than one upcoming appointment sits on the identifier, and
# `_name_spoken` is cleared on every new match and re-set by llm_stream only
# when the matched name actually reaches TTS. The write gate reads both.
#
# B-54 adds a THIRD key on a different axis. `_name_spoken` answers "is this the
# right person"; it cannot answer "is this the right appointment", and on
# CA156fa25 all 15 matches were the same person, so the name settled nothing.
# `_slot_spoken` is set only when the matched appointment's DATE reaches TTS, so
# the caller has heard which of their appointments is about to be written to.
LOOKUP_AMBIGUOUS_KEY   = "_lookup_ambiguous"
LOOKUP_NAME_SPOKEN_KEY = "_lookup_name_spoken"
LOOKUP_SLOT_SPOKEN_KEY = "_lookup_slot_spoken"
LOOKUP_MATCH_COUNT_KEY = "_lookup_match_count"


# B-44 — steering, attached to the ambiguous lookup result itself.
#
# The B-42 gate sits at the WRITE, which is right for safety: it is the last
# point before something irreversible. But on CAdbc84848 that made the
# conversation clumsy — the caller stated an intention to cancel FOUR times
# across 89 s, because identity was settled AFTER the retention question and the
# retention question then had to be asked again.
#
# The natural place to say the name is the read-back, where Susie already recites
# the day and time. This rule is delivered on the tool result rather than in the
# prompt for the same reason B-36's Layer 2 was: it arrives at the moment of use,
# it cannot drift out of step with the gate, and it applies to every clinic
# without touching a 24k-line prompt or any clinic.json.
#
# Steering only. If the model ignores it the B-42 gate still refuses the write —
# which is exactly the division of labour that made B-36's cause 2d effective on
# CA9cc1a23e, where the steering resolved the turn and the guard never fired.
_LOOKUP_AMBIGUOUS_RULE = (
    "There are {count} upcoming appointments on this phone number, so you know "
    "neither which person you are speaking to NOR which of the appointments "
    "they mean. SAY HOW MANY THERE ARE — a caller cannot ask for a different "
    "one if they do not know others exist. When you read this appointment back, "
    "SAY THE NAME and the day, the DATE and the time — the weekday alone is not "
    "enough, because a caller with a course of treatment has several on the same "
    "weekday — and ask BOTH questions, because the name settles the person and "
    "only the date settles which appointment: for example \"I've got {count} "
    "appointments on this number — this one's for {name} on [day] the [date] at "
    "[time], is that you? And is that the one you mean?\" "
    "— and settle who they are before asking anything else, "
    "including before asking whether they want to reschedule or cancel. If they "
    "say it is not them, OR that it is not the appointment they meant, call "
    "lookup_patient again with next=true to step to the following match."
)


def _note_lookup_ambiguity(session: Dict[str, Any], total: int) -> None:
    """Record whether the caller's identifier resolved to more than one person.

    Called from BOTH lookup back-ends (Google Calendar and Acuity) so template
    clinics and Theorem behave identically. `_name_spoken` resets here rather
    than only on the first lookup: stepping to the next match with `next=true`
    changes who we are talking about, so the name has to be spoken again.

    `_slot_spoken` resets for the same reason and it matters more here: stepping
    to the next match changes WHICH APPOINTMENT is about to be written to, which
    is the whole of B-54. Carrying a previous match's confirmation forward would
    reproduce the defect one step further down the list.
    """
    session[LOOKUP_AMBIGUOUS_KEY] = total > 1
    session[LOOKUP_NAME_SPOKEN_KEY] = False
    session[LOOKUP_SLOT_SPOKEN_KEY] = False
    # The COUNT itself, not just the boolean. The write-gate refusal message in
    # llm_stream has to be able to tell the caller how many there are, and it
    # only has the session — B-54 turns on the caller knowing that others exist.
    session[LOOKUP_MATCH_COUNT_KEY] = total


def _with_ambiguity_rule(
    result: Dict[str, Any], name: str, total: int
) -> Dict[str, Any]:
    """B-44 — attach the read-back-the-name rule when, and only when, the
    identifier resolved to more than one person.

    Not attached on a single match: that is the overwhelmingly common case, and
    naming the patient there would add a turn to every ordinary cancel for no
    safety gain — the same reason `book_appointment` is outside the B-42 gate.
    """
    if total > 1:
        result["caller_message_rule"] = _LOOKUP_AMBIGUOUS_RULE.format(
            name=name or "that patient", count=total
        )
    return result


async def _lookup_patient_gcal(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """Find an upcoming appointment on the clinic's Google Calendar by phone or
    name. Mirrors the Acuity lookup_patient contract (found / patient_name /
    appointment_type / appointment_time / appointment_id / match_count /
    has_more) and supports next=true stepping through multiple matches."""
    from app.tools.calendar_google import list_upcoming_events
    from app.clinic_config import get_clinic

    def _emit(ev: Dict[str, Any], idx: int, total: int) -> Dict[str, Any]:
        nm = _gcal_event_patient_name(ev)
        _id = ev.get("id", "")
        start = (ev.get("start") or {}).get("dateTime", "")
        svc = _gcal_event_service(ev)
        session["_lookup_patient_name"] = nm
        session["_lookup_appointment_id"] = _id
        session["_lookup_appointment_datetime"] = start
        session["_lookup_appointment_type"] = svc
        _note_lookup_ambiguity(session, total)
        logger.info(
            "[ms_tools] lookup_patient (gcal): match %d/%d name=%r id=%r%s",
            idx + 1, total, nm, _id,
            " AMBIGUOUS — name must be read back (B-42)" if total > 1 else "",
        )
        return _with_ambiguity_rule({
            "found": True,
            "patient_name": nm,
            "appointment_type": svc,
            "appointment_time": start,
            "appointment_id": _id,
            "match_count": total,
            "has_more": idx < total - 1,
        }, nm, total)

    # Step to the next match (caller said the read-back wasn't theirs).
    if args.get("next"):
        matches = session.get("_lookup_matches") or []
        idx = int(session.get("_lookup_match_index", 0)) + 1
        if matches and idx < len(matches):
            session["_lookup_match_index"] = idx
            return _emit(matches[idx], idx, len(matches))
        return {"found": False, "exhausted": True,
                "message": "No further upcoming appointments under that number."}

    name = (args.get("name") or "").strip()
    phone = (args.get("phone") or "").strip()
    if not name and not phone:
        return {"found": False, "message": "Provide name or phone to look up an appointment"}

    tokens = await _get_tokens()
    if not tokens:
        return {"found": False, "message": "Calendar not connected"}
    clinic = get_clinic(session.get("clinic_id"))
    calendar_id = _resolve_calendar_id(clinic, (args.get("location") or ""))
    try:
        events = await asyncio.to_thread(list_upcoming_events, tokens, 60, 50, calendar_id)
        await _save_gcal_tokens(tokens)
    except Exception as exc:
        logger.warning("_lookup_patient_gcal: list_upcoming_events failed: %r", exc)
        return {"found": False, "message": "Could not retrieve appointments"}

    name_lower = name.lower()
    pk = _phone_key(phone)
    matches = [
        ev for ev in events
        if (ev.get("start") or {}).get("dateTime")  # skip all-day / busy blocks
        and ((name_lower and name_lower in _gcal_event_patient_name(ev).lower())
             or (pk and pk == _phone_key(_gcal_event_phone(ev))))
    ]
    matches.sort(key=lambda e: (e.get("start") or {}).get("dateTime", ""))

    if not matches:
        return {"found": False, "message": f"No upcoming appointment found for {name or phone}"}
    session["_lookup_matches"] = matches
    session["_lookup_match_index"] = 0
    return _emit(matches[0], 0, len(matches))


async def _exec_lookup_patient(args: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """
    Routes based on purpose:
      history   → _exec_get_patient_history (treatment history)
      cancel / reschedule → find upcoming appointment by name or phone
        (Acuity for Theorem; Google Calendar for template clinics like jv_v1).
    """
    purpose = args.get("purpose", "history")

    if purpose == "history":
        return await _exec_get_patient_history(args, session)

    # cancel / reschedule: look up upcoming appointment
    if session.get("clinic_id") not in ("theorem", "theorem_v2", "theorem_v3"):
        return await _lookup_patient_gcal(args, session)

    adapter = _get_acuity_adapter()
    if not adapter:
        return {"found": False, "message": "Scheduling system not configured"}

    name = (args.get("name") or "").strip()
    phone = (args.get("phone") or "").strip()

    def _emit(appt: dict, idx: int, total: int) -> Dict[str, Any]:
        """Build the tool result for one match and persist it as the active
        appointment (so cancel/reschedule target it by exact id)."""
        _nm = f"{appt.get('firstName', '')} {appt.get('lastName', '')}".strip()
        _id = str(appt.get("id", ""))
        session["_lookup_patient_name"] = _nm
        session["_lookup_appointment_id"] = _id
        session["_lookup_appointment_datetime"] = appt.get("datetime", "")
        session["_lookup_appointment_type"] = appt.get("type", "")
        # ── The appointment decides the clinic (T-18 follow-up, 2026-08-05) ──
        # The prompt's STRICT RULE has always said location for a cancel or
        # reschedule comes from this appointment_type and never from the
        # session. Nothing enforced it, and two things made that dangerous the
        # moment the reschedule flow stopped asking which clinic:
        #
        #   - `selected_location` DEFAULTS to "alcester" in DEFAULT_MS_SESSION.
        #     It is not a confirmed choice, but every downstream tool reads
        #     `args.get("location") or session["selected_location"]`, and
        #     check_availability's "location must never be guessed" gate reads
        #     the session ONLY — so the default silently satisfies it.
        #   - That left one thing standing between a Redditch appointment and
        #     Alcester slots: the model remembering to pass location= itself.
        #     Forget it once and the caller is moved to the wrong town, with
        #     nothing in the call to hear.
        #
        # Setting it here makes the session agree with the booking record, so
        # the outcome no longer depends on the model passing an argument.
        # Only fires when the type names exactly one known clinic; other
        # clinics' appointment types name no town, so nothing changes for them.
        _appt_type = appt.get("type", "") or ""
        _appt_loc = location_from_appointment_type(_appt_type)
        if _appt_loc:
            if session.get("selected_location") != _appt_loc:
                logger.info(
                    "[ms_tools] lookup_patient: location taken from the "
                    "appointment — %r → selected_location=%s (was %r)",
                    _appt_type, _appt_loc, session.get("selected_location"),
                )
            session["selected_location"] = _appt_loc
        _note_lookup_ambiguity(session, total)
        logger.info(
            "[ms_tools] lookup_patient: match %d/%d name=%r appointment_id=%r",
            idx + 1, total, _nm, _id,
        )
        return _with_ambiguity_rule({
            "found": True,
            "patient_name": _nm,
            "appointment_type": appt.get("type", ""),
            "appointment_time": appt.get("datetime", ""),
            "appointment_id": _id,
            "match_count": total,
            "has_more": idx < total - 1,
        }, _nm, total)

    # ── Advance to the NEXT match (caller said the readback wasn't the one) ──
    # Re-uses the stored match list from the first lookup — no name/phone or
    # re-fetch needed.
    if args.get("next"):
        _matches = session.get("_lookup_matches") or []
        _idx = int(session.get("_lookup_match_index", 0)) + 1
        if _matches and _idx < len(_matches):
            session["_lookup_match_index"] = _idx
            return _emit(_matches[_idx], _idx, len(_matches))
        return {
            "found": False,
            "exhausted": True,
            "message": "No further upcoming appointments under that number.",
        }

    if not name and not phone:
        return {"found": False, "message": "Provide name or phone to look up an appointment"}

    today = datetime.now(LONDON_TZ).date()
    end = today + timedelta(days=60)

    try:
        appointments = await adapter.list_appointments(min_date=today, max_date=end)
    except Exception as exc:
        logger.warning("_exec_lookup_patient: list_appointments failed: %r", exc)
        return {"found": False, "message": "Could not retrieve appointments"}

    name_lower = name.lower()
    # Format-agnostic phone match: reduce both sides to the UK "core" number so
    # a caller's local form ("07502211207") matches an appointment stored in
    # E.164 ("+447502211207"). A naive substring match failed across formats —
    # e.g. an already-rescheduled appointment (stored E.164) became unfindable.
    _pk = _phone_key(phone)
    matches = [
        appt for appt in appointments
        if (name_lower
            and name_lower in f"{appt.get('firstName', '')} {appt.get('lastName', '')}".strip().lower())
        or (_pk and _pk == _phone_key(appt.get("phone") or ""))
    ]

    if not matches:
        return {
            "found": False,
            "message": f"No upcoming appointment found for {name or phone}",
        }

    # Earliest first, and store the full list so the caller can step through
    # multiple bookings under one number ("no, the other one").
    matches.sort(key=lambda a: a.get("datetime", "") or "")
    session["_lookup_matches"] = [
        {
            "id": a.get("id"),
            "firstName": a.get("firstName", ""),
            "lastName": a.get("lastName", ""),
            "datetime": a.get("datetime", ""),
            "type": a.get("type", ""),
        }
        for a in matches
    ]
    session["_lookup_match_index"] = 0
    return _emit(matches[0], 0, len(matches))


TOOL_EXECUTORS: Dict[str, Any] = {
    "check_availability":     _exec_check_availability,
    "book_appointment":       _exec_book_appointment,
    "cancel_appointment":     _exec_cancel_appointment,
    "reschedule_appointment": _exec_reschedule_appointment,
    "lookup_patient":         _exec_lookup_patient,
    "transfer_to_human":      _exec_transfer_to_human,
    "add_to_waitlist":        _exec_add_to_waitlist,
    # Internal executors — not in TOOL_SCHEMAS but called by state machine / other executors
    "get_clinic_info":        _exec_get_clinic_info,
    "collect_and_store":      _exec_collect_and_store,
    "send_followup_sms":      _exec_send_followup_sms,
    "log_call_outcome":       _exec_log_call_outcome,
    "get_patient_history":    _exec_get_patient_history,
}
