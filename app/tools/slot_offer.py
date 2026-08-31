"""Build the spoken slot offer, its record and its keypad map — in one place.

Step 1 of `docs/plan/DETERMINISTIC_SLOT_PRESENTATION.md`. Pure: no session, no
I/O, no wiring. Nothing imports this yet.

WHY THIS EXISTS. Today a language model writes the slot sentence and the system
then works out what it said — capping the options, parsing the speech back into
slots, extracting the keypad map with a regex, re-splitting on "Number N", and
reconciling the "a few others that day" claim against the payload. That repair
layer is ~900 lines and it is where fifteen B-numbers live (B-78b, B-80, B-93,
B-95, B-97..B-102, B-112, B-115, B-116, B-125, B-126) — 118 of the 180 commits
in the eight days to 31 Aug 2026.

It cannot converge, because the reverse-parse does not work. Measured 31 Aug:

    resolve_spoken_options(["Monday 7th September — ten in the morning or five
                            in the evening", ...])  ->  []

That is the exact shape `SLOT_FORMATTER_SYSTEM_PROMPT` mandates, so the record
of what was offered is empty or projected on essentially every multi-day call.
`CA44f1bdbe` and `CA7e3ccfd4` both logged "could not resolve spoken option(s)",
and on the first of them a guard reading the projection overwrote a correct
read-back: the caller was told nine in the morning while Acuity held 18:00.

THE INVARIANT. There is exactly ONE offer at any moment: a set of concrete
slots, the sentence that named them, and the keypad map for it — produced
together, by this function. `SlotOffer.slots` is therefore a record, not a
reconstruction, and cannot disagree with what the caller heard.

DEFAULTS REPRODUCE TODAY'S SPEECH — up to 3 days x 2 times, or one day x 3
times — so wiring this in is not also a behaviour change. The numbers are an
owner decision and now live in one place, rather than disagreeing across
`_MAX_PRESENTED_DAYS` (2), `MAX_SPOKEN_OPTIONS` (3) and the prompt (6).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.tools.slot_followup import (
    _closing_question,
    _spoken_series,
    flatten_bookable_slots,
    more_times_tail,
)

MULTI_DAY_MAX_DAYS = 3
MULTI_DAY_TIMES_PER_DAY = 2
SINGLE_DAY_MAX_TIMES = 3


class SlotOffer:
    """The speech, the slots it named, and the keypad map — one object."""

    __slots__ = ("chunks", "slots", "dtmf_map", "more_times", "mode")

    def __init__(self, chunks, slots, dtmf_map, more_times, mode):
        self.chunks = chunks
        self.slots = slots
        self.dtmf_map = dtmf_map
        self.more_times = more_times
        self.mode = mode

    @property
    def text(self) -> str:
        return " ".join(self.chunks)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "SlotOffer(mode={!r}, {} slot(s), more_times={}, text={!r})".format(
            self.mode, len(self.slots), self.more_times, self.text
        )


def _part_of_day(start: Any) -> str:
    """morning / afternoon / evening from an ISO start, for the second pick."""
    try:
        hour = int(str(start)[11:13])
    except (TypeError, ValueError):
        return ""
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _pick_times_for_day(
    slots: List[Dict[str, Any]], limit: int
) -> List[Dict[str, Any]]:
    """The earliest, then later ones — preferring a different part of the day.

    The formatter prompt asked for "the earliest, plus one later option that day
    — ideally in a different part of the day", and the model obeyed it out of
    `available_days` while the trimmed payload carried one time. Same rule,
    decided here, so the record matches the sentence.

    The LATEST slot in the other part, not the next one: "ten in the morning or
    eleven in the morning" is not a choice a caller experiences as two options.
    """
    if limit <= 0 or not slots:
        return []
    ordered = sorted(slots, key=lambda s: str(s.get("start") or ""))
    picked = [ordered[0]]
    if limit == 1:
        return picked
    first_part = _part_of_day(ordered[0].get("start") or "")
    rest = ordered[1:]
    other = [s for s in rest if _part_of_day(s.get("start") or "") != first_part]
    if other:
        picked.append(other[-1])
    elif rest:
        picked.append(rest[-1])
    # Three or more on ONE day: spread across the ordered list rather than
    # topping up from the front. Filling forwards gave nine, ten, five in the
    # evening on the CA7e3ccfd4 payload — two adjacent times and then a jump,
    # which reads as a worse choice than the model's own clustered
    # ten/eleven/midday. Evenly spaced gives nine, midday, five in the evening:
    # a morning, a middle and an evening, which is what a receptionist offers.
    if limit >= 3 and len(ordered) >= limit:
        step = (len(ordered) - 1) / float(limit - 1)
        picked = [ordered[int(round(i * step))] for i in range(limit)]
        seen, spread = set(), []
        for slot in picked:
            key = str(slot.get("start") or "")
            if key not in seen:
                seen.add(key)
                spread.append(slot)
        picked = spread

    for slot in rest:
        if len(picked) >= limit:
            break
        if slot not in picked:
            picked.append(slot)
    picked.sort(key=lambda s: str(s.get("start") or ""))
    return picked[:limit]


def earliest_lead_in_is_true(
    full_day: Any, presented_day: Any
) -> bool:
    """May this readout open "The earliest I have is ..."?

    B-125, and the reason this check exists at all: `first_day` reaching the
    formatter has ALREADY been trimmed by `choose_presented_indices`, which
    prefers times the caller has not heard. So the first slot of the presented
    list is not necessarily the first slot of the DAY —

        "The earliest I have is Tuesday 1st September — Number 1, five past
         nine"   ... while eight in the morning sat bookable that same day,
                     and had been read out twenty seconds earlier.

    On the model path Gate 5a-f catches that afterwards. A payload-built
    sentence never reaches Gate 5, so the claim is decided here instead, from
    the untrimmed day — the same one-way, deny-by-default shape the guards use.

    False whenever it cannot be established, including a missing or unusable
    payload: a neutral opener is always safe, and a false ranking claim is not.
    """
    def _first_start(day):
        if not isinstance(day, dict):
            return None
        starts = [
            str(s.get("start"))
            for s in flatten_bookable_slots([day])
            if s.get("start")
        ]
        return min(starts) if starts else None

    presented_first = _first_start(presented_day)
    if not presented_first:
        return False
    full_first = _first_start(full_day)
    if not full_first:
        return False
    return presented_first == full_first


def build_slot_offer(
    available_days: Any,
    *,
    lead_in: str = "",
    max_days: int = MULTI_DAY_MAX_DAYS,
    times_per_day: int = MULTI_DAY_TIMES_PER_DAY,
    single_day_max_times: int = SINGLE_DAY_MAX_TIMES,
    more_times: Optional[bool] = None,
) -> Optional[SlotOffer]:
    """Build the spoken offer, its record and its keypad map from the payload.

    Returns None when there is nothing to offer, so the caller keeps whatever it
    already does about an empty day rather than inheriting a sentence from here.

    `more_times` is DECIDED from the data — the count the payload holds against
    the count named — never claimed by a model and never reconciled afterwards.
    A day filtered by a time-of-day band reports `times_not_shown`, and those
    hidden slots count as "more" even though no walk over `slots` can see them
    (B-97).

    PASS `more_times` when the days handed in have ALREADY been trimmed to what
    should be spoken. `_cap_presented_slots` selects those positions through
    `choose_presented_indices`, which prefers times this caller has not heard
    (B-116) — knowledge this function does not have and must not overrule. Given
    a pre-trimmed day it would see nothing held back and would wrongly fall
    silent about the rest of the diary, so the retrieval path's own answer wins.
    """
    days = [
        d for d in (available_days or [])
        if isinstance(d, dict) and d.get("date")
    ]
    if not days:
        return None
    days.sort(key=lambda d: str(d.get("date") or ""))

    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for slot in flatten_bookable_slots(days):
        if slot.get("start"):
            by_day.setdefault(slot["date"], []).append(slot)
    days = [d for d in days if by_day.get(d["date"])]
    if not days:
        return None

    def _hidden(day: Dict[str, Any]) -> int:
        try:
            return int(day.get("times_not_shown") or 0)
        except (TypeError, ValueError):
            return 0

    spoken_days = days[:max_days]
    more = len(days) > len(spoken_days)
    _more_is_given = more_times is not None

    named: List[Dict[str, Any]] = []
    dtmf_map: Dict[str, str] = {}
    chunks: List[str] = []

    if len(spoken_days) == 1:
        day = spoken_days[0]
        all_slots = by_day[day["date"]]
        picked = _pick_times_for_day(all_slots, single_day_max_times)
        named = picked
        if len(all_slots) > len(picked) or _hidden(day):
            more = True
        label = day.get("day_label") or "that day"
        if len(picked) == 1:
            only = picked[0]["spoken"]
            dtmf_map["1"] = only
            if lead_in == "earliest":
                chunks = ["The earliest I have is {} — {}.".format(label, only)]
            else:
                chunks = ["The slot I have on {} is {}.".format(label, only)]
        else:
            if lead_in == "earliest":
                opener = "The earliest I have is {} —".format(label)
            else:
                opener = "The available slots for {} are —".format(label)
            for i, slot in enumerate(picked, start=1):
                dtmf_map[str(i)] = slot["spoken"]
                piece = "Number {}, {}.".format(i, slot["spoken"])
                chunks.append("{} {}".format(opener, piece) if i == 1 else piece)
        mode = "single_day"
    else:
        for i, day in enumerate(spoken_days, start=1):
            all_slots = by_day[day["date"]]
            picked = _pick_times_for_day(all_slots, times_per_day)
            named.extend(picked)
            if len(all_slots) > len(picked) or _hidden(day):
                more = True
            label = day.get("day_label") or "that day"
            dtmf_map[str(i)] = label
            series = _spoken_series([s["spoken"] for s in picked])
            piece = "Number {}, {} — {}.".format(i, label, series)
            chunks.append(
                "Here's what we've got coming up — {}".format(piece)
                if i == 1 else piece
            )
        mode = "multi_day"

    if not named:
        return None

    if _more_is_given:
        more = bool(more_times)

    # The tail is a claim about the clinic's diary, so it is made only where it
    # has a referent — ONE day. "A few others that day" after a three-day
    # readout names no day, which is the B-99 rule, here by construction.
    tail = ""
    if more and mode == "single_day":
        tail = " " + more_times_tail(len(named))
    chunks[-1] = "{}{} {}".format(
        chunks[-1], tail, _closing_question(len(dtmf_map))
    )

    return SlotOffer(
        chunks=chunks,
        slots=[dict(s) for s in named],
        dtmf_map=dtmf_map,
        more_times=more,
        mode=mode,
    )
