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
from datetime import date as _date
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
_SPOKEN_LOC_KEY = "slot_starts_spoken_loc"

# Days whose spoken record is a LOSSY projection, not a transcript of what was
# read out. B-126: on multi_day, `_sync_last_offered_to_spoken` records ONE slot
# per day -- `slots[0]`, because `_resolve_slot_iso` indexes that list BY
# POSITION for an ordinal choice -- while the formatter prompt instructs TWO
# times per day and may reach into `available_days` for the second. A day listed
# here was heard with times the record does not hold, so "exactly one time was
# spoken for it" is an artefact of the projection and nothing may be corrected
# against it. Written by the availability executor; read by
# reconcile_readback_time.
LOSSY_SPOKEN_DAYS_KEY = "_lossy_spoken_days"


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

    B-115: the VALUE is no longer compared for equality -- see
    `_day_record_survives`. It stays because `old.get(day) is not None` is how
    both readers ask "have we ever vouched for this day", because the shape has
    to keep parsing for a call in flight across the deploy, and because it is
    worth having in the log when a day does get dropped.
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


def _day_slot_starts(available_days: Any) -> Dict[str, set]:
    """{day: {ISO starts it currently holds}} -- what `_day_record_survives`
    tests the spoken record against."""
    by_day: Dict[str, set] = {}
    for slot in flatten_bookable_slots(available_days):
        start = str(slot.get("start") or "")[:19]
        if start:
            by_day.setdefault(_day_key(slot), set()).add(start)
    return by_day


def _days_showing_a_filtered_view(available_days: Any) -> set:
    """Days whose payload is a FILTERED view rather than the whole day.

    `times_not_shown` is the count a time-of-day band removed before the
    session ever saw the day (B-97). Where it is positive, a slot missing from
    `slots` is missing because of the filter, and its absence is no evidence
    at all about the diary.
    """
    out: set = set()
    for day in (available_days or []):
        if not isinstance(day, dict):
            continue
        try:
            if int(day.get("times_not_shown") or 0) > 0:
                date = str(day.get("date") or "")
                if date:
                    out.add(date)
        except Exception:
            continue
    return out


def _spoken_on_day(spoken: Any, day: str) -> List[str]:
    return [str(s)[:19] for s in (spoken or []) if str(s)[:10] == day]


def _day_record_survives(
    current_starts: Optional[set],
    spoken_on_day: List[str],
    day_is_a_filtered_view: bool = False,
) -> bool:
    """May we still trust what the caller was recorded as having HEARD on this
    day?

    ONE owner, because two functions ask it -- `_spoken_key_set`, which drops
    the record, and `_spoken_starts_for_current_offer`, which declines to read
    it. They disagreed once already (B-102) and the cost was the B-101 shape
    surviving its own fix.

    Yes when every start the caller heard is still in the day. A slot that has
    since been booked by someone else, or a day that has become a different
    clinic's diary, takes the record with it.

    B-115, CA0f8ffe7b (28 Aug 2026, theorem_v3). The test used to be equality
    on the day's `count|first|last` fingerprint, which cannot tell a day that
    GREW from a day that CHANGED:

        10:40:37  band-filtered payload, 2 of the day's 7 slots
                  fingerprint 2|...T09:00|...T10:00 -- caller hears both
        10:40:56  B-98 sees the band is spent and opens the day to all 7
                  fingerprint 7|...T09:00|...T16:00
        10:40:57  spoken record dropped for ['2026-09-08']

    B-98 opens a day precisely BECAUSE its in-band times have been spoken, so
    the act of opening it destroyed the record that justified opening it. The
    two slots were still there; five more had appeared beside them.

    Nothing the caller heard on that call was lost by the drop -- the
    presentation is spoken-blind and re-read them anyway -- so this is a
    latent fault, not the cause of that re-offer. It is a prerequisite: any
    future presentation that filters by "already heard" reads this record, and
    would find it empty at exactly the moment it matters.

    A day absent from the current payload keeps its record (B-101): a lookup
    for Wednesday says nothing about Friday.

    And a day shown through a BAND keeps it too. A payload can shrink for two
    unrelated reasons -- the diary lost a slot, or a filter hid one -- and the
    slot list alone cannot tell them apart. `times_not_shown` can: where it is
    positive the view is partial by construction, so a missing heard time is
    missing because of the filter.

    Found by the failing-set diff, not by design. The first cut of B-115 read
    any absence as removal, which broke
    test_b112::test_a_day_heard_in_full_before_a_band_shrank_it_is_not_re_promised
    -- a caller who had heard all seven of a day unbanded, then met a banded
    payload showing two, had their record wiped and was promised "a few others
    that day" with nothing left to give. Over-promising is the harm
    reconcile_extra_slots_claim exists to prevent, so the rule reached the one
    outcome this family must never produce.
    """
    if current_starts is None:
        return True
    if day_is_a_filtered_view:
        return True
    return all(start in current_starts for start in spoken_on_day)


def _spoken_key_set(session: Dict[str, Any]) -> set:
    """The ISO starts the caller has heard, dropping any day that has moved."""
    new = _day_fingerprints(session.get("available_days") or [])
    starts_now = _day_slot_starts(session.get("available_days") or [])
    old = session.get(_SPOKEN_FP_KEY)
    if not isinstance(old, dict):
        # Either the pre-B-101 single-string form (a call in flight across the
        # deploy) or nothing at all. Neither can verify a day, so nothing is
        # trusted -- the same fail-closed direction the old whole-payload
        # mismatch took.
        session[_SPOKEN_KEY] = []
        old = {}

    # A different clinic's diary is not the same day, however similar the
    # times look. The old equality test caught this by accident whenever the
    # slot COUNT happened to differ; B-115's rule would not, because a 9am
    # that exists at both locations looks like the same 9am. Made explicit
    # rather than left to coincidence -- and the whole record goes, because
    # every day in it was read off the other diary.
    _loc = str(session.get("selected_location") or "")
    _loc_before = session.get(_SPOKEN_LOC_KEY)
    if _loc_before is not None and _loc_before != _loc:
        logger.info(
            "[slot_followup] spoken record cleared -- the location moved from "
            "%r to %r, so every day in it was read off another diary (B-115).",
            _loc_before, _loc,
        )
        session[_SPOKEN_KEY] = []
        old = {}
    session[_SPOKEN_LOC_KEY] = _loc

    _spoken = session.get(_SPOKEN_KEY) or []
    _filtered = _days_showing_a_filtered_view(session.get("available_days") or [])
    changed = {
        day for day in new
        if old.get(day) is not None
        and not _day_record_survives(
            starts_now.get(day), _spoken_on_day(_spoken, day), day in _filtered,
        )
    }
    if changed:
        session[_SPOKEN_KEY] = [
            s for s in (session.get(_SPOKEN_KEY) or [])
            if str(s)[:10] not in changed
        ]
        logger.info(
            "[slot_followup] spoken record dropped for %s -- a time the caller "
            "heard is no longer in that day. Every other day is kept "
            "(B-101/B-115).", sorted(changed),
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


# Cardinals as well as ordinals: after a numbered readout a caller says
# "the SECOND day" and equally often "number TWO". _fold_ordinals covers the
# first form only -- "second" is in _ORDINAL_UNITS, "two" is not.
_CARDINAL_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_CARDINAL_RE = re.compile(
    r"\b(" + "|".join(sorted(_CARDINAL_UNITS, key=len, reverse=True)) + r")\b"
)

# A position is only a position when it is FRAMED as one. A bare number is a
# date far more often than an index ("the 2nd of September"), so every arm
# here carries an explicit positional word -- number/option/day/one -- and a
# lone digit matches nothing.
_POSITION_RE = re.compile(
    r"\b(?:number|option)\s+(\d{1,2})\b"
    r"|\b(\d{1,2})\s+(?:one|day)\b"
    r"|\bday\s+(\d{1,2})\b"
)


def _positions_named(text: str) -> set:
    """Every list position `text` refers to, as a set of ints.

    Matched against BOTH folded forms, because the two foldings are mutually
    destructive on the commonest phrasing of all:

        "the second one"  --ordinals-->  "2 one"  --cardinals-->  "2 1"

    Cardinal folding is what makes "number TWO" resolve, and it is also what
    eats the positional noun in "the second ONE" -- leaving "2 1", which
    matches nothing. Neither ordering saves both, so both forms are tried and
    the hits unioned.

    Unioning is safe in the direction that matters: it can only ever ADD a
    position, and two positions make the caller ambiguous, which declines. A
    phrase resolving to one position under both forms stays one.
    """
    ordinal_only = _fold_ordinals(_caller_norm(text))
    both = _CARDINAL_RE.sub(
        lambda m: str(_CARDINAL_UNITS[m.group(1)]), ordinal_only
    )
    return {
        int(g)
        for form in (ordinal_only, both)
        for match in _POSITION_RE.finditer(form)
        for g in match.groups() if g
    }


def day_selected_by_position(
    available_days: Any, session: Dict[str, Any], text: str
) -> "str | None":
    """The calendar day the caller picked BY ITS POSITION in the readout.

    B-105, CA0eb9a12c (JV go-live rehearsal, 27 Aug 2026). The rung B-103 and
    B-104 left open. Those two taught this family to honour a day the caller
    NAMES; a numbered readout invites the caller to pick by NUMBER instead,
    and that phrasing still fell through to `last_offered_slots[0]`:

        offer:   Number 1, Monday 7th September | Number 2, Tuesday 8th September
        caller:  "the second day suits me, could you give me all the slots
                  you have on that day"
        answer:  "On MONDAY 7th September I also have ..."

    The caller then picked a time from Monday's list, the read-back corrector
    saw a time that was not in Tuesday's offer and rewrote the TIME to fit the
    day the model had drifted to, and the caller was asked to agree to -- and
    did agree to -- a day and time that were never on the table together.

    Resolved against `v3_dtmf_slot_map`, which is the same index -> label map
    the keypad path uses, so a spoken "number two" and a pressed 2 resolve
    through one table rather than two that can disagree.

    Returns None unless the map's value CONTAINS a day label: the identical
    map is built for a time_selection readout, where "the second one" means a
    time and scoping a day by it would be the very error this prevents.
    Matching the resolved label back against `available_days` is what tells
    the two apart, and it costs nothing when the map is absent.

    Containment rather than equality, and the difference is not cosmetic.
    `extract_slot_options` cuts an option's label at an em dash, an en dash or
    a full stop -- and at nothing else. "Number 2, Tuesday 8th September - five
    in the evening" stores "Tuesday 8th September" and would match either way;
    "Number 2, Tuesday 8th September at five in the evening" stores the whole
    line, which equals no day label at all. That phrasing is the model's to
    choose, so an equality test would leave this guard silently inert on a
    wording nothing enforces -- the failure mode this fix exists to end.

    Requiring exactly ONE label to be contained keeps the loosening safe: two
    labels in one option is not a day pick, and declines to the old behaviour
    rather than guessing between them.
    """
    if not isinstance(text, str) or not isinstance(available_days, list):
        return None
    slot_map = (session or {}).get("v3_dtmf_slot_map") or {}
    if not isinstance(slot_map, dict) or not slot_map:
        return None

    hits = _positions_named(text)
    if len(hits) != 1:
        return None                      # named none, or named two -- decline
    label = slot_map.get(str(next(iter(hits))))
    if not label:
        return None

    hay = f" {_caller_norm(label)} "
    hits = []
    for day in available_days:
        if not isinstance(day, dict):
            continue
        day_label = _caller_norm(day.get("day_label") or "")
        date = str(day.get("date") or "").strip()
        if day_label and date and f" {day_label} " in hay:
            hits.append(date)
    # More than one label inside one option is not a day pick -- decline.
    return hits[0] if len(hits) == 1 else None


def label_for_spoken_position(
    session: Dict[str, Any], text: str, available_days: Any = None
) -> "str | None":
    """The readout label the caller picked BY POSITION, on a SELECTION turn.

    B-127, CA6a59e59f0a67fe964693a64690f70544 (1 Sept 2026, build 5ebe0211,
    the first live 3x2 multi_day readout):

        Number 1, Tuesday 1st September -- twenty past eleven, or ten past five.
        Number 2, Wednesday 2nd September -- eight in the morning, or ...
        Number 3, Thursday 3rd September -- ...

        caller:  "uh yeah the second one please"
        Susie:   "Tuesday the 1st of September at twenty past eleven"

    Number 2's position, resolved to Number 1's day AND Number 1's first time.
    The wrong slot was then latched as confirmed.

    WHY THE EXISTING GUARDS ALL MISSED IT, each for a different reason:

      * `day_selected_by_position` resolves exactly this, correctly -- "the
        second one" folds to "2 one", matches `_POSITION_RE`, and the day-keyed
        map returns Wednesday. It has ONE caller,
        `remaining_unspoken_on_current_day`, which is the "what else have you
        got THAT DAY" follow-up. The SELECTION turn never consulted it, so the
        ordinal was resolved by the model instead of from data.
      * `reconcile_readback_time` compares the read-back against what was
        SPOKEN. The model named a time that genuinely belonged to the day it
        named, so the sentence was internally consistent and passed straight
        through. It corrects "the TIME to the DAY, never the reverse", and here
        the DAY was the wrong half.
      * The keypad was right the whole time: pressing 2 resolves through this
        same map and takes Wednesday. Speech and keypad disagreed because only
        one of them read the table.

    So this is not a new rule. It is the keypad's own resolution, made
    available to the spoken path, so that "the second one" and a pressed 2
    cannot diverge -- which is the property
    `test_the_ordinal_list_and_the_keypad_agree_position_for_position` already
    asserts of the MAP, and which was untrue of the two READERS.

    DENY BY DEFAULT. Returns None unless every one of these holds:

      * a slot map is live and has not been superseded (B-80 -- a superseded
        map resolves to a time the caller was offered EARLIER and is no longer
        being offered, which is a silent wrong-slot booking);
      * the caller framed exactly one position (`_positions_named` unions two
        foldings and two hits decline, so "one or two" is ambiguous, not 1);
      * that position is actually in the map;
      * the caller named NO day and referred to NO time themselves.

    That last guard is B-105's rule pointed the same way: an explicit naming
    beats a positional reference to the same readout. "Number 2, but Thursday
    if you have it" must reach the model whole rather than being rewritten to
    "Wednesday 2nd September" -- the caller said something this table cannot
    represent, and flattening it would lose the half that matters.

    The day guard is deliberately WIDER than `day_named_by_caller`, which
    requires a full naming ("wednesday the 2nd of september") and returns None
    for a bare weekday by design -- a partial naming is Tier 2 and out of scope
    there. That is right for B-105, which only SCOPES a follow-up query and
    leaves the caller's words intact. It is not right here, because resolving a
    position REPLACES what the caller said. A guard on a destructive rewrite
    has to fail in the safe direction, so any weekday word at all declines and
    the utterance reaches the model whole.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(session, dict):
        return None
    if session.get("v3_slot_map_superseded"):
        return None
    slot_map = session.get("v3_dtmf_slot_map") or {}
    if not isinstance(slot_map, dict) or not slot_map:
        return None

    hits = _positions_named(text)
    if len(hits) != 1:
        return None
    label = slot_map.get(str(next(iter(hits))))
    if not label or not str(label).strip():
        return None

    # An explicit naming beats a positional pick -- see the docstring.
    days = available_days if available_days is not None else (
        session.get("available_days") or []
    )
    if day_named_by_caller(days, text):
        return None
    _low = _caller_norm(text)
    if any(f" {_w} " in f" {_low} " for _w in _WEEKDAY_WORDS):
        return None
    if _TIME_REFERENCE_RE.search(text):
        return None

    return str(label).strip()


def remaining_unspoken_on_current_day(
    session: Dict[str, Any], user_text: str = ""
) -> List[Dict[str, Any]]:
    """remaining_unspoken(), scoped to the day under discussion.

    "Anything else THAT DAY?" means the day the caller is discussing. Where
    that day comes from, in order:

      1. The day the CALLER NAMED in this very utterance, when they named
         exactly one (B-103).
      1b. The day the caller picked BY POSITION -- "the second day",
         "number two" -- resolved through the same index -> label map the
         keypad uses (B-105). Below naming on purpose: an explicit date
         beats a positional reference to the same readout.
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

    Step 1 only fires on an unambiguous full naming, step 1b on an
    unambiguous positional pick; everything else falls to step 2 and behaves
    exactly as before.
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
    picked = day_selected_by_position(days, session, user_text)
    if picked:
        logger.info(
            "[slot_followup] scoping the follow-up to %s -- the caller picked "
            "it by position in the readout, and the offer on the table leads "
            "with %s (B-105)",
            picked, _lead,
        )
        return [slot for slot in remaining if _day_key(slot) == picked]
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


# A bare hour word is a TIME only when something nearby says so.
#
# B-114. "one", "two" and their siblings are pronouns and determiners at least
# as often as they are clock times, and this module resolves them straight
# into a slot the caller is then asked to confirm. Deny by default: an
# unmarked number word falls through to the model, which can re-ask. A wrong
# hit gets read back as a real appointment on a real date.
#
# Strong enough on its own to outrank a leading determiner, because "the one
# o'clock" is a time and "the one after" is not -- the trailing marker is what
# separates them.
_CLOCK_MARKERS_AFTER = (
    "o'clock", "oclock", "am", "pm", "a.m.", "p.m.",
    "in the morning", "in the afternoon", "in the evening",
    "thirty", "fifteen", "forty",
)
# Only markers that cannot mean an OPTION NUMBER. "take one" and "book two"
# are how a caller picks from a numbered readout, so take/book/make are
# deliberately absent: reading those as times is the same defect wearing a
# different hat, and the ordinal path already owns them.
_CLOCK_MARKERS_BEFORE = (
    "at", "around", "about",
    # "do you have six", "have you got six", "does six work", "is six free" --
    # asking whether a time EXISTS is the commonest way a caller reaches for an
    # unspoken slot, and it carries no other marker.
    "have", "got", "do", "does", "is", "any",
)
# ...and the same question asked the other way round.
_CLOCK_MARKERS_AFTER_LOOSE = (
    "free", "available", "work", "works", "suit", "suits", "instead", "ok",
)
# Words that turn the number back into a quantity or a pointer even when a
# leading marker is present -- "at one of them".
_NOT_A_TIME_AFTER = (
    "of", "after", "before", "coming", "more", "other", "others",
    "option", "thing", "things", "week", "weeks", "day", "days",
    "month", "months", "year", "years", "hour", "hours",
    "minute", "minutes", "each", "apiece",
)


def _bare_hour_word_is_a_clock_reference(text: str, word: str) -> bool:
    """True when `word` is used as a time somewhere in `text`.

    Every occurrence is tested, and one is enough: "not the one after -- the
    one o'clock" names a time in its second half.
    """
    t = (text or "").lower()
    for m in re.finditer(rf"\b{re.escape(word)}\b", t):
        before = t[:m.start()].split()
        after = t[m.end():].split()
        nxt = after[0] if after else ""
        tail = " ".join(after[:3])
        if any(tail.startswith(mk) or nxt == mk for mk in _CLOCK_MARKERS_AFTER):
            return True
        if nxt in _NOT_A_TIME_AFTER:
            continue
        if before and before[-1] in _CLOCK_MARKERS_BEFORE:
            return True
        if nxt in _CLOCK_MARKERS_AFTER_LOOSE:
            return True
    return False


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

    # bare hour word, but only where it is USED as a time. Uniqueness in
    # `remaining` was the old safety and it is not one: on a three-day sweep
    # exactly one slot sat at 13:00, so "the one after that, not the one
    # coming up, the one after" resolved cleanly and confidently to a Friday
    # the caller had never mentioned (B-114).
    for hour_word, h12 in _BARE_HOUR_WORDS.items():
        if not re.search(rf"\b{hour_word}\b", t):
            continue
        if not _bare_hour_word_is_a_clock_reference(t, hour_word):
            continue
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
    text: str,
    remaining: List[Dict[str, Any]],
    available_days: Any = None,
) -> Optional[Dict[str, Any]]:
    """Match a caller time phrase to exactly one remaining slot, else None.

    `remaining` spans the WHOLE sweep by design, so a caller who names a time
    on a day other than the one on the table still reaches it. `available_days`
    is what keeps that from turning into an answer about a day nobody asked
    about -- see the day guard at the bottom. It is optional only so the
    signature stays back-compatible; BOTH production call sites pass it, and
    test_b114 asserts they still do.

    B-114, CA0f8ffe7b (28 Aug 2026, theorem_v3, Alcester). The caller said

        "um no could you tell me what you have next monday
         the one after that not the one coming up the one after"

    and was answered "Yes -- one in the afternoon on Friday 4th September is
    free. Shall I book that in for you?" -- a booking prompt, on a day they had
    never mentioned, one second after they said "no". They had to say "that's
    not what i asked".

    Three of the words were "one", every one of them a pronoun. Both of the
    paths below resolved it, independently:

      the soft-core path matched "one" as a SUBSTRING, which also makes
        "none of those work", "could someone call me back" and "phone me"
        each resolve to a one o'clock slot -- and "none of those work" is the
        single most common thing a caller says to a list of times.

      the hhmm path emitted 01:00/13:00 for any bare hour word anywhere in the
        utterance. Its stated safety was uniqueness in `remaining`, which is
        not a safety at all: exactly one slot sat at 13:00, so the wrong
        answer was the confident one.
    """
    if not remaining or not (text or "").strip():
        return None
    t = text.lower()

    # Prefer full spoken-label containment (most precise)
    label_hits = [s for s in remaining if s.get("spoken") and s["spoken"].lower() in t]
    if len(label_hits) == 1:
        return _reject_if_caller_named_another_day(
            label_hits[0], available_days, text,
        )
    # partial: "half past seven" without "in the evening"
    soft_hits = []
    for s in remaining:
        spoken = (s.get("spoken") or "").lower()
        core = spoken.replace(" in the evening", "").replace(" in the afternoon", "").replace(" in the morning", "")
        if not core:
            continue
        # Word-boundary, not containment. "one" sits inside none, phone,
        # someone, anyone, money and gone; the old test matched every one of
        # them (B-114).
        if not re.search(rf"\b{re.escape(core)}\b", t):
            continue
        # A core that is a bare number word has to be USED as a time, exactly
        # as in _candidate_hhmm_from_text. Multi-word cores ("half past
        # seven") and named cores ("midday") are unambiguous and skip this.
        if core in _BARE_HOUR_WORDS and not _bare_hour_word_is_a_clock_reference(t, core):
            continue
        soft_hits.append(s)
    if len(soft_hits) == 1:
        return _reject_if_caller_named_another_day(
            soft_hits[0], available_days, text,
        )

    candidates = _candidate_hhmm_from_text(t)
    time_hits = [s for s in remaining if s.get("time") in candidates]
    if len(time_hits) == 1:
        return _reject_if_caller_named_another_day(
            time_hits[0], available_days, text,
        )
    return None


def _reject_if_caller_named_another_day(
    hit: Dict[str, Any], available_days: Any, text: str,
) -> Optional[Dict[str, Any]]:
    """Drop a resolved slot that sits on a day the caller ruled out by naming
    a different one.

    The whole-sweep scope above is deliberate and stays. This only refuses the
    case where the caller's own words identify ONE day of the payload and the
    slot is not on it -- the same signal, and the same helper, B-103 uses to
    scope a follow-up batch.

    Silent on purpose when nothing was named: `day_named_by_caller` returns
    None both for "named nothing" and for "named several", and neither is
    evidence that this slot is wrong.

    Not what saved CA0f8ffe7b -- "next monday" named no day of a payload that
    held Tuesday, Wednesday and Friday, so this returns None there and the word
    fix above is what does the work. It closes the sibling shape: an offer
    spanning three days, a caller naming one of them, and a bare time landing
    on another.
    """
    try:
        named = day_named_by_caller(available_days, text)
    except Exception:
        return hit          # never let a guard be the thing that fails a lookup
    if not named:
        return hit
    if _day_key(hit) == named:
        return hit
    logger.info(
        "[slot_followup] refusing a time on %s -- the caller named %s in the "
        "same breath (B-114)", _day_key(hit), named,
    )
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
# A claim that the listed times are the COMPLETE set.
#
# No longer read by reconcile_extra_slots_claim: it matches the plural
# list-introducing opener as well as a real completeness claim, and B-112
# records what suppressing the tail on that cost a caller. Kept as the
# definition of the family for the tests that pin the singular pattern
# against it, and because the two must not drift apart if either is widened.
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

    Both halves of the day are read, and that is the whole point:

      available_days holds the SURVIVORS of the caller's time-of-day band.
        Walking those catches the ordinary case -- times presented and not yet
        spoken.
      times_found_on_day holds what the day really has. A slot the band hid is
        never in available_days at all, so no walk over that list can see it,
        and the caller cannot possibly have heard it.

    B-112, CAf5c4febac4 (28 Aug 2026, theorem_v3, Alcester). "can you show me
    the dates on the 8th" -> the model sent date_hint "Tuesday 8 September 2026
    morning", the band kept 2 of the day's 7 slots, and both were read out as
    Number 1 and Number 2. This function walked the two survivors, found both
    spoken, and returned False. That answer overrides the tool's more_times in
    llm_stream (which _was_ correct -- times_not_shown was 5), so the tail was
    never appended, the caller said "no, none of those work", and Susie moved
    them to the following week with five bookable slots left on the day.

    That is B-97's false completeness reaching the caller through a third door,
    and this docstring's own claim to subsume every more_times producer was
    what made the override look safe. The promise is keepable: B-98 opens a
    band-spent day on the next lookup, so a caller who asks for the others is
    served them.

    Counting rather than matching, because the hidden slots are not in the
    payload -- only the count is. Heard fewer distinct times on that day than
    the day holds => something is left. That subsumes the walk above and stays
    correct when a caller heard the whole day UNBANDED on an earlier turn and a
    later banded fetch shrank it, which a bare `times_not_shown > 0` test would
    have called "more available" with nothing left to offer.
    """
    spoken = _spoken_key_set(session)
    for slot in flatten_bookable_slots(session.get("available_days") or []):
        if _day_key(slot) != day:
            continue
        if str(slot.get("start") or "")[:19] not in spoken:
            return True

    # Slots the band removed before the session ever saw them.
    heard_on_day = sum(1 for _s in spoken if str(_s)[:10] == day)
    for _d in (session.get("available_days") or []):
        if not isinstance(_d, dict) or str(_d.get("date") or "") != day:
            continue
        try:
            # Falls back to the visible count, which makes this a no-op for a
            # reader whose payload carries no such field -- the pre-B-112
            # behaviour exactly, rather than a guess about a day it cannot see.
            _found = int(
                _d.get("times_found_on_day")
                or len(_d.get("slots") or [])
            )
        except Exception:
            return False
        return heard_on_day < _found
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


# Clock-face words as SPOKEN vs as TRANSCRIBED. `_spoken_slot_time` builds
# every label in words ("twenty past twelve in the afternoon"); AssemblyAI
# hands the caller's echo of it back in numerals ("20 past 12"). Folding both
# onto digits is what lets the containment tests below compare them at all.
#
# D1, 2 Sep 2026, CA-pending (northgate). Sibling of B-91, which fixed the
# SAME defect on `_norm_offer_label` in connection.py and never reached this
# file. Copying B-91's table across would NOT have been enough: it maps the
# HOURS one..twelve only, so "twenty past twelve" still met "20 past 12" as
# "twenty past 12" and missed. The MINUTE words are the half that was absent,
# and they are the half a clock-face label leads with.
#
# Deliberately NOT folded into `_readback_norm` itself, though that is where
# it looks like it belongs. That normaliser also feeds `_caller_norm`, which
# applies `_fold_ordinals` AFTER it, and a cardinal fold running first turns
# "twenty second" into "20 2" -- the compound regex stops matching and B-104's
# date matching breaks. Measured, not feared. So the fold lives here and is
# applied at the TIME comparisons only; every date path is untouched by
# construction rather than by care.
_CLOCK_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "twenty": 20,
}
# Compounds before units, so "twenty five past" is 25 and not "20 5". Same
# ordering as _fold_ordinals, and for the same reason.
_CLOCK_COMPOUNDS = {"twenty five": 25}
_CLOCK_COMPOUND_RE = re.compile(
    r"\b(" + "|".join(sorted(_CLOCK_COMPOUNDS, key=len, reverse=True)) + r")\b"
)
_CLOCK_UNIT_RE = re.compile(
    r"\b(" + "|".join(sorted(_CLOCK_UNITS, key=len, reverse=True)) + r")\b"
)


def _fold_clock_words(text: str) -> str:
    """"twenty past twelve" and "20 past 12" both become "20 past 12".

    "quarter" and "half" are left alone on purpose -- both sides spell them
    the same way, so folding them would buy nothing and widen the surface for
    no reason.
    """
    t = _CLOCK_COMPOUND_RE.sub(lambda m: str(_CLOCK_COMPOUNDS[m.group(1)]), text)
    return _CLOCK_UNIT_RE.sub(lambda m: str(_CLOCK_UNITS[m.group(1)]), t)


def _time_named_in(phrase: str, value: Any) -> bool:
    """Does `phrase` name the time `value`? Folded, and WORD-BOUNDED.

    The boundary is not a nicety, it is the whole safety of the fold. Folding
    turns a bare hour label into one or two digits -- "nine in the morning"
    strips to "nine" and folds to "9" -- and a read-back names its date in
    digits too. Plain containment then matched "9" inside "Wednesday the 9th
    of September", so `_offered_time_named_without_its_band` reported that the
    sentence named an offered time when it named only the DAY.

    That is B-126 exactly: the guard stands down and a caller is told a time
    the diary does not hold. Caught by B-126's own tests, which is the reason
    they exist.
    """
    needle = _time_norm(value)
    if not needle:
        return False
    return re.search(r"\b" + re.escape(needle) + r"\b", phrase) is not None


def _time_norm(value: Any) -> str:
    """`_readback_norm` plus the clock-word fold. For TIME comparisons ONLY.

    Use this wherever a spoken slot LABEL meets caller or model speech. Use
    `_readback_norm` where a DAY does -- see the note above for why the two
    cannot be the same function.
    """
    return _fold_clock_words(_readback_norm(value))


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
    # Same predicate as the writer, deliberately. These two disagreed once
    # (B-102) and the defect the writer had just been taught to avoid came
    # straight back through the reader. B-115 moved the rule itself into
    # _day_record_survives so there is nothing left here to drift.
    starts_now = _day_slot_starts(session.get("available_days") or [])
    _filtered = _days_showing_a_filtered_view(session.get("available_days") or [])
    _spoken = session.get(_SPOKEN_KEY) or []
    trusted = {
        day for day in {str(s)[:10] for s in _spoken}
        if old.get(day) is not None
        and _day_record_survives(
            starts_now.get(day), _spoken_on_day(_spoken, day), day in _filtered,
        )
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


def part_of_day(start: Any) -> str:
    """morning / afternoon / evening from an ISO start.

    One definition, imported by `slot_offer` rather than copied, because two
    copies of a boundary is two answers to "is half four an afternoon slot".
    """
    try:
        hour = int(str(start)[11:13])
    except (TypeError, ValueError):
        return ""
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _spread(slots: Any, pool: List[int], limit: int) -> List[int]:
    """Choose `limit` positions from `pool`, spread across the day.

    Owner request 1 Sept 2026, from a live call: the two times offered per day
    were "eight in the morning, or ten to nine in the morning" -- fifty minutes
    apart, which is not a choice a caller experiences as two options. The
    earliest is kept, because it is what most callers want; the second is the
    LATEST in a different part of the day, so the pair spans the day.

    Only the ORDER of preference changes, never the pool. `pool` is already
    whatever B-116/B-119 decided this caller may hear, so spreading cannot
    reach a time they were meant not to be offered.

    Under a band filter this degrades correctly rather than needing a special
    case. A caller who asked for mornings has had the afternoons removed
    upstream, so nothing is in "a different part of the day" and it falls
    through to earliest-plus-latest -- eight and half eleven rather than eight
    and ten to nine, which is still the better pair.
    """
    if limit <= 0 or not pool:
        return []
    if len(pool) <= limit:
        return list(pool)

    def _start(i: int) -> str:
        try:
            return str((slots[i] or {}).get("start") or "")
        except (IndexError, TypeError, AttributeError):
            return ""

    if limit == 1:
        return [pool[0]]

    first = pool[0]
    if limit == 2:
        first_part = part_of_day(_start(first))
        other = [i for i in pool[1:] if part_of_day(_start(i)) != first_part]
        return sorted([first, other[-1] if other else pool[-1]])

    # Three or more: one per PART OF THE DAY, not evenly spaced by index.
    # Index spacing looks right and is not -- on a day running 08:00 to 17:10 it
    # picks 08:00, 11:20, 17:10, which is two mornings and an evening, because
    # the slots are not spread evenly across the parts. Taking the earliest and
    # then the LAST of each later part gives a morning, an afternoon and an
    # evening, which is what a receptionist offers.
    first_part = part_of_day(_start(first))
    last_of_part: Dict[str, int] = {}
    for i in pool[1:]:
        p = part_of_day(_start(i))
        if p and p != first_part:
            last_of_part[p] = i          # later positions overwrite earlier
    chosen = [first] + sorted(last_of_part.values())

    # Too few parts to fill `limit` -- a day inside one band, or two parts
    # against a limit of three. Top up by even spacing over what is left, which
    # is still better than filling from the front.
    if len(chosen) < limit:
        rest = [i for i in pool if i not in chosen]
        need = min(limit - len(chosen), len(rest))
        if need > 0:
            step = (len(rest) - 1) / float(need) if need > 1 else 0.0
            chosen.extend(
                rest[min(len(rest) - 1, int(round(k * step)))]
                for k in range(need)
            )
    return sorted(set(chosen))[:limit]


_LAST_POSITION_RE = re.compile(r"\b(?:last|final|latest)\b", re.IGNORECASE)
_FIRST_POSITION_RE = re.compile(r"\b(?:first|earliest|soonest)\b", re.IGNORECASE)
_BAND_WORDS = ("morning", "afternoon", "evening")

ACCEPTED_SLOT_KEY = "_accepted_slot_iso"


def slot_llm_reply_can_only_be_discarded(session: "Dict[str, Any] | None") -> bool:
    """Is the post-check_availability model call certain to be thrown away? PURE.

    `_flush_slot_buf` speaks the deterministic offer and discards the model's
    version of it, EXCEPT where one of the two stand-down guards fires. Those
    guards are the only reason the reply is ever read, and each has a
    precondition that can be checked without it:

      * P6  -- `last_offered_slots`: an offer is already standing, so the caller
        has heard options and may be answering a pick rather than being read a
        list;
      * P6b -- `ACCEPTED_SLOT_KEY`: an acceptance resolved to a slot THIS TURN,
        so a reply naming it is a confirmation, not a presentation.

    With a deterministic offer built and NEITHER precondition set, this is a
    first lookup: no options have been spoken, so there is no pick to confirm
    and the discard is guaranteed by construction. The caller then waits ~1.3s
    for a sentence nobody will ever hear (measured twice live, 2 Sep 2026), and
    the call can be skipped.

    False is the safe answer everywhere else: it keeps the call, keeps both
    guards, and keeps today's behaviour exactly.
    """
    s = session or {}
    det = s.get("_slot_offer_prebuilt")
    if not isinstance(det, dict) or not det.get("chunks"):
        return False
    if s.get("last_offered_slots"):
        return False
    if s.get(ACCEPTED_SLOT_KEY):
        return False
    return True


def _band_named(text: str) -> "str | None":
    """The ONE part-of-day `text` names, or None when it names 0 or 2+."""
    low = (text or "").lower()
    hits = {b for b in _BAND_WORDS if re.search(r"\b" + b + r"\b", low)}
    return hits.pop() if len(hits) == 1 else None


def _position_named(text: str, n: int) -> "int | None":
    """The 1-based list position `text` names, or None when it is not exactly one.

    `_positions_named` owns "number two" / "the second one" / "day 1" and is
    reused verbatim. What it does NOT own is the relative end of the list --
    "the last day", "the first one" -- because those are meaningless without
    knowing how long the list is, which is why they live here and take `n`.

    "the last day at 6 in the evening works" is the utterance that opened P6 on
    a live Vital Edge call, and "the last day in the afternoon works"
    reproduced it on the demo line the next night. Neither named a number.

    Declines on disagreement: a sentence that names both a number and an end
    ("the last one, number 2") is ambiguous and gets no answer, which is the
    standing rule in this module for two positions.
    """
    if n <= 0:
        return None
    found = {p for p in _positions_named(text) if 1 <= p <= n}
    if _LAST_POSITION_RE.search(text or ""):
        found.add(n)
    if _FIRST_POSITION_RE.search(text or ""):
        found.add(1)
    return found.pop() if len(found) == 1 else None


def _offered_day_by_weekday(offered: Any, text: str) -> "str | None":
    """The one OFFERED day whose WEEKDAY the caller named, or None.

    `day_named_by_caller` requires the payload's full day_label ("Saturday 5th
    September") to appear in the speech, and records a bare weekday as a
    PARTIAL naming that matches nothing -- Tier 2, deliberately out of scope
    there because resolving "that wednesday" against the calendar is date
    parsing and needs its own corpus.

    This is not that. The question here is narrower and closed: of the two or
    three days ALREADY READ OUT to this caller, does the weekday they said pick
    exactly one? No calendar, no parsing, no ambiguity about which week -- the
    candidates are the offer itself.

    Vital Edge, 2026-09-02. Susie read out Saturday, and the caller said "the
    saturday at 6 in the evening works". Nothing resolved it:
    `utterance_is_slot_selection` is containment against the full spoken label
    and the caller had dropped the date; `day_named_by_caller` saw a partial.
    So the pick was read as a fresh time-of-day filter, check_availability ran
    a second time and was refused, and the model -- with no tool result and no
    scripted next step -- improvised the rest of the turn.

    DENY BY DEFAULT, like every other step of the resolver:
      * exactly ONE weekday word in the speech (two is a comparison, not a
        pick -- "is it saturday or monday?");
      * exactly ONE offered day falling on it. A fortnight's offer containing
        two Saturdays declines rather than guessing the nearer.
    """
    try:
        _words = [w for w in _WEEKDAY_WORDS
                  if f" {w} " in f" {_caller_norm(text)} "]
        if len(_words) != 1:
            return None
        _dates = sorted({
            str((o or {}).get("start") or "")[:10]
            for o in (offered or [])
            if isinstance(o, dict)
        } - {""})
        _hits = [
            d for d in _dates
            if _date.fromisoformat(d).strftime("%A").lower() == _words[0]
        ]
        return _hits[0] if len(_hits) == 1 else None
    except Exception:
        # A caller mid-booking must never lose their turn to a resolver.
        return None


def slot_accepted_by_caller(
    session: Dict[str, Any], text: str
) -> "str | None":
    """The ISO start of the slot the caller just ACCEPTED, or None. PURE.

    Step 1 of P6/P6b. Susie reads a numbered offer, the caller picks in words,
    and until now nothing on the main path resolved that pick:
    `utterance_is_slot_selection` is containment against the spoken labels, so
    an ordinal matches nothing, and `day_selected_by_position` -- which does
    understand ordinals -- is only wired into the FOLLOW-UP path.

    The model then re-reads the caller's words as a fresh filter and calls
    `check_availability` again, and `choose_presented_indices` withholds the
    accepted slot from the new readout BECAUSE it was just heard (B-116). Two
    live calls ended that way, 21:46 and 00:03 on 1-2 Sep, both abandoned.

    DENY BY DEFAULT, and every step here can decline:

      1. it must not be a "more times" or "different day" request -- those have
         their own paths and reading one as a pick would set a filter that
         deletes slots (B-90);
      2. exactly one DAY, by list position (including "the last") or by name;
      3. exactly one TIME on that day, and only among times the caller was
         actually READ -- an unspoken slot cannot have been accepted.

    Returning None is cheap: the caller is no worse off than before this
    existed. Returning the WRONG slot would pin it into the next readout and
    read it back as an appointment, so ambiguity always declines.
    """
    if not isinstance(session, dict) or not isinstance(text, str) or not text.strip():
        return None
    if utterance_requests_more_slots(text) or utterance_requests_different_day(text):
        return None

    offered = session.get("last_offered_slots")
    if not isinstance(offered, list) or not offered:
        return None

    # -- 2. which day -----------------------------------------------------
    date = None
    pos = _position_named(text, len(offered))
    if pos is not None:
        date = str((offered[pos - 1] or {}).get("start") or "")[:10] or None
    if not date:
        named = day_named_by_caller(session.get("available_days"), text)
        if isinstance(named, dict):
            date = named.get("date")
        elif isinstance(named, str):
            date = named
    if not date:
        # Last resort, and the narrowest of the three: a bare weekday that
        # picks exactly one day out of the offer just read. See
        # _offered_day_by_weekday -- it resolves against the offer, never
        # against the calendar, so it is not the date parsing Tier 2 needs.
        date = _offered_day_by_weekday(offered, text)
    if not date:
        return None

    # -- 3. which time, among what was SPOKEN -----------------------------
    try:
        spoken = spoken_starts_for_offer(session)
    except Exception:
        return None
    flat = flatten_bookable_slots(session.get("available_days"))
    heard = [
        s for s in flat
        if s.get("date") == date
        and str(s.get("start") or "")[:19] in spoken
    ]
    if not heard:
        return None
    if len(heard) == 1:
        # One time on the day is normally the whole answer -- but not when
        # the caller named a band it contradicts. "The last day at 6 in the
        # evening" against a day holding only 09:10 is not an acceptance of
        # 09:10; it is a caller whose day and time disagree, and the
        # docstring's contract is that ambiguity declines. Pinning it would
        # put a slot the caller never chose into the next readout and read
        # it back as their appointment -- the one outcome this resolver is
        # forbidden from producing.
        _band = _band_named(text)
        if _band and part_of_day(heard[0].get("start")) != _band:
            return None
        return heard[0].get("start") or None

    phrase = _time_norm(text)
    hits = []
    for s in heard:
        label = str(s.get("spoken") or "").strip()
        if not label:
            continue
        bare = _strip_part_of_day(label)
        if _time_named_in(phrase, label) or (
            bare and bare != label and _time_named_in(phrase, bare)
        ):
            hits.append(s)
    if len(hits) == 1:
        return hits[0].get("start") or None

    band = _band_named(text)
    if band:
        in_band = [s for s in heard if part_of_day(s.get("start")) == band]
        if len(in_band) == 1:
            return in_band[0].get("start") or None
    return None


def accepted_slot_is_named_in(session: Dict[str, Any], text: str) -> bool:
    """Does `text` name the slot the caller just accepted? PURE.

    The second half of P6b. The pin makes the accepted slot survive a re-read;
    this stops the re-read happening at all when the model has already said the
    right thing.

    On CA5a126fe4e6addcf812836220cdf7ea44 the model recovered correctly after
    its own re-query -- it wrote "Wednesday 9th September -- Number 1, twenty
    past four in the afternoon", naming the accepted slot -- and the payload
    offer replaced it with three earlier times. The P6 stand-down could not
    help: it declines whenever the model numbers an option, and this text
    numbered one.

    Asking "does the model name the accepted slot?" separates the two cases
    that matter without reading the wording: a model CONFIRMING the pick names
    it, a model presenting a fresh list of alternatives does not. Both halves
    of the comparison come from the payload -- the accepted ISO and the spoken
    label the offer was read with -- so this is not a match against a phrase
    anyone wrote by hand.

    False here is the safe answer: the payload offer wins, exactly as today.
    """
    iso = str((session or {}).get(ACCEPTED_SLOT_KEY) or "")[:19]
    if not iso or not isinstance(text, str) or not text.strip():
        return False
    label = next(
        (
            str(sl.get("spoken") or "").strip()
            for sl in flatten_bookable_slots((session or {}).get("available_days"))
            if str(sl.get("start") or "")[:19] == iso
        ),
        "",
    )
    if not label:
        return False
    phrase = _time_norm(text)
    if _time_named_in(phrase, label):
        return True
    bare = _strip_part_of_day(label)
    return bool(bare and bare != label and _time_named_in(phrase, bare))


def _pin_accepted_index(
    session: Dict[str, Any], day: Dict[str, Any], chosen: List[int], limit: int
) -> List[int]:
    """Force the slot the caller ACCEPTED back into a readout that dropped it.

    P6b, CA5a126fe4e6addcf812836220cdf7ea44 (2 Sep 2026, northgate) and P6,
    CA82b240ccad48ed219371c3f2fddfffb8 (1 Sep, vital_edge). Both callers
    accepted a slot, the model re-queried, and the fresh readout did not
    contain the slot they had just agreed to. Northgate's payload HELD 16:20 --
    "twenty past four in the afternoon", the accepted time -- and the readout
    withheld it.

    Nothing was broken when it did that. `choose_presented_indices` prefers
    times this caller has not heard (B-116), the accepted slot had been heard
    21 seconds earlier, and so the one time that must survive is the one time
    guaranteed not to. The rule was written for "what else have you got?",
    where withholding is right; it has no notion of "this one was just
    accepted", and that is the gap this closes.

    Deliberately a WRAPPER, and the B-116 body below is untouched. That
    function is the single owner of "how many, and which" for every readout on
    every clinic, with four readers on it -- widening its selection rule in
    place is how you get a defect in a readout nobody was looking at.

    The accepted slot displaces the LAST of the chosen, never adds to them, so
    `limit` still means what `_cap_presented_slots` says it means. Chronological
    order is preserved, because the keypad map is built from this order and a
    caller pressing 2 means the second thing they heard.
    """
    iso = str(session.get(ACCEPTED_SLOT_KEY) or "")[:19]
    if not iso or limit < 1:
        return chosen
    slots = day.get("slots") if isinstance(day, dict) else None
    if not isinstance(slots, list):
        return chosen
    idx = next(
        (i for i, sl in enumerate(slots)
         if str((sl or {}).get("start") or "")[:19] == iso),
        None,
    )
    if idx is None or idx in chosen:
        return chosen          # not this day, or already being spoken
    keep = [i for i in chosen if i != idx][: max(0, limit - 1)]
    out = sorted(set(keep + [idx]))
    logger.info(
        "[slot_followup] pinned the accepted slot back into the readout -- "
        "%s was heard, so B-116 had dropped it (P6b). %r -> %r",
        iso, chosen, out,
    )
    return out


def choose_presented_indices(
    session: Dict[str, Any], day: Dict[str, Any], limit: int
) -> List[int]:
    """Which positions in a day's parallel slot arrays should be SPOKEN.

    Wrapper: picks by the B-116 rule below, then pins the slot the caller has
    just accepted back in if that rule dropped it. See `_pin_accepted_index`.

    Returns CHRONOLOGICAL indices, at most `limit`, preferring times this
    caller has not already heard.

    B-116, CA13b8dc5cb8 (28 Aug 2026, theorem_v3, Alcester). B-98 opened a
    band-spent day from 2 slots to its full 7, exactly as designed:

        band 'morning tuesday 8 september 2026' is SPENT on ['2026-09-08']
        slot_times 09:00 10:00 12:00 13:00 14:00 15:00 16:00

    The readout then took the chronologically first three - 09:00, 10:00,
    12:00 - and two of those were the two the caller had just been read. She
    had asked "do you have anything else that day then" and was given
    two-thirds old news. The retrieval was right and the readout threw it away.

    ONE owner, because there were already two answers to "what else is there"
    in this codebase and they disagreed on that call: this module's unspoken
    follow-up subtracted what was spoken and got it right thirty seconds later,
    while both presentation caps sliced [:limit] blind. A caller got a correct
    or a repeating answer depending only on which route their wording took.

    NEVER STARVES A REPEAT. When every time on the day has been heard the
    unheard list is empty and this returns the first `limit` chronologically -
    byte-identical to the old behaviour.

    It DOES withhold while anything is still unheard (B-119). Padding a
    short unheard list back up to `limit` with times already read out is the
    very defect B-116 exists to prevent, arriving one turn later and
    contradicting B-117's spent-band sentence out loud. Returning FEWER than
    `limit` is the correct answer to "what else have you got".

    Falls back to chronological whenever it cannot prove the arrays are
    parallel. Speaking `slot_times_spoken[i]` against `slots[j]` would name a
    time the caller cannot book, so a desynchronised day is not worth a
    cleverer readout.
    """
    return _pin_accepted_index(
        session, day, _choose_presented_indices_b116(session, day, limit), limit
    )


def _choose_presented_indices_b116(
    session: Dict[str, Any], day: Dict[str, Any], limit: int
) -> List[int]:
    """The B-116 selection, exactly as it was. Do not add rules here."""
    slots = day.get("slots") if isinstance(day, dict) else None
    n = len(slots) if isinstance(slots, list) else 0
    if n == 0 or limit <= 0:
        return list(range(max(0, min(limit, n))))

    # The three arrays are built from one list and documented as aligned 1:1.
    # If that ever stops being true, say the safe thing.
    for key in ("slot_times", "slot_times_spoken"):
        value = day.get(key)
        if isinstance(value, list) and len(value) != n:
            logger.warning(
                "[slot_followup] %s has %d entries against %d slots -- falling "
                "back to a chronological readout (B-116).", key, len(value), n,
            )
            return list(range(min(limit, n)))

    if n <= limit:
        return list(range(n))

    try:
        spoken = spoken_starts_for_offer(session)
    except Exception:      # never let a readout fail on its own preference
        spoken = set()
    if not spoken:
        # The commonest case by far -- the first lookup of a call. This is
        # exactly where "eight, ten to nine" came from: the first `limit`
        # positions chronologically.
        return _spread(slots, list(range(n)), limit)

    unheard = [
        i for i, s in enumerate(slots)
        if str((s or {}).get("start") or "")[:19] not in spoken
    ]
    if unheard:
        # Fewer than `limit` unheard is NOT a reason to pad with heard
        # ones. B-119, CA9bafe3615359 (28 Aug 2026, theorem_v3, Alcester,
        # build e430d7ec -- B-116/117/118 all live). Two unheard slots
        # remained on the day (15:00, 16:00) against a limit of 3, so the
        # back-fill this replaces reached back for index 0 and `sorted`
        # led the readout with it:
        #
        #     13:57:23  "I've given you all the mornings I have that day"
        #     13:57:46  "Number 1, nine in the morning."
        #
        # Twenty-three seconds apart, in one caller's ear. They repeated
        # themselves and hung up without booking (judge score 1). Speaking
        # two times is a smaller failure than speaking three where one
        # contradicts the sentence before it.
        return _spread(slots, unheard, limit)
    # Every time on the day has been heard, so this IS a repeat request.
    # Answer it chronologically -- byte-identical to the old behaviour.
    return _spread(slots, list(range(n)), limit)


def choose_presented_days(
    session: Dict[str, Any], days: Any, max_days: int
) -> List[Dict[str, Any]]:
    """Which DAYS to speak, preferring days this caller has not been offered.

    The day-level twin of `choose_presented_indices`, and it exists because the
    cap above it was still doing what B-116 removed one level down:
    `days[:max_days]` -- the first three, blind.

    Owner decision, 2026-09-02, from the demo call at 09:15. Susie offered
    Monday, Tuesday and Wednesday; the caller asked "what else have you got";
    and the honest answer to that question after a three-day readout is THREE
    MORE DAYS, not a second helping of Monday. Re-slicing `[:3]` would have
    read Monday, Tuesday and Wednesday straight back at him with different
    times on them.

    Same three rules as its twin, for the same reasons:

      * NEVER STARVES A REPEAT. When every day in the sweep has been offered
        the unoffered list is empty and this returns the first `max_days`
        chronologically -- byte-identical to the slice it replaces.
      * IT WITHHOLDS WHILE ANYTHING IS UNOFFERED (B-119 at day level).
        Two fresh days is the right answer to "what else"; padding back to
        three with a day he has already heard is the defect this prevents,
        arriving one turn later.
      * CHRONOLOGICAL ORDER SURVIVES. The keypad map is built from this order
        and a caller pressing 2 means the second day they heard.

    A day counts as OFFERED once any of its times has been spoken. That is
    deliberately generous: a day he heard one time from is a day he has been
    told about, and leading with it again is what makes Susie sound like she
    is going in circles.
    """
    if not isinstance(days, list) or not days or max_days <= 0:
        return list(days or [])[:max(0, max_days)]
    if len(days) <= max_days:
        return list(days)
    try:
        spoken = spoken_starts_for_offer(session)
    except Exception:          # a readout preference must never fail a lookup
        return list(days)[:max_days]
    if not spoken:
        return list(days)[:max_days]      # first lookup of the call

    def _heard(day: Any) -> bool:
        if not isinstance(day, dict):
            return False
        for slot in (day.get("slots") or []):
            if str((slot or {}).get("start") or "")[:19] in spoken:
                return True
        return False

    unoffered = [d for d in days if not _heard(d)]
    if unoffered:
        logger.info(
            "[slot_followup] %d of %d days already offered -- leading with the "
            "%d the caller has not heard",
            len(days) - len(unoffered), len(days),
            min(len(unoffered), max_days),
        )
        return unoffered[:max_days]
    return list(days)[:max_days]


def pick_by_index(value: Any, indices: List[int]) -> Any:
    """Select `indices` from a parallel slot array, leaving non-lists alone."""
    if not isinstance(value, list):
        return value
    return [value[i] for i in indices if 0 <= i < len(value)]


_BAND_SPENT_SENTENCE = "I've given you all the {label} I have that day, I'm afraid."


def acknowledge_spent_band(text: str, label: str) -> Tuple[str, str]:
    """Say WHY the times that follow are outside the band the caller asked for.

    Returns `(text, action)`; action is "unchanged" or "prepended".

    B-117, the wording half of B-116. Once B-98 opens a band the caller has
    used up, the readout leads with times outside it -- afternoons to someone
    who said "morning". That is the only new true thing left to say about the
    day, and B-116 makes it what she says. Unexplained it still sounds like she
    ignored the question; the caller on CA13b8dc5cb8 asked for the mornings a
    third time and hung up.

    So this is a SENTENCE change, not a selection change. It must never alter
    which times are offered, and there is a test that fails if it does.

    The claim is decided by the retrieval path and carried here on the payload
    (`band_spent_label`), never re-derived from the text. "You have heard all
    the mornings" is a statement about this caller's history, and the only code
    that knows it is the code that opened the band.

    Idempotent: a re-flush of the same buffer must not stack the apology.
    """
    _t = (text or "").strip()
    _l = (label or "").strip()
    if not _t or not _l:
        return text, "unchanged"
    _sentence = _BAND_SPENT_SENTENCE.format(label=_l)
    if _sentence.lower() in _t.lower():
        return text, "unchanged"
    return f"{_sentence} {_t}", "prepended"


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
    # The DAY tests below compare against `phrase`, the TIME tests against
    # `tphrase`. Holding them apart is what keeps the clock-word fold off a
    # date -- see the _fold_clock_words note.
    tphrase = _time_norm(text)

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
    if any(_time_named_in(tphrase, t) for t in offered):
        return text, "unchanged", ""      # names a time really offered that day
    if _offered_time_named_without_its_band(tphrase, offered):
        return text, "unchanged", ""      # same time, said without "in the ..."
    if not _TIME_REFERENCE_RE.search(text):
        return text, "unchanged", ""      # names the day but no time at all

    # B-126. The record for this day is a positional projection of a multi_day
    # offer, so it cannot say how many times the caller heard on it. Reporting
    # is still right -- a read-back naming an unrecorded time is worth having
    # in the call record -- but rewriting it is not. On CA44f1bdbe a correct
    # "six in the evening" was overwritten with the projected "nine in the
    # morning" three times, including in the closing, while Acuity held 18:00.
    if date in set(session.get(LOSSY_SPOKEN_DAYS_KEY) or ()):
        _dl = next(
            (_s["day_label"] for _s in flat
             if _s.get("date") == date and _s.get("day_label")),
            date,
        )
        return (
            text,
            "mismatch",
            f"read-back names a time not in the record for {_dl}, and that "
            f"record is a multi_day projection -- it holds one time per day, "
            f"so it cannot say what was spoken. Left as written (B-126).",
        )

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
        if _time_named_in(tphrase, label):
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


_PART_OF_DAY_TAIL_RE = re.compile(
    r"\s+in\s+the\s+(?:morning|afternoon|evening)\s*$", re.IGNORECASE
)


def _strip_part_of_day(label: str) -> str:
    """"half past three in the afternoon" -> "half past three". Tail only."""
    return _PART_OF_DAY_TAIL_RE.sub("", str(label or "")).strip()


def _offered_time_named_without_its_band(phrase: str, offered: List[str]) -> bool:
    """True when the read-back names an offered time but drops "in the ...".

    P7, CAabe1acabf5eddee255fa53e681773034 (1 Sep 2026, northgate). Friday was
    offered at eight in the morning and half past three in the afternoon. Susie
    read back

        "So that's Friday the 4th of September at half past three -- could I
         take your first name and surname?"

    which is correct, and the whole-label containment above called it a
    mismatch, because the label is "half past three in the afternoon" and the
    sentence stops at "three". Once the DAY has been named the part-of-day adds
    nothing, so dropping it is the natural way to say it -- and it made the
    B-95 net cry wolf on a good call.

    UNIQUENESS IS THE WHOLE SAFETY ARGUMENT, and it is why this is not simply a
    looser match. "half past three" is ambiguous between 03:30 and 15:30 in
    general; it is unambiguous only when the day offers exactly one of them.
    Where a day offers both, the stripped forms collide, this returns False and
    the sentence stays a mismatch -- which is the right answer, because nobody
    can tell which one she meant either.

    Both sides are stripped: an offer of "half past three in the afternoon" and
    "half past three in the morning" must collide, and they only do so after
    the tails come off.
    """
    stripped = [_strip_part_of_day(t) for t in offered]
    for i, bare in enumerate(stripped):
        if not bare or bare == str(offered[i] or "").strip():
            continue                      # no tail to drop -- nothing new to try
        if stripped.count(bare) != 1:
            continue                      # two times share it: genuinely ambiguous
        if _time_named_in(phrase, bare):
            return True
    return False


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


def append_other_dates_offer(text: str, other_dates) -> Tuple[str, str]:
    """Name the further dates matching the caller's weekday, deterministically.

    B-111. B-109/B-110 put `other_dates_for_requested_day` in the payload and
    wrote guidance telling the model to name those dates. On CA811ddccb03 the
    payload carried Tuesday 8th, 15th and 22nd September with 7, 8 and 9 times,
    and the caller heard "The available slot for Tuesday 1st September is nine
    in the morning." The formatter never used it, and could not have: its
    system prompt enumerates its inputs, never mentions this field, and for
    single_day says to use ONLY first_day.

    The obvious fix is to teach the formatter the field. That is the wrong
    shape here, and the repo already learned why: 8de7e7d0 REMOVED the "a few
    others that day" example from that prompt because the model copied it onto
    a day that had no further times and invented availability. The prompt now
    says outright that the model must never mention further availability and
    that "the system adds that sentence itself". So this sentence is built
    here, from the tool result, in the same place and the same way as the
    more_times tail.

    Returns `(text, action)` with action "appended" or "unchanged".

    No times are ever spoken for these dates: the payload deliberately carries
    none (naming a time for a date nobody heard is the B-108b defect). Only
    dates that reached the payload are named, so this cannot invent one.
    """
    if not text or not isinstance(other_dates, list) or not other_dates:
        return text, "unchanged"

    spoken = [
        str(d.get("spoken") or "").strip()
        for d in other_dates
        if isinstance(d, dict) and str(d.get("spoken") or "").strip()
    ]
    if not spoken:
        return text, "unchanged"

    # Already named by the reply itself: say nothing twice.
    _low = text.lower()
    if any(s.lower() in _low for s in spoken):
        return text, "unchanged"

    # "Tuesday 8th September" -> weekday "Tuesday", day "8th". When every date
    # shares the weekday (they always do -- the payload filters to the
    # requested one) the natural sentence names it once.
    parts = [s.split() for s in spoken]
    weekdays = {p[0] for p in parts if len(p) >= 2}
    if len(weekdays) == 1 and all(len(p) >= 2 for p in parts):
        weekday = parts[0][0]
        days = [f"the {p[1]}" for p in parts]
        if len(days) == 1:
            body = f"another {weekday}, {days[0]}, if that would suit"
        else:
            joined = ", ".join(days[:-1]) + f" and {days[-1]}"
            suit = "either" if len(days) == 2 else "any of those"
            body = f"other {weekday}s, {joined}, if {suit} would suit"
    else:
        joined = (", ".join(spoken[:-1]) + f" and {spoken[-1]}") if len(spoken) > 1 else spoken[0]
        suit = "that" if len(spoken) == 1 else "any of those"
        body = f"times on {joined}, if {suit} would suit"

    # No em dash and no ellipsis: TTS pause punctuation is chunker input and
    # would split this across two synthesis calls.
    tail = f" I've also got {body}."
    return text.rstrip() + tail, "appended"


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
    if _SINGULAR_COMPLETENESS_RE.search(text):
        # A completeness claim the rewrite above could not reach. Staying quiet
        # is still right: appending "and I've a few others" next to a sentence
        # that has just said the day holds exactly one would make Susie
        # contradict herself in one breath.
        #
        # This used to test _COMPLETENESS_RE, which also matches the plural
        # list-introducing opener -- "The available slots for Wednesday are —
        # Number 1 ...". That is the sentence B-112 died on. It introduces a
        # list; it does not claim the list is the whole day, and _EXTRA_QUANTITY
        # _RE's own comment calls it a legitimate opener. Suppressing on it
        # meant every NUMBERED readout of a band-filtered day went out with no
        # tail -- which is the same false completeness the plural opener was
        # being credited with avoiding, arriving as silence instead.
        #
        # "The available slots for Tuesday 8th September are — Number 1, nine
        # in the morning. Number 2, ten in the morning. And I've a few others
        # that day if neither suits. Any of those work?" is what a receptionist
        # says, and on the 28 Aug call it was also the truth: the day held five
        # more.
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


#: Session key: the offer the exhaustion sentence has already been said about.
EXHAUSTION_SAID_KEY = "_exhaustion_sentence_said_for"


def exhaustion_offer_signature(session: Dict[str, Any]) -> str:
    """What "that day" and "those times" refer to right now. PURE.

    The DATE alone is not enough. A caller who is told a day is finished, then
    gets a real lookup that puts new times on that same date, is entitled to
    hear the sentence again if the new offer is also complete -- that is a new
    fact, not a repeat. The offer's own slots are what changed, so they are
    what the signature is built from.

    Returns "" when the session cannot answer, which reads as "no previous
    claim" and therefore never suppresses.
    """
    try:
        offered = session.get("last_offered_slots") or []
        if not isinstance(offered, list) or not offered:
            return ""
        starts = sorted(
            str((o or {}).get("start") or "") for o in offered if isinstance(o, dict)
        )
        starts = [s for s in starts if s]
        if not starts:
            return ""
        return "|".join(starts)
    except Exception:
        return ""


def exhaustion_sentence_already_said(session: Dict[str, Any]) -> bool:
    """Has Susie already made this exact completeness claim about this offer?

    Fails OPEN -- an unreadable session reports False and the sentence is
    spoken. That is the right direction: the sentence is TRUE (it has already
    passed exhaustion_claim_is_supported), so the cost of a wrong False is one
    repetition, while a wrong True would swallow a correct answer the first
    time the caller asks.
    """
    sig = exhaustion_offer_signature(session)
    if not sig:
        return False
    return session.get(EXHAUSTION_SAID_KEY) == sig


def note_exhaustion_sentence_said(session: Dict[str, Any]) -> None:
    """Record that it has been said, against the offer it was said about."""
    sig = exhaustion_offer_signature(session)
    if sig:
        session[EXHAUSTION_SAID_KEY] = sig


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


def more_days_speech(session: Dict[str, Any]) -> Optional[str]:
    """Answer "what else have you got" with DAYS he has not heard. Or None.

    The second producer, and it is a producer rather than a decline because of
    what happened when it was one. CA3184d8e3c2, 2026-09-02 09:43: this branch
    stood aside so a real lookup could answer, the model answered from its own
    context WITHOUT calling the tool, and nothing wrote the record. The keypad
    still said Monday/Tuesday/Wednesday while Susie had just offered Thursday;
    the caller said "the last day in the morning works"; the resolver read the
    stale record and pinned Wednesday; the model confirmed Saturday. Three days
    and no two agreeing.

    So this speaks AND records, through the same two functions the primary
    readout uses -- `build_slot_offer` for the words, `apply_offer_to_session`
    for every record of them. There is no third way to put an offer on the
    table, which is the point.

    Owner decision, 2026-09-02: after a multi-day readout, "what else have you
    got" means MORE DAYS. Answered from the cached payload, so it costs no tool
    call -- the latency this path exists to protect is kept, and correctness no
    longer depends on the model choosing to look something up.

    Returns None -- and the caller falls through unchanged -- whenever it
    cannot do this honestly: not a multi_day offer, no payload, or every day in
    the sweep already offered. That last one is the real end of the week, and
    the existing exhaustion sentence is the right answer to it, not a repeat.
    """
    if str(session.get("_slot_presentation_mode") or "") != "multi_day":
        return None
    days = session.get("available_days")
    if not isinstance(days, list) or not days:
        return None
    try:
        # Deferred: slot_offer imports this module, and receptionist_tools
        # imports it too -- both edges exist only inside functions.
        from app.tools.slot_offer import (
            apply_offer_to_session, build_slot_offer, offer_as_record,
        )
        from app.tools.receptionist_tools import (
            _MAX_PRESENTED_DAYS, _MAX_PRESENTED_TIMES_MULTI_DAY,
        )
    except Exception:
        logger.exception("[slot_followup] more-days offer unavailable")
        return None

    fresh = choose_presented_days(session, days, _MAX_PRESENTED_DAYS)
    # `choose_presented_days` never starves a repeat -- when every day has been
    # heard it returns the first three again, which here would re-read days he
    # has already had. Only genuinely unheard days may be spoken as "what else".
    try:
        spoken = spoken_starts_for_offer(session)
    except Exception:
        return None
    def _heard(day: Any) -> bool:
        return any(
            str((sl or {}).get("start") or "")[:19] in spoken
            for sl in ((day or {}).get("slots") or [])
        )
    fresh = [d for d in fresh if not _heard(d)]
    if not fresh:
        logger.info(
            "[slot_followup] 'what else' after a multi-day readout, but every "
            "day in the sweep has been offered -- falling through"
        )
        return None

    presented: List[Dict[str, Any]] = []
    for day in fresh:
        trimmed = dict(day)
        idx = choose_presented_indices(session, trimmed, _MAX_PRESENTED_TIMES_MULTI_DAY)
        for key in ("slot_times", "slot_times_spoken", "slots"):
            if isinstance(trimmed.get(key), list):
                trimmed[key] = pick_by_index(trimmed[key], idx)
        presented.append(trimmed)

    try:
        offer = build_slot_offer(presented)
    except Exception:
        logger.exception(
            "[slot_followup] more-days offer failed to build -- falling through"
        )
        return None
    if not offer.chunks:
        return None

    # THE ANCHOR KEEPS ITS MEANING. `v3_last_offered_day_iso` is the PAYLOAD's
    # first day to four readers, and the payload has not changed here -- this
    # is a second readout of the same sweep -- so it is passed through
    # unchanged rather than repointed at the days now being spoken.
    _anchor = days[0].get("date") if isinstance(days[0], dict) else None
    apply_offer_to_session(
        session, offer_as_record(offer, day_iso=_anchor), offer.chunks
    )
    logger.info(
        "[slot_followup] 'what else' answered with %d day(s) he has not heard: "
        "%s", len(presented), [d.get("date") for d in presented],
    )
    return offer.text


def numbered_more_times_speech(
    session: Dict[str, Any], batch: List[Dict[str, Any]], more: bool
) -> Optional[str]:
    """Speak "the others on that day" as a NUMBERED offer, and record it.

    P9, CA665dc0309da186874a37f30034196e33 (2 Sep 2026, northgate). Susie
    offered three numbered times on Tuesday, promised "a few others that day",
    the caller asked for them, and this path read EIGHT more times in one
    306-character breath with no numbers — then updated `last_offered_slots` to
    those eight while leaving `v3_dtmf_slot_map` pointing at the original three.

        "the second one"  ->  10:30, half past ten     (correct)
        pressing 2        ->  16:20, twenty past four  (a slot he never heard)

    One utterance, two appointments, decided by whether the caller spoke or
    pressed — and the wrong one is a genuinely free slot, so it books silently.

    OWNER DECISION, 2026-09-02, and it SUPERSEDES the rule in
    `all_remaining_on_next_day`: three numbered, then "a few more after those".
    That rule (24 Aug) said an explicit "tell me the others" gets ALL of them
    and never a slot silently withheld, and it was written against a two-at-a-
    time batch that made the caller ask three times to walk one Tuesday. Eight
    in one breath overshot it. Three plus a tail is what the PRIMARY readout
    already does with the same problem, and a caller who wants the rest asks
    again — now from a list they can actually press.

    Built and recorded through the same two functions as every other offer, so
    the speech and the keypad cannot disagree again.
    """
    if not batch:
        return None
    try:
        from app.tools.slot_offer import (
            apply_offer_to_session, build_slot_offer, offer_as_record,
        )
    except Exception:
        logger.exception("[slot_followup] numbered follow-up unavailable")
        return None

    first = batch[0] or {}
    date = first.get("date") or str(first.get("start") or "")[:10]
    day = {
        "date": date,
        "day_label": first.get("day_label") or "that day",
        "slot_times": [str(s.get("start") or "")[11:16] for s in batch],
        "slot_times_spoken": [s.get("spoken") or "" for s in batch],
        "slots": [
            {"start": s.get("start"), "end": s.get("end") or ""} for s in batch
        ],
        # Carried, not zeroed: a band filter may have hidden times on this day
        # that no walk over `batch` can see, and B-97 counts those as "more".
        "times_not_shown": int(first.get("times_not_shown") or 0),
    }
    try:
        # `more_times=None` lets it decide from the data -- the batch is NOT
        # pre-trimmed, so its own count is the honest one. Forced True only
        # when the nine-slot keypad ceiling already hid some.
        offer = build_slot_offer(
            [day], lead_in="also", more_times=True if more else None
        )
    except Exception:
        logger.exception(
            "[slot_followup] numbered follow-up failed to build -- falling "
            "back to the unnumbered sentence"
        )
        return None
    if offer is None or not offer.chunks:
        return None

    apply_offer_to_session(session, offer_as_record(offer, day_iso=date), offer.chunks)
    logger.info(
        "[slot_followup] 'more times that day' answered with %d numbered "
        "option(s) of %d remaining on %s (more=%s)",
        len(offer.slots), len(batch), date, bool(offer.more_times),
    )
    return offer.text


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
            if exhaustion_sentence_already_said(session):
                # Said once about this same offer already. The caller has asked
                # again, so repeating it word for word answers nothing -- fall
                # through and let a real lookup happen instead.
                logger.info(
                    "[slot_followup] the exhaustion sentence has already been "
                    "said about this offer -- not repeating it verbatim; "
                    "falling through to a real lookup"
                )
                return None
            note_exhaustion_sentence_said(session)
            return format_next_batch_speech([], False)
        return None

    # Specific unspoken time first (V5).
    # `days` too: the guard needs the payload's labels to tell whether the
    # caller named a day (B-114). Both call sites pass it -- see test_b114.
    hit = resolve_requested_time(user_text, remaining, days)
    if hit is not None:
        return apply_resolved_time_to_session(session, hit)

    if utterance_requests_more_slots(user_text):
        # "What else" after a MULTI-DAY readout means more DAYS, and it is
        # answered here rather than handed to the model -- see more_days_speech
        # for the call that proved why. Only the unscoped case: a caller who
        # NAMES a day, or picks one by position, still gets that day's remaining
        # times below, which is B-103 and B-105 unchanged.
        _payload_days = session.get("available_days") or []
        if not day_named_by_caller(_payload_days, user_text) and not                 day_selected_by_position(_payload_days, session, user_text):
            _more_days = more_days_speech(session)
            if _more_days:
                return _more_days

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
        # P9: numbered and RECORDED, three at a time. The unnumbered sentence
        # below stays as the fallback -- it is what ships if the builder
        # cannot make an offer out of this batch.
        _numbered = numbered_more_times_speech(session, batch, more)
        if _numbered:
            return _numbered
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
            if exhaustion_sentence_already_said(session):
                logger.info(
                    "[slot_followup] the exhaustion sentence has already been "
                    "said about this offer -- not repeating it verbatim; "
                    "falling through to a real lookup"
                )
                return None
            note_exhaustion_sentence_said(session)
            return format_next_batch_speech([], False)
        return apply_next_batch_to_session(session, batch, more)

    return None
