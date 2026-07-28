# app/tools/slot_followup.py
"""
Deterministic unspoken-slot follow-up (V5).

After the first spoken offer, the model answers "anything later?" / a specific
unspoken time from what it already said — even when session["available_days"]
still holds the full day. Re-fetching check_availability cannot fix that: a
fresh fetch leads with the earliest times again, and the already_retrieved
guard tells the model to present "the existing slots".

These helpers compute remaining = available_days − last_offered and either:
  * offer the next two unspoken times, or
  * confirm a caller-named time that is still in remaining.

No LLM judgment about what exists.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def _slot_start(slot: Dict[str, Any]) -> str:
    return str(slot.get("start") or "")


def flatten_bookable_slots(available_days: Any) -> List[Dict[str, Any]]:
    """Flatten available_days into ordered slot dicts with time + spoken labels."""
    if not isinstance(available_days, list):
        return []
    out: List[Dict[str, Any]] = []
    for day in available_days:
        if not isinstance(day, dict):
            continue
        times = day.get("slot_times") or []
        spoken = day.get("slot_times_spoken") or []
        slots = day.get("slots") or []
        n = max(len(times), len(slots))
        for i in range(n):
            raw = slots[i] if i < len(slots) and isinstance(slots[i], dict) else {}
            start = _slot_start(raw)
            time = times[i] if i < len(times) else (start[11:16] if len(start) >= 16 else "")
            label = spoken[i] if i < len(spoken) else time
            out.append({
                "start": start or (f"{day.get('date')}T{time}:00" if day.get("date") and time else ""),
                "end": str(raw.get("end") or ""),
                "time": time,
                "spoken": label,
                "date": day.get("date"),
                "day_label": day.get("day_label") or "",
            })
    return out


def remaining_slots_after_offer(
    available_days: Any,
    last_offered_slots: Any,
) -> List[Dict[str, Any]]:
    """Bookable slots in available_days whose start is not in last_offered."""
    offered_starts = set()
    if isinstance(last_offered_slots, list):
        for s in last_offered_slots:
            if isinstance(s, dict) and s.get("start"):
                offered_starts.add(str(s["start"])[:19])  # trim tz noise
    remaining = []
    for slot in flatten_bookable_slots(available_days):
        start = slot["start"][:19]
        if start and start not in offered_starts:
            remaining.append(slot)
    return remaining


def next_slot_batch(
    remaining: List[Dict[str, Any]], n: int = 2
) -> Tuple[List[Dict[str, Any]], bool]:
    batch = list(remaining[:n])
    more = len(remaining) > n
    return batch, more


def utterance_requests_different_day(text: str) -> bool:
    t = (text or "").lower()
    return any(
        p in t
        for p in (
            "different day",
            "another day",
            "other day",
            "different date",
            "another date",
            "different week",
            "next week",
        )
    )


def utterance_requests_more_slots(text: str) -> bool:
    """True if caller wants more times on the *same* availability set."""
    t = (text or "").lower().strip()
    if not t or utterance_requests_different_day(t):
        return False
    signals = (
        "later",
        "else",
        "other",
        "another",
        "different",
        "instead",
        "any more",
        "anymore",
        "anything else",
        "any others",
        "any other",
        "more times",
        "more slots",
        "full list",
        "every slot",
        "all slots",
        "all the slots",
        "what else",
        "anything after",
    )
    return any(s in t for s in signals)


_BARE_HOUR_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def _candidate_hhmm_from_text(text: str) -> List[str]:
    """Pull possible HH:MM values the caller may have meant."""
    t = (text or "").lower()
    found: List[str] = []

    def _add_hour_variants(h: int, mm: int) -> None:
        if h > 23 or mm > 59:
            return
        found.append(f"{h:02d}:{mm:02d}")
        # Clinic evenings are 24h; callers say "730" meaning 19:30.
        if 1 <= h <= 12:
            found.append(f"{h + 12:02d}:{mm:02d}")

    for m in re.finditer(r"\b([01]?\d|2[0-3])[:\.]([0-5]\d)\b", t):
        _add_hour_variants(int(m.group(1)), int(m.group(2)))
    # bare "730" / "1930" without separator
    for m in re.finditer(r"\b([01]?\d|2[0-3])([0-5]\d)\b", t):
        _add_hour_variants(int(m.group(1)), int(m.group(2)))

    # half past / quarter past / quarter to
    for hour_word, h12 in _BARE_HOUR_WORDS.items():
        if f"half past {hour_word}" in t:
            _add_hour_variants(h12, 30)
        if f"quarter past {hour_word}" in t:
            _add_hour_variants(h12, 15)
        if f"quarter to {hour_word}" in t:
            prev = h12 - 1 if h12 > 1 else 12
            _add_hour_variants(prev, 45)

    # bare hour word "six" / "at six" — only useful if unique in remaining
    for hour_word, h12 in _BARE_HOUR_WORDS.items():
        if re.search(rf"\b{hour_word}\b", t):
            _add_hour_variants(h12, 0)

    # de-dupe preserving order
    seen = set()
    out = []
    for x in found:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def resolve_requested_time(
    text: str, remaining: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Match a caller time phrase to exactly one remaining slot, else None."""
    if not remaining or not (text or "").strip():
        return None
    t = text.lower()

    # Prefer full spoken-label containment (most precise)
    label_hits = [s for s in remaining if s.get("spoken") and s["spoken"].lower() in t]
    if len(label_hits) == 1:
        return label_hits[0]
    # partial: "half past seven" without "in the evening"
    soft_hits = []
    for s in remaining:
        spoken = (s.get("spoken") or "").lower()
        core = spoken.replace(" in the evening", "").replace(" in the afternoon", "").replace(" in the morning", "")
        if core and core in t:
            soft_hits.append(s)
    if len(soft_hits) == 1:
        return soft_hits[0]

    candidates = _candidate_hhmm_from_text(t)
    time_hits = [s for s in remaining if s.get("time") in candidates]
    if len(time_hits) == 1:
        return time_hits[0]
    return None


def format_next_batch_speech(batch: List[Dict[str, Any]], more: bool) -> str:
    if not batch:
        return (
            "I don't have any further times on that day — would you like me "
            "to look at a different day?"
        )
    day = batch[0].get("day_label") or "that day"
    if len(batch) == 1:
        spoken = batch[0]["spoken"]
        tail = (
            f" And I've a few others that day if that doesn't suit."
            if more else ""
        )
        return (
            f"On {day} I also have {spoken}.{tail} "
            f"Does that work?"
        )
    a, b = batch[0]["spoken"], batch[1]["spoken"]
    tail = (
        " And I've a few others that day if neither suits."
        if more else ""
    )
    return (
        f"On {day} I also have {a}, or {b}.{tail} "
        f"Either of those work?"
    )


def format_time_available_speech(slot: Dict[str, Any]) -> str:
    day = slot.get("day_label") or "that day"
    spoken = slot.get("spoken") or slot.get("time") or "that time"
    return (
        f"Yes — {spoken} on {day} is free. "
        f"Shall I book that in for you?"
    )


def apply_next_batch_to_session(
    session: Dict[str, Any],
    batch: List[Dict[str, Any]],
    more: bool,
) -> str:
    """Advance last_offered to this batch and return the spoken offer."""
    session["last_offered_slots"] = [
        {"start": s["start"], "end": s.get("end") or ""} for s in batch
    ]
    session["slot_labels"] = [s.get("spoken") or s.get("time") for s in batch]
    return format_next_batch_speech(batch, more)


def apply_resolved_time_to_session(
    session: Dict[str, Any],
    slot: Dict[str, Any],
) -> str:
    """Present the resolved unspoken time as the current offer / selection."""
    offered = {"start": slot["start"], "end": slot.get("end") or ""}
    session["last_offered_slots"] = [offered]
    session["slot_labels"] = [slot.get("spoken") or slot.get("time")]
    # Mirror fast-path slot selection so the LLM / booking path sees it.
    session["selected_slot"] = offered
    try:
        from app.media_streams.config import F_SELECTED_SLOT
        session[F_SELECTED_SLOT] = offered
    except Exception:
        pass
    return format_time_available_speech(slot)


def build_followup_tool_result(
    available_days: Any,
    batch: List[Dict[str, Any]],
    more: bool,
) -> Dict[str, Any]:
    """Shape a check_availability-like result for the Haiku slot formatter."""
    if not batch:
        return {
            "error": "No further times on that day.",
            "available_days": available_days if isinstance(available_days, list) else [],
        }
    day_label = batch[0].get("day_label") or ""
    date = batch[0].get("date")
    first_day = {
        "date": date,
        "day_label": day_label,
        "slot_times": [s["time"] for s in batch],
        "slot_times_spoken": [s["spoken"] for s in batch],
        "slots": [{"start": s["start"], "end": s.get("end") or ""} for s in batch],
        "more_times": more,
    }
    return {
        "status": "next_unspoken_batch",
        "presentation_mode": "single_day",
        "first_day": first_day,
        "available_days": available_days if isinstance(available_days, list) else [],
        "total_days": 1,
        "message": (
            "Caller asked for other times. Present ONLY first_day "
            "(Number 1 / Number 2). more_times="
            + ("true" if more else "false")
            + ". Do NOT claim these are the only times if more_times is true."
        ),
    }


def try_unspoken_followup_speech(
    session: Dict[str, Any], user_text: str
) -> Optional[str]:
    """
    If this turn is an unspoken-slot follow-up, update session and return
    speech. Otherwise return None (caller falls through to the LLM).
    """
    # Only while the caller is still choosing a time — not during name/phone
    # or after a slot is locked.
    if session.get("v3_confirmed_slot_phrase"):
        return None
    _col = session.get("collected") or {}
    if _col.get("name") or _col.get("full_name") or session.get("patient_name"):
        return None
    if session.get("booking_write_confirmed") or session.get("booking_confirmed"):
        return None

    offered = session.get("last_offered_slots") or []
    days = session.get("available_days") or []
    if not offered or not days:
        return None

    remaining = remaining_slots_after_offer(days, offered)
    if not remaining:
        return None

    # Specific unspoken time first (V5).
    hit = resolve_requested_time(user_text, remaining)
    if hit is not None:
        return apply_resolved_time_to_session(session, hit)

    if utterance_requests_more_slots(user_text):
        batch, more = next_slot_batch(remaining, n=2)
        if not batch:
            return None
        return apply_next_batch_to_session(session, batch, more)

    return None
