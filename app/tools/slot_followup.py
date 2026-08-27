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

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _slot_start(slot: Dict[str, Any]) -> str:
    return str(slot.get("start") or "")


def _day_key(slot: Dict[str, Any]) -> str:
    """The calendar day a slot belongs to. `date` when present, else the ISO date.

    Never returns None, so slots with no usable date group together rather than
    each looking like its own day.
    """
    return str(slot.get("date") or "") or _slot_start(slot)[:10]


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


# ───────────────────────────────────────────────────────────────────────────
# B-78b — what the caller has been offered ACROSS the whole day, not just now.
#
# `last_offered_slots` is the CURRENT offer: apply_next_batch_to_session
# REPLACES it. So subtracting it alone makes the previous batch unoffered
# again, and repeated "anything else?" walks a two-state loop:
#
#     ask 1 → half six, quarter past seven
#     ask 2 → five, quarter to six      ← already heard
#     ask 3 → half six, quarter past seven ...
#
# On CA7cd9bed5's Tuesday (5 slots) that loop never reaches 20:00 — the last
# slot of the day is unreachable no matter how many times the caller asks,
# while Susie keeps promising "a few others that day".
#
# So the cumulative record is kept separately. `last_offered_slots` keeps its
# meaning untouched — _resolve_slot_iso, the DTMF map and fast_path all read it
# as "the offer on the table".
# ───────────────────────────────────────────────────────────────────────────

_SPOKEN_KEY = "slot_starts_spoken"
_SPOKEN_FP_KEY = "slot_starts_spoken_fp"


def _day_fingerprints(available_days: Any) -> Dict[str, str]:
    """One fingerprint PER DAY, so a fetch can invalidate a day without
    invalidating the days it does not mention.

    Self-healing, same as before and for the same reason: no call site has to
    remember to reset, and a day whose slot set has really moved drops its
    record rather than hiding times behind a stale one. What changed is the
    GRANULARITY.

    B-101, CA315e501a (27 Aug 2026). The fingerprint used to cover the whole
    payload, so ANY lookup for ANY day wiped the record for EVERY day. The
    caller heard Friday's two o'clock, asked about Wednesday, and that lookup
    erased Friday. When they came back to Friday, B-98 could not tell the 2pm
    had been spoken, did not open the day, and re-offered the same 2pm -- the
    caller had to name "midday" themselves to reach the slot the band was
    hiding. Two round trips for one appointment.

    The record is a set of ISO starts, and an ISO start already names its own
    day, so the data was always day-separable; only the guard was not.
    """
    by_day: Dict[str, List[str]] = {}
    for slot in flatten_bookable_slots(available_days):
        start = str(slot.get("start") or "")
        if start:
            by_day.setdefault(_day_key(slot), []).append(start)
    return {
        day: f"{len(starts)}|{starts[0]}|{starts[-1]}"
        for day, starts in by_day.items()
    }


def _spoken_key_set(session: Dict[str, Any]) -> set:
    """The ISO starts the caller has heard, dropping any day that has moved."""
    new = _day_fingerprints(session.get("available_days") or [])
    old = session.get(_SPOKEN_FP_KEY)
    if not isinstance(old, dict):
        # Either the pre-B-101 single-string form (a call in flight across the
        # deploy) or nothing at all. Neither can verify a day, so nothing is
        # trusted -- the same fail-closed direction the old whole-payload
        # mismatch took.
        session[_SPOKEN_KEY] = []
        old = {}
    changed = {
        day for day, fp in new.items()
        if old.get(day) is not None and old.get(day) != fp
    }
    if changed:
        session[_SPOKEN_KEY] = [
            s for s in (session.get(_SPOKEN_KEY) or [])
            if str(s)[:10] not in changed
        ]
        logger.info(
            "[slot_followup] spoken record dropped for %s -- their slots have "
            "moved since the caller heard them. Every other day is kept "
            "(B-101).", sorted(changed),
        )
    old.update(new)
    session[_SPOKEN_FP_KEY] = old
    return {str(s)[:19] for s in (session.get(_SPOKEN_KEY) or [])}


def record_spoken_slots(session: Dict[str, Any], slots: Any) -> None:
    """Add `slots` to the day's cumulative spoken record."""
    _spoken_key_set(session)  # resets first if availability changed
    current = list(session.get(_SPOKEN_KEY) or [])
    for s in (slots or []):
        start = str((s or {}).get("start") or "")[:19]
        if start and start not in current:
            current.append(start)
    session[_SPOKEN_KEY] = current


def remaining_unspoken(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Bookable slots the caller has not been offered at ANY point this day.

    Folds the current offer into the cumulative record as it goes, so the
    caller walks forward through the day instead of ping-ponging.
    """
    record_spoken_slots(session, session.get("last_offered_slots") or [])
    spoken = _spoken_key_set(session)
    return [
        slot
        for slot in flatten_bookable_slots(session.get("available_days") or [])
        if str(slot.get("start") or "")[:19] not in spoken
    ]


_WEEKDAY_WORDS = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday",
)


# Day-of-month as a caller may say it. A closed set of 31, in the same spirit
# as _WEEKDAY_WORDS: bounded vocabulary, no date parsing. ORDINALS ONLY --
# "second", not "two". A date is spoken as an ordinal, and mapping cardinals
# as well would fold "two in the afternoon" into a bare 2 for no gain.
_ORDINAL_UNITS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "thirtieth": 30,
}
_ORDINAL_COMPOUNDS = {
    f"{tens_word} {unit_word}": tens + unit
    for tens_word, tens in (("twenty", 20), ("thirty", 30))
    for unit_word, unit in _ORDINAL_UNITS.items()
    if unit <= 9 and tens + unit <= 31
}
_ORDINAL_SUFFIX_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b")
_ORDINAL_COMPOUND_RE = re.compile(
    r"\b(" + "|".join(sorted(_ORDINAL_COMPOUNDS, key=len, reverse=True)) + r")\b"
)
_ORDINAL_UNIT_RE = re.compile(
    r"\b(" + "|".join(sorted(_ORDINAL_UNITS, key=len, reverse=True)) + r")\b"
)


def _fold_ordinals(text: str) -> str:
    """"22nd", "22" and "twenty second" all become "22".

    B-104. The payload writes "Tuesday 22nd September"; callers say any of

        "tuesday the 22nd of september"      <- the only one that used to match
        "tuesday the twenty second of september"
        "tuesday the 22 of september"

    and the two that did not match fell back to scoping by the offer's first
    slot -- B-103's defect, reached by phrasing rather than by code. Folding
    BOTH sides to a bare number makes them one string.

    Compounds before units, so "twenty second" is 22 and not "20 2".
    """
    t = _ORDINAL_SUFFIX_RE.sub(r"\1", text)
    t = _ORDINAL_COMPOUND_RE.sub(lambda m: str(_ORDINAL_COMPOUNDS[m.group(1)]), t)
    return _ORDINAL_UNIT_RE.sub(lambda m: str(_ORDINAL_UNITS[m.group(1)]), t)


def _caller_norm(value: Any) -> str:
    """Caller speech and a payload label, folded onto one comparable form.

    `_readback_norm` rather than its sibling `_norm_day` because this reads
    CALLER speech, which arrives with punctuation -- "not wednesday, what else
    on friday the 4th" -- and `_norm_day` keeps it, which silently breaks the
    token counting below. Both drop the filler that separates "Wednesday 2nd
    September" from "wednesday the 2nd of september"; only this one also
    strips the comma.

    B-104 adds ordinal folding, applied to the LABEL as well as the speech --
    the point is that both land on the same string, so doing it to one side
    only would move the mismatch rather than remove it.
    """
    return _fold_ordinals(_readback_norm(value))


def _days_the_caller_named(available_days: Any, text: str) -> Dict[str, str]:
    """{date: normalised label} for every day of the payload `text` names in
    full. Word-boundary matched -- both sides are space-joined single words
    after normalisation, so padding makes containment a token test."""
    if not isinstance(text, str) or not text.strip():
        return {}
    if not isinstance(available_days, list):
        return {}
    hay = f" {_caller_norm(text)} "
    found: Dict[str, str] = {}
    for day in available_days:
        if not isinstance(day, dict):
            continue
        label = _caller_norm(day.get("day_label") or "")
        date = str(day.get("date") or "").strip()
        if label and date and f" {label} " in hay:
            found[date] = label
    return found


def caller_named_conflicting_days(available_days: Any, text: str) -> bool:
    """True when `text` refers to more days than it pins down to exactly one.

    Two ways that happens, and the second is why counting matches is not
    enough on its own:

      1. Two full labels match.
      2. ONE full label matches and a weekday is named that the label cannot
         account for -- because callers elide:

             "anything else on wednesday the 2nd or friday the 4th
              of september"

         names TWO days and matches ONE label. The month is spoken once, so
         "wednesday the 2nd" is a partial and invisible to the match. Without
         this check that reads as an unambiguous naming and gets answered
         about Friday.

    Weekdays are a closed set of seven words, so this stays a token count and
    does not become the date parsing Tier 2 needs.

    Eager on purpose: "not wednesday, what else on friday the 4th of
    september" trips it too, and loses an answer this could have got right.
    That is the acceptable direction ONLY because an ambiguous scope now falls
    through to a real lookup rather than to day one -- see
    remaining_unspoken_on_current_day. Bailing to day one would have made this
    guard a way of producing the very wrong-day answer it exists to stop.
    """
    named = _days_the_caller_named(available_days, text)
    if len(named) > 1:
        return True
    if not named:
        return False
    label = next(iter(named.values()))
    hay = f" {_caller_norm(text)} "
    spoken = sum(hay.count(f" {w} ") for w in _WEEKDAY_WORDS)
    return spoken > sum(label.count(w) for w in _WEEKDAY_WORDS)


def day_named_by_caller(available_days: Any, text: str) -> "str | None":
    """The one calendar day the CALLER named, or None.

    B-103. Sibling of `day_named_in_readout`, and deliberately not the same
    function. That one judges SUSIE'S readout, which echoes `day_label`
    verbatim, so its raw substring test is exactly right there and normalising
    it would loosen a guard that is load-bearing for B-93's offer record. This
    one judges CALLER SPEECH, which never arrives verbatim: the payload says
    "Wednesday 2nd September" and the caller says "wednesday the 2nd of
    september".

    None when they named nothing, and None when they named more than one --
    the same rule and the same reason as `day_named_in_readout`. The two cases
    are NOT interchangeable to the consumer, which asks
    `caller_named_conflicting_days` to tell them apart.

    A PARTIAL naming -- "that wednesday", "friday the 28th" -- matches nothing
    and returns None with no conflict, so it keeps the pre-B-103 behaviour
    exactly. That is Tier 2, out of scope on purpose: it needs its own corpus
    before anything reads it.
    """
    if caller_named_conflicting_days(available_days, text):
        return None
    named = _days_the_caller_named(available_days, text)
    return next(iter(named)) if len(named) == 1 else None


def remaining_unspoken_on_current_day(
    session: Dict[str, Any], user_text: str = ""
) -> List[Dict[str, Any]]:
    """remaining_unspoken(), scoped to the day under discussion.

    "Anything else THAT DAY?" means the day the caller is discussing. Where
    that day comes from, in order:

      1. The day the CALLER NAMED in this very utterance, when they named
         exactly one (B-103).
      2. NOTHING, when they named more than one and none can be picked. An
         empty scope makes the caller's branch decline (a multi-day offer
         cannot support an exhaustion claim) and fall through to a real
         lookup. Falling back to rule 3 there would answer about a day they
         did not ask about, which is the defect itself.
      3. Otherwise the offer they were just given -- `last_offered_slots[0]`.

    `remaining_unspoken` flattens the whole sweep — a clinic on a fixed evening
    rota has four more days of it — so an unscoped batch takes remaining[0]'s
    day, which is whichever day sorts first, not the one under discussion.

    Found 24 Aug 2026 while testing B-79: a caller offered Wednesday times and
    asking "anything else that day?" was answered with TUESDAY, announced under
    Tuesday's own label. That is CA5c4fb14f's failure mode — a real patient
    sent to the clinic on the wrong day — reached through a different door.

    B-103 is that same door, one step further in. `last_offered_slots[0]` is
    the FIRST slot of the offer, so on a multi-day offer it is day one whatever
    the caller then asks about:

        offer:   Friday 28 Aug | Wednesday 2 Sep | Friday 4 Sep
        caller:  "what else have you got on wednesday the 2nd of september"
        answer:  "On Friday 28th August I also have 16:00."

    B-99 stopped this branch CLAIMING A DAY IS FULL when it cannot identify
    one. It did not make it identify one, so a caller who names a day is still
    answered about a different day — confidently, and under that day's label,
    which is the shape that sends a patient in on the wrong date.

    Step 1 only fires on an unambiguous full naming; everything else falls to
    step 2 and behaves exactly as before.
    """
    remaining = remaining_unspoken(session)
    days = session.get("available_days") or []
    _lead = str(((session.get("last_offered_slots") or [{}])[0] or {})
                .get("start") or "?")[:10]
    named = day_named_by_caller(days, user_text)
    if named:
        logger.info(
            "[slot_followup] scoping the follow-up to %s -- the caller named "
            "it, and the offer on the table leads with %s (B-103)",
            named, _lead,
        )
        return [slot for slot in remaining if _day_key(slot) == named]
    if caller_named_conflicting_days(days, user_text):
        logger.info(
            "[slot_followup] the caller named more than one day and none of "
            "them can be picked -- scoping to nothing so this falls through "
            "to a real lookup rather than answering about %s (B-103)", _lead,
        )
        return []
    offered = session.get("last_offered_slots") or []
    day = str((offered[0] or {}).get("start") or "")[:10] if offered else ""
    if not day:
        return remaining
    return [slot for slot in remaining if _day_key(slot) == day]


def next_slot_batch(
    remaining: List[Dict[str, Any]], n: int = 2
) -> Tuple[List[Dict[str, Any]], bool]:
    """The next `n` unspoken slots, ALL ON ONE DAY.

    Single-day is a correctness requirement, not a preference. `remaining` is
    flattened across every day in available_days, so `remaining[:n]` could
    straddle a day boundary — and both consumers of this batch present it as one
    day, taking their label from batch[0]:

        format_next_batch_speech  -> "On {batch[0].day_label} I also have A, or B"
        build_followup_tool_result -> first_day.date/day_label from batch[0],
                                      first_day.slots from the WHOLE batch

    Each slot keeps its own true `start`, so the caller picking the second option
    books the day it really belongs to — while having been told the first slot's
    day. That is exactly how CA5c4fb14f (30 Jul 2026) told a caller "Tuesday the
    4th of August at seven in the evening" and booked 2026-08-05T19:00. Nothing
    downstream can catch it, because nothing downstream is wrong: the booking
    matches the slot, only the speech does not.

    So the batch is confined to remaining[0]'s day. `more` means "more times
    STILL ON THAT DAY", which is what the speech it feeds actually claims ("I've
    a few others that day"). Slots on later days are not lost — they are simply
    not announced under the wrong day's name.
    """
    if not remaining:
        return [], False
    day = _day_key(remaining[0])
    same_day = [s for s in remaining if _day_key(s) == day]
    batch = list(same_day[:n])
    more = len(same_day) > n
    return batch, more


def all_remaining_on_next_day(
    remaining: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], bool]:
    """EVERY unspoken slot on remaining[0]'s day.

    Owner rule, 24 Aug 2026: the first offer is capped (three times, so the
    caller is not read a wall of numbers), but a caller who has been told "I've
    a few others that day" and asks for them gets ALL of them — not another
    pair, and never a slot silently withheld. On CA6b90c3a2 the two-at-a-time
    batching meant three separate asks to walk one Tuesday.

    Delegates to next_slot_batch so the single-day confinement invariant has
    exactly one implementation. Slots on LATER days are still excluded —
    announcing them under this day's label is CA5c4fb14f.

    Ceiling of nine, and it is not a style choice: SLOT_OPTION_ANCHOR_RE and the
    DTMF map are single-digit, so a tenth option is spoken as "Number 10", which
    anchors as "Number 1" and points the keypad at the wrong time. A day that
    holds more than nine unspoken times therefore gets nine and `more=True`, so
    the caller is told the rest exist rather than being read a number they
    cannot press. `more` is False in every realistic case — the whole day is on
    the table.
    """
    batch, _ = next_slot_batch(remaining, n=len(remaining) or 1)
    if len(batch) > MAX_KEYPAD_OPTIONS:
        logger.info(
            "[slot_followup] %d unspoken times on that day — offering %d, "
            "the most the keypad can address",
            len(batch), MAX_KEYPAD_OPTIONS,
        )
        return batch[:MAX_KEYPAD_OPTIONS], True
    return batch, False


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


# Job 3c.1 / CAce1457d1: caller accepting an already-offered slot must not be
# steered to "present the existing slots" again (forced a second accept).
_SLOT_ACCEPT_PHRASES: frozenset = frozenset({
    "suits me", "any of them", "any of those", "that works",
    "fine with me", "any is fine", "any is good", "whatever",
    "any of those suit me", "they all work", "all good",
    "anytime", "any", "fine", "good", "okay", "ok",
    "that works for me", "works for me", "all fine", "all work",
    "either", "either works", "either of those", "both fine",
    "sounds good", "sounds fine", "any would work", "any works",
    "yes", "yeah", "yep", "yup", "sure", "perfect", "great",
    "go ahead", "go for it", "book that", "book it", "take that",
    "i'll take that", "ill take that", "the first one", "the second one",
    "number one", "number two", "option one", "option two",
})


def utterance_accepts_offered_slot(text: str) -> bool:
    """True when the caller is accepting / locking an already-offered slot.

    Excludes "more times" and "different day" requests — those still need the
    follow-up / re-fetch paths.
    """
    t = (text or "").lower().strip().strip(".,!?;:")
    if not t:
        return False
    if utterance_requests_more_slots(t) or utterance_requests_different_day(t):
        return False
    if t in _SLOT_ACCEPT_PHRASES:
        return True
    # Short affirmatives with filler ("yeah that works", "yes please")
    if len(t.split()) <= 5 and any(
        t == p or t.startswith(p + " ") or t.endswith(" " + p) or f" {p} " in f" {t} "
        for p in (
            "that works", "works for me", "sounds good", "go ahead",
            "book that", "book it", "perfect", "yes please", "yeah please",
        )
    ):
        return True
    return False


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


# ───────────────────────────────────────────────────────────────────────────
# The "there are more times that day" claim — one home for the literal.
#
# This sentence asserts WORLD STATE: that bookable times exist beyond the ones
# just read out. Only `more_times`, computed from the provider's own slot data,
# knows whether that is true. It was previously produced by the Haiku slot
# formatter copying a prompt example, and on 24 Aug 2026 (CA98557584dc) it told
# a caller "And I've a few others that day if neither suits" about a Tuesday
# that had exactly the two slots she had just been offered.
#
# So the tail is now appended by CODE from `more_times`, and stripped when the
# model emits it anyway. See reconcile_extra_slots_claim below.
# ───────────────────────────────────────────────────────────────────────────

MORE_TIMES_TAIL_ONE = "And I've a few others that day if that doesn't suit."
MORE_TIMES_TAIL_MANY = "And I've a few others that day if neither suits."
MORE_TIMES_TAIL_SEVERAL = "And I've a few others that day if none of those suit."


def more_times_tail(n_offered: int) -> str:
    """The canonical tail for `n_offered` times just read out.

    "neither" means two. _check_availability_acuity caps a single day's spoken
    times at THREE before setting more_times, so the two-option wording would
    have been read out over a three-option list — the grammar-does-not-match-
    the-data snag already logged against this sentence. Now that code emits it,
    code agrees with the count.
    """
    if n_offered <= 1:
        return MORE_TIMES_TAIL_ONE
    if n_offered == 2:
        return MORE_TIMES_TAIL_MANY
    return MORE_TIMES_TAIL_SEVERAL


# A sentence claiming further availability beyond what was just listed.
# Deliberately a FAMILY, not one literal: the failure being guarded against is
# a language model paraphrasing, and a guard that matches only the exact
# example sentence would be defeated by "I've got a couple more that day".
# Both halves must be present in the SAME sentence — an "extra quantity" word
# and a "further times" noun — which is what keeps it off the legitimate
# openers ("The available slots for Tuesday are —", "Any of those work?").
_EXTRA_QUANTITY_RE = re.compile(
    r"\b(?:a\s+few|a\s+couple|some|several|plenty|more|other|others)\b",
    re.IGNORECASE,
)
_FURTHER_TIMES_RE = re.compile(
    r"\b(?:others?|more|further|times|slots|openings|availability)\b",
    re.IGNORECASE,
)
# A claim that the listed times are the COMPLETE set. Used only to SUPPRESS an
# optional append (never to rewrite), so a false positive costs nothing.
_COMPLETENESS_RE = re.compile(
    r"\bthe\s+available\s+(?:slot|slots|time|times)\b[^.!?]{0,40}?\b(?:is|are)\b",
    re.IGNORECASE,
)

# The SINGULAR completeness claim, and only that. "The available TIME is X" /
# "the only time available that day is X" says the day holds exactly one
# bookable appointment. When more_times is true that is false, and it is the
# one form that can be corrected without touching the times themselves.
#
# Deliberately NOT _COMPLETENESS_RE, which matches the plural opener too ("The
# available slots for Wednesday are — Number 1 ...") and whose own comment
# records that it is safe only because it never rewrites. Widening that one
# into a rewrite would mangle the legitimate multi-slot readout.
#
# B-100, CA315e501a: "Friday 28th August — the available time is two in the
# afternoon." Friday held midday AND two in the afternoon.
_SINGULAR_COMPLETENESS_RE = re.compile(
    r"\bthe\s+(?:only|available)\s+(?:time|slot)\b"
    r"(?:\s+available)?(?:\s+(?:that|on\s+that)\s+day)?\s+is\b",
    re.IGNORECASE,
)
_SINGULAR_COMPLETENESS_SUB = "I've got"

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


# ---------------------------------------------------------------------------
# What was actually READ OUT — the one place that knows.
#
# The option count is a claim about how much the caller has to hold in their
# head, and until now nothing owned it. The tool capped its own payload, but
# the `already_retrieved` re-entry (llm_stream) hands the model the FULL
# available_days and says "present the existing slots" — so on CA6b90c3a2 a
# five-slot Tuesday was read out in one breath, five numbered options deep,
# after the first offer had been correctly capped at two.
#
# Capping in the prompt would be a fourth attempt to win an argument with a
# language model about a number. It is enforced here instead, on the assembled
# text, immediately before the DTMF map and the Number re-split are derived
# from it — so the keypad, the speech and the record cannot disagree.
# ---------------------------------------------------------------------------

MAX_SPOKEN_OPTIONS = 3

# The most options a single readout can carry, set by the KEYPAD, not by taste:
# SLOT_OPTION_ANCHOR_RE matches one digit, so "Number 10" anchors as "Number 1".
MAX_KEYPAD_OPTIONS = 9

# The closing question a capped readout is given when the trim removed the
# model's own. A slot readout that ends on a statement is dead air the caller
# has to break. Checked against turn_handler._BANNED_SENTENCE_RE.
CAPPED_READOUT_QUESTION = "Any of those work?"

# Numbered-option anchors. Defined HERE and imported by llm_stream so the trim,
# the DTMF map and the TTS re-split are derived from one pattern — a cap that
# counted options differently from the map would trim to a boundary the keypad
# does not share.
SLOT_OPTION_ANCHOR_RE = re.compile(
    r"Number\s+([1-9])\b|(?<!\d)([1-9])\s*[\u2014\u2013\-]\s*",
    re.IGNORECASE,
)

_OPTION_LABEL_STOP_RE = re.compile(r"[\u2014\u2013.]")

# "Or two in the afternoon" / "and quarter past eight" \u2014 how the model joins a
# SECOND time inside ONE numbered option. Without stripping it the segment can
# never match a slot's spoken label, so the time is spoken and never recorded.
_LEADING_CONNECTIVE_RE = re.compile(r"^(?:or|and)\b[\s,]*", re.IGNORECASE)


def _option_anchors(text: str) -> List[Tuple[int, int, str]]:
    return [
        (m.start(), m.end(), m.group(1) or m.group(2))
        for m in SLOT_OPTION_ANCHOR_RE.finditer(text or "")
    ]


def extract_slot_options(text: str) -> Dict[str, str]:
    """{digit: spoken label} for every numbered option in `text`, in order."""
    text = text or ""
    anchors = _option_anchors(text)
    out: Dict[str, str] = {}
    for i, (_start, end, digit) in enumerate(anchors):
        nxt = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
        label = text[end:nxt].lstrip(", ")
        label = _OPTION_LABEL_STOP_RE.split(label)[0].strip().rstrip(".,;- ")
        if label:
            out[digit] = label
    return out


def option_label_candidates(text: str) -> Dict[str, List[str]]:
    """{digit: [label candidates]} for every numbered option, best-effort first.

    `extract_slot_options` commits to the first segment before an em dash, which
    is right for the DTMF map — the keypad injects that label as a synthetic
    transcript, and "Thursday 27th August" is what a caller pressing 1 means.

    It is wrong for RESOLUTION. In the multi-day readout the option is
    "Thursday 27th August — half past seven in the evening", and the time is the
    only part that can match a slot: `_resolve_within` compares against the
    slot's `spoken` field by normalised equality, and that field holds a time.
    Truncating at the dash threw the time away, so every multi-day readout
    failed to resolve and the offer record was never written — on three
    consecutive live calls, every time the search widened.

    The single-day form put the day in the PREAMBLE and each option was already
    a bare time, which is why this was invisible until a widened search made
    Susie name a different day per option.

    Candidates are segments of what was actually spoken for that option, so a
    wrong match is not available to them: day segments match nothing (slot
    labels are times), leaving the time segment as the only thing that can hit.
    """
    out: Dict[str, List[str]] = {}
    text = text or ""
    anchors = _option_anchors(text)
    for i, (_start, end, digit) in enumerate(anchors):
        nxt = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
        whole = text[end:nxt].lstrip(", ").strip().rstrip(".,;- ")
        seen: List[str] = []
        for cand in [whole] + _OPTION_LABEL_STOP_RE.split(whole):
            cand = cand.strip().rstrip(".,;- ").lstrip(", ")
            # An option carrying two times reads "<day> — <t1>. Or <t2>", so the
            # trailing segment arrives as "Or two in the afternoon" and could
            # never match a slot's spoken label. Live on CAcb5988e0: the second
            # time was read out and never recorded, so the follow-up re-offered
            # it 19 seconds later.
            cand = _LEADING_CONNECTIVE_RE.sub("", cand).strip()
            if cand and cand not in seen:
                seen.append(cand)
        if seen:
            out[digit] = seen
    return out


def cap_spoken_options(
    text: str, cap: int = MAX_SPOKEN_OPTIONS
) -> Tuple[str, int, int]:
    """Trim a numbered readout to its first `cap` options.

    Returns `(text, n_before, n_after)`. `n_before != n_after` means the model
    read out more than it was allowed to and the excess was removed — which
    also means further times on that day now exist by construction, so the
    caller must set more_times True off this result.

    Whatever followed the LAST option's own sentence is re-attached: that is
    where the closing question lives ("... eight in the evening. Any of those
    work?"). Cutting at the anchor alone would take the question with it.
    """
    s = (text or "").strip()
    if not s or cap < 1:
        return text, 0, 0
    anchors = _option_anchors(s)
    n = len(anchors)
    if n <= cap:
        return text, n, n

    head = s[: anchors[cap][0]].rstrip()
    if not head:
        # Nothing at all before option cap+1 — not a shape we can safely
        # rewrite, so leave it and let the count mismatch be logged.
        return text, n, n
    if head[-1] not in ".!?":
        head += "."

    trailing = s[anchors[-1][1]:].strip()
    parts = _SENTENCE_SPLIT_RE.split(trailing, maxsplit=1)
    remainder = parts[1].strip() if len(parts) > 1 else ""
    return f"{head} {remainder or CAPPED_READOUT_QUESTION}".strip(), n, cap


def _norm_label(label: str) -> str:
    return " ".join(str(label or "").lower().split()).strip(" .,;:!?-")


def _norm_day(value: Any) -> str:
    """Normalise a day label for comparison, dropping the filler the model adds.

    The payload says "Monday 31st August"; the model often speaks "Monday the
    31st of August". Both must key the same day, or the scoping below silently
    stops applying and the ambiguity it exists to resolve comes back.
    """
    text = _norm_label(value if isinstance(value, str) else "")
    return " ".join(w for w in text.split() if w not in ("the", "of"))


def _resolve_within(
    slots: List[Dict[str, Any]],
    labels: List[str],
    known_days: Optional[set] = None,
) -> Optional[List[Dict[str, Any]]]:
    if not slots or not labels:
        return None
    by_label: Dict[str, List[Dict[str, Any]]] = {}
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for slot in slots:
        by_label.setdefault(_norm_label(slot.get("spoken") or ""), []).append(slot)
        _dl = _norm_day(slot.get("day_label") or "")
        if _dl:
            by_day.setdefault(_dl, []).append(slot)
    out: List[Dict[str, Any]] = []
    for label in labels:
        # A label may be one string, or an ordered list of candidates for the
        # same spoken option (see option_label_candidates). Candidates are
        # segments of one option, so at most one of them can be a time — trying
        # them in order cannot widen what is reachable, only recover the part
        # the em-dash truncation used to discard.
        cands = [label] if isinstance(label, str) else list(label or [])
        # The option names its OWN day in the multi-day readout
        # ("Number 1, Monday 31st August - quarter past eight in the evening").
        # Scope this option's time lookup to that day.
        #
        # Without it the time is looked up across every day at once, and a
        # clinic running the same rota two evenings running makes "quarter past
        # eight in the evening" ambiguous — so the all-or-nothing rule denied
        # data it could actually have told apart. Live on CA9bd4ecf0 (25 Aug):
        # the candidates carried both halves and resolution still returned
        # nothing.
        #
        # `prefer_day` at the caller cannot do this job: it is ONE day, and a
        # multi-day readout presents several, so it can only ever rescue one.
        _scoped = by_label
        for _c in cands:
            _nd = _norm_day(_c)
            if known_days is not None and _nd in known_days and _nd not in by_day:
                # This option NAMES a day the current pool does not contain —
                # it belongs to another day entirely. Falling through to the
                # pool's global map would resolve it to whatever slot happens to
                # share its spoken time, which is how "Number 2, Tuesday 8th
                # September" was recorded as MONDAY 09:00 on CA0453bd85.
                #
                # Both options then shared one day, so the single-day branch
                # wrote last_offered_slots — the record _resolve_slot_iso
                # indexes BY POSITION — and "the second one" would have booked
                # the wrong day. Refuse, so the caller retries unscoped.
                return None
            _pool = by_day.get(_nd)
            if _pool:
                _scoped = {}
                for _s in _pool:
                    _scoped.setdefault(
                        _norm_label(_s.get("spoken") or ""), []
                    ).append(_s)
                break
        hit = None
        for cand in cands:
            hits = _scoped.get(_norm_label(cand)) or []
            if len(hits) > 1:
                # Ambiguous is REFUSED, never retried with another candidate:
                # the same time on two days cannot be told apart from speech,
                # and picking one is how a caller is booked into a day they
                # never heard.
                return None
            if hits:
                hit = hits[0]
                break
        if hit is None:
            return None
        out.append(hit)
    return out


def resolve_spoken_options(
    available_days: Any, labels: Any, prefer_day: Optional[str] = None
) -> Optional[List[Dict[str, Any]]]:
    """Map spoken labels back to bookable slots. All-or-nothing, deny by default.

    Returns None unless EVERY label resolves to exactly one slot. A label that
    appears on more than one day counts as unresolvable rather than guessed:
    picking the wrong day would write a wrong `last_offered_slots`, and a wrong
    offer on the table is how a caller is booked into a day they never heard.

    `prefer_day` is what makes this usable rather than theoretical. A clinic
    running the same rota every evening has "five in the evening" on Tuesday
    AND Wednesday, and `available_days` holds the whole sweep — so a plain
    global lookup would be ambiguous on almost every real readout and quietly
    resolve nothing. The day being presented is known at the call site (the
    tool result's first_day, or the day of the offer already on the table), so
    the search is scoped to it first and only falls back to the whole set.
    """
    flat = flatten_bookable_slots(available_days)
    labels = list(labels or [])
    if not flat or not labels:
        return None
    # Every day the payload knows, so a prefer_day-scoped pass can tell
    # "this option names another day" apart from "this option names no day".
    _known_days = {
        _norm_day(s.get("day_label") or "") for s in flat
    } - {""}
    if prefer_day:
        scoped = _resolve_within(
            [s for s in flat if _day_key(s) == prefer_day], labels, _known_days
        )
        if scoped is not None:
            return scoped
    return _resolve_within(flat, labels, _known_days)


def resolve_all_spoken_times(
    available_days: Any, labels: Any, prefer_day: Optional[str] = None
) -> List[Dict[str, Any]]:
    """EVERY slot the readout actually named — for the cumulative record ONLY.

    `resolve_spoken_options` returns at most ONE slot per numbered option,
    deliberately: `last_offered_slots` is indexed BY POSITION for an ordinal
    choice ("the second one"), so a second entry for option 1 would shift what
    option 2 means and book the wrong slot.

    But an option can carry more than one time — "Number 1, Monday 7th
    September — ten in the morning. Or two in the afternoon" — and the caller
    HEARD both. On CAcb5988e0 only the first was recorded, so "what else have
    you got?" re-offered "two in the afternoon" 19 seconds after reading it out.

    Same day-scoping as the positional resolver, but collects every candidate
    that hits instead of stopping at the first. Best-effort by design: this
    feeds only `record_spoken_slots`, where a missed slot costs a repeat and a
    wrong slot would cost a withheld one. Nothing here reaches a booking, which
    is why it may return a partial set where the positional resolver refuses.
    """
    flat = flatten_bookable_slots(available_days)
    labels = list(labels or [])
    if not flat or not labels:
        return []
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for slot in flat:
        _dl = _norm_day(slot.get("day_label") or "")
        if _dl:
            by_day.setdefault(_dl, []).append(slot)

    out: List[Dict[str, Any]] = []
    seen_starts: set = set()
    for label in labels:
        cands = [label] if isinstance(label, str) else list(label or [])
        # Scope to the day THIS option names; else the presented day; else all.
        pool = None
        for cand in cands:
            _p = by_day.get(_norm_day(cand))
            if _p:
                pool = _p
                break
        if pool is None and prefer_day:
            pool = [s for s in flat if _day_key(s) == prefer_day] or None
        search = pool if pool is not None else flat
        by_label: Dict[str, List[Dict[str, Any]]] = {}
        for slot in search:
            by_label.setdefault(
                _norm_label(slot.get("spoken") or ""), []
            ).append(slot)
        for cand in cands:
            hits = by_label.get(_norm_label(cand)) or []
            if len(hits) != 1:
                continue          # ambiguous or absent — never guessed
            start = str(hits[0].get("start") or "")
            if start and start not in seen_starts:
                seen_starts.add(start)
                out.append(hits[0])
    return out


def unspoken_remain_on_day(session: Dict[str, Any], day: str) -> bool:
    """True if `day` still holds a bookable slot the caller has never heard.

    Ground truth for the "a few others that day" tail, read from the CUMULATIVE
    spoken record rather than from one turn's tool flag. All three producers of
    more_times are subsumed: a follow-up batch that finishes a day reads False
    here even though its own payload knows only about its own slots.
    """
    spoken = _spoken_key_set(session)
    for slot in flatten_bookable_slots(session.get("available_days") or []):
        if _day_key(slot) != day:
            continue
        if str(slot.get("start") or "")[:19] not in spoken:
            return True
    return False


def day_key_of(slot: Dict[str, Any]) -> str:
    """Public alias — the calendar day a slot belongs to."""
    return _day_key(slot)


_READBACK_FILLER = {"the", "of", "at", "on", "a", "so", "thats", "that", "is"}

# A read-back this guard can judge has to name a TIME. Without this,
# "let me look at Friday 4th September" names a known day, names none of its
# offered times, and is reported as a mismatch -- a WARNING on every ordinary
# sentence that mentions a date. That noise is paid for by the operator who
# has to pick the real ones out of it, on the very surface this defect is
# about. Deliberately generous: it only decides whether to LOOK, and a
# hallucinated time ("half past four") must still be caught.
_TIME_REFERENCE_RE = re.compile(
    r"\b\d{1,2}[:.]\d{2}\b"
    r"|\bo\W?clock\b"
    r"|\b(?:midday|noon|midnight)\b"
    r"|\b(?:half|quarter)\s+(?:past|to)\b"
    r"|\b(?:morning|afternoon|evening)\b",
    re.IGNORECASE,
)


def _readback_norm(value: Any) -> str:
    """Fold a spoken phrase or a payload label onto one comparable form.

    "Friday the 4th of September at two in the afternoon" and the payload's
    "Friday 4th September" have to meet somewhere: the model narrates, the
    payload labels. Only filler is dropped — every content word survives, so
    "one in the afternoon" and "two in the afternoon" stay distinct.
    """
    if not isinstance(value, str):
        return ""
    t = re.sub(r"[^a-z0-9\s]", " ", value.lower())
    return " ".join(w for w in t.split() if w not in _READBACK_FILLER)


def _spoken_starts_for_current_offer(session: Dict[str, Any]) -> set:
    """The ISO starts this caller has actually HEARD, for the current fetch.

    Read-only on purpose. `_spoken_key_set` RESETS the record when the
    availability fingerprint moves, and a Gate 5 text guard must never be the
    thing that clears a booking record on its way past. So the fingerprint is
    compared here and a stale record is declined rather than rebuilt.

    B-102, CA102f053758f4720339a5278a98fc8b9f (27 Aug 2026, theorem_v3,
    Alcester). B-101 made the WRITER day-granular and this reader was left
    deciding trust the other way round, so the two-round-trip shape B-101 was
    aimed at stayed live:

      10:37:44  spoken record dropped for ['2026-09-02'] -- Friday's 14:00
                SURVIVED the Wednesday detour, exactly as B-101 intends
      10:38:03  back to Friday -> ["14:00"], NO band-spent line
      10:38:17  caller asks a second time -> band ... is SPENT -> 12:00, 14:00

    The trusted set used to be built by iterating `new` -- the day set of the
    payload the session is HOLDING. As the public alias below documents, the
    availability builders call this while `available_days` still holds the
    PREVIOUS fetch, so on the first return to Friday `new` was the Wednesday
    payload, Friday was not a key in it, and the record B-101 had just
    preserved was filtered straight back out. The second ask only worked
    because the first return had by then put Friday back into `available_days`.

    So trust is decided against `old`, which is the writer's rule and the same
    sentence: a day the payload in hand does not mention keeps what it knew,
    because ABSENCE IS NOT CHANGE. `new` can still veto -- a day it mentions
    with a different fingerprint has really moved and cannot vouch for what was
    heard on it, which is the B-97 protection the reset existed for.

    Iterating the RECORD rather than either fingerprint map is what makes the
    empty-payload case fall out correctly instead of needing its own early
    return: no record, no opinion. `old.get(day) is not None` keeps it
    fail-closed -- a day nothing ever vouched for is not trusted on the
    strength of appearing in the record.
    """
    old = session.get(_SPOKEN_FP_KEY)
    if not isinstance(old, dict):
        return set()          # pre-B-101 shape, or nothing -- verify nothing
    new = _day_fingerprints(session.get("available_days") or [])
    trusted = {
        day for day in {str(s)[:10] for s in (session.get(_SPOKEN_KEY) or [])}
        if old.get(day) is not None and new.get(day, old[day]) == old[day]
    }
    if not trusted:
        return set()
    return {
        str(s)[:19] for s in (session.get(_SPOKEN_KEY) or [])
        if str(s)[:10] in trusted
    }


def spoken_starts_for_offer(session: Dict[str, Any]) -> set:
    """Public alias -- the ISO starts the caller has HEARD, for the current offer.

    Read-only, like the function it wraps. The availability builders call this
    while session["available_days"] still holds the PREVIOUS fetch, which is
    exactly the comparison they want: what did this caller already hear, before
    the lookup now running overwrites it.
    """
    return _spoken_starts_for_current_offer(session)


def reconcile_readback_time(
    text: str, session: Dict[str, Any]
) -> Tuple[str, str, str]:
    """Make a confirmation read-back name a time that was actually OFFERED.

    Returns `(text, action, detail)`; action is "unchanged", "corrected" or
    "mismatch".

    B-95, CA1cd253cb (26 Aug 2026, theorem_v3). Two options were read out:

        Number 1, Wednesday 2nd September - two in the afternoon.
        Number 2, Friday 4th September - one in the afternoon.

    The caller said "the second one please" and heard back

        "So that's Friday the 4th of September at TWO in the afternoon
         - could I take your first name and surname?"

    Number 2's day with Number 1's time. Nothing compared the read-back against
    the option that had been selected, so the caller was asked to agree to a
    time they had never been offered.

    It reached the caller because a multi-day readout deliberately does not
    write the position-indexed offer record, so an ordinal choice is resolved by
    the model rather than from data. Rather than widen that record, this checks
    the sentence against the payload on the way out.

    THE DENOMINATOR IS WHAT WAS SPOKEN, NOT WHAT IS BOOKABLE. This is the whole
    difficulty. `_cap_presented_slots` says it outright - "available_days stays
    the FULL bookable set ... Does not touch session['available_days']" - and in
    multi_day it speaks exactly ONE time per day. So available_days for Friday
    holds every time the diary has free, while the caller heard one of them.
    Checking against available_days would fail in both directions at once: the
    live sentence would be unfixable ("more than one time that day, nothing to
    choose between") AND, where the wrongly-named time happens to be bookable
    but unspoken that day, it would be waved through as correct. The guard would
    be inert on the exact call it was written for.

    The set the caller actually heard is the cumulative spoken record, which IS
    written on the multi-day path (`record_spoken_slots(session, _all_heard or
    _r)`) even where the offer record is not. Cumulative rather than
    `last_offered_slots` on purpose: a caller may confirm a time from an earlier
    offer in the same call, and that is a legitimate confirmation, not a
    mismatch.

    THE TIME IS CORRECTED TO THE DAY, NEVER THE REVERSE, for the same reason the
    weekday corrector goes one way only: the caller picked an option and the
    slot map corroborates which DAY that option was; nothing corroborates the
    time. Rewriting the day to suit a time the model invented would move the
    appointment rather than repair the sentence.

    Deny by default. A correction happens only when the phrase names exactly one
    known day, names no time that was spoken for that day, and exactly ONE time
    was spoken for it, so there is nothing to choose between. Anything else is
    returned untouched and reported as "mismatch", because a wrong time the code
    cannot safely fix is still worth having in the call record.
    """
    if not text or not isinstance(text, str):
        return text, "unchanged", ""
    if not isinstance(session, dict):
        return text, "unchanged", ""

    available_days = session.get("available_days")
    if not isinstance(available_days, list) or not available_days:
        return text, "unchanged", ""

    phrase = _readback_norm(text)
    if not phrase:
        return text, "unchanged", ""

    flat = [s for s in flatten_bookable_slots(available_days) if s.get("start")]
    if not flat:
        return text, "unchanged", ""

    # Exactly one known day, or no opinion. A sentence naming two days is a
    # readout, not a confirmation, and is none of this function's business.
    dates = {
        s.get("date")
        for s in flat
        if s.get("date")
        and s.get("day_label")
        and _readback_norm(s["day_label"]) in phrase
    }
    if len(dates) != 1:
        return text, "unchanged", ""
    date = dates.pop()

    spoken_starts = _spoken_starts_for_current_offer(session)
    if not spoken_starts:
        return text, "unchanged", ""

    offered: List[str] = []
    for s in flat:
        if s.get("date") != date:
            continue
        if str(s.get("start") or "")[:19] not in spoken_starts:
            continue                      # bookable that day, but never said
        label = str(s.get("spoken") or "").strip()
        if label and label not in offered:
            offered.append(label)
    if not offered:
        return text, "unchanged", ""
    if any(_readback_norm(t) in phrase for t in offered):
        return text, "unchanged", ""      # names a time really offered that day
    if not _TIME_REFERENCE_RE.search(text):
        return text, "unchanged", ""      # names the day but no time at all

    day_label = next(
        (s["day_label"] for s in flat if s.get("date") == date and s.get("day_label")),
        date,
    )

    # The phrase names this day and none of its offered times. Find the time it
    # DID name, and only among labels the payload actually contains - never a
    # free-form parse, so an unrecognised phrasing is left alone.
    wrong = None
    for s in flat:
        label = str(s.get("spoken") or "").strip()
        if not label or label in offered:
            continue
        if _readback_norm(label) in phrase:
            wrong = label
            break

    if wrong is None or len(offered) != 1:
        return (
            text,
            "mismatch",
            f"read-back names {day_label} but not one of the times offered on "
            f"it {offered!r}",
        )

    out = re.sub(re.escape(wrong), offered[0], text, flags=re.IGNORECASE)
    if out == text:
        return text, "mismatch", f"could not locate {wrong!r} to correct"
    return out, "corrected", f"{wrong!r} -> {offered[0]!r} for {day_label}"


def day_named_in_readout(available_days: Any, text: str) -> "str | None":
    """The calendar day this readout NAMES, or None when it names 0 or 2+.

    B-93, CA903bd6ef (26 Aug 2026, vital_edge). A readout may put the day in a
    HEADER rather than inside each option —

        "Tuesday 1st September — Number 1, one in the afternoon.
         Number 2, two in the afternoon. Number 3, three in the afternoon."

    — which leaves every option a bare time. Bare times cannot say which day
    they belong to, so the resolver leans on `_slot_presented_day`, and that
    field is inherited from the previous payload's FIRST day. When the previous
    offer spanned two days and the caller picked the second, the inherited day
    is the one they did NOT choose: the caller heard Tuesday and the offer
    record was written with Monday's ISO times.

    Matched against the payload's own `day_label` strings rather than parsed out
    of prose. Those labels are what the formatter is given and what it echoes,
    so this asks "which of the days I know about did this sentence name?" — a
    data question with a checkable answer — instead of trying to read English
    dates. A paraphrase matches nothing and returns None, which falls back to
    the previous behaviour rather than guessing.

    None on 2+ matches is deliberate: a multi-day readout has no single day, and
    the multi_day branch above already declines to write the offer record.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(available_days, list):
        return None
    low = text.lower()
    hits = set()
    for day in available_days:
        if not isinstance(day, dict):
            continue
        label = str(day.get("day_label") or "").strip().lower()
        date = str(day.get("date") or "").strip()
        if not label or not date:
            continue
        if label in low:
            hits.add(date)
    if len(hits) == 1:
        return hits.pop()
    return None


def _is_extra_slots_claim(sentence: str) -> bool:
    """True when `sentence` asserts further times beyond those listed."""
    s = sentence.strip()
    if not s:
        return False
    # A numbered option is never a claim, whatever words it contains — it is
    # parsed for keypad selection and must survive untouched.
    if re.search(r"\bNumber\s+[1-9]\b", s, re.IGNORECASE):
        return False
    # TWO signals means two WORDS. The alternations overlap — "other",
    # "others" and "more" are members of both — so a lone match satisfied the
    # quantity half and the further-times half at once, and the two-signal rule
    # the docstring promises collapsed to a one-word rule.
    #
    # B-92, CAe0bccbcf (26 Aug 2026, theorem_v3). "Would one of the other days
    # work better for you?" tripped both halves off the single word "other" and
    # was deleted as an unfounded availability claim. It is not a claim about
    # times at all — it is the offer to look elsewhere, and the day it was
    # deleted from had one slot while the clinic had 95 across the month. The
    # caller had asked three times, heard "No, that's the only slot ..." with no
    # question behind it, and hung up. The watchdog BACKSTOP armed one turn
    # later, which is the sentence-with-no-question state the append path is
    # explicitly ordered to avoid; the strip path had no such protection.
    #
    # Requiring the two matches at DIFFERENT offsets restores the rule as
    # written without touching either alternation: "a few others", "a couple
    # more" and "more times" all still carry two distinct words and are still
    # stripped.
    _q = [m.span() for m in _EXTRA_QUANTITY_RE.finditer(s)]
    _f = [m.span() for m in _FURTHER_TIMES_RE.finditer(s)]
    return any(_qs != _fs for _qs in _q for _fs in _f)


def reconcile_extra_slots_claim(
    text: str, more_times: bool, n_offered: int = 2, allow_append: bool = True
) -> Tuple[str, str]:
    """Align a slot presentation's "more times that day" claim with the truth.

    Returns `(text, action)` where action is one of "stripped", "appended" or
    "unchanged", for logging.

    The two directions are deliberately NOT symmetric:

      more_times False → any such claim is REMOVED. Over-promising availability
        is the harm: the caller is told times exist that do not, and the
        follow-up path will contradict it one turn later with "I don't have any
        further times on that day".

      more_times True  → the tail is appended only when the reply does not
        already make the claim AND does not assert completeness. Under-informing
        is safe and recoverable — a caller who asks "anything else that day?"
        is served the real next batch by next_slot_batch(). Appending next to a
        completeness opener would make Susie contradict herself in one breath,
        which is worse than staying quiet.

    `allow_append` is False for a multi_day presentation. The tail says "that
    day", and a multi_day reply has just named TWO different days — there is no
    "that day" for it to refer to. The sentence only ever belonged to the
    single_day cases. Stripping stays unconditional: a false claim is wrong in
    either presentation mode.
    """
    if not (text or "").strip():
        return text, "unchanged"

    sentences = _SENTENCE_SPLIT_RE.split(text.strip())

    if not more_times:
        kept = [s for s in sentences if not _is_extra_slots_claim(s)]
        if len(kept) == len(sentences):
            return text, "unchanged"
        if not kept:
            # Never blank a reply. A presentation that is ENTIRELY an
            # availability claim is not something we can safely rewrite, so
            # leave it and let the caller hear it — the mismatch is logged.
            return text, "unchanged"
        return " ".join(kept).strip(), "stripped"

    # more_times is TRUE from here, so a claim that the day holds exactly one
    # time is false. Correcting it comes BEFORE the allow_append bail for the
    # same reason stripping does in the other direction: a false claim is wrong
    # in either presentation mode, and only the optional tail is mode-gated.
    #
    # The phrase is replaced, not the sentence removed -- the sentence carries
    # the time the caller needs, and "never blank a reply" applies here too.
    text, _n_rewritten = _SINGULAR_COMPLETENESS_RE.subn(
        _SINGULAR_COMPLETENESS_SUB, text,
    )
    if _n_rewritten:
        sentences = _SENTENCE_SPLIT_RE.split(text.strip())
        # Loud on purpose, like the strip in the other direction: this is the
        # model telling a caller a day is full when the tool result says it is
        # not, and it belongs in the call record.
        logger.warning(
            "[slot_followup] CORRECTED a false 'the only time is' claim — the "
            "day holds more times (B-100). after=%r", text[:160],
        )

    if not allow_append:
        return text, ("rewritten" if _n_rewritten else "unchanged")
    if any(_is_extra_slots_claim(s) for s in sentences):
        return text, ("rewritten" if _n_rewritten else "unchanged")
    if _COMPLETENESS_RE.search(text):
        # A completeness claim this cannot safely correct (the plural opener).
        # Staying quiet is still right: appending "and I've a few others" next
        # to it would make Susie contradict herself in one breath.
        return text, ("rewritten" if _n_rewritten else "unchanged")

    tail = more_times_tail(n_offered)
    # BEFORE the closing question, not after it. "Any of those work? And I've a
    # few others that day" makes the caller's "yes" ambiguous between "yes, one
    # of those works" and "yes, tell me the others" — and it ends the readout on
    # a statement, which arms the watchdog BACKSTOP and reads as dead air.
    # Offering the extra times first and then asking is the order a receptionist
    # would use, and it is the order the caller can answer.
    if sentences and sentences[-1].rstrip().endswith("?"):
        head = " ".join(sentences[:-1]).strip()
        if head:
            return f"{head} {tail} {sentences[-1].strip()}", "appended"
    return f"{text.rstrip()} {tail}", "appended"


def _spoken_series(labels: List[str]) -> str:
    """"a", "a, or b", "a, b, or c" — a spoken list, not a written one.

    No Oxford comma before a two-item "or": "five, or half five" is how a
    receptionist says it, and it is what the two-slot form has always emitted.
    """
    labels = [l for l in labels if l]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])}, or {labels[-1]}"


def _closing_question(n: int) -> str:
    if n <= 1:
        return "Does that work?"
    if n == 2:
        return "Either of those work?"
    return "Any of those work?"


def format_next_batch_speech(batch: List[Dict[str, Any]], more: bool) -> str:
    """Speak a follow-up batch of ANY size on one day.

    Sizes 1 and 2 are byte-identical to the two-slot form this replaced. Sizes
    3+ exist because the owner rule (24 Aug 2026) is that the FIRST offer is
    capped at three and an explicit "tell me the others" is answered with every
    remaining time on that day — see all_remaining_on_next_day.
    """
    if not batch:
        return (
            "I don't have any further times on that day — would you like me "
            "to look at a different day?"
        )
    day = batch[0].get("day_label") or "that day"
    series = _spoken_series([s.get("spoken") or "" for s in batch])
    tail = f" {more_times_tail(len(batch))}" if more else ""
    return (
        f"On {day} I also have {series}.{tail} "
        f"{_closing_question(len(batch))}"
    )


def format_time_available_speech(slot: Dict[str, Any]) -> str:
    day = slot.get("day_label") or "that day"
    spoken = slot.get("spoken") or slot.get("time") or "that time"
    return (
        f"Yes — {spoken} on {day} is free. "
        f"Shall I book that in for you?"
    )


def _supersede_slot_map(session: Dict[str, Any]) -> None:
    """B-80 — the keypad map no longer describes what the caller just heard.

    `v3_dtmf_slot_map` is built in `_flush_slot_buf` from a NUMBERED readout.
    The deterministic follow-up paths below speak their times directly and
    UNNUMBERED, never reaching that function, so the map from the previous
    readout survives intact while the offer has moved on. On CA6b90c3a2
    (24 Aug, 12:24:39) the map still listed all five times while the follow-up
    had just offered 20:00 alone: a keypress would have booked a time the
    caller heard earlier and was no longer being offered.

    Marking rather than clearing, deliberately. `v3_dtmf_slot_map` is the
    OWNER of the slot window -- `_derive_slot_window` re-derives
    `v3_awaiting_slot_selection` from it every turn, and
    `_should_clear_slot_cache` reads its presence to decide whether the next
    turn may wipe `last_offered_slots`. Popping the map here would therefore
    hand the next turn permission to wipe the very input these follow-up
    paths open with (`if not offered: return None`) -- re-breaking B-78, the
    defect this whole module exists to fix. The window must stay open for
    VOICE; only the digit-to-label resolution is invalidated.

    Cleared again wherever a fresh map is armed, and when the window closes.
    """
    session["v3_slot_map_superseded"] = True


def apply_next_batch_to_session(
    session: Dict[str, Any],
    batch: List[Dict[str, Any]],
    more: bool,
) -> str:
    """Advance last_offered to this batch and return the spoken offer."""
    # Cumulative FIRST — last_offered_slots is about to be overwritten, and it
    # is the only record that this batch was ever spoken (B-78b).
    record_spoken_slots(session, batch)
    session["last_offered_slots"] = [
        {"start": s["start"], "end": s.get("end") or ""} for s in batch
    ]
    session["slot_labels"] = [s.get("spoken") or s.get("time") for s in batch]
    # B-80: these times are spoken UNNUMBERED, so 1..N no longer refer to them.
    _supersede_slot_map(session)
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
    # B-80: the offer is now this single time; the numbered map is stale.
    _supersede_slot_map(session)
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
    # Fail-safe: this struct declares presentation_mode="single_day" and carries
    # ONE date/day_label, so every slot in it must belong to that day. next_slot_batch
    # guarantees that; this is the backstop for any future caller that does not.
    # Dropping the off-day slots is the safe direction — offering fewer times costs
    # the caller a follow-up question, whereas announcing a slot under the wrong
    # day's name sends a real patient to the clinic on a day they have no
    # appointment (CA5c4fb14f, 30 Jul 2026).
    _day = _day_key(batch[0])
    _same_day = [s for s in batch if _day_key(s) == _day]
    if len(_same_day) != len(batch):
        logger.error(
            "[slot_followup] multi-day batch reached build_followup_tool_result — "
            "dropping %d off-day slot(s) to protect the spoken day label. "
            "kept=%s dropped=%s",
            len(batch) - len(_same_day),
            [s.get("start") for s in _same_day],
            [s.get("start") for s in batch if _day_key(s) != _day],
        )
        batch = _same_day
        more = True  # the dropped slots still exist, just not on this day

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


def offer_day_hides_times(session: Dict[str, Any]) -> bool:
    """True when a preference band removed times from a day now on the table.

    available_days is what SURVIVED the caller's time-of-day band, so when this
    is True the session's copy of that day is not the day. Every follow-up here
    subtracts from that copy, so it can only ever offer the survivors -- and
    then report the day exhausted when it runs out of them.

    B-98 taught the retrieval path to open such a day up once its in-band times
    have been spoken. This is how the session-served paths know to let it.
    """
    days_by_date = {
        str(d.get("date") or ""): d
        for d in (session.get("available_days") or [])
        if isinstance(d, dict)
    }
    for offer in (session.get("last_offered_slots") or []):
        day = days_by_date.get(str((offer or {}).get("start") or "")[:10])
        try:
            if day is not None and int(day.get("times_not_shown") or 0) > 0:
                return True
        except Exception:
            continue
    return False


def exhaustion_claim_is_supported(session: Dict[str, Any]) -> bool:
    """May Susie say "I don't have any further times on that day"?

    That sentence is a completeness claim about a DAY -- the same claim B-97
    caught in "that's the only one we have that day", made by a different
    producer that no banned-phrase table and no availability guard can see. It
    needs the same two things to be true, and this is the one place that asks.

    B-99, CA890b511e (27 Aug 2026, theorem_v3, Alcester). At 08:42:49 Susie
    said it about Friday 28 August. At 08:43:39, fifty seconds later and on the
    same call, that day produced a midday appointment. Both halves were wrong
    at once:

      1. THE DAY WAS NOT IDENTIFIED. The caller had asked about "wednesday the
         2nd of september". The offer on the table spanned THREE days, and the
         follow-up takes "the day under discussion" from last_offered_slots[0]
         -- whichever sorts first, here Friday 28 August. So the answer was
         about a day nobody had asked about, in words ("that day") that sound
         like it was about the one they did.

      2. THE DAY WAS NOT EXHAUSTED. The caller had said "afternoons", so the
         band had already removed midday from Friday before the session ever
         saw it. Subtracting the spoken times from the survivors reaches zero
         while the day still holds a bookable appointment.

    So: exactly one day on the table, and that day complete. Anything else and
    the caller is better served by a real lookup, which B-98 will open up.

    Fails CLOSED, like _scarcity_claim_is_supported: an unreadable session
    declines to make the claim rather than making it unverified.
    """
    try:
        offered = session.get("last_offered_slots") or []
        if not isinstance(offered, list) or not offered:
            return False
        days = {str((o or {}).get("start") or "")[:10] for o in offered}
        days.discard("")
        if len(days) != 1:
            return False          # no single "that day" to be speaking about
        want = days.pop()
        # POSITIVE proof, deliberately not "we did not find a reason to doubt".
        # The day has to be present and readable and say it hides nothing --
        # an available_days this cannot parse has verified NOTHING, and the
        # asymmetry with offer_day_hides_times is the point: that one opens the
        # guard on positive knowledge of hiding, this one speaks a sentence on
        # positive knowledge of completeness, and both stay quiet when the
        # session cannot answer.
        for day in (session.get("available_days") or []):
            if not isinstance(day, dict) or str(day.get("date") or "") != want:
                continue
            return int(day.get("times_not_shown") or 0) == 0
        return False
    except Exception:
        return False


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

    # Cumulative, not just the current offer — see B-78b above.
    remaining = remaining_unspoken(session)
    if not remaining:
        # The day is genuinely exhausted. Say so HERE rather than falling to
        # the model: "have you got anything else?" with nothing left is the
        # exact prompt that produced "Those are the two available slots on that
        # day" while three sat unoffered. The honest answer is deterministic,
        # so it should not be generated.
        if utterance_requests_more_slots(user_text):
            if not exhaustion_claim_is_supported(session):
                logger.info(
                    "[slot_followup] declining the exhaustion sentence -- the "
                    "offer on the table is not one complete day, so 'no further "
                    "times on that day' is unverifiable here (B-99). Falling "
                    "through to a real lookup."
                )
                return None
            return format_next_batch_speech([], False)
        return None

    # Specific unspoken time first (V5).
    hit = resolve_requested_time(user_text, remaining)
    if hit is not None:
        return apply_resolved_time_to_session(session, hit)

    if utterance_requests_more_slots(user_text):
        # Scoped to the day under discussion. `remaining` above stays whole-
        # sweep on purpose: resolve_requested_time names the slot's OWN day, so
        # a caller-named time on another day cannot mislead, and refusing it
        # would tell them a real time does not exist.
        #
        # user_text is handed down so a day the caller NAMED in this utterance
        # wins over the first slot of the offer (B-103). Without it the scope
        # is always day one of a multi-day offer, whatever they asked about.
        batch, more = all_remaining_on_next_day(
            remaining_unspoken_on_current_day(session, user_text)
        )
        if not batch:
            # This DAY is exhausted even though other days remain. Say so
            # rather than falling to the model — the same reasoning as the
            # empty-remaining branch above, and the same sentence.
            #
            # ...but only when there is one day it could be about and that day
            # is really empty. B-99: on a three-day offer this branch spoke
            # about last_offered_slots[0] while the caller had named a
            # different day, and did it about a day whose midday the band had
            # hidden. See exhaustion_claim_is_supported.
            if not exhaustion_claim_is_supported(session):
                logger.info(
                    "[slot_followup] declining the exhaustion sentence -- the "
                    "offer on the table is not one complete day, so 'no further "
                    "times on that day' is unverifiable here (B-99). Falling "
                    "through to a real lookup."
                )
                return None
            return format_next_batch_speech([], False)
        return apply_next_batch_to_session(session, batch, more)

    return None
