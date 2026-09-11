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


def _rec_offer(session: Any, **kwargs: Any) -> None:
    """`app.obs.slot_offers.record_offer`, imported at call time. NEVER RAISES.

    S-14. Lazy because `app.obs` pulls the store in and this module is imported
    by the pure-predicate replay harnesses, which must keep running without a
    database. One definition rather than three inline imports, so the three
    producers below cannot drift into three ways of failing to record.

    `record_offer` already swallows everything itself; this second guard is for
    the import, which is the part it cannot defend. An observability row must
    never be able to cost a caller their booking.
    """
    try:
        from app.obs.slot_offers import record_offer
        record_offer(session, **kwargs)
    except Exception:  # pragma: no cover - defensive; live call path
        logger.warning("[slot_followup] offer not recorded to obs", exc_info=True)


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

# The last slot readout's WORDS, durably (D-o, spec DT-30/31). Written by
# `apply_offer_to_session` beside every other record of an offer; read by
# `repeat_speech`. Distinct from B-120's `_slot_readout_chunks`, which is
# popped the instant a later chunk plays -- correct for B-120's purpose and
# wrong for this one. A dict of {"chunks", "mode", "options"}; only "chunks"
# is load-bearing.
LAST_READOUT_KEY = "_slot_last_readout"


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


# ── REPEAT (spec §2.1; D-o; DT-30, DT-31) ─────────────────────────────────
#
# "Say that again" after a readout. On both demo calls of 11 Sep morning
# (CA778651b7 08:44, CA34942aee 08:52) this went to the model: it read the
# right three times in the right order -- from its own context, in 2.0-2.4 s
# against 0.13 s for a producer, and as the author of three slot facts, which
# D-n forbids. N3 (10 Sep) was the same act dropped as a fragment for 19 s.
#
# A REPEAT is answered with the WORDS Susie last spoke about slots, verbatim.
# Not the selector re-run: that applies novelty against the spoken record and
# returns three different times, which is how "say that again" once became a
# new readout. Not a re-query: the diary can move under the caller and the
# five refusal branches are waiting. The words are in `LAST_READOUT_KEY`,
# written by `apply_offer_to_session` -- the one place every producer records
# what it is about to say.
#
# The phrase set is CLOSED and small on purpose. The 10 Sep note above the
# meaning-word list in connection.py warns against a "repeat-request phrase
# list", and it is right that one cannot be complete -- but this one does not
# gate what the model may HEAR (that list only ever widens), it selects what
# a producer will ANSWER. An utterance this misses still reaches the model,
# which still answers it; the cost of a miss is the 2 s the model takes, and
# the cost of a false positive is the wrong sentence, so the set is narrow.
_REPEAT_RE = re.compile(
    r"(?:"
    r"\b(?:say|read|run|go|give)\b.{0,24}?\b(?:again|once more|one more time)\b"
    r"|\brepeat\b"
    r"|\bcome again\b"
    r"|\bpardon\b"
    r"|\b(?:didn'?t|did not|never|couldn'?t|could not)\s+(?:quite\s+)?"
    r"(?:catch|hear|get)\b"
    r"|\bmissed (?:that|them|those|it)\b"
    r"|\bwhat (?:were|was) (?:they|those|that|the (?:times|options|choices|"
    r"first|second|third|last)(?: one)?)\b"
    r"|\bone more time\b"
    r"|\bonce more\b"
    r")",
    re.IGNORECASE,
)
# A whole utterance that is only one of these is a repeat request too:
# "sorry?", "what?", "again?". STT sends no question mark, so the shape is the
# bare word. Longer utterances starting "sorry, ..." carry their own act and
# are NOT matched here -- "sorry, Monday doesn't work" is a refusal.
_REPEAT_BARE = frozenset({
    "sorry", "what", "again", "pardon", "eh", "huh", "come again",
    "sorry what", "say again", "say that again", "what was that",
})
# Not a repeat, whatever else the utterance says: a request to LOOK again is
# a lookup; "else / other / different / more / what about" is ASK_OPTIONS,
# and a day or clock time named alongside "again" is a pick or a request.
_REPEAT_NOT_RE = re.compile(
    r"\b(?:check|look|search|try|have a look|see)\b.{0,16}?\bagain\b"
    r"|\b(?:else|other|others|different|another|later|earlier|sooner)\b"
    r"|\bmore\b(?! time\b)"
    r"|\bwhat about\b"
    r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b(?:morning|afternoon|evening|midday|lunchtime)\b"
    r"|\b\d{1,2}(?::\d{2})?\s*(?:am|pm|o'?clock)?\b",
    re.IGNORECASE,
)


def utterance_requests_repeat(text: str) -> bool:
    """True when the caller is asking Susie to say the last thing again.

    The REPEAT act of spec §2.1. Narrow by design -- see the note above
    `_REPEAT_RE` for why a miss is cheap and a false positive is not.
    """
    t = (text or "").lower().strip().strip(".,!?;:")
    if not t:
        return False
    if t in _REPEAT_BARE:
        return True
    if _REPEAT_NOT_RE.search(t):
        return False
    return bool(_REPEAT_RE.search(t))


def repeat_speech(session: Dict[str, Any], user_text: str) -> Optional[str]:
    """DT-30 / DT-31: the last readout, verbatim, when the caller asks for it.

    Returns None -- and the turn falls through to the model -- when:

      * the utterance is not a REPEAT;
      * nothing has been recorded as spoken about slots (`LAST_READOUT_KEY`
        absent): there is nothing honest to repeat, and D-o's last clause
        applies (say so and re-query), which the model's prompt already does;
      * an ORDINARY answer has played since the readout. B-120's
        `_slot_readout_chunks` is popped the instant a later turn's chunk
        plays, and B-132's `_content_turn_chunks` is what that later turn
        leaves behind; if the second is present and the first is not, the
        last thing the caller heard was an answer about their ankle, and
        "say that again" is about THAT. A watchdog re-ask ("any of those
        work?") pops both and leaves neither, and a repeat after it is still
        about the options -- so the durable copy is what is read.

    Never re-runs the selector, never touches the keypad map (inv. 10: it
    already equals this list) and records nothing new as heard (inv. 16: these
    times already are).
    """
    if not utterance_requests_repeat(user_text):
        return None
    saved = session.get(LAST_READOUT_KEY) or {}
    chunks = [str(c) for c in (saved.get("chunks") or []) if str(c).strip()]
    if not chunks:
        return None
    if (
        session.get("_content_turn_chunks")
        and not session.get("_slot_readout_chunks")
    ):
        logger.info(
            "[slot_followup] REPEAT declined -- an ordinary answer has played "
            "since the last readout, so 'say that again' is about that "
            "answer, not the options (DT-30)"
        )
        return None
    # The same words go out again, so B-120's copy is refreshed to match: a
    # barge-in that tears down THIS re-read can put it back too.
    session["_slot_readout_chunks"] = list(chunks)
    logger.info(
        "[slot_followup] REPEAT answered with the last readout verbatim -- "
        "%d chunk(s), mode=%s, no selector run (DT-30)",
        len(chunks), saved.get("mode") or "?",
    )
    return " ".join(chunks)


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


#: How far from the time the caller named a slot may sit and still BE that time.
#: northgate CA82c05845, 9 Sep 2026, judge 2, abandoned: the caller asked for
#: "around 12 o'clock" three times and was offered eight, five-ten, ten-to-nine
#: and twenty-past-four. Wednesday 16th held 12:10 throughout.
#:
#: The matching was exact string equality on both sides of this module, and this
#: clinic's grid runs in FIFTY-minute steps -- 08:00, 08:50, 09:40, 10:30, 11:20,
#: 12:10, 13:00. Only two round hours exist in a whole day, so a caller naming
#: nine, ten, eleven, twelve, two, three or four could never match anything. The
#: defect was not the parser; it was assuming a caller's "12" and a diary's
#: "12:10" are different times.
#:
#: 20 minutes picks exactly one slot on a 50-minute grid and cannot reach across
#: to the neighbour, so it stays a nearest-match rather than becoming a range.
NEAREST_TIME_TOLERANCE_MIN = 20

#: Minutes-past-the-hour a caller says when they mean ROUGHLY that time. Any
#: other value was read off a diary -- see `nearest_time_index`.
_SPOKEN_MINUTE_MARKS = frozenset({0, 15, 30, 45})


def _hhmm_to_minutes(value: Any) -> "int | None":
    """"HH:MM" (or an ISO start) to minutes past midnight, or None."""
    s = str(value or "")
    # An ISO start carries the clock after "T"; a bare slot time does not.
    if "T" in s:
        s = s.split("T", 1)[1]
    if len(s) < 5 or s[2] != ":":
        return None
    try:
        h, m = int(s[:2]), int(s[3:5])
    except ValueError:
        return None
    if not (0 <= h <= 24 and 0 <= m <= 59):
        return None
    return h * 60 + m


def nearest_time_index(
    times: Any, wanted: Any, tolerance_min: int = NEAREST_TIME_TOLERANCE_MIN
) -> "int | None":
    """The ONE slot the caller meant, by nearest clock time, or None.

    ONE owner for "which slot did the caller name?", because the exact-match
    version of this question was written twice -- in `resolve_requested_time`
    and in `_pin_requested_time_index` -- and both were wrong the same way on
    the same call. A second copy is how they drift apart again.

    `wanted` may hold a 12-hour TWIN ("at 5" is 05:00 and 17:00, and
    `requested_clock_times` returns both). The decline rules are unchanged from
    the exact-match version and are about the CALLER, not the tolerance:

      * no reading lands within tolerance -- the time is not on this day, which
        is ordinary and silent;
      * two readings land on DIFFERENT slots -- which was meant is unknowable
        here, so pin neither. A band word the caller actually said ("at five in
        the evening") has already collapsed the pair upstream.

    Within a single reading the NEAREST slot wins outright -- two candidates at
    different distances is not ambiguity, it is a grid. But an EXACT TIE
    declines, and that rule is load-bearing rather than fussy: `remaining` in
    `resolve_requested_time` spans the whole sweep, so the same clock time on
    three different days ties three ways. The exact-match version this replaced
    got that right by accident (`len(time_hits) == 1`), and the first cut of
    this function took the earliest instead -- which answered "wednesday around
    12" with MONDAY on CAd7495e58, 9 Sep 2026, judge 2. Declining is the only
    safe reading of a tie: the caller is asked again, rather than booked into a
    day they never said.

    TOLERANCE SCALES WITH THE CALLER'S OWN PRECISION, and this is the half that
    was nearly wrong. Replaying the real grids surfaced "oh yeah 20 to 10 will
    work" -> 09:40 being pulled to 10:00, and "ten to nine in the morning" ->
    08:50 pulled to 09:00. A caller who says twenty-to-ten means 09:40 and is
    almost always QUOTING a slot back; moving them twenty minutes is worse than
    the defect this fixes. So only the times people SAY when they mean roughly
    -- o'clock, quarter past, half past, quarter to -- are matched loosely. A
    time off those marks is a time read off a diary, and must match exactly.

    Never raises. A readout preference must not be what fails a lookup.
    """
    try:
        if not isinstance(times, (list, tuple)) or not times:
            return None
        if isinstance(wanted, str) or not isinstance(wanted, (list, tuple, set)):
            return None
        slots = [_hhmm_to_minutes(t) for t in times]
        picks = set()
        for w in wanted:
            wm = _hhmm_to_minutes(w)
            if wm is None:
                continue
            # See the docstring: an on-the-mark time is approximate speech, a
            # time off it is quoted from a diary and gets no tolerance at all.
            _tol = tolerance_min if (wm % 60) in _SPOKEN_MINUTE_MARKS else 0
            best_d, best_idxs = None, []
            for i, sm in enumerate(slots):
                if sm is None:
                    continue
                d = abs(sm - wm)
                if d > _tol:
                    continue
                if best_d is None or d < best_d:
                    best_d, best_idxs = d, [i]
                elif d == best_d:
                    best_idxs.append(i)
            # An exact tie is ambiguity -- most often the SAME time on two
            # different days. See the docstring: decline, never guess.
            if len(best_idxs) > 1:
                return None
            if best_idxs:
                picks.add(best_idxs[0])
        if len(picks) != 1:
            return None
        return picks.pop()
    except Exception:
        logger.exception("[slot_followup] nearest_time_index failed")
        return None


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

    # N-2. Two changes, one defect (northgate CA82c05845, 9 Sep 2026).
    #
    # THE PARSER. `_candidate_hhmm_from_text` reads the WORD form ("at twelve")
    # and returns nothing at all for every DIGIT form -- "at 12", "12 o'clock",
    # "any slots at 12" all yield []. So this path could not match the caller's
    # time even when the diary held it exactly. `requested_clock_times` is D8's
    # parser, replayed over 2,509 stored caller turns with zero inventions, and
    # it reads all of them. Unioned rather than swapped: the old reader is the
    # only consumer of that function and still contributes its word forms.
    #
    # THE MATCHING. Exact equality against a 50-minute grid -- see
    # NEAREST_TIME_TOLERANCE_MIN. "at 12" now reaches 12:10.
    #
    # The "exactly one" discipline is unchanged and still lives in
    # `nearest_time_index`, so a 12-hour twin straddling two real slots
    # declines here exactly as it did before.
    candidates = list(_candidate_hhmm_from_text(t))
    try:
        candidates += [c for c in requested_clock_times(t) if c not in candidates]
    except Exception:
        logger.exception("[slot_followup] requested_clock_times failed in resolve")
    _idx = nearest_time_index([s.get("time") for s in remaining], candidates)
    if _idx is not None:
        return _reject_if_caller_named_another_day(
            remaining[_idx], available_days, text,
        )
    return None


#: Weekday name -> Python weekday index, for the refusal in the guard below.
_WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_WEEKDAY_RE = re.compile(r"\b(" + "|".join(_WEEKDAY_NAMES) + r")s?\b", re.I)


def _reject_if_another_weekday(
    hit: Dict[str, Any], text: str
) -> Optional[Dict[str, Any]]:
    """Drop a slot that falls on a different weekday from the one named.

    Only fires when the caller named EXACTLY ONE weekday -- naming two ("monday
    or tuesday") says nothing about which, and naming none says nothing at all.
    Both keep the hit, which is the behaviour that existed before this.
    """
    try:
        found = {m.group(1).lower() for m in _WEEKDAY_RE.finditer(text or "")}
        if len(found) != 1:
            return hit
        want = _WEEKDAY_NAMES[next(iter(found))]
        key = _day_key(hit)
        if not key:
            return hit
        from datetime import date as _rw_date
        if _rw_date.fromisoformat(str(key)[:10]).weekday() == want:
            return hit
        logger.info(
            "[slot_followup] refusing a time on %s -- the caller named %s and "
            "that is a different weekday (CAd7495e58)",
            key, next(iter(found)),
        )
        return None
    except Exception:
        logger.exception("[slot_followup] weekday refusal failed")
        return hit


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
        # A BARE weekday names no calendar day, so `day_named_by_caller`
        # returns None -- its docstring calls that Tier 2 and defers it. That
        # was safe while a bare time could not resolve at all; it stopped being
        # safe the moment the resolver learned to read "around 12". CAd7495e58,
        # 9 Sep 2026, judge 2: "wednesday around 12" was answered with "ten past
        # twelve on MONDAY 14th September is free. Shall I book that in?"
        #
        # REFUSING on a weekday is not the same as SELECTING on one, which is
        # why this does not need the Tier 2 corpus. The worst a false positive
        # can do is decline and let the caller be asked again; a false negative
        # books them into a day they never said.
        return _reject_if_another_weekday(hit, text)
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

#: The claim that the day holds more than was just read out.
#:
#: SHORTENED 7 Sep 2026. It was three sentences differing only in a trailing
#: conditional that had to agree with the count -- "if that doesn't suit" /
#: "if neither suits" / "if none of those suit" -- because the grammar had once
#: been read out over the wrong number of options. Dropping the clause removes
#: the disagreement it was built to avoid, so the three collapse to one string
#: and `more_times_tail` no longer has a count-dependent answer to give.
#:
#: The reason for shortening is LAT-1, and the evidence is a live call.
#: CA8b40d1ed, northgate, 7 Sep 2026 11:49:07 -- the caller barged in while
#: this sentence was playing, having already heard all three options:
#:
#:     barge-in start
#:     interrupted_text="Number 3, ten past five in the evening. And I've a few other"
#:
#: It was 12 of the 47 words in that read-back: 26%, ~3.1 s of a measured 12.7 s.
#: The same sentence is also what pushes `last_bot_prompt` past its 200-char cap
#: (B-31) -- the truncation on that call cut at "And I've a few others tha", so
#: the prompt lost its "?" and clinical screening fell back to `last_question`.
#:
#: WHAT MUST NOT CHANGE: the sentence has to keep reading as an extra-slots
#: claim to `_is_extra_slots_claim`, which needs an "extra quantity" word AND a
#: "further times" noun at DIFFERENT offsets ("a few" + "others"). That
#: predicate is how a model-invented claim gets stripped when `more_times` is
#: false, and how the append path knows a claim is already present and does not
#: add a second. "that day" stays because B-99 requires the claim to have a
#: referent, and the referent is the one day this tail is allowed on.
MORE_TIMES_TAIL = "And I've a few others that day."

#: Kept as names because callers and tests import them; they no longer differ.
MORE_TIMES_TAIL_ONE = MORE_TIMES_TAIL
MORE_TIMES_TAIL_MANY = MORE_TIMES_TAIL
MORE_TIMES_TAIL_SEVERAL = MORE_TIMES_TAIL


def more_times_tail(n_offered: int) -> str:
    """The canonical tail for `n_offered` times just read out.

    `n_offered` no longer changes the answer, and the parameter is kept
    deliberately rather than removed. It existed because the tail used to end
    in a conditional that had to agree with the count -- "if that doesn't
    suit" / "if neither suits" / "if none of those suit" -- after the
    two-option wording was once read out over a three-option list. Dropping
    that clause for LAT-1 removed the disagreement, so there is nothing left
    for the count to decide.

    The signature stays because three call sites pass a real count, and a tail
    that needs to agree with it again is one edit away; taking the argument
    out now would mean putting it back with every call site to re-find.
    """
    return MORE_TIMES_TAIL


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

# Where those two words are NOT naming a position in the offer. Both shapes were
# found by scripts/replay_slot_decisions.py over the stored corpus once P12 made
# a position on a single-day offer settle the pick outright: until then the
# looseness was masked, because a position selected a DAY and step 3 then failed
# to find a time, so these turns resolved to nothing by accident.
#
# Vocabulary is not the discriminator and a longer word list is the trap. Both
# rules are about the SHAPE the word sits in:
#
#   1. part of a compound noun. "um yeah quentin roch um the last name is
#      spelled r-o-c-h" -- a caller SPELLING THEIR SURNAME resolved to a
#      bookable slot, because "last" was read as the end of the list. Nobody
#      chooses an appointment by saying "first name".
#
#   2. the object of a question about what the clinic HAS, rather than a choice
#      from what was read out: "actually what's the soonest you've got"
#      resolved to the first slot in the offer. Seven stored turns, every one
#      of them asking to be shown something else. `classify_intent` already
#      reads these correctly as Intent.EARLIEST; only this resolver took them
#      for a pick.
#
# Deny-by-default, as everywhere else here: "I'll take the first one you have"
# trips rule 2 and resolves nothing, which costs one more "which suits?" and
# cannot pin a slot the caller did not choose.
_POSITION_IS_NOT_A_POSITION_RE = re.compile(
    r"\b(?:first|last)\s+name\b"
    r"|\b(?:first|earliest|soonest|last|final|latest)\b(?:\s+\w+){0,3}?\s+"
    r"(?:you(?:'ve|\s+have|\s+ve)?\s+(?:got|have)\b"
    r"|you\s+got\b|(?:is|are)\s+available\b)",
    re.IGNORECASE)
_BAND_WORDS = ("morning", "afternoon", "evening")

ACCEPTED_SLOT_KEY = "_accepted_slot_iso"


def chosen_slot_steer(session: "Dict[str, Any] | None") -> str:
    """The CALL STATE line telling the model the caller has just picked. PURE.

    The engine resolves a caller's pick to an exact slot, logs
    `caller ACCEPTED 2026-09-09T15:00:00+01:00`, and steers Gate 5 and the hold
    speech with it -- and never tells the MODEL. The model learns a slot is
    settled only from `v3_confirmed_slot_phrase`, which is captured out of its
    OWN sentence at the name request. So it is told the choice is made only
    after it has already said so; if it does anything else, nothing corrects it.

    That is a circular dependency, and it is the second instance of one in this
    codebase: the first name is likewise only ever learned from Susie's own
    speech, so a deleted acknowledgement asks the caller for it forever
    (`gate5g`). Same shape, same cost.

    On CA4215ab7f (8 Sep 2026) what it did instead was call check_availability
    again. `_narrows_to_the_chosen_slot` is the backstop for that and cannot be
    ignored; this is the cure, and it is worth having as well because the
    backstop still pays for the round trip -- that turn's content reached the
    caller 7.25s after they finished speaking.

    ONE OWNER, two builders. `theorem_v3` renders from `_build_theorem_v3` and
    every other clinic from the template, so a rule written in one is absent
    from the other -- which is exactly how the read-back name steer had to be
    done, and why this is a function rather than a paragraph.

    Turn-scoped by construction: the pin is popped and re-resolved at the top of
    every caller turn, so this line describes THIS turn's choice or nothing. It
    cannot go stale mid-call the way `v3_confirmed_slot_phrase` did for three
    callers who changed day and were read the day they had left.

    Deliberately does NOT say "book it". The caller has chosen a time; the name,
    the phone and the confirmation all still have to happen, and a steer that
    skipped them would trade this defect for a worse one.
    """
    s = session if isinstance(session, dict) else {}
    iso = str(s.get(ACCEPTED_SLOT_KEY) or "")[:19]
    if not iso:
        return ""
    label = ""
    try:
        for sl in flatten_bookable_slots(s.get("available_days") or []):
            if str(sl.get("start") or "")[:19] == iso:
                label = "%s at %s" % (
                    sl.get("day_label") or "", sl.get("spoken") or "")
                break
    except Exception:
        label = ""
    label = (label or iso).strip(" at").strip()
    return (
        "SLOT JUST CHOSEN - in the turn you are answering now, the caller "
        "picked " + label + ". That is settled. Do NOT call check_availability "
        "again and do NOT read out any list of days or times: they have "
        "already chosen from one. Confirm that slot back to them and continue "
        "with the next step you owe them."
    )


def slot_llm_reply_can_only_be_discarded(session: "Dict[str, Any] | None") -> bool:
    """Is the post-check_availability model call certain to be thrown away? PURE.

    `_flush_slot_buf` speaks the deterministic offer and discards the model's
    version of it, EXCEPT where one of the two stand-down guards fires. Those
    guards are the only reason the reply is ever read, and each has a
    precondition that can be checked without it:

      * P6  -- `v3_dtmf_slot_map`: an offer is already standing, so the caller
        has heard options and may be answering a pick rather than being read a
        list;
      * P6b -- `ACCEPTED_SLOT_KEY`: an acceptance resolved to a slot THIS TURN,
        so a reply naming it is a confirmation, not a presentation.

    NOT `last_offered_slots`, which is what this function asked for until
    2026-09-03 and which made it return False on 100% of the turns it targets.
    `_exec_check_availability` writes that key on its main path
    (`receptionist_tools.py:6385`, before the only return on its direct body;
    the provider delegations record it themselves), so by the time this runs it
    always describes THIS turn's lookup. A deterministic
    offer existing implies slots were presented implies the key is set: the two
    conditions were mutually exclusive, and the skip was unreachable. Verified
    on CAdcd52a8a (2 Sep 2026, build 32cd187b): a textbook first lookup where
    the model call ran anyway and its reply was discarded as always.

    `v3_dtmf_slot_map` is the honest signal, and the one `connection.py:2690`
    already instructs callers to use ("Guard on the MAP, never on
    `v3_awaiting_slot_selection`"). It is written by `_flush_slot_buf` AFTER
    this point in the turn, so here it still describes the PREVIOUS turn --
    exactly "has an offer already been put to the caller and is it still open?"

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
    if s.get("v3_dtmf_slot_map"):
        return False
    if s.get(ACCEPTED_SLOT_KEY):
        return False
    return True


def _band_named(text: str) -> "str | None":
    """The ONE part-of-day `text` names, or None when it names 0 or 2+."""
    low = (text or "").lower()
    hits = {b for b in _BAND_WORDS if re.search(r"\b" + b + r"\b", low)}
    return hits.pop() if len(hits) == 1 else None


#: A clock hour stated with a meridiem: "8pm", "8 p.m.", "10 am".
_MERIDIEM_RE = re.compile(r"\b(\d{1,2})\s*([ap])\.?\s?m\.?\b", re.IGNORECASE)

#: A bare clock number the caller could only mean as a TIME. Ordinals are
#: excluded by the word boundary itself -- "7th" has a word character after
#: the 7 -- and a digit introduced as an option is excluded explicitly,
#: because "number 2" is a position and positions resolve before any of this.
_BARE_CLOCK_RE = re.compile(r"(?<!\bnumber )(?<!\boption )\b(\d{1,2})\b")


def _meridiem_hour_named(text: str) -> "int | None":
    """The 24-hour hour named as ``<digit> am/pm``, or None for 0 or 2+.

    ``_band_named`` cannot answer this and must not be taught to. Its
    vocabulary is morning/afternoon/evening and "pm" spans two of them, so
    folding the meridiem into it would either name a band the caller did not
    say or make the band check ambiguous. This is narrower on purpose: it
    decides only which of the 24 hours the caller meant, and only when they
    said so outright.

    Two readings decline, which is the standing rule in this module for two
    of anything.
    """
    found = set()
    for m in _MERIDIEM_RE.finditer(text or ""):
        h = int(m.group(1))
        if not 1 <= h <= 12:
            continue
        found.add(h % 12 + (12 if m.group(2).lower() == "p" else 0))
    return found.pop() if len(found) == 1 else None


#: "one" as a PRONOUN, not as one o'clock. The clock fold turns every spelled
#: number into a digit, so "the morning one" arrives here as "morning 1" and
#: would otherwise read as a caller naming a time. Only the pronoun positions
#: are masked: "one in the afternoon" is still a time, and so is "at one".
_PRONOUN_ONE_RE = re.compile(
    r"\b(?:the|that|this|which|first|last|other|either"
    r"|morning|afternoon|evening)\s+one\b"
    r"|\bone\s+(?=(?:works|suits|please|is|will|sounds|on|for)\b)",
    re.IGNORECASE,
)


def _clock_time_named(text: str) -> bool:
    """Did the caller name a clock time at all? Folded, so words count.

    "ten past five" folds to "10 past 5" and answers True; "the morning one"
    answers False. Used to decide whether the band fallback is allowed to
    choose FOR the caller -- see ``slot_accepted_by_caller``.
    """
    return _BARE_CLOCK_RE.search(
        _time_norm(_PRONOUN_ONE_RE.sub(" ", text or ""))
    ) is not None


def _time_contradicts(text: str, start: Any) -> bool:
    """Does ``text`` name a time that ``start`` is not? PURE, and deny-biased.

    Two independent readings, either of which convicts:

      * the BAND -- "the last day in the afternoon" against an 09:10 slot.
        This half already existed inline on the single-slot branch; it is
        lifted here so all three exits ask the same question.
      * the MERIDIEM -- "monday at 8 pm" against 08:00, which the band check
        cannot see because "pm" is not a band word. That is the substitution
        this guard was written for: the caller said eight in the evening, the
        resolver pinned eight in the morning, and every verbal read-back
        afterwards is generated FROM the pin, so it sounds correct all the way
        to the calendar.
    """
    band = _band_named(text)
    if band and part_of_day(start) != band:
        return True
    hour = _meridiem_hour_named(text)
    if hour is not None:
        try:
            if int(str(start)[11:13]) != hour:
                return True
        except (TypeError, ValueError):
            return True
    return False


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
    # The relative-end words only, because that is where the looseness is: they
    # are ordinary English ("last name", "the soonest you've got") in a way that
    # "number two" is not. Masked rather than rejecting the whole utterance, so
    # a sentence that names a real position AND happens to contain one of these
    # shapes still resolves on the part that IS a position.
    _relative = _POSITION_IS_NOT_A_POSITION_RE.sub(" ", text or "")
    if _LAST_POSITION_RE.search(_relative):
        found.add(n)
    if _FIRST_POSITION_RE.search(_relative):
        found.add(1)
    return found.pop() if len(found) == 1 else None


def _names_a_different_weekday(text: Any, iso_date: Any) -> bool:
    """Did the caller name a weekday that is NOT this date's? PURE.

    B-138, CAdd64c466 (northgate, 4 Sep 2026):

        11:34:44  'um do you have any do you have a 10 past 12 for wednesday
                   for example'
        11:34:44  caller ACCEPTED 2026-09-10T12:10:00+01:00      <- THURSDAY

    A QUESTION about Wednesday resolved as an ACCEPTANCE of Thursday.

    The last-resort branch below fires when the offer holds exactly one date,
    reading that as "the caller named only a time, so nothing is ambiguous".
    Its own comment states the premise --

        "this only fires when the offer holds exactly ONE date, so it cannot
         guess between days"

    -- and the premise is FALSE whenever the caller names a day the offer does
    not hold. Nothing checked for it. B-134 made the branch reachable by
    writing a single-date record, and was reverted for it; the hole is older
    than B-134 and any future single-date record re-exposes it, so the check
    belongs here rather than there.

    Declining is free -- the caller is no worse off than before the branch
    existed -- and a wrong day is a wrong BOOKING, so this is deliberately
    generous about when it declines:

      * It declines when NO weekday the caller named is this date's. Naming
        several ("saturday or thursday") still resolves a Thursday offer --
        the day is among them, so the offer is not being contradicted.
      * A stray weekday in an unrelated clause declines too. That is a
        deliberate false decline: the cost is one extra "which day?", against
        an appointment written on the wrong day.
      * A date it cannot parse declines nothing, because a check that cannot
        read its input must not be the thing that pins an appointment.

    Day-of-month is NOT covered -- "the 9th" against a Thursday-10th offer
    still resolves. That needs the ordinal vocabulary above and its own tests;
    this closes the shape that reached a live caller.
    """
    words = [
        w for w in _WEEKDAY_WORDS
        if f" {w} " in f" {_caller_norm(text)} "
    ]
    if not words:
        return False
    try:
        named = _date.fromisoformat(str(iso_date or "")[:10]).strftime("%A").lower()
    except Exception:
        return False
    return named not in words


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


def _payload_day_by_weekday(available_days: Any, text: str) -> "str | None":
    """The one PAYLOAD day whose WEEKDAY the caller named, or None. PURE.

    B-148, `CAdf1e02ca`, northgate, 2026-09-06 10:15:23. Susie had just narrowed
    to Monday. The caller said "um check for tuesday please" and D-B did not
    fire at all:

        situational head (named_day): 'Let me have a look at Tuesday for you -'
        second filler phrase (genuine stall, 10.0s since dispatch)
        LAT turn_seq=12 llm_ttft_ms=12876 content_ttfa_ms=13700
        "from the data I already have for Tuesday the 8th - I've got ten past
         nine in the morning, or twenty past five in the evening"

    Two of Tuesday's times, thirteen seconds, and the model's internal phrasing
    ("from the data I already have") spoken to a caller. The head was right and
    nothing was behind it -- D-B's own defect, arriving again because the
    producer could not resolve the day.

    `_offered_day_by_weekday` resolves a bare weekday against the OFFER, which
    is correct for a PICK: you can only choose from what was read out. It is the
    wrong scope for a REQUEST. Once the conversation narrows -- and B-145 makes
    narrowing commoner, because a day acceptance now narrows too -- the offer
    holds one day, and every other day the clinic actually has becomes
    unreachable by name.

    NOT the calendar, and therefore not the date parsing Tier 2 needs: the
    candidates are the payload this caller's own lookup returned. Same
    deny-by-default rules as its sibling, and the second one matters more here
    because a payload can span more than a week:

      * exactly ONE weekday word in the speech -- two is a comparison;
      * exactly ONE payload day falling on it. A fortnight holding two Tuesdays
        declines rather than guessing the nearer, which is the rule
        `weekday names repeat every seven days` exists for.

    Never raises: a caller mid-booking must not lose their turn to a resolver.
    """
    try:
        _words = [w for w in _WEEKDAY_WORDS
                  if f" {w} " in f" {_caller_norm(text)} "]
        if len(_words) != 1:
            return None
        _dates = sorted({
            str((d or {}).get("date") or "")
            for d in (available_days or [])
            if isinstance(d, dict) and (d.get("slot_times") or [])
        } - {""})
        _hits = [
            d for d in _dates
            if _date.fromisoformat(d).strftime("%A").lower() == _words[0]
        ]
        return _hits[0] if len(_hits) == 1 else None
    except Exception:
        return None


_REQUEST_FOR_SLOTS_RE = re.compile(
    r"\b(tell|give|offer|send|list|read|repeat|show|run through|go through)\b"
    r"[^.?!]{0,40}?\b(slots?|times?|options?|availability)\b", re.I)

_NEGATED_POSITION_RE = re.compile(
    r"\bnot\b(?:\s+\w+){0,2}?\s+"
    r"\b(first|second|third|fourth|fifth|sixth|last|one|two|three|four|five)\b",
    re.I)

# A caller REJECTING what they were offered, while naming part of it.
#
# `_NEGATED_POSITION_RE` above already claims this ground -- the docstring below
# says "a REJECTION is a negator sitting in front of the position" -- but it
# only covers POSITIONS. A negated DAY or time walks straight through it:
#
#   "no saturday is not soon enough i need to be seen as soon as possible"
#   "uh yeah monday doesn't work"
#   "no no saturday doesn't work i need one now"
#
# All three resolved to a slot, because naming a day IS a selection on a
# multi-day offer and nothing was asking whether the caller had said no to it.
# Five such turns in the stored corpus, all northgate, all unambiguous.
#
# Found by MEASURING a fix rather than by a phone call: widening the re-query
# guard to consult this resolver newly blocked 307 turns across 921 calls, and
# reading all of them is what turned up the five. A resolver feeding a BLOCKING
# guard has to be right about rejection, because refusing the lookup for a
# caller who has just said "that doesn't work" is worse than the defect being
# fixed.
#
# Deliberately narrow, and deliberately biased. A leading "no" is a rejection
# only when it is not "no problem" / "no worries" / "no rush" -- those are
# agreement. Everything else here needs an explicit negation attached to
# WORKING or to being soon enough. A miss costs the old behaviour; a false
# positive costs a caller their pick, so the doubt goes to not-a-rejection.
_REJECTS_THE_OFFER_RE = re.compile(
    r"^\s*(?:uh|um|er|ah|oh|well|yeah|yes|ok|okay)?[\s,]*"
    r"(?:no|nope|nah)\b(?!\s+(?:problem|worries|rush|bother|trouble))"
    r"|\bnot\s+\w*\s*enough\b"
    r"|\b(?:does|do|wo|will)\s?n['\u2019]?o?t\s+(?:really\s+)?work\b"
    r"|\bno\s+good\b",
    re.I)



# A caller who did not HEAR is asking for the readout again, not choosing from
# it. Found by the replay harness once P12 let a position settle a single-day
# pick outright:
#
#   "i didn't catch that last bit can you repeat yourself"  ->  the LAST slot
#
# "last bit" is the same compound-noun shape as "last name", but the honest
# discriminator is not the noun -- the whole utterance is a request to repeat.
#
# NOT `Intent.REPEAT_ASK` from hold_speech, though it covers this exactly and
# reusing it was the first attempt. That intent also matches "i said", which
# there means the caller is RESTATING -- and restating is how a caller re-asserts
# a pick Susie missed the first time. Replay measured the cost: four stored turns
# stopped resolving, every one of them a real pick from an audibly impatient
# caller --
#
#   "yeah i said 9 in the morning works"
#   "i said quarter past six please"
#
# -- who would have been read the list a second time. That is the P6 symptom
# this whole resolver exists to remove, and it would have landed on precisely
# the callers who had already hit it once. So the narrow half is spelled out
# here: not hearing is a request, repeating yourself is not.
_DID_NOT_HEAR_RE = re.compile(
    r"\b(?:did\s?n'?o?t\s+(?:hear|catch|get)"
    r"|missed\s+that|come\s+again|pardon"
    r"|say\s+(?:that\s+)?again"
    r"|repeat\s+(?:that|yourself|it))\b",
    re.IGNORECASE)

def utterance_is_a_request_not_a_pick(text: str) -> bool:
    """True when a position or time appears inside a REQUEST, not a choice.

    Two shapes, found by the replay harness on the stored corpus once the
    single-day branch started resolving ordinals:

      "could you offer me the slots for the first friday that you offered me"
      "could you tell me the slots you have on the tuesday again um not the
       first one ..."

    Both name a position and neither is a pick. `utterance_requests_more_slots`
    misses them because it is a list of literal signals ("what else", "more
    slots") and these say the same thing in words that are not on it. Adding
    them to that list is the trap -- the shape is what distinguishes these, not
    the vocabulary: a REQUEST is a speech verb taking slots/times as its
    object, and a REJECTION is a negator sitting in front of the position.

    Deny-by-default is the whole point. "the first one, not the second" trips
    this and resolves nothing, which leaves the caller exactly where they were;
    resolving it to either slot would pin a choice they did not make.
    """
    t = (text or "").strip()
    if not t:
        return False
    return bool(_REQUEST_FOR_SLOTS_RE.search(t)
                or _NEGATED_POSITION_RE.search(t)
                or _REJECTS_THE_OFFER_RE.search(t)
                or _DID_NOT_HEAR_RE.search(t))
    if _REQUEST_FOR_SLOTS_RE.search(t) or _NEGATED_POSITION_RE.search(t):
        return True
    # A caller who says they did not HEAR is asking for the readout again, not
    # choosing from it. Found by the replay harness once P12 let a position
    # settle a single-day pick outright:
    #
    #   "i didn't catch that last bit can you repeat yourself"  ->  the LAST slot
    #
    # "last bit" is the same compound-noun shape as "last name", but the honest
    # discriminator is not the noun -- it is that the whole utterance is a
    # request to repeat. `classify_intent` already owns that question and reads
    # this correctly as REPEAT_ASK, so it is asked rather than answered a second
    # time here: two matchers for one intent is two answers to it, and the one
    # that drifts is the copy.
    #
    # Lazy and defensive, like every other exit in this resolver: a caller
    # mid-booking must never lose their turn because a classifier raised.
    try:
        from app.hold_speech import Intent, classify_intent
        if Intent.REPEAT_ASK in (classify_intent(t) or []):
            return True
    except Exception:
        pass
    return False


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
    if utterance_is_a_request_not_a_pick(text):
        return None

    offered = session.get("last_offered_slots")
    if not isinstance(offered, list) or not offered:
        return None

    # -- 2. which day -----------------------------------------------------
    # What the offer covers. `apply_offer_to_session` writes ONE entry per DAY
    # on multi_day and EVERY SLOT otherwise, so a named position means
    # different things in the two modes. Reading it as a day in both is why an
    # ordinal resolved nothing on a single-day offer: it selected the only date
    # there was, and step 3 then demanded a time the ordinal never names.
    # Found by the replay harness over the stored corpus, not on a call.
    offered_dates = [d for d in
                     (str((o or {}).get("start") or "")[:10] for o in offered)
                     if d]
    single_day_offer = len(set(offered_dates)) == 1

    try:
        spoken = spoken_starts_for_offer(session)
    except Exception:
        return None

    pos = _position_named(text, len(offered))
    if pos is not None and single_day_offer:
        # The positional entries ARE the slots here, so the position settles
        # the pick outright. Still deny by default: a position may only select
        # a slot this caller was actually READ.
        start = str((offered[pos - 1] or {}).get("start") or "")
        if not start or start[:19] not in spoken:
            return None
        # B-138, through the door this branch opens. That guard sits on the
        # LAST-RESORT day branch, which this return jumps over entirely, so
        # without repeating it here "have you got number two on wednesday?"
        # against a Thursday-only offer books the Thursday -- the same wrong-day
        # booking B-138 was written for, reached by ordinal instead of by time.
        # The offer named its day once; a caller naming a different one has
        # supplied exactly the ambiguity this branch assumes away.
        if _names_a_different_weekday(text, start[:10]):
            return None
        # And the same question all three exits in step 3 ask: a position paired
        # with a time that is not this slot's ("number two, the one at half
        # nine") is a contradiction, not a pick. Declining costs one more "which
        # suits?"; resolving it writes the wrong time to the diary, where every
        # verbal read-back afterwards is generated FROM the pin and so sounds
        # correct all the way to the calendar.
        if _time_contradicts(text, start):
            return None
        return start

    date = None
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
        # THE OFFER NAMES ONE DAY, so the caller naming only a time has not
        # left anything ambiguous -- there is nothing for the day to be.
        #
        # This is the shape after Susie narrows: "Monday it is —", then
        # "Monday the 7th — I've got eight in the morning or ten past five in
        # the evening. Which suits?". The caller answers with a TIME, because
        # they have already said the day and she has just repeated it.
        #
        # Live 2026-09-03 13:07:24 on the demo line:
        #
        #   13:07:24  'um 10 past 5 in the evening works'
        #   13:07:25  situational head (time_band):
        #             "Let me see what I've got in the evening —"
        #   13:07:27  "So that's Monday the 7th of September at ten past five"
        #
        # She promised a lookup and then did not do one -- she confirmed. The
        # pick resolved to nothing, so `_hs_picking` stayed false and the
        # TIME_BAND diary head fired. That is the promised-work defect, and it
        # is reached by the most ordinary answer a caller can give.
        #
        # Deny-by-default is preserved: this only fires when the offer holds
        # exactly ONE date, so it cannot guess between days. A multi_day offer
        # still needs the day named, which is what the three steps above are
        # for.
        _dates = {
            str((o or {}).get("start") or "")[:10]
            for o in offered if isinstance(o, dict)
        } - {""}
        if len(_dates) == 1:
            _only = next(iter(_dates))
            # B-138: unless the caller named a DIFFERENT day. The branch above
            # reads a lone offered date as "no ambiguity"; a caller who names
            # another weekday has supplied exactly the ambiguity it assumes
            # away, and pinning it books the wrong day.
            if _names_a_different_weekday(text, _only):
                return None
            date = _only
    if not date:
        return None

    # -- 3. which time, among what was SPOKEN -----------------------------
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
        if _time_contradicts(text, heard[0].get("start")):
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
    # The bare form is the hour with its band stripped off, so "eight in the
    # morning" is matched by the digit 8 alone. "monday at 8 pm" then matches
    # an 08:00 slot on nothing but the digit, and the meridiem the caller
    # actually said is discarded. Live 2026-09-03 00:46:26: pinned 08:00 for
    # "8 pm", and it survived only because the model happened to notice Monday
    # has no evening eight and re-asked.
    #
    # The meridiem FILTERS rather than vetoes, which matters on a day holding
    # both 08:00 and 20:00: both labels strip to "8", so both match on the
    # digit and a veto would decline a slot the caller was actually offered.
    # Filtering leaves exactly the one they named. The rule the owner stated,
    # and the right one: a caller who names a slot from the readout gets that
    # slot. Declining is only for a time the offer does not hold.
    hits = [s for s in hits if not _time_contradicts(text, s.get("start"))]
    if len(hits) == 1:
        return hits[0].get("start") or None

    band = _band_named(text)
    if band and not _clock_time_named(text):
        # The band picks FOR the caller, so it may only do so when they left
        # the choice open. A caller who named an explicit clock time that
        # matched nothing has not left it open -- they asked for something the
        # offer does not contain, and the honest answer is to decline and let
        # the turn re-ask.
        #
        # Live 2026-09-03 01:29:14, northgate: Monday was offered 08:00 and
        # 17:10; the caller said "monday the 7th at 10 in the morning"; no
        # label matched, and this fallback returned 08:00 because it was the
        # only MORNING slot. The read-back guard caught it downstream, the
        # caller heard a question they had already answered, and the call was
        # abandoned with a judge score of 2.
        in_band = [s for s in heard if part_of_day(s.get("start")) == band]
        if len(in_band) == 1:
            return in_band[0].get("start") or None
    return None


#: A REQUEST for a day, not an acceptance of one. The whole hazard of
#: `day_accepted_by_caller` lives here: "what about Monday" names exactly one
#: offered day and is a question, and treating it as a pick would put
#: "Monday it is -" in front of a lookup that really is happening -- the
#: promised-work defect, which this family has produced three times.
_DAY_REQUEST_RE = re.compile(
    r"\b(?:what|how)\s+about\b"
    r"|\bdo\s+you\s+have\b|\bhave\s+you\s+(?:got|any)\b"
    r"|\bis\s+there\b|\bare\s+there\b|\banything\s+(?:on|for)\b"
    r"|\bany\s+(?:slots?|times?|availability|openings?)\b"
    r"|\bcan\s+(?:i|you)\b|\bcould\s+(?:i|you)\b|\bwould\s+(?:i|you)\b"
    r"|\bwhat(?:'?s| is)\b[^?]{0,30}\b(?:free|available|open)\b"
    r"|\bis\b[^?]{0,20}\b(?:free|available|any\s+good)\b"
    # Found by scripts/replay_day_picks.py over the 828-call corpus, and none
    # of them had reached a caller yet. Each scored ACCEPT only because "yeah"
    # opens it and nothing above matched:
    #
    #   "yeah check for tuesday please"
    #   "yeah i'll ask for you to present tuesday the 8th"
    #   "what do you mean monday the 10th works"
    #
    # The first two ask for a LOOKUP, so "Tuesday it is —" in front of them is
    # the promised-work defect. The third is a confused caller, and confidently
    # confirming at them is its own harm.
    #
    # "check" is safe to take whole: a caller ACCEPTING a slot has no reason to
    # say it, and every use of it in the corpus asks for a diary to be opened.
    # "see" is deliberately NOT here -- "let's see that saturday slot please"
    # is an acceptance, and a bare "see" would eat it.
    r"|\bcheck\b|\bwhat\s+do\s+you\s+mean\b"
    r"|\b(?:i'?ll|i\s+will|you\s+can)\s+(?:ask|look|present)\b",
    re.IGNORECASE,
)

#: An ACCEPTANCE. Deny-by-default: a day named with none of these is not a
#: pick, it is the caller thinking out loud, and silence is the answer that
#: has always been given.
_DAY_ACCEPT_RE = re.compile(
    r"\b(?:works?|working|fine|good|great|perfect|ideal|lovely|suits?|"
    r"suitable|ok|okay|yeah|yes|yep|please|brilliant|grand|"
    r"i'?ll\s+take|let'?s\s+do|go\s+for|happy\s+with|that'?ll\s+do)\b",
    re.IGNORECASE,
)

#: A REFUSAL, which every word in `_DAY_ACCEPT_RE` can be carrying.
#:
#: "monday doesn't work" matched `works?` and was read as an acceptance. While
#: the only consumer was the hold-speech head that cost "Monday it is -" in
#: front of a caller who had just said the opposite; with a producer behind it
#: it costs Monday READ OUT to a caller who has just refused Monday, which is
#: the duplicate-write family's shape -- speech that contradicts what the
#: caller said, generated from a record that agrees with the speech.
#:
#: Deny by default and deny WHOLESALE: any negator anywhere in the utterance
#: declines. `day_accepted_by_caller`'s own docstring already states which way
#: this must fail -- "returning None costs a head", and that is the cheap side.
#: A negated acceptance is never clean enough to act on.
_DAY_REFUSE_RE = re.compile(
    r"\b(?:no|nope|nah|not|none|never|cannot|rather)\b|n'?t\b",
    re.IGNORECASE,
)


def day_accepted_by_caller(session: Dict[str, Any], text: str) -> "str | None":
    """The ISO DATE of the offered day the caller just accepted, or None. PURE.

    The gap `slot_accepted_by_caller` cannot close, and it is not a defect in
    that function: a caller who says "yeah Monday works" after a multi-day
    readout has accepted a DAY and no TIME, and Monday holds two times they
    were read and chose between neither. Declining is the correct answer to
    "which SLOT did they accept". It is the wrong answer to "did they pick
    something", and that second question is what the hold-speech head needs.

    Live 2026-09-03 01:56:49, demo line, build 2a8a6ee6:

        'yeah monday works'  ->  LAT turn_seq=3 ttfa_ms=2097 content_ttfa_ms=2097

    Equal, so nothing spoke. `Intent.SLOT_PICKED` -- "Monday it is -" -- exists
    for exactly that utterance and could not be reached, because both inputs to
    the head's `slot_selection` argument declined: `utterance_is_slot_selection`
    is containment against the spoken labels and a bare weekday matches none of
    them, and `slot_accepted_by_caller` needs a time.

    DENY BY DEFAULT, on BOTH sides, because the failure modes are asymmetric.
    Returning None costs a head. Returning a day for a caller who was ASKING
    about it costs "Monday it is -" in front of a lookup that is really
    happening -- the promised-work defect, which this family has produced three
    times and is the reason the 30 Aug decision gave picks silence in the first
    place.

      1. not a "more times" or "different day" request -- those have their own
         paths and their own answers;
      2. not shaped like a question or a request ("what about Monday", "do you
         have Monday", a trailing "?");
      3. carries an acceptance word. A bare "Monday" is not a pick;
      4. names NO clock time. If they named one, `slot_accepted_by_caller`
         owns the turn -- and if IT declined, the time did not match the offer
         and the acceptance is not clean enough to speak to;
      5. names exactly ONE day out of the offer just read, by weekday or by
         full label. Positions are deliberately excluded: "number two" names no
         day, `subject_for` has nothing to render, and the 30 Aug decision that
         a positional pick gets silence stands untouched.
    """
    if not isinstance(session, dict) or not isinstance(text, str):
        return None
    if not text.strip():
        return None

    offered = session.get("last_offered_slots")
    if not isinstance(offered, list) or not offered:
        return None

    if utterance_requests_more_slots(text) or utterance_requests_different_day(text):
        return None
    if "?" in text or _DAY_REQUEST_RE.search(text):
        return None
    if not _DAY_ACCEPT_RE.search(text):
        return None
    if _DAY_REFUSE_RE.search(text):
        return None
    if _clock_time_named(text):
        return None

    date = _offered_day_by_weekday(offered, text)
    if not date:
        named = day_named_by_caller(session.get("available_days"), text)
        if isinstance(named, dict):
            date = named.get("date")
        elif isinstance(named, str):
            date = named
    return date or None


def day_refused_by_caller(session: Dict[str, Any], text: str) -> "str | None":
    """The ISO DATE of the offered day the caller just RULED OUT, or None. PURE.

    B-147, `CAdf1e02ca`, northgate, 2026-09-06 10:15:06. The caller said
    "monday doesn't work" and heard Monday's times read out:

        [slot_followup] 'Monday 7th September' answered from the payload --
        3 of 17 bookable times spoken, offer and keypad recorded (D-B)

    Both day producers resolve the weekday the caller NAMED, and neither can
    tell refusal from interest by the day alone. `day_accepted_by_caller`
    already declines a negated utterance; `named_day_speech` did not, and it is
    the louder failure — it does not mis-acknowledge, it reads out the day the
    caller has just ruled out and renumbers the keypad onto it.

    Deny by default. The bar is the same as the acceptance door's, inverted:

      1. the utterance names exactly ONE offered day, resolved against the
         OFFER and never the calendar;
      2. it carries a refusal;
      3. it is not a "more slots" request — that path already answers well and
         must not be stolen ("monday doesn't work, what else have you got" is
         answered identically either way, but by ONE owner).

    Returns the date rather than a bool so a caller of this can say which day
    was ruled out if it ever needs to. `try_unspoken_followup_speech` answers
    it with `more_days_speech` — the days he has not heard.
    """
    if not isinstance(session, dict) or not isinstance(text, str):
        return None
    if not text.strip():
        return None
    if not _DAY_REFUSE_RE.search(text):
        return None
    if utterance_requests_more_slots(text):
        return None

    offered = session.get("last_offered_slots")
    if not isinstance(offered, list) or not offered:
        return None

    date = _offered_day_by_weekday(offered, text)
    if not date:
        named = day_named_by_caller(session.get("available_days"), text)
        if isinstance(named, dict):
            date = named.get("date")
        elif isinstance(named, str):
            date = named
    if not date:
        # B-150, CA176b7a0d, northgate, 2026-09-06 22:07:59. Susie had narrowed
        # to Monday one turn earlier, so `last_offered_slots` held Monday's
        # times and nothing else. The caller said "um tuesday doesn't work" and
        # this declined -- Tuesday could not be resolved -- so the refusal fell
        # through to the model instead of being answered by `more_days_speech`.
        #
        # The rule is right and the SCOPE was too narrow: a caller can only
        # refuse a day they were read, but "were read" is a fact about the
        # CALL, not about the current offer. Tuesday was read out three turns
        # earlier in the multi_day offer, and the cumulative record
        # (`slot_starts_spoken`, B-78b) has held it ever since.
        #
        # NOT widened to the payload the way `named_day_speech` is (B-148).
        # A REQUEST may name a day the caller has never heard -- that is the
        # point of asking. A refusal of a day nobody offered rules out nothing,
        # and resolving it would let `more_days_speech` be steered by a day the
        # caller was never told about.
        date = _spoken_day_by_weekday(session, text)
    return date or None


def _spoken_day_by_weekday(session: Dict[str, Any], text: str) -> "str | None":
    """The one day READ OUT AT ANY POINT this call whose weekday was named. PURE.

    Reads `slot_starts_spoken` directly rather than through `_spoken_key_set`,
    which resets the record when availability moves -- this must not mutate the
    session, and `remaining_unspoken` has already refreshed it earlier in
    `try_unspoken_followup_speech` on every turn that reaches here.

    Deny by default, the same two rules as its siblings: exactly ONE weekday
    word in the speech, and exactly ONE spoken day falling on it.
    """
    try:
        _words = [w for w in _WEEKDAY_WORDS
                  if f" {w} " in f" {_caller_norm(text)} "]
        if len(_words) != 1:
            return None
        _dates = sorted({
            str(s or "")[:10] for s in (session.get(_SPOKEN_KEY) or [])
        } - {""})
        _hits = [
            d for d in _dates
            if _date.fromisoformat(d).strftime("%A").lower() == _words[0]
        ]
        return _hits[0] if len(_hits) == 1 else None
    except Exception:  # pragma: no cover - defensive; live call path
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
    # `slot_accepted_by_caller` opens with the same check, for the same reason:
    # a caller mid-booking must not lose their turn to a resolver. `(session or
    # {})` covers None and the falsy shapes, not a truthy non-dict.
    if not isinstance(session, dict):
        return False
    iso = str(session.get(ACCEPTED_SLOT_KEY) or "")[:19]
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

    # A confirmation names the accepted slot's DAY, or names no day at all.
    # Text that names only OTHER weekdays is a fresh list, whatever its times
    # happen to be. Same helper and the same one-way rule as B-138.
    #
    # CA4215ab7f (theorem_v3, 8 Sep 2026 01:15) is why: the caller accepted
    # Wednesday 9th at three in the afternoon, and this returned True for
    #
    #   "here's what we've got coming up -- Number 1, Friday 11th September --
    #    nine in the morning or two in the afternoon. Number 2, Monday 14th ...
    #    Number 3, Tuesday 15th September ..."
    #
    # Gate 5 stood down, spoke it, and the caller heard sixteen seconds of
    # three days they had not asked for after picking one.
    if _names_a_different_weekday(text, iso[:10]):
        return False

    phrase = _time_norm(text)

    def _names(candidate: str) -> bool:
        """Does the text name this label, with B-114's question asked?

        A BARE NUMBER IS NOT A TIME. `_time_norm` folds a clock word to a
        digit -- "three" -> "3" -- and every numbered read-out contains
        "Number 3", so an unguarded match makes a three o'clock slot "named"
        by any list with a third option.

        Applied to BOTH candidates, not only the bare fallback. A clinic with
        `speak_part_of_day: false` has labels that are ALREADY bare ("three",
        not "three in the afternoon"), so for those the full label IS the bare
        number and it reaches this by the first call, never the second.
        Guarding only the fallback left exactly those clinics exposed -- and
        they are the ones the wording change was made for.
        """
        if not candidate:
            return False
        if candidate in _BARE_HOUR_WORDS and not _bare_hour_word_is_a_clock_reference(
            text, candidate
        ):
            return False
        return _time_named_in(phrase, candidate)

    if _names(label):
        return True

    bare = _strip_part_of_day(label)
    if not bare or bare == label:
        return False
    return _names(bare)


# A sentence, and the contrast that starts a new one in the middle of it.
# "Monday's fully booked, BUT Tuesday has ten past twelve" is two claims, and
# only the second is an offer.
_CLAUSE_SPLIT = re.compile(
    r"[.!?;]+|\s+(?:but|however|although|though|whereas)\s+", re.I
)

# A clause that says a time is NOT available. Deliberately broad -- every entry
# costs at most one clause being ignored, which is the behaviour that existed
# before `payload_slots_named_in` did, while a missed one records a slot Susie
# has just told the caller she does not have.
_NO_SLOT_IN_THIS_CLAUSE = (
    "n't", " not ", " no ", " none", "nothing", "unfortunately", "afraid",
    "fully booked", "booked up", "unavailable", "sorry", " gone", " taken",
    "already booked", "no longer",
)


def offer_clauses(text: Any) -> List[str]:
    """The clauses of `text` that are OFFERING something. PURE.

    B-139. `payload_slots_named_in` reads the times a stood-down sentence
    SPOKE, and the first version of it read them out of the whole sentence at
    once -- so

        "Wednesday doesn't have ten past twelve"

    recorded 12:10 as an offer the caller had heard. The caller could then
    accept a slot Susie had, in that same breath, told them she did not have.

    Splitting first means the negation is judged where it applies. A clause
    carrying any availability negator offers nothing; the rest are read as
    before. The DAY is still matched against the whole sentence, because
    "Monday's fully booked, but I have ten past twelve" names the day once and
    in the clause being rejected.

    Residual, recorded rather than assumed: a time named in BOTH a negated and
    an offering clause -- "Monday's eight is gone, but Tuesday has eight in the
    morning" -- is still recorded for both days if both days are named. It is
    bounded by `flatten_bookable_slots`, so the worst case is a slot the
    PAYLOAD says is free and the model says is not, and the payload is the one
    that books.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    out: List[str] = []
    for clause in _CLAUSE_SPLIT.split(text):
        c = str(clause or "").strip()
        if not c:
            continue
        padded = f" {c.lower()} "
        if any(n in padded for n in _NO_SLOT_IN_THIS_CLAUSE):
            continue
        out.append(c)
    return out


def payload_slots_named_in(
    session: Dict[str, Any], text: str
) -> List[Dict[str, Any]]:
    """Which BOOKABLE payload slots does `text` name, by DAY and TIME? PURE.

    Sibling of `accepted_slot_is_named_in`, and the same idea one step wider:
    that one asks "does this sentence name the ONE slot we pinned", this asks
    "which slots does this sentence name at all". Both halves of every
    comparison come from the payload -- the day label and the spoken time the
    offer was read with -- so neither is a match against a phrase written by
    hand, which is the rule `write-gates-match-one-literal` records.

    Why it exists. CA9c39d09f (4 Sep 2026, northgate). The caller asked for
    "around midday, 11 o'clock". The P6 stand-down spoke the model's sentence

        "Monday 7th September -- twenty past eleven in the morning, or ten
         past twelve in the afternoon. Either of those work?"

    and left the record describing the offer BEFORE it: Monday at eight in the
    morning and ten past five in the evening. Both spoken times were real
    payload slots -- 11:20 and 12:10 were in `available_days` -- but neither
    was recorded as heard. So when he said "oh yeah 10 past 12 works",
    `slot_accepted_by_caller` declined, exactly as its own contract says it
    must: it accepts a time "only among times the caller was actually READ".
    Nothing resolved, the read-back guard warned three times, and he hung up
    at the confirmation.

    The DAY is required as well as the time, and that is the safety. A spoken
    label repeats across days -- "eight in the morning" exists on most of them
    -- so matching on time alone would record slots on days the sentence never
    mentioned. Requiring both means a sentence has to name a slot the way a
    person would before it counts.

    Returns [] on anything it cannot read. Declining is free here: the caller
    is left exactly where they were before this existed.
    """
    if not isinstance(session, dict) or not isinstance(text, str) or not text.strip():
        return []
    slots = flatten_bookable_slots(session.get("available_days"))
    if not slots:
        return []

    # B-139: per CLAUSE, so a time Susie has just said she does NOT have is
    # not recorded as one the caller was offered. The day stays whole-sentence
    # -- it is routinely named only in the clause being rejected.
    phrase_times = [_time_norm(c) for c in offer_clauses(text)]
    phrase_times = [pt for pt in phrase_times if pt]
    phrase_day = _readback_norm(text)
    if not phrase_times:
        return []

    out: List[Dict[str, Any]] = []
    for sl in slots:
        label = str(sl.get("spoken") or "").strip()
        if not label:
            continue
        bare = _strip_part_of_day(label)
        named = any(
            _time_named_in(pt, label)
            # P7's allowance: once the day is named, "half past three" is how a
            # person says "half past three in the afternoon".
            or bool(bare and bare != label and _time_named_in(pt, bare))
            for pt in phrase_times
        )
        if not named:
            continue
        day_label = str(sl.get("day_label") or "").strip()
        if day_label:
            day_norm = _readback_norm(day_label)
            if day_norm and day_norm not in phrase_day:
                continue
        out.append(sl)
    return out


# ───────────────────────────────────────────────────────────────────────────
# D8 -- a caller who names an exact time is read the day's default times.
#
# theorem_v3, 7 Sep 2026 21:33, CA7d48a879ed6cb0554a3738dee8941380, judge 3,
# tag `loop`:
#
#     caller : wednesday the 9th of september at 12 pm
#     Susie  : Number 1, ten in the morning. Number 2, eleven in the morning.
#              Number 3, three in the afternoon. And I've a few others that day.
#     caller : no what else have you got that day
#     Susie  : On Wednesday 9th September I also have -- Number 1, midday.
#     caller : number 1 midday
#
# Midday was bookable the whole time. The band filter had already done its job
# -- `_has_explicit_clock` deliberately skips the coarse morning/afternoon band
# so a named time is never FILTERED OUT. But surviving the filter is not being
# SPOKEN: `choose_presented_indices` then applies B-116, "times this caller has
# not heard, chronologically", which has no notion of a time they asked for.
#
# THE PARSER IS THE RISK, NOT THE PIN. This repo's date handling has already
# turned "September 19th" into 19 AUGUST through two independent day-first
# gates, and the corpus is full of callers who name a date and a time in one
# breath -- "monday the 7th at 10 in the morning", "half past 4 on the 24th",
# "the 10th of august at 5 in the evening". A bare number-grab reads the DATE
# as the hour. That is B-126's defect exactly, one layer down: there, "9"
# matched inside "Wednesday the 9th of September" and a guard stood down.
#
# So dates are MASKED OUT before a single digit is read as a time.
# ───────────────────────────────────────────────────────────────────────────

_MONTH_WORD = (
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*"
)

#: Ordinals spelled out. Never a time, always a date or a position, so masking
#: them costs nothing and stops "the ninth" being read as nine o'clock.
_WORD_ORDINAL = (
    r"(?:twenty[-\s]?|thirty[-\s]?)?"
    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
    r"|eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth"
    r"|seventeenth|eighteenth|nineteenth|twentieth|thirtieth)"
)

#: Everything that is a DATE and could be misread as a clock time. Ordered
#: widest-span first: a month with its number goes before the bare month, or
#: masking the month leaves the number behind to be read as an hour.
_DATE_NOISE_RES = [
    re.compile(r"\b\d{1,2}\s*(?:of\s+)?" + _MONTH_WORD + r"\b", re.I),
    re.compile(r"\b" + _MONTH_WORD + r"\s+\d{1,2}\b", re.I),
    re.compile(r"\b\d{1,2}\s*(?:st|nd|rd|th)\b", re.I),
    re.compile(r"\b" + _WORD_ORDINAL + r"\b", re.I),
    re.compile(r"\b" + _MONTH_WORD + r"\b", re.I),
]

_PAST_TO_UNITS = {"half": 30, "quarter": 15}

#: A number followed by one of these is a DURATION, not a clock time. "I've had
#: knee pain for about 3 weeks" is the opening reason on a booking call and the
#: loose "about N" arm below read it as three o'clock. The band words cannot
#: save that one -- "3 weeks" carries none.
_DURATION_UNIT = (
    r"(?:wk|week|day|night|month|yr|year|hour|hr|min|minute|sec|second"
    r"|time|week's|month's|year's)s?\b"
)

_BAND_TESTS = (
    ("morning", lambda h: h < 12),
    ("afternoon", lambda h: 12 <= h < 17),
    ("evening", lambda h: h >= 17),
    ("night", lambda h: h >= 17),
)


def _mask_dates(text: str) -> str:
    """Blank every date-shaped span so no digit in one can be read as an hour.

    Replaced with spaces rather than removed, so nothing that was two words
    becomes one and starts matching across the gap.
    """
    out = text
    for rx in _DATE_NOISE_RES:
        out = rx.sub(lambda m: " " * len(m.group(0)), out)
    return out


def requested_clock_times(text: Any) -> List[str]:
    """Every 24-hour HH:MM the caller might have meant by *text*. PURE.

    Returns candidates, not an answer, and that is deliberate: a bare "at 5"
    is 05:00 or 17:00 and only the diary knows which. The caller of this
    function matches the candidates against the day's REAL bookable times, so
    an impossible reading simply never matches. Where a band word is present
    ("at 5 in the evening") the impossible half is dropped here, because that
    the caller did say.

    Empty for text that names no time at all -- including text that names only
    a date, which is the whole point of `_mask_dates`.
    """
    raw = str(text or "").lower()
    if not raw.strip():
        return []
    masked = _mask_dates(raw)
    t = _fold_clock_words(masked)

    exact: List[int] = []          # minutes-since-midnight, unambiguous
    ambiguous: List[int] = []      # 12-hour readings needing a twin

    def _add(h: int, mm: int, certain: bool) -> None:
        if not (0 <= h <= 23 and 0 <= mm <= 59):
            return
        (exact if certain else ambiguous).append(h * 60 + mm)

    if re.search(r"\b(?:midday|noon)\b", t):
        _add(12, 0, True)
    if re.search(r"\bmidnight\b", t):
        _add(0, 0, True)

    # "half past four", "20 to 10", "quarter past 5" -- folded to digits above.
    for m in re.finditer(
        r"\b(half|quarter|\d{1,2})\s+(past|to)\s+(\d{1,2})\b", t
    ):
        unit_raw, direction, hour_raw = m.group(1), m.group(2), int(m.group(3))
        unit = _PAST_TO_UNITS.get(unit_raw)
        if unit is None:
            try:
                unit = int(unit_raw)
            except ValueError:
                continue
        if not (1 <= unit <= 59) or not (1 <= hour_raw <= 12):
            continue
        if direction == "past":
            _add(hour_raw, unit, False)
        else:
            prev = hour_raw - 1 if hour_raw > 1 else 12
            _add(prev, 60 - unit, False)

    # "3pm", "8 a.m." -- the meridiem settles it outright.
    #
    # BLANKED once read, because every looser arm below also matches a bare
    # digit and "monday at 8 am" would otherwise yield 08:00 AND 20:00 -- the
    # meridiem answering the question and the "at N" arm asking it again. The
    # twin it invents is a real bookable hour on these clinics, so it would
    # pin the wrong slot rather than simply fail to match.
    def _meridiem(m: "re.Match[str]") -> str:
        h = int(m.group(1))
        if 1 <= h <= 12:
            _add(h % 12 + (12 if m.group(2) == "p" else 0), 0, True)
            # "12 am" is MIDDAY far more often than midnight on a clinic line.
            # N2, CA2ac47ad5889388b2974ccf19ee37ff5b (10 Sep 2026 21:11,
            # jv_v1): "um do you have around 12 am is that a slot you have"
            # resolved to 00:00 alone, and because this arm BLANKS the text
            # the looser "around 12" arm below never ran. No clinic opens at
            # midnight, so the candidate matched nothing and the pin failed as
            # silently as if he had named no time at all.
            #
            # The literal reading is KEPT, not replaced. This function returns
            # CANDIDATES and the day's real bookable times decide between
            # them, so a diary that does hold 00:00 is unaffected -- and a
            # caller who means midnight on a clinic line is a caller whose
            # request cannot be served either way.
            if h == 12 and m.group(2) == "a":
                _add(12, 0, True)
        return " " * len(m.group(0))

    t = re.sub(r"\b(\d{1,2})\s*([ap])\.?\s?m\.?\b", _meridiem, t)

    # "12:30", "9.30", and the spoken "6 10" / "7 15" of a digits-only readout.
    for m in re.finditer(r"\b(\d{1,2})\s*[:.]\s*([0-5]\d)\b", t):
        _add(int(m.group(1)), int(m.group(2)), False)
    # "at 7 15", "at 6 10 in the evening" -- a colon-less spoken time. The
    # preposition is REQUIRED, because two bare numbers in a row are far more
    # often something else: on the corpus this arm read "um he's 18 17 i mean
    # he's turning 18 in a couple months" -- an AGE, on the one clinic that has
    # an under-age gate -- as 18:17. The candidate would have matched no real
    # slot and died quietly, which is the design working; it is closed anyway
    # because a rule that is only saved by the diary is a rule waiting for a
    # diary that disagrees.
    for m in re.finditer(
        r"\b(?:at|around|about|near|by|from)\s+(\d{1,2})\s+([0-5]\d)\b(?![:.\d])",
        t,
    ):
        _add(int(m.group(1)), int(m.group(2)), False)

    # "3 o'clock", "at 8", "around 4".
    for m in re.finditer(r"\b(\d{1,2})\s*o'?\s?clock\b", t):
        _add(int(m.group(1)), 0, False)
    for m in re.finditer(
        r"\b(?:at|around|about|near|by|for)\s+(\d{1,2})\b"
        r"(?!\s*[:.]?\s*\d)"
        r"(?!\s*" + _DURATION_UNIT + r")",
        t,
    ):
        _add(int(m.group(1)), 0, False)

    # "as close as possible as 12", "closest to 12", "close to 1".
    #
    # N2, same call, 21:11:55. The arm above requires a preposition from
    # at|around|about|near|by|for and the caller said "as", so "yeah saturday
    # as close as possible as 12 please" named no time at all. 11:45 was
    # bookable, D8 had nothing to pin, and he was read the three times
    # FURTHEST from noon out of the five that day held -- 11:45 and 12:30 both
    # dropped. He asked three times before he was offered it, and booked it.
    #
    # "as" was deliberately NOT added to the preposition list above. On this
    # clinic 18:00 and 20:00 are real bookable times, so "he's as old as 18"
    # would invent a candidate that pins a REAL slot rather than dying
    # quietly -- the same age reading the corpus caught on the "at N N" arm,
    # which is closed above for exactly this reason. What the caller actually
    # said is a PROXIMITY word, so that is what is matched, and the bounded
    # gap keeps the match inside the clause the number is in.
    for m in re.finditer(
        r"\b(?:close|closest|closer|nearest|near)\b[^\d\n]{0,20}?\b(\d{1,2})\b"
        r"(?!\s*[:.]?\s*\d)"
        r"(?!\s*" + _DURATION_UNIT + r")",
        t,
    ):
        _add(int(m.group(1)), 0, False)

    # A band word the caller SAID resolves the 12-hour twin. Only one band may
    # be present -- two is a caller changing their mind mid-sentence and the
    # standing rule in this module for two of anything is to decline.
    named = [(w, fn) for w, fn in _BAND_TESTS if re.search(r"\b" + w, t)]
    #: "evening" and "night" are the same band under two names, so they are one
    #: reading, not two. Anything genuinely contradictory declines.
    band = named[0][1] if len({fn(18) for _, fn in named}) == 1 and named else None

    out: List[str] = []
    seen = set()

    def _emit(mins: int) -> None:
        hh, mm = divmod(mins, 60)
        if hh > 23:
            return
        v = "%02d:%02d" % (hh, mm)
        if v not in seen:
            seen.add(v)
            out.append(v)

    for mins in exact:
        _emit(mins)
    for mins in ambiguous:
        twins = [mins]
        if mins < 12 * 60:
            twins.append(mins + 12 * 60)
        kept = [x for x in twins if band is None or band(x // 60)]
        for x in (kept or twins):
            _emit(x)
    return out


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


#: Set once per availability lookup by the three `check_availability` entry
#: points, from that lookup's own `date_hint`. Written on EVERY lookup, empty
#: included, so a time named three turns ago cannot pin a slot in a readout
#: that has nothing to do with it.
REQUESTED_TIMES_KEY = "_requested_clock_times"


def _pin_requested_time_index(
    session: Dict[str, Any], day: Dict[str, Any], chosen: List[int], limit: int
) -> List[int]:
    """Force the time the caller ASKED FOR back into a readout that dropped it.

    D8, CA7d48a879ed6cb0554a3738dee8941380 (7 Sep 2026, theorem_v3), judge 3,
    tag `loop`. The caller said "wednesday the 9th of september at 12 pm" and
    was read ten, eleven and three -- then had to ask again to be told midday
    existed, which it had all along.

    The band filter was never the problem: `_has_explicit_clock` already skips
    the coarse morning/afternoon band precisely so a named time survives. But
    SURVIVING is not being SPOKEN, and `_choose_presented_times` then applies
    B-116 -- "times this caller has not heard, chronologically" -- which has no
    notion of a time they asked for.

    The same shape as `_pin_accepted_index` above and for the same reason, so
    it is the same kind of wrapper: B-116 is the single owner of "how many, and
    which" for every readout on every clinic, and widening its rule in place is
    how you break a readout nobody was looking at.

    TWO READINGS DECLINE. "at 5" is 05:00 or 17:00 and
    `requested_clock_times` returns both; if the day happens to hold both, this
    cannot know which was meant and pins neither. A band word the caller
    actually said ("at 5 in the evening") has already collapsed the pair
    upstream, so the common case still pins.

    Runs INSIDE `_pin_accepted_index`, never outside it: a slot the caller has
    ACCEPTED outranks a time they merely asked about, so the accepted pin must
    get the last word on what to displace.
    """
    wanted = session.get(REQUESTED_TIMES_KEY) if isinstance(session, dict) else None
    # Type-checked rather than trusted. The key is written by three entry
    # points and read here; "a readout preference must never fail a lookup" is
    # the standing rule in this file, and `x in 12` raises.
    if not isinstance(wanted, (list, tuple, set)) or not wanted or limit < 1:
        return chosen
    times = day.get("slot_times") if isinstance(day, dict) else None
    if not isinstance(times, list) or not times:
        return chosen
    # N-1. Was exact string equality, which on a 50-minute grid meant a caller
    # naming a round hour matched nothing -- see NEAREST_TIME_TOLERANCE_MIN.
    # The decline rules live in `nearest_time_index` now, shared with
    # `resolve_requested_time`, which had the identical bug.
    idx = nearest_time_index(times, wanted)
    if idx is None:
        return chosen
    if idx in chosen:
        return chosen                      # already being spoken
    keep = [i for i in chosen if i != idx][: max(0, limit - 1)]
    out = sorted(set(keep + [idx]))
    logger.info(
        "[slot_followup] pinned the requested time back into the readout -- "
        "the caller asked for %s and B-116 had dropped it (D8). %r -> %r",
        times[idx], chosen, out,
    )
    return out


def _day_iso_of(day: Dict[str, Any]) -> str:
    """The calendar date a day payload is about, "" if it cannot be established.

    `date` is what four readers already treat as the day's identity, so it is
    read first; a payload assembled without it still carries the date on every
    slot start, and a readout preference that fails a lookup is the one thing
    this file will not do.
    """
    if not isinstance(day, dict):
        return ""
    value = str(day.get("date") or "")[:10]
    if len(value) == 10:
        return value
    slots = day.get("slots")
    if isinstance(slots, list):
        for slot in slots:
            start = str((slot or {}).get("start") or "")[:10]
            if len(start) == 10:
                return start
    return ""


def _prefer_unheard_clock_times(
    session: Dict[str, Any], day: Dict[str, Any], chosen: List[int], limit: int,
    also_heard: Any = None,
) -> List[int]:
    """On a day the caller has NOT heard, prefer clock times they have not heard.

    T1, CA5e14516b (9 Sep 2026, northgate, build 8e838f0f), judge 2, tagged
    `dead_end` + `booking_error`:

        caller: "do you have anything wednesday around 12"
        Susie : Wednesday 16th -- eight in the morning / ten past twelve /
                twenty past four
        caller: "what about monday at 12"
        Susie : Monday 14th   -- eight in the morning / ten past twelve /
                twenty past four

    Three times, twice, and the second readout carried no information at all.
    He stopped asking.

    B-116's "already heard" is a set of DATED ISO starts, so `2026-09-16T08:00`
    and `2026-09-14T08:00` are different members of it and its unheard filter
    can never carry across a day boundary: on a fresh day every slot is
    unheard, the pool is the whole day, and `_spread` picks by position. On
    northgate's uniform 50-minute grid the same positions are the same clock
    times, so every day reads identically. PRE-EXISTING -- the raw selection
    was [0, 10, 11] on both days before the D8 pin touched it, so this is not
    the 9 Sep fixes' bill.

    A WRAPPER, not a widening. B-116 is the single owner of "how many, and
    which" for every readout on every clinic and the file's own comment says
    that widening its rule in place is how you break a readout nobody was
    looking at. This is the third wrapper of the same shape, beside
    `_pin_accepted_index` and `_pin_requested_time_index`.

    B-116'S POOL IS NEVER WIDENED. On a day the caller has already heard, the
    candidates here are exactly the slots B-116 would have chosen from -- the
    ones unheard ON THIS DAY -- and this picks among those. A time they have
    already been offered that day can no more come back than it could before.

    S-2, CA8214b75c (10 Sep 2026, northgate). Until then this returned `chosen`
    unchanged for ANY heard day, on the ground that B-116 owned that case. For
    a single day that is right. But a multi-day readout makes every day it
    named a heard day, so from the first readout onwards every day the caller
    could ask about took the early return and the cross-day preference never
    fired again -- exactly when they are comparing days and a repeat is most
    audible. "Twenty to ten" was offered for Monday and again for Tuesday,
    seventeen seconds apart (09:35:46 -> 09:36:03).

    B-116 cannot cover that case by construction: its "already heard" is a set
    of DATED ISO starts, so it subtracts what was heard on THIS day and has no
    notion of a clock time heard on another one. The two rules compose; neither
    replaces the other.

    "SOONER" IS STILL THE OPPOSITE QUESTION. B-137/B-142: a caller who asked
    for the earliest appointment wants the earliest time on every day they are
    offered, repeated clock times and all, so this stands down for them rather
    than pushing the day's real earliest out of the readout.

    `_spread` OUTRANKS THIS. The owner decided on 1 Sep 2026 that two slots
    fifty minutes apart are not a choice a caller experiences as two options,
    and `_spread` exists to make every pair span the day. The first cut of
    this rule filled a short preferred pool back up from the rest of the day,
    which put 08:00 and 08:50 in one breath -- exactly the pairing that
    decision forbids, and two regression tests said so within the minute.

    So the preference is ALL OR NOTHING: it applies only when the unheard
    clock times can fill the readout on their own. When they cannot, B-116's
    selection stands unchanged and a clock time repeats across days. That is
    the lesser harm -- a repeated time is a readout that carries less new
    information, while a bad pair is a readout that offers the caller no real
    choice at all, on a day they can still book either way.
    """
    if not isinstance(session, dict) or not isinstance(day, dict):
        return chosen
    if limit < 1 or not chosen:
        return chosen
    slots = day.get("slots")
    if not isinstance(slots, list) or not slots:
        return chosen
    n = len(slots)
    # The desynchronised-arrays case is B-116's, and its answer there is a
    # chronological readout rather than a cleverer one. Speaking a label from
    # one slot against another's time names an appointment the caller cannot
    # book, so a day that cannot prove its arrays are parallel does not get
    # reordered here either.
    for key in ("slot_times", "slot_times_spoken"):
        value = day.get(key)
        if isinstance(value, list) and len(value) != n:
            return chosen
    if caller_wants_soonest(session):
        return chosen
    try:
        spoken = spoken_starts_for_offer(session)
    except Exception:      # never let a readout fail on its own preference
        return chosen
    today = _day_iso_of(day)
    if not today:
        return chosen
    # S-2. The candidate pool, and the ONE place the two rules meet. On a day
    # already heard it is B-116's own pool -- the slots unheard on this day --
    # so choosing within it cannot hand back a time the caller was offered that
    # day. On a fresh day every slot is a candidate, as T1 had it.
    if today in {str(s)[:10] for s in spoken}:
        pool = [
            i for i, s in enumerate(slots)
            if str((s or {}).get("start") or "")[:19] not in spoken
        ]
        if not pool:
            # Every time on the day has been heard. B-116 answers this
            # chronologically and there is nothing here to prefer.
            return chosen
    else:
        pool = list(range(n))
    heard_clocks = {str(s)[11:16] for s in spoken if len(str(s)) >= 16}
    # T1b. Clock times committed to SIBLING days earlier in this same
    # readout. Nothing has been spoken yet when a multi-day offer is
    # assembled, so `spoken` is empty and cannot carry them -- which is
    # why a first lookup read 08:00 on all three days. Passed in by the
    # loop that owns the readout, because only it knows the order.
    if also_heard:
        heard_clocks = heard_clocks | {
            str(c)[:5] for c in also_heard if str(c)[:5]
        }
    if not heard_clocks:
        return chosen

    def _clock(i: int) -> str:
        try:
            return str((slots[i] or {}).get("start") or "")[11:16]
        except (IndexError, TypeError, AttributeError):
            return ""

    fresh = [i for i in pool if _clock(i) and _clock(i) not in heard_clocks]
    if not fresh:
        # Every clock time available on this day was heard on another one.
        # There is nothing to prefer, and withholding the day would be worse
        # than repeating it.
        return chosen
    if all(_clock(i) not in heard_clocks for i in chosen):
        return chosen      # B-116 already picked clean -- do not disturb it
    if len(fresh) < limit:
        # Not enough unheard clock times to fill the readout on their own.
        # Mixing them with heard ones defeats `_spread` -- see the docstring.
        return chosen
    out = _spread(slots, fresh, limit)
    logger.info(
        "[slot_followup] B-116 had picked %r for %s -- clock times this caller "
        "already heard on another day (T1/S-2). Reading %r instead, chosen "
        "from %d candidate%s; heard clocks %s",
        [_clock(i) for i in chosen], today, [_clock(i) for i in out],
        len(pool), "" if len(pool) == 1 else "s", sorted(heard_clocks),
    )
    return out


def _keep_times_heard_on_named_day(
    session: Dict[str, Any], day: Dict[str, Any], chosen: List[int], limit: int,
) -> List[int]:
    """N1 -- "what about Monday?" hears Monday, INCLUDING the times that made them ask.

    CA12036a4529eaf8e46919432a9ebc1a6a (10 Sep 2026 21:57, northgate), and
    reproduced on demand by CA91d1f12332f6230ed51ad1a427f91f5c (11 Sep 23:10):

        Susie : Monday 14th -- eight in the morning, or ten past five.
                Tuesday 15th -- ten to nine, or twenty past four. ...
        caller: "um what about monday"
        Susie : Monday 14th -- half past ten, twenty past eleven, twenty to three.

    Zero overlap. The two times that made the caller ask about Monday were the
    two the readout guaranteed to withhold. Measured over the corpus: 6 of 6
    re-readouts of an offered day, on every day with slots to spare.

    THE OWNER IS B-116, NOT S-2, and a live log line settled it. B-116's own
    pick was already 08:50/09:40/16:20 -- it subtracts by dated ISO start, so
    the two Monday times from the spread are "heard on this day" and gone
    before `_prefer_unheard_clock_times` is consulted. Reverting S-2 would
    have handed the caller 08:50/09:40/16:20: still zero overlap.

    THREE QUESTIONS, and B-116 cannot tell them apart from the session:

      * "what else have you got?"  -> withhold what they heard. B-116.
      * "anything sooner?"         -> the earliest, repeats and all. B-142.
      * "what about Monday?"       -> the day, offered times included. THIS.

    So the signal is not a session key. Nothing on the session distinguishes a
    day named off the spread from a day named cold, and the fourth wrapper on
    this function is the wrong place to guess. It is the CALLER of this
    function that knows: `speak_one_day_from_payload` is the only producer
    that answers a request about ONE named day, and it passes `named_day=True`.
    Every other reader -- the tool caps, the refusals, "what else on Monday"
    -- passes nothing and is byte-identical to before.

    A WRAPPER, not a rule inside B-116 ("Do not add rules here"). It never
    touches the pool B-116 and S-2 chose from; it re-admits only the times
    this caller was already read ON THIS DAY, and fills the remaining places
    from `chosen` -- which is already clean of cross-day repeats wherever S-2
    could make it so. Nothing can be spoken here that was not either offered
    to this caller for this day, or chosen by the existing rules.

    Deny by default, and every condition declines to `chosen` unchanged:

      * nothing heard on this day -- a day named cold (B-148) has nothing to
        keep, and the existing rules are already right for it;
      * `limit` or more heard on this day -- they have had a full readout of
        it already, so this is closer to "what else" than to "tell me about
        it", and re-reading all of it would carry no new time at all. B-116
        answers that, as it did;
      * every heard time is already in `chosen`;
      * the arrays are not provably parallel (B-116's desync rule).

    WHICH time fills the spare place. A part of the day the kept times do not
    cover, so the readout still spans the day (`_spread`, owner 1 Sep). For a
    caller who asked for the SOONEST it is the earliest instead -- B-142's
    question outranks the spread, exactly as it does one level down.
    """
    if not isinstance(session, dict) or not isinstance(day, dict):
        return chosen
    if limit < 2 or not isinstance(chosen, list):
        return chosen
    slots = day.get("slots")
    if not isinstance(slots, list) or not slots:
        return chosen
    n = len(slots)
    for key in ("slot_times", "slot_times_spoken"):
        value = day.get(key)
        if isinstance(value, list) and len(value) != n:
            return chosen
    try:
        spoken = spoken_starts_for_offer(session)
    except Exception:      # never let a readout fail on its own preference
        return chosen
    if not spoken:
        return chosen

    def _start(i: int) -> str:
        try:
            return str((slots[i] or {}).get("start") or "")
        except (IndexError, TypeError, AttributeError):
            return ""

    heard = [i for i in range(n) if _start(i)[:19] and _start(i)[:19] in spoken]
    if not heard or len(heard) >= limit:
        return chosen
    if all(i in chosen for i in heard):
        return chosen
    rest = [i for i in chosen if isinstance(i, int) and 0 <= i < n and i not in heard]
    need = limit - len(heard)
    if caller_wants_soonest(session):
        fill = rest[:need]
    else:
        covered = {part_of_day(_start(i)) for i in heard}
        uncovered = [i for i in rest if part_of_day(_start(i)) not in covered]
        fill = (uncovered + [i for i in rest if i not in uncovered])[:need]
    out = sorted(set(heard + fill))
    logger.info(
        "[slot_followup] the caller asked about %s, which they were already "
        "offered at %r -- keeping those times in the readout rather than "
        "withholding them (N1). %r -> %r",
        _day_iso_of(day), [_start(i)[11:16] for i in heard],
        [_start(i)[11:16] for i in chosen if isinstance(i, int) and 0 <= i < n],
        [_start(i)[11:16] for i in out],
    )
    return out


def choose_presented_indices(
    session: Dict[str, Any], day: Dict[str, Any], limit: int,
    *, also_heard_clock_times: Any = None, named_day: bool = False,
) -> List[int]:
    """Which positions in a day's parallel slot arrays should be SPOKEN.

    Wrapper, applied outwards: picks by the B-116 rule below; prefers clock
    times unheard when the DAY is one the caller has not heard (T1,
    `_prefer_unheard_clock_times`); when the caller asked about THIS day by
    name, keeps the times they were already offered on it (N1,
    `_keep_times_heard_on_named_day`, only when `named_day` is passed); pins a
    time they asked for (D8, `_pin_requested_time_index`); then pins the slot
    they have ACCEPTED (`_pin_accepted_index`), which gets the last word on
    what to displace.

    `named_day` is passed by ONE caller, `speak_one_day_from_payload`, and
    must stay that way: it is the only reader that knows the caller asked to
    hear a day rather than asked what else there is. See N1's docstring.

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
    chosen = _prefer_unheard_clock_times(
        session, day, _choose_presented_times(session, day, limit),
        limit, also_heard_clock_times,
    )
    if named_day:
        chosen = _keep_times_heard_on_named_day(session, day, chosen, limit)
    return _pin_accepted_index(
        session, day,
        _pin_requested_time_index(session, day, chosen, limit),
        limit,
    )


def _choose_presented_times(
    session: Dict[str, Any], day: Dict[str, Any], limit: int
) -> List[int]:
    """B-116's selection, with B-137's question asked one level down.

    B-142, CA1c6c8360d218 (4 Sep 2026, northgate, build 3868895f). The caller
    opened with "as soon as possible", was offered Saturday 09:00, said "no
    that's not soon enough", and every time in the second offer moved LATER:

        offer 1   Sat 09:00   Mon 08:00   Tue 08:50
        offer 2   Sat 09:50   Mon 08:50   Tue 09:40

    He asked a third time, was told "those are the soonest available", and hung
    up. 09:00 was bookable throughout.

    This is B-137 exactly, one level down, and B-137's fix did not reach here.
    `caller_wants_soonest` was read only by `choose_presented_days`, so the DAY
    order was corrected while the TIME order inside each day stayed inverted:
    the unheard-first rule drops the earliest time PRECISELY BECAUSE it has
    been spoken, and the earliest time is the only one that can answer
    "anything sooner".

    "Sooner" and "what else" are opposite questions. Repeating 09:00 is not
    circular here -- it is the true answer, and it is what he asked for three
    times.

    The unheard filter is dropped, not the spread: `_spread` is a separate
    owner decision (1 Sep) that two slots fifty minutes apart are not a choice,
    and it still holds. Index 0 -- the day's real earliest -- is what `_spread`
    always keeps, which is the whole point here.
    """
    if caller_wants_soonest(session):
        slots = day.get("slots") if isinstance(day, dict) else None
        n = len(slots) if isinstance(slots, list) else 0
        if n and limit > 0:
            logger.info(
                "[slot_followup] caller asked for the soonest "
                "(day_preference=%r) -- reading this day from its earliest "
                "time, not from the ones they have not heard (B-142)",
                session.get("day_preference"),
            )
            return _spread(slots, list(range(n)), limit)
    return _choose_presented_indices_b116(session, day, limit)


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


# ───────────────────────────────────────────────────────────────────────────
# B-137 — "sooner" and "what else" are opposite questions.
#
# `choose_presented_days` below leads with the days the caller has not heard.
# That is right for "what else have you got" and exactly inverted for "anything
# sooner", because the day they HAVE heard is the earliest one -- so the only
# day that could satisfy the request is the only day guaranteed to be dropped.
#
# CA5685a2ab (4 Sep 2026, theorem_v3, Redditch, build 4eda31f3c8c9). Redditch
# runs Thursdays, so the sweep found 10, 17, 24 Sep and 1 Oct. She offered the
# 10th; he said "no i need it as soon as possible i can't wait a week"; and the
# unheard-first rule answered him with the 17th, the 24th and the 1st of
# October. He hung up seven seconds into the readout.
#
# The captured `day_preference` already said "as soon as possible" and nothing
# in the selection read it.
# ───────────────────────────────────────────────────────────────────────────

# Captured `day_preference` values that mean "the earliest you have". Only the
# unambiguous ones: "next week" and a bare weekday scope the caller AWAY from
# today and have their own handling, and "whenever" is the opposite request.
_SOONEST_DAY_PREFERENCES: frozenset = frozenset({
    "as soon as possible", "today", "tomorrow", "tonight", "this week",
})


def day_preference_supersedes(previous: Any, candidate: Any) -> bool:
    """Should a newly heard day preference replace the stored one? PURE.

    ONE DIRECTION: a concrete day replaces a preference that means "soonest";
    nothing replaces a concrete day. Re-arming "soonest" from a later vague
    phrase is what makes readouts lead with the earliest again, which is the
    behaviour this exists to correct.

    Lives here, next to `_SOONEST_DAY_PREFERENCES` and `caller_wants_soonest`,
    because those two are what the rule is stated in -- and because the first
    version of it lived inline in `connection.py` with the tests keeping their
    own copy. That copy stayed green when the rule was neutered, which is the
    failure mode this codebase keeps paying for: a test that exercises a
    reimplementation cannot see the call site change.

    Day-to-day changes deliberately return False. `caller_wants_soonest` is
    false either way, so they reorder nothing; that is a different rule with a
    different blast radius and it is not this one.
    """
    prev = str(previous or "").strip().lower()
    new = str(candidate or "").strip().lower()
    if not new:
        return False
    if not prev:
        return True
    return prev in _SOONEST_DAY_PREFERENCES and new not in _SOONEST_DAY_PREFERENCES


def caller_wants_soonest(session: Dict[str, Any]) -> bool:
    """True when this caller has asked for the earliest appointment available.

    Read from the captured `day_preference` rather than re-parsed from speech:
    the capture happens once, early, in connection.py, and re-deriving it here
    would be a second matcher to keep in step with the first.
    """
    if not isinstance(session, dict):
        return False
    pref = str(session.get("day_preference") or "").strip().lower()
    return pref in _SOONEST_DAY_PREFERENCES


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

    # B-137. "Anything sooner?" is answered by the earliest days there are,
    # even when the caller has already heard them. Repeating the 10th is not
    # circular here -- it is the true answer, and `sparse_rota_note` is the
    # sentence that says WHY it is repeated. Withholding it to lead with three
    # later days is what lost CA5685a2ab.
    if caller_wants_soonest(session):
        logger.info(
            "[slot_followup] caller asked for the soonest (day_preference=%r)"
            " -- leading with the %d earliest of %d days, not the unheard ones",
            session.get("day_preference"), min(max_days, len(days)), len(days),
        )
        return list(days)[:max_days]

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


_SPARSE_ROTA_LEAD = "The only days I've got at {loc} are {days}."
_SPARSE_ROTA_OFFER = "{other} runs {n} days a week. Shall I check there instead?"


def _weekday_phrase(names: List[str]) -> str:
    """['Thursday'] -> 'Thursdays'; ['Monday','Thursday'] -> 'Mondays and Thursdays'."""
    plural = [f"{n}s" for n in names]
    if not plural:
        return ""
    if len(plural) == 1:
        return plural[0]
    return ", ".join(plural[:-1]) + " and " + plural[-1]


def acknowledge_sparse_rota(text: str, note: Any) -> Tuple[str, str]:
    """Say WHY the soonest is this far out, and offer the busier clinic.

    Returns `(text, action)`; action is "unchanged" or "applied".

    B-137, the sentence half. The selection fix above makes her read the
    EARLIEST days to a caller who asked for the soonest -- which, when they
    have already heard those days, is the same list twice. Repeating a list
    unexplained is what "going in circles" sounds like. The explanation is the
    thing that makes the repeat honest:

        "The only days I've got at Redditch are Thursdays."
        "Number 1, Thursday 10th September - nine in the morning, or one..."
        "Alcester runs five days a week. Shall I check there instead?"

    Two claims, two different sources, deliberately:

      * The LEAD is about slots RETRIEVED -- the weekdays actually present in
        this payload. It is never read from configured opening hours, because
        Theorem's config says Redditch opens Mondays and Thursdays while every
        Monday in the 30-day sweep came back empty. Saying "we're only open
        Thursdays" would have been false; saying "the only days I've GOT are
        Thursdays" is what the diary supports.

      * The OFFER is about the ROTA, never about slots. "Alcester runs five
        days a week" is a fact from `location_working_hours`. "I have something
        sooner at Alcester" would be a promise about a calendar nobody has
        queried, and the caller most motivated to be disappointed by it is
        exactly this one. So it ends in a question, and the answer to that
        question is what triggers the lookup.

    Sentence only. It must never change which days or times were chosen, and
    there is a test that fails if it does.

    Idempotent: a re-flush of the same buffer must not stack either half.
    """
    _t = (text or "").strip()
    if not _t or not isinstance(note, dict):
        return text, "unchanged"

    loc = str(note.get("location_label") or "").strip()
    days = str(note.get("open_days_phrase") or "").strip()
    other = str(note.get("other_location_label") or "").strip()
    n_word = str(note.get("other_open_days_word") or "").strip()
    if not (loc and days and other and n_word):
        return text, "unchanged"

    lead = _SPARSE_ROTA_LEAD.format(loc=loc, days=days)
    offer = _SPARSE_ROTA_OFFER.format(other=other, n=n_word)

    low = _t.lower()
    if lead.lower() in low or offer.lower() in low:
        return text, "unchanged"

    return f"{lead} {_t} {offer}", "applied"


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


def append_other_dates_offer(text: str, other_dates, also_named=None) -> Tuple[str, str]:
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

    `also_named` is what the reply NAMED, when the caller knows that better
    than the prose does. The dedupe below reads the rendered sentence, so it
    silently stops working the moment a producer words a date differently
    from the payload label -- which S-1(a) then did: a multi-day readout says
    "Tuesday the 15th" from day two onward, the haystack no longer contains
    "Tuesday 15th September", and Susie offered "another Tuesday, the 15th"
    one sentence after reading it out. Caught by
    `test_a_date_already_named_in_an_earlier_chunk_is_not_said_twice`.

    So the caller passes its RECORD of the days it named and the dedupe stops
    depending on wording. Optional, and absent it behaves exactly as before.

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

    # Already named by the reply itself: say nothing twice. Matched against
    # the sentence AND against what the caller says it named -- see the
    # docstring; the sentence alone is a wording dependency, not a record.
    _hay = [text.lower()]
    if isinstance(also_named, (list, tuple, set)):
        _hay.extend(str(n).lower() for n in also_named if str(n or "").strip())
    if any(s.lower() in h for s in spoken for h in _hay):
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


def _requested_clock_times_safe(text: Any) -> List[str]:
    """`requested_clock_times`, never raising: this sits on the hot path."""
    try:
        return list(requested_clock_times(text) or [])
    except Exception:
        logger.exception("[slot_followup] requested_clock_times failed")
        return []


def format_time_available_speech(
    slot: Dict[str, Any], asked: Optional[List[str]] = None
) -> str:
    """The one-time answer to a caller who named a time.

    DT-8's "and says so": when the time being offered is not the one asked
    for -- "around 12" against a grid that holds 12:10 -- the sentence names
    the caller's time and says this is the nearest. "Yes — ten past twelve is
    free" to a caller who said "twelve" invites "I said twelve". The caller's
    own time is speakable by the guard's rules (`note_caller_speech`); the
    offered one is real by construction. `asked` is HH:MM candidates from
    `requested_clock_times`, so a 12-hour twin ("2" -> 02:00/14:00) that
    matched exactly still reads as a plain yes.
    """
    day = slot.get("day_label") or "that day"
    spoken = slot.get("spoken") or slot.get("time") or "that time"
    got = str(slot.get("time") or "")
    if asked and got and got not in asked:
        try:
            from app.tools.receptionist_tools import _spoken_slot_time
            # The candidate on the same side of noon as the slot, else the
            # first -- "two" asked, 14:40 offered, reads back "two", not "two
            # in the morning".
            _same = [a for a in asked if (int(a[:2]) >= 12) == (int(got[:2]) >= 12)]
            _asked_spoken = _spoken_slot_time((_same or asked)[0])
            return (
                f"The nearest I've got to {_asked_spoken} is {spoken} on {day}. "
                f"Shall I book that in for you?"
            )
        except Exception:
            logger.exception("[slot_followup] nearest phrasing failed")
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
    asked: Optional[List[str]] = None,
) -> str:
    """Present the resolved unspoken time as the current offer / selection.

    `asked` is what the caller said, as HH:MM candidates, so the sentence can
    say "nearest" when it is not the same time (DT-8). Optional, and every
    pre-existing caller passes nothing, which keeps their sentence unchanged.
    """
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
    return format_time_available_speech(slot, asked=asked)


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
            if int(day.get("times_not_shown") or 0) != 0:
                return False
            # PRESENTED IS NOT BOOKABLE. `times_not_shown` answers "did a
            # time-of-day band filter this day?" -- it does NOT answer "how
            # much of this day did the caller actually hear", and this
            # sentence is a claim about the second.
            #
            # The readout trims to two times per day on a multi-day offer and
            # `available_days` keeps the FULL day, so the two numbers come
            # apart on every multi-day call. MEASURED 4 Sep 2026 over every
            # stored slot_offer: 102 of 102 day-entries held bookable times
            # the caller never heard, and `times_not_shown` read 0 on all 102.
            # Monday 7 September was twelve bookable, two spoken, and this
            # function said the day was finished.
            #
            # The honest measure is SPOKEN versus BOOKABLE, which the spoken
            # record already holds. Fails CLOSED, like everything else here:
            # an untrusted or empty record leaves every time unheard and the
            # claim unmade. B-95's presented-vs-bookable split, reaching the
            # one predicate whose whole job is to be positive proof.
            heard = spoken_starts_for_offer(session)
            unheard = [
                slot for slot in flatten_bookable_slots([day])
                if str(slot.get("start") or "")[:19] not in heard
            ]
            if unheard:
                logger.info(
                    "[slot_followup] exhaustion claim DECLINED for %s -- the "
                    "band hid nothing, but %d of %d bookable times were never "
                    "spoken. times_not_shown says how much a FILTER removed, "
                    "not how much the caller heard.",
                    want, len(unheard), len(day.get("slot_times") or []),
                )
                return False
            return True
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

    # THE UNHEARD DAYS, SELECTED HERE RATHER THAN BY `choose_presented_days`.
    #
    # D12, 9 Sep 2026, northgate, build 909a90ad3752. That helper answers a
    # different question -- which days to LEAD a fresh readout with -- and it
    # short-circuits on `caller_wants_soonest` to `days[:max_days]`, the three
    # EARLIEST days. For a caller who asked for the soonest, those are exactly
    # the three they have just heard, so the unheard filter below emptied the
    # list and this producer declined for the rest of the call:
    #
    #     caller: uh what's the soonest you've got
    #     Susie : Starting with the soonest -- Number 1 Wednesday, 2 Thursday,
    #             3 Friday ...
    #     caller: okay then what else have you got this week
    #     [slot_followup] every day in the sweep has been offered -- falling
    #                     through
    #     Susie : this week I've also got Thursday at eight in the morning, or
    #             ... Friday at eight in the morning, or half past three ...
    #
    # -- Thursday and Friday read straight back, which is the going-in-circles
    # shape B-137 and D11 both exist to end.
    #
    # LATENT UNTIL 38709d5f. `day_preference` was only ever set by the literal
    # "as soon as possible"/"asap", so `caller_wants_soonest` was almost always
    # False here and the helper fell to its unheard branch. Teaching the capture
    # the words people actually use made the short-circuit reachable, so this is
    # that commit's bill and it is paid here rather than by narrowing it: the
    # soonest ordering is RIGHT on a fresh readout and wrong only in this
    # producer, where the question is "what have I NOT heard".
    #
    # `choose_presented_days`' own unheard branch returns `unoffered[:max_days]`
    # -- which is what this now does directly, minus the short-circuit above it.
    try:
        spoken = spoken_starts_for_offer(session)
    except Exception:
        return None
    def _heard(day: Any) -> bool:
        return any(
            str((sl or {}).get("start") or "")[:19] in spoken
            for sl in ((day or {}).get("slots") or [])
        )
    _unheard = [d for d in days if not _heard(d)]
    #: D10, this producer's copy. `fresh` below is capped to
    #: `_MAX_PRESENTED_DAYS`, so `build_slot_offer`'s own "did I drop any days"
    #: test is blind here in exactly the way it is on the live readout path.
    #: Counted over the WHOLE payload, because "what else have you got" is a
    #: question about the diary, not about the three days this answer names.
    _unheard_total = len(_unheard)
    fresh = _unheard[:_MAX_PRESENTED_DAYS]
    if not fresh:
        logger.info(
            "[slot_followup] 'what else' after a multi-day readout, but every "
            "day in the sweep has been offered -- falling through"
        )
        return None

    presented: List[Dict[str, Any]] = []
    # T1b's twin in this producer. Same reason as `_cap_presented_slots`:
    # the days are picked in a loop against one spoken record, so without
    # this every day of a "what else have you got" answer opens at the
    # same clock time.
    _clocks_used: set = set()
    for day in fresh:
        trimmed = dict(day)
        idx = choose_presented_indices(
            session, trimmed, _MAX_PRESENTED_TIMES_MULTI_DAY,
            also_heard_clock_times=_clocks_used,
        )
        for _i in idx:
            try:
                _clocks_used.add(
                    str(((trimmed.get("slots") or [])[_i] or {}).get("start"))[11:16]
                )
            except (IndexError, TypeError, AttributeError):
                pass
        for key in ("slot_times", "slot_times_spoken", "slots"):
            if isinstance(trimmed.get(key), list):
                trimmed[key] = pick_by_index(trimmed[key], idx)
        presented.append(trimmed)

    try:
        offer = build_slot_offer(
            presented, more_days=_unheard_total > len(presented),
        )
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
    # S-14. `record_offer` had ONE call site -- Gate 5 -- so the offer corpus
    # held only the offers the MODEL's tool turn produced. On
    # CAb8ac636017de7d35370fd7951c54d3cf, 4 of the 5 readouts left no row, and
    # they were the four that exposed S-13. Every harness reading
    # `calls.slot_offers` was measuring the Gate 5 path and calling it the
    # system. Recorded here, beside the `apply_offer_to_session` that already
    # owns "anything that speaks an offer calls this", because a producer that
    # SPEAKS and does not RECORD is the same class of omission one layer out.
    #
    # `presented` is passed from this producer rather than derived, because
    # only the producer knows which days it trimmed -- that gap between payload
    # and presented IS B-95's split, and a row that guessed it would be worse
    # than no row. Never raises; `record_offer` swallows everything itself.
    # S-7: born spoken. This records BESIDE `apply_offer_to_session`, on the
    # path that says the sentence -- unlike gate5, which records where the
    # offer is BUILT and may still stand it down.
    _rec_offer(session, source="producer", spoken=True,
               payload_days=session.get("available_days"),
               offer=offer, presented_days=presented)
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
        #
        # S-5: `pretrimmed=False` says the same thing to the contract check,
        # and it is the ONE producer entitled to say it. `batch` is what
        # `all_remaining_on_next_day` / `next_slot_batch` left -- the slots
        # this caller has NOT been read -- so B-116's subtraction has already
        # happened upstream and the only question left is which three of the
        # remainder, which is `_pick_times_for_day`'s to answer. Without this
        # the check would fire on every "tell me the others" turn and the
        # warning would be noise instead of a finding.
        offer = build_slot_offer(
            [day], lead_in="also", more_times=True if more else None,
            pretrimmed=False,
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
    # S-14, and the one producer whose `presented` is the WHOLE day on purpose:
    # `pretrimmed=False` above says the batch arrived already subtracted, so the
    # payload/presented gap here is genuinely nil and the row must say so.
    # S-7: born spoken. This records BESIDE `apply_offer_to_session`, on the
    # path that says the sentence -- unlike gate5, which records where the
    # offer is BUILT and may still stand it down.
    _rec_offer(session, source="producer", spoken=True,
               payload_days=session.get("available_days"),
               offer=offer, presented_days=[day])
    logger.info(
        "[slot_followup] 'more times that day' answered with %d numbered "
        "option(s) of %d remaining on %s (more=%s)",
        len(offer.slots), len(batch), date, bool(offer.more_times),
    )
    return offer.text


def named_day_speech(
    session: Dict[str, Any], user_text: str
) -> Optional[str]:
    """Answer "what about Wednesday" from the PAYLOAD, not from the model. Or None.

    The third producer, and it exists for the same reason as the second.

    D-B, `CA90ccb117`, northgate, 2026-09-03 15:41. Susie read a three-day
    offer. The caller said "uh yeah check for tuesday please". The head fired
    correctly -- "Let me have a look at Tuesday for you --" -- and then **no
    `check_availability` call ran at all**. The model answered from the offer
    already in its context and said:

        "That day I've got ten to nine in the morning, or ten past five in the
         evening -- which suits?"

    Those are exactly the two Tuesday slots it had already read out. Tuesday's
    payload held twelve. Three failures from one missing tool call:

      1. the head promised a lookup that never happened -- the promised-work
         defect arriving from the opposite direction to every previous
         instance: not a head in front of no work, but no work behind a
         justified head;
      2. the caller was re-read 2 of 12 and told nothing else existed;
      3. `v3_dtmf_slot_map` still held three DAYS while she had just spoken two
         TIMES, so pressing 1 would have picked Monday. Speech and record
         disagreed for the rest of the call.

    `calls.slot_offers` recorded ONE entry for that call. The Tuesday reply
    went through no producer, so every guard downstream was reading a record
    nobody had written.

    THE FIX IS NOT TO MAKE THE MODEL CALL THE TOOL. That is trigger-side, and
    this codebase has been wrong in that direction three times (B-107's
    "undecidable at the partial", Option 5 on B-127, Option 4 deferred). It is
    also unnecessary: the payload is already on the session, so the honest
    answer costs no tool call at all -- which is the same argument
    `more_days_speech` makes, and this is that function with the scope
    inverted. There it is the days he has NOT heard; here it is the one day he
    just named.

    Deny by default, and every step can decline:

      1. a multi_day offer only. On single_day the day under discussion is
         already the only one, `remaining_unspoken_on_current_day` owns it, and
         re-reading it here would repeat the offer;
      2. the caller names exactly ONE day, resolved against the OFFER rather
         than the calendar -- `day_named_by_caller` for the full label,
         `_offered_day_by_weekday` for a bare weekday;
      3. that day is in the payload and holds bookable times;
      4. it is not a "more slots" or "different day" request -- those have
         their own paths and their own answers, and this must not steal them;
      5. it is not a TIME pick. "Tuesday at ten past five" is an acceptance,
         `slot_accepted_by_caller` owns it, and answering it with a readout
         would talk over a caller who has already chosen.

    Speaks AND records, through the same two functions the primary readout
    uses -- `build_slot_offer` for the words, `apply_offer_to_session` for
    every record of them, including the keypad map. There is no third way to
    put an offer on the table, which is the entire point of the convergence
    plan.

    Never raises: a producer fault must leave the caller with the model's
    answer, which is what they got before this existed.
    """
    try:
        if str(session.get("_slot_presentation_mode") or "") != "multi_day":
            return None
        if utterance_requests_more_slots(user_text):
            return None
        if utterance_requests_different_day(user_text):
            return None
        if _DAY_REFUSE_RE.search(user_text or ""):
            return None
        # B-147, CAdf1e02ca, northgate, 2026-09-06 10:15:06. The caller said
        # "monday doesn't work" and was read Monday's times:
        #
        #     'Monday 7th September' answered from the payload -- 3 of 17
        #     bookable times spoken ... (D-B)
        #
        # Naming a day is not the same as ASKING about it, and this producer
        # answers requests. `utterance_requests_different_day` returns False
        # for that sentence -- it looks for "another day", not for a refusal --
        # so the day resolved and the readout ran. The acceptance door already
        # declines here (`_DAY_REFUSE_RE`, same commit family); the REQUEST
        # door had no such guard, and it is the louder of the two: it does not
        # merely mis-acknowledge, it reads out the day the caller just ruled
        # out. Same wholesale rule, same reason -- a refusal anywhere in the
        # utterance declines, and `try_unspoken_followup_speech` answers it
        # with the days he has NOT heard instead.

        days = session.get("available_days")
        if not isinstance(days, list) or not days:
            return None

        # A caller naming a TIME has picked, not asked. Let the resolver own it.
        if slot_accepted_by_caller(session, user_text):
            return None

        # And a caller ACCEPTING a day has picked too. "yeah monday works" is
        # not "what about monday", and the difference is the whole subject of
        # `day_accepted_by_caller` -- deny-by-default on both sides, built for
        # exactly this line on 2026-09-03 and verified on a live call the same
        # day ("Monday it is -", 13:07:12).
        #
        # Intercepting it here would be a REGRESSION of that verified path: the
        # caller would be read a list instead of acknowledged, and the
        # SLOT_PICKED head would never fire. This producer answers REQUESTS.
        # What happens after an acceptance is already settled and is not ours
        # to change.
        if day_accepted_by_caller(session, user_text):
            return None

        # WHICH day, resolved against the offer. Full label first, then a bare
        # weekday against the days actually read out -- the same ladder
        # `slot_accepted_by_caller` uses, and for the same reason: resolving a
        # weekday against the calendar is date parsing and needs its own corpus.
        named = day_named_by_caller(days, user_text)
        date = named.get("date") if isinstance(named, dict) else named
        if not date:
            date = _offered_day_by_weekday(
                session.get("last_offered_slots") or [], user_text
            )
        if not date:
            # B-148. The two steps above resolve against what was READ OUT, and
            # once the conversation narrows to one day that is every other day
            # the clinic has. A REQUEST is not a pick: "check for Tuesday" names
            # a day the caller wants looked at, not one they are choosing from,
            # so the candidate set is the payload their own lookup returned.
            date = _payload_day_by_weekday(days, user_text)
        if not date:
            return None

        return speak_one_day_from_payload(
            session, days, date, why="D-B", user_text=user_text,
        )
    except Exception:  # pragma: no cover - defensive; live call path
        logger.exception("[slot_followup] named-day offer unavailable")
        return None


def speak_one_day_from_payload(
    session: Dict[str, Any],
    days: Any,
    date: str,
    *,
    why: str,
    user_text: Any = None,
) -> Optional[str]:
    """Narrow the conversation to ONE day, speaking and recording it. Or None.

    Extracted from `named_day_speech` on 2026-09-06 because a SECOND caller
    turn needs exactly this and copying it would have made two answers to
    "what did she just offer?" -- the failure `apply_offer_to_session` was
    itself extracted to prevent.

    `build_slot_offer` for the words, `offer_as_record` + `apply_offer_to_
    session` for every record of them, including the keypad map. `day_iso` is
    passed because this NARROWS the conversation to a single day, and the
    anchor must move with it or the next follow-up scopes to the old day.

    `why` names the caller turn in the log line, so the two producers stay
    distinguishable in a Render log without being distinguishable in code.
    """
    day = next(
        (d for d in (days or [])
         if isinstance(d, dict) and d.get("date") == date), None
    )
    if not day or not (day.get("slot_times") or []):
        return None

    from app.tools.slot_offer import (
        SINGLE_DAY_MAX_TIMES, apply_offer_to_session, build_slot_offer,
        offer_as_record,
    )

    # WHICH times, not just how many. T1b, CAfb09f66e (10 Sep 2026,
    # northgate, build 8ed9e195), judge 2, outcome=abandoned:
    #
    #     Susie : Monday 14th / Tuesday 15th / Wednesday 16th -- eight in
    #             the morning, or ...            [the multi-day offer]
    #     caller: "um yeah can you tell me about monday"
    #     Susie : Monday 14th -- eight in the morning, one in the
    #             afternoon, ten past five in the evening
    #     caller: "and what about tuesday"
    #     Susie : Tuesday 15th -- eight in the morning, ten past twelve,
    #             twenty past four
    #
    # He heard "eight in the morning" four times in one call and hung up.
    # Both Monday times and both Tuesday times he was re-read had been
    # spoken to him thirty seconds earlier in the multi-day offer.
    #
    # This handed `build_slot_offer` the WHOLE day, and that function's own
    # docstring states the contract being broken: "PASS `more_times` when
    # the days handed in have ALREADY been trimmed to what should be
    # spoken. `_cap_presented_slots` selects those positions through
    # `choose_presented_indices`, which prefers times this caller has not
    # heard (B-116) -- knowledge this function does not have and must not
    # overrule." It was never given that trim, so it fell back to the
    # chronological head of the day, which is precisely the slice B-116
    # exists to replace.
    #
    # The blast radius was every named-day answer on every clinic -- both
    # callers of this function, D-B and B-145 -- and it took the D8
    # requested-time pin down with it, since that also lives inside
    # `choose_presented_indices`. A caller who named a day AND a time was
    # read neither preference.
    #
    # `more_times` must be passed explicitly now, for the reason the
    # docstring gives: a pre-trimmed day looks complete to the formatter,
    # and it would fall silent about the rest of the diary (B-97).
    # S-13, CAb8ac636017de7d35370fd7951c54d3cf (10 Sep 2026 13:43, northgate,
    # build 9259595f50f5), judge 1, outcome=abandoned. "do you have anything
    # around midday on tuesday" was answered with 08:00, 09:40 and 15:30; he
    # rephrased and was answered with three more times, none near noon; 12:10
    # and 13:00 had been read to him sixteen seconds earlier and were bookable
    # throughout. He hung up.
    #
    # D8 reads ONE key and all three of its writers are inside
    # `check_availability`. This path runs no tool -- the log line below says
    # "no tool call needed" -- so nothing here had ever written the caller's
    # own words into it, and the pin was a no-op on every named-day follow-up
    # in the system's life. Its log line has never appeared in a stored call.
    #
    # WRITTEN ON EVERY PAYLOAD TURN, EMPTY INCLUDED, for the reason the key's
    # own docstring gives one layer up: partial writing re-creates the
    # staleness it guards against, and a time named three turns ago must not
    # pin a slot in a readout that has nothing to do with it.
    # `requested_clock_times` is the same parser the tool path uses -- a second
    # one would be a second thing to keep in step with it.
    try:
        session[REQUESTED_TIMES_KEY] = requested_clock_times(user_text)
    except Exception:  # pragma: no cover - defensive; live call path
        # The standing rule in this file: a readout preference must never fail
        # a lookup. A dead pin reads the day as it did before D8 existed.
        logger.exception("[slot_followup] requested-time resolve failed")

    _times = list(day.get("slot_times") or [])
    # N1. This producer answers a request about ONE named day -- both of its
    # callers, D-B's "what about Monday" and B-145's "yeah Monday works" -- so
    # it is the one reader that may say so. The times the caller was already
    # read for this day are what made them ask; withholding every one of them
    # is the defect. See `_keep_times_heard_on_named_day`.
    _idx = choose_presented_indices(
        session, day, SINGLE_DAY_MAX_TIMES, named_day=True,
    )
    _spoken_day = dict(day)
    for _key in ("slot_times", "slot_times_spoken", "slots"):
        if isinstance(_spoken_day.get(_key), list):
            _spoken_day[_key] = pick_by_index(_spoken_day[_key], _idx)
    _more = (
        len(_spoken_day.get("slot_times") or []) < len(_times)
        or int(day.get("times_not_shown") or 0) > 0
    )

    offer = build_slot_offer([_spoken_day], more_times=_more)
    if offer is None or not offer.chunks:
        return None

    apply_offer_to_session(
        session, offer_as_record(offer, day_iso=date), offer.chunks
    )
    # S-14. `record_offer` had ONE call site -- Gate 5 -- so the offer corpus
    # held only the offers the MODEL's tool turn produced. On
    # CAb8ac636017de7d35370fd7951c54d3cf, 4 of the 5 readouts left no row, and
    # they were the four that exposed S-13. Every harness reading
    # `calls.slot_offers` was measuring the Gate 5 path and calling it the
    # system. Recorded here, beside the `apply_offer_to_session` that already
    # owns "anything that speaks an offer calls this", because a producer that
    # SPEAKS and does not RECORD is the same class of omission one layer out.
    #
    # `presented` is passed from this producer rather than derived, because
    # only the producer knows which days it trimmed -- that gap between payload
    # and presented IS B-95's split, and a row that guessed it would be worse
    # than no row. Never raises; `record_offer` swallows everything itself.
    # S-7: born spoken. This records BESIDE `apply_offer_to_session`, on the
    # path that says the sentence -- unlike gate5, which records where the
    # offer is BUILT and may still stand it down.
    # `producer=why` so the replay harness can tell a named-day readout from
    # every other producer row and replay it with `named_day=True`. Without it
    # the harness replays these rows down the "what else" path and reports
    # UNCHANGED for exactly the readouts N1 changes -- a blind harness that
    # reads green. Forward-only: rows before this commit carry no producer.
    _rec_offer(session, source="producer", spoken=True, producer=why,
               payload_days=session.get("available_days"),
               offer=offer, presented_days=[_spoken_day])
    logger.info(
        "[slot_followup] '%s' answered from the payload -- %d of %d bookable "
        "times spoken, offer and keypad recorded, no tool call needed (%s)",
        day.get("day_label") or date,
        len(offer.slots), len(day.get("slot_times") or []), why,
    )
    return offer.text


def _acknowledge_day_pick(session: Dict[str, Any], user_text: str) -> str:
    """"Monday it is -", or "" when this clinic does not do hold speech.

    The head normally fires inside `llm_stream`'s streaming call. This producer
    answers BEFORE that call and returns, so the head has to be spoken here or
    not at all -- and losing it is exactly the regression `named_day_speech`'s
    own comment refuses to cause. Never raises: an acknowledgement is a nicety
    and the offer behind it is the answer.
    """
    try:
        from app.hold_speech import (
            Intent, hold_speech_enabled, render_intent_head, subject_for,
        )
        if not hold_speech_enabled(session):
            return ""
        subject = subject_for(user_text)
        if not subject:
            return ""
        return render_intent_head(
            Intent.SLOT_PICKED,
            subject=subject,
            index=len(session.get("used_fillers") or []),
            avoid=str(session.get("last_bot_prompt") or ""),          # D5
        )
    except Exception:  # pragma: no cover - defensive
        return ""


def day_acceptance_speech(
    session: Dict[str, Any], user_text: str
) -> Optional[str]:
    """Acknowledge an accepted DAY and put that day's offer on the table. Or None.

    B-145, `CAa0389cae`, northgate, 2026-09-05 23:10. Susie read a three-day
    offer. The caller said "oh yeah monday works". The head was right --
    `situational head (slot_picked): 'Monday it is -'` -- and then the MODEL
    narrowed the day in prose:

        "that day I've got eight in the morning or ten past five in the evening"

    Those are the two Monday slots it had already read out. Monday's payload
    held TWELVE. One missing producer, four consequences, all on the same turn:

      1. 2 of 12 times spoken, and no lookup behind the acknowledgement;
      2. no "and I've a few others that day" tail -- that tail is single_day
         only by construction (`slot_offer.py`, the B-97/B-99 comment), and no
         single_day offer was ever built, so it could not be said;
      3. `v3_dtmf_slot_map` still held three DAYS, so pressing 1 would have
         re-picked Monday rather than a time;
      4. and the NEXT turn broke on the same state. "um 10 past 5 in the
         evening suits" could not be resolved, because `slot_accepted_by_
         caller`'s lone-date branch requires the offer to hold exactly ONE
         date and three were still on it -- so `_hs_picking` stayed false and
         a TIME_BAND head promised a lookup in front of a confirmation.

    `named_day_speech` declines an acceptance on purpose, and that stays right:
    a caller who accepts must be ACKNOWLEDGED, not read a list as though they
    had asked a question. This is the other half of that decision rather than a
    reversal of it -- the acknowledgement leads, and the day behind it comes
    from the producer instead of from the model.

    Deny by default, and every step can decline:

      1. a multi_day offer only. On single_day the accepted day is already the
         only one and re-reading it would repeat the offer;
      2. NOT a time pick. "Tuesday at ten past five" is a slot acceptance,
         `slot_accepted_by_caller` owns it, and narrowing a day underneath a
         caller who has chosen a time would talk over them;
      3. `day_accepted_by_caller` resolves the day, and it is deny-by-default
         on both sides already -- a request ("what about Monday") resolves to
         None here and is answered by `named_day_speech` above;
      4. the day is in the payload and holds bookable times.

    Never raises: a producer fault must leave the caller with the model's
    answer, which is what they got before this existed.
    """
    try:
        if str(session.get("_slot_presentation_mode") or "") != "multi_day":
            return None

        days = session.get("available_days")
        if not isinstance(days, list) or not days:
            return None

        # A caller who named a TIME has picked a SLOT, not a day.
        if slot_accepted_by_caller(session, user_text):
            return None

        date = day_accepted_by_caller(session, user_text)
        if not date:
            return None

        offer = speak_one_day_from_payload(
            session, days, date, why="B-145", user_text=user_text,
        )
        if not offer:
            return None

        head = _acknowledge_day_pick(session, user_text)
        return f"{head} {offer}".strip() if head else offer
    except Exception:  # pragma: no cover - defensive; live call path
        logger.exception("[slot_followup] day-acceptance offer unavailable")
        return None


#: D11 -- a caller pushing back on the offer because it is not soon enough.
#:
#: 9 Sep 2026, northgate, build 38709d5fbecb. She read "Starting with the
#: soonest -- Number 1, Wednesday 9th September -- half past three in the
#: afternoon, or ten past five", and the caller said "um that's not soon
#: enough". It was 13:43, so half three TODAY was the first slot in the diary
#: and there was genuinely nothing before it. The reply repeated the same two
#: times with no acknowledgement:
#:
#:     "I've got today -- Wednesday the 9th of September -- at half past three
#:      in the afternoon, or ten past five. Do either of those work?"
#:
#: The content was right and the sentence that makes it honest was missing --
#: the same defect shape as B-137's, one turn later. B-137 fixed WHICH slots a
#: push-back gets; nothing ever said "this already is the earliest".
#:
#: Not caught by `utterance_requests_more_slots`: its signals are "later",
#: "else", "other", "another", "instead" -- every one of them a request to move
#: AWAY, and this caller is asking to move nearer. So the turn fell through to
#: the model, which cannot know it is looking at the whole diary.
_SOONER_REQUEST_RE = re.compile(
    r"(?:\bnot|n't)\s+soon\s+enough\b"
    r"|\b(?:any|anything|something|nothing|owt)\s+(?:sooner|earlier)\b"
    r"|\b(?:sooner|earlier)\s+than\b"
    # "before that" needs a determiner in front of it. Bare, it swallows
    # narrative -- "before that I had physio elsewhere" is a caller answering
    # the reason question, not pushing back on an offer.
    r"|\b(?:any|anything|nothing|owt|something)\s+before\s+(?:that|then|it)\b"
    r"|\b(?:too|so)\s+(?:far|long)\s+(?:away|off|out)\b"
    # "can't wait" only as impatience, never as anticipation. "I can't wait to
    # get this sorted" is a caller being NICE about an offer they have just
    # accepted, and answering it with an apology reads as not listening.
    r"|\bcan'?t\s+wait\b(?!\s+to\b)"
    r"|\bneed\s+(?:it\s+|to\s+be\s+seen\s+)?(?:something\s+)?(?:sooner|earlier)\b"
    r"|\b(?:got|have)\s+(?:anything\s+|owt\s+)?(?:sooner|earlier)\b"
)


def utterance_asks_for_something_sooner(text: Any) -> bool:
    """True when the caller is pushing for an EARLIER slot than the one offered.

    Distinct from `utterance_requests_more_slots`, which means "show me
    something else" and is answered with different slots. This one can only be
    answered with the truth about the earliest slot there is -- and when that
    slot has already been spoken, the truth is that there is nothing sooner.

    Bare "sooner"/"earlier" must sit in a request frame. A caller saying "the
    earlier one" is PICKING from what they just heard, and that utterance
    belongs to `resolve_requested_time`, not here.
    """
    return bool(_SOONER_REQUEST_RE.search(str(text or "").lower()))


def _day_phrase_for(date_iso: Any, day_label: Any) -> str:
    """"Today" / "Tomorrow" / the payload's own label. Never raises.

    The label is the fallback rather than the default because "Wednesday 9th
    September" for a slot two hours away is how a diary talks, not how a person
    does -- and this sentence is the one place Susie concedes something to the
    caller, so it should not sound like a lookup.
    """
    try:
        from datetime import datetime as _dt
        from app.tools.receptionist_tools import LONDON_TZ
        today = _dt.now(LONDON_TZ).date()
        d = _date.fromisoformat(str(date_iso)[:10])
        if d == today:
            return "Today"
        if (d - today).days == 1:
            return "Tomorrow"
    except Exception:
        pass
    return str(day_label or "").strip() or "That day"


def nothing_sooner_speech(
    session: Dict[str, Any], user_text: str
) -> Optional[str]:
    """Say that the earliest slot already offered IS the earliest there is.

    Returns None -- and the caller falls through unchanged -- unless ALL of the
    following hold. Each is a way the sentence could be a lie, and a lie here is
    worse than the bare repeat it replaces:

      1. the caller ASKED for something sooner on THIS turn. Deliberately not
         `caller_wants_soonest`, which is a standing preference captured earlier
         in the call: the concession only makes sense as an answer to a
         push-back, and firing it on a first readout would apologise for an
         offer nobody had objected to;
      2. no day in the payload is a FILTERED view. Where `times_not_shown` is
         positive a band filter removed times before the session ever saw them
         (B-97), so the earliest slot here is merely the earliest that SURVIVED
         the filter. "Nothing before it" would be false in exactly the case the
         caller is most likely to catch;
      3. the earliest slot in the payload has ALREADY been spoken to this
         caller. If something earlier sits unspoken, the honest answer is to
         read it out -- which the producers below already do -- and denying it
         would be both false and infuriating;
      4. it has not been said about this same slot before. Answering a repeated
         push-back with the identical sentence is the going-in-circles shape
         this exists to end, so a second one falls through to the model.

    Touches no offer state on purpose. The keypad map, `last_offered_slots` and
    `v3_awaiting_slot_selection` all still describe the offer on the table, and
    that offer is still live -- this turn adds a sentence about it, it does not
    replace it. Re-pointing the keypad at the single named slot would destroy
    the caller's ability to take one of the other days just read to them.
    """
    if not utterance_asks_for_something_sooner(user_text):
        return None
    days = session.get("available_days")
    if not isinstance(days, list) or not days:
        return None

    # 2 -- a filtered day makes "the earliest" unknowable from here.
    try:
        if _days_showing_a_filtered_view(days):
            logger.info(
                "[slot_followup] declining the nothing-sooner sentence -- a "
                "band filter hid times before the session saw them, so the "
                "earliest slot in this payload is not the earliest there is "
                "(B-97)"
            )
            return None
    except Exception:
        return None

    try:
        bookable = [
            s for s in flatten_bookable_slots(days) if str(s.get("start") or "")
        ]
        if not bookable:
            return None
        earliest = min(bookable, key=lambda s: str(s.get("start"))[:19])
        spoken = spoken_starts_for_offer(session)
    except Exception:
        logger.exception(
            "[slot_followup] nothing-sooner check failed -- falling through"
        )
        return None

    key = str(earliest.get("start"))[:19]
    # 3 -- something earlier is still unspoken, so read it rather than deny it.
    if key not in spoken:
        logger.info(
            "[slot_followup] caller asked for something sooner and %s has NOT "
            "been spoken yet -- falling through so it can be offered rather "
            "than denied", key,
        )
        return None

    # 4 -- said once already about this same slot.
    if str(session.get("_nothing_sooner_said_for") or "") == key:
        logger.info(
            "[slot_followup] the nothing-sooner sentence has already been said "
            "about %s -- not repeating it verbatim; falling through", key,
        )
        return None

    _time = str(earliest.get("spoken") or "").strip()
    if not _time:
        return None
    session["_nothing_sooner_said_for"] = key
    _day = _day_phrase_for(earliest.get("date"), earliest.get("day_label"))
    logger.info(
        "[slot_followup] caller pushed for something sooner and %s is the "
        "earliest slot in the payload AND already spoken -- saying so rather "
        "than re-reading the same offer (D11)", key,
    )
    return (
        "I wish I had something sooner \u2014 {} at {} is genuinely the first "
        "slot we've got, nothing before it. Does that one work for you?"
    ).format(_day, _time)


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

    # ── REPEAT (D-o, DT-30/31) ───────────────────────────────────────────
    # ABOVE the offer/payload gate: a repeat is answered from the WORDS last
    # spoken, which survive an offer being cleared, and above every selector
    # below because none of them may run on this act -- re-deriving the offer
    # applies novelty and changes the times (N3). It declines on anything that
    # is not a repeat, so it cannot take another producer's turn.
    _repeat = repeat_speech(session, user_text)
    if _repeat:
        return _repeat

    offered = session.get("last_offered_slots") or []
    days = session.get("available_days") or []
    if not offered or not days:
        return None

    # ── D11. "That's not soon enough", when there IS nothing sooner ─────────
    # ABOVE the exhaustion branch below, and that placement is the point rather
    # than a preference. This question is about the EARLIEST slot in the diary,
    # which is answerable whether or not unspoken times remain -- and the
    # `if not remaining:` block below returns None for any utterance that is
    # not a more-slots request, so a push-back arriving after a fully-read day
    # would never reach a producer at all.
    #
    # It cannot take another producer's turn. `nothing_sooner_speech` declines
    # unless the earliest slot in the payload has ALREADY been spoken, so
    # whenever there is something earlier to offer, this returns None and the
    # producers below read it out exactly as they do today. What it catches is
    # only the case where the honest answer is a sentence, not a slot.
    _nothing_sooner = nothing_sooner_speech(session, user_text)
    if _nothing_sooner:
        return _nothing_sooner

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
    #
    # DT-7/8 (spec §5.1; precedence level 3): scoped to the DAY UNDER
    # DISCUSSION first, whole sweep second. CA34942aee, demo line, 11 Sep
    # 08:51:21 -- after "tell me about Monday" the caller asked "what have
    # you got around 12". `remaining` spans every day of the sweep, 12:10 sits
    # on all four of them, and the resolver's "exactly one" discipline --
    # right for a 12-hour twin -- declined a time the diary held on the very
    # day being discussed. The turn went to the model, whose sentence Gate 5
    # stripped, and the caller heard "Does that work?" about nothing. On
    # CA778651b7 the same question happened to trigger a tool call and D8
    # pinned the time inside the executor; which of the two a caller gets
    # depended on the model, which is invariant 20 exactly.
    #
    # `remaining_unspoken_on_current_day` is the same scope the more-times
    # branch below uses: the day the caller named in this utterance, else the
    # one picked by position, else the offer they were just given (B-103,
    # B-105). A time on another day still resolves through the whole-sweep
    # call underneath, exactly as before.
    #
    # `_asked` is for the SENTENCE only ("nearest I've got to ..."); the
    # resolver reads word forms ("ten past twelve") the digit parser does not,
    # so the scoped attempt is not gated on it.
    _asked = _requested_clock_times_safe(user_text)
    _scoped = remaining_unspoken_on_current_day(session, user_text)
    # A bare weekday ("ten past twelve on tuesday") is a partial naming to
    # `day_named_by_caller`, so the scope above falls to the offer's first
    # day and the weekday refusal then (rightly) drops the hit -- and the
    # named-day producer reads Tuesday's three with the asked time absent.
    # `_payload_day_by_weekday` (B-148) resolves the weekday against the
    # payload, deny-by-default, and the scope follows it.
    _wk = _payload_day_by_weekday(days, user_text)
    if _wk:
        _scoped = [s for s in remaining if _day_key(s) == _wk]
    hit = resolve_requested_time(user_text, _scoped, days) if _scoped else None
    if hit is not None:
        logger.info(
            "[slot_followup] a time the caller named resolved on the day "
            "under discussion (%s), not across the sweep (DT-7/8)",
            _day_key(hit),
        )
        return apply_resolved_time_to_session(session, hit, asked=_asked)
    hit = resolve_requested_time(user_text, remaining, days)
    if hit is not None:
        return apply_resolved_time_to_session(session, hit, asked=_asked)

    # "What about Wednesday" after a MULTI-DAY readout. Answered from the
    # payload, for the same reason "what else have you got" is: on D-B the
    # model answered it from its own context with NO tool call, re-read the
    # two slots it had already given, and left the keypad pointing at days.
    #
    # BELOW resolve_requested_time on purpose. "Tuesday at ten past five" names
    # a day AND a time; that is a pick, the resolver above owns it, and a
    # readout here would talk over a caller who has already chosen.
    #
    # ABOVE the more-slots branch, and it declines when that branch applies, so
    # neither can take the other's turn.
    # "Monday doesn't work" — a day REFUSED. Above the two day producers,
    # because both of them resolve the weekday the caller named and neither
    # can tell refusal from interest by the day alone (B-147). The honest
    # answer is the one "what else have you got" already gets: the days he has
    # NOT heard, spoken and recorded by the same producer.
    if day_refused_by_caller(session, user_text):
        _more_days = more_days_speech(session)
        if _more_days:
            logger.info(
                "[slot_followup] a day was REFUSED -- answering with the days "
                "the caller has not heard rather than re-reading it (B-147)"
            )
            return _more_days

    _named_day = named_day_speech(session, user_text)
    if _named_day:
        return _named_day

    # "yeah Monday works" after the same readout. The mirror image of the line
    # above and the reason that one declines an acceptance: a caller who ASKS
    # gets a list, a caller who ACCEPTS gets acknowledged AND a list. Below it
    # so the two cannot take each other's turn, and both decline on the other's
    # utterance shape rather than on ordering (B-145).
    _accepted_day = day_acceptance_speech(session, user_text)
    if _accepted_day:
        return _accepted_day

    # "yeah Monday works" after the same readout. The mirror image of the line
    # above and the reason that one declines an acceptance: a caller who ASKS
    # gets a list, a caller who ACCEPTS gets acknowledged AND a list. Below it
    # so the two cannot take each other's turn, and both decline on the other's
    # utterance shape rather than on ordering (B-145).

    if utterance_requests_more_slots(user_text):
        # "What else" after a MULTI-DAY readout means more DAYS, and it is
        # answered here rather than handed to the model -- see more_days_speech
        # for the call that proved why. Only the unscoped case: a caller who
        # NAMES a day, or picks one by position, still gets that day's remaining
        # times below, which is B-103 and B-105 unchanged.
        _payload_days = session.get("available_days") or []
        # B-147, the same defect wearing the more-slots hat. Naming a day here
        # SCOPES the answer to it -- which is right for "what else have you got
        # on Monday" and exactly wrong for "monday doesn't work, what else have
        # you got", where the day is named in order to RULE IT OUT. The second
        # sentence was answered with more Monday times.
        #
        # A refusal therefore un-scopes: the caller who ruled a day out is
        # asking about the others, so they get the days they have not heard.
        _day_ruled_out = bool(_DAY_REFUSE_RE.search(user_text or ""))
        if _day_ruled_out or (
            not day_named_by_caller(_payload_days, user_text)
            and not day_selected_by_position(_payload_days, session, user_text)
        ):
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
