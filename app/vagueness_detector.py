# app/vagueness_detector.py
"""
Vagueness detector for the Susie AI receptionist.

Detects when a caller gives a vague availability response ("I'm flexible",
"whenever", "any time") so the booking flow can offer concrete options rather
than re-asking the same question.

Applies ONLY to ask_day (PRESENT_DAYS) and ask_time (PRESENT_TIMES) states.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VAGUE_PHRASES = [
    "not sure", "don't mind", "don't really mind", "flexible", "anytime",
    "whenever", "doesn't matter", "any day", "any time", "whatever works",
    "up to you", "you decide", "whatever you have", "anything really",
    "not fussed", "no preference", "i'm open", "doesn't bother me",
]

DAY_PATTERN = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|wed|thu|fri|sat|sun|today|tomorrow|weekend|weekday)\b",
    re.IGNORECASE
)

TIME_PATTERN = re.compile(
    r"\b(morning|afternoon|evening|am|pm|\d{1,2}:\d{2}|\d{1,2}o.clock)\b",
    re.IGNORECASE
)


def is_vague_availability(utterance: str) -> bool:
    """
    Return True if the utterance is a vague availability answer.

    Keyword match only — no LLM. Completes well under 50ms.
    Never raises; returns False on any error.
    """
    try:
        lower = utterance.lower().strip()
        if any(phrase in lower for phrase in VAGUE_PHRASES):
            return True
        words = lower.split()
        if len(words) < 3 and not DAY_PATTERN.search(lower) and not TIME_PATTERN.search(lower):
            return True
        return False
    except Exception:
        return False


def _time_to_speech(hhmm: str) -> str:
    """
    Convert 'HH:MM' to a natural spoken form.

    Examples:
      '09:00' → 'nine o'clock in the morning'
      '14:30' → 'half past two in the afternoon'
      '16:00' → 'four o'clock in the afternoon'
    """
    try:
        parts = hhmm.split(":")
        hour24 = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        period = "in the morning" if hour24 < 12 else "in the afternoon"
        hour12 = hour24 if hour24 <= 12 else hour24 - 12
        if hour12 == 0:
            hour12 = 12
        hour_words = {
            1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
            6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
            11: "eleven", 12: "twelve",
        }
        hour_str = hour_words.get(hour12, str(hour12))
        if minute == 0:
            return f"{hour_str} o'clock {period}"
        elif minute == 30:
            return f"half past {hour_str} {period}"
        elif minute == 15:
            return f"quarter past {hour_str} {period}"
        elif minute == 45:
            return f"quarter to {hour_words.get(hour12 % 12 + 1, str(hour12 + 1))} {period}"
        else:
            return f"{hour_str} {minute:02d} {period}"
    except Exception:
        return hhmm


def build_vague_options(available_days: list) -> list:
    """
    Build up to 2 concrete slot options from the available_days data.

    Each option is a dict:
        {"day_label": str, "time_hhmm": str, "time_speech": str,
         "slot_iso": str, "day_index": int}

    Returns an empty list if available_days is empty or malformed.
    """
    options = []
    for i, day_entry in enumerate(available_days[:2]):
        try:
            slot_times = day_entry.get("slot_times", [])
            slots      = day_entry.get("slots", [])
            if not slot_times or not slots:
                continue
            time_hhmm = slot_times[0]
            slot_iso  = slots[0].get("start", "")
            options.append({
                "day_label":  day_entry["day_label"],
                "time_hhmm":  time_hhmm,
                "time_speech": _time_to_speech(time_hhmm),
                "slot_iso":   slot_iso,
                "day_index":  i,
            })
        except Exception as exc:
            logger.warning("[vagueness] build_vague_options error: %r", exc)
    return options


def build_vague_response_phrase(options: list) -> str:
    """
    Build the TTS phrase for presenting vague options.

    Returns an empty string if options is empty.
    """
    if not options:
        return ""
    if len(options) == 1:
        o = options[0]
        return (
            f"How about {o['day_label']} at {o['time_speech']}? "
            "That's the next one I've got."
        )
    o1, o2 = options[0], options[1]
    return (
        f"No problem — how about {o1['day_label']} at {o1['time_speech']}, "
        f"or {o2['day_label']} at {o2['time_speech']}? "
        "Either of those work?"
    )


def parse_option_selection(
    utterance: str,
    options: list,
) -> Optional[dict]:
    """
    Parse the caller's utterance as a selection from the presented options.

    Returns the selected option dict if matched, None if ambiguous.

    Matching rules:
      - "first" / "first one" / "the first" → options[0]
      - "second" / "other one" / "the second" → options[1]
      - Direct day match (e.g. "Thursday") → whichever option's day_label matches
      - Direct time match (e.g. "nine") → whichever option's time_speech contains it
    """
    if not options:
        return None

    lower = utterance.lower().strip()

    # Ordinal matching
    first_signals  = ("first", "first one", "the first", "that first")
    second_signals = ("second", "second one", "the second", "other one", "the other")
    if any(sig in lower for sig in first_signals):
        return options[0]
    if any(sig in lower for sig in second_signals) and len(options) > 1:
        return options[1]

    # Day name matching
    for opt in options:
        day_words = opt["day_label"].lower().split()
        if any(word in lower for word in day_words if len(word) > 3):
            return opt

    # Time matching
    for opt in options:
        time_words = opt["time_speech"].lower().split()
        if any(word in lower for word in time_words if len(word) > 3):
            return opt

    return None
