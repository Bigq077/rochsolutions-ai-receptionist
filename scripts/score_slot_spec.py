"""Score the engine against the decision table in SLOT_PRESENTATION_SPEC.md.

WHY THIS EXISTS
---------------
Three weeks of slot work was validated one phone call at a time, on one clinic
whose diary is a uniform 50-minute grid. Each call reaches whichever path its
wording happens to hit, so each call finds a DIFFERENT defect, the fix is
written against that exhibit, and the next call lands on a neighbouring path.
`SLOT_PRESENTATION_ANALYSIS_2026-09-11.md` §1.3 lists five recorded cases where
that loop actively misled us -- including three separate tests that pinned N1 as
CORRECT behaviour.

This inverts it. The table says what the answer is; this runs the engine against
every row on every diary shape and reports pass / fail / unreachable. A phone
call then CONFIRMS rows instead of discovering them.

WHAT A RESULT MEANS
-------------------
`PASS`         the engine produced what the row requires, on that diary.
`FAIL`         it did not. This is a defect whether or not a caller has hit it.
`UNREACHABLE`  the row cannot be scored offline today, and the reason is
               reported. That is a finding in itself: an unreachable row is one
               that only a phone call can check, which is precisely the
               architectural problem invariant 20 is about. Never counted as a
               pass.

THE DIARY SHAPES ARE THE POINT. Every phone verification in the register was on
a uniform grid, so no rule has ever been tested against a day holding one slot,
a sparse two-day rota, or a twelve-slot day -- and three of the shapes below
break rules that look fine on the grid.

NOT A TEST SUITE. It reports, it does not gate. The rows that fail are the
backlog; a row failing only on a diary shape no clinic actually runs is worth
less than one failing on the uniform grid, and the shape is in the output so
that judgement can be made.

THE LIMIT, STATED UP FRONT
--------------------------
This scores PRODUCERS, not the DISPATCHER. Each row calls the engine function
that should serve that act, so it measures what that function does once it is
reached -- and says nothing about whether it is reached. N6 is exactly that
gap: "what else have you got on Monday" never arrives at the named-day producer,
it is taken by B-137's "lead with unheard days". The router is
`handle_transcript`, one 15,734-line method, and nothing can drive it offline
today.

So rows whose defect lives in the routing return UNREACHABLE even when the
producer behaves, with the producer's answer in the detail. That is deliberate:
a PASS there would retire the only thing currently catching N6, which is a
phone call. Closing that gap -- making the dispatcher drivable -- is the single
highest-value thing this harness is missing, and it is invariant 20.

FOUR ROWS IN THE FIRST RUN WERE THE SCORER'S OWN BUGS, not the engine's: a
non-round probe time that invariant 18 declines by design, a bare string where
`nearest_time_index` requires a list, three invented session keys where
`choose_presented_days` reads the spoken record, and DT-4 asserting past the
documented boundary of N1's fix. Each looked exactly like a finding. If a row
here fails, reproduce it against the engine by hand before filing anything --
`anchor-defect-rows-before-scheduling`.

Usage
-----
    python scripts/score_slot_spec.py                # everything
    python scripts/score_slot_spec.py --row DT-14    # one row
    python scripts/score_slot_spec.py --shape sparse
    python scripts/score_slot_spec.py --md           # markdown for the spec
    python scripts/score_slot_spec.py --verbose      # detail on passes too
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, ".")

from app.tools.receptionist_tools import _spoken_slot_time            # noqa: E402
from app.tools import slot_followup as sf                             # noqa: E402
from app.tools.slot_offer import build_slot_offer                     # noqa: E402

PASS, FAIL, UNREACHABLE = "PASS", "FAIL", "UNREACHABLE"

# ───────────────────────────────────────────────────────────────────────────
# Diary shapes
#
# `label` is how the payload names the day, because every day-level comparison
# in the engine matches the payload's OWN label against the text -- never a
# phrase written by hand. Building these the same way keeps the scorer honest:
# a row that only passes against a prettier label than production emits is not
# passing.
# ───────────────────────────────────────────────────────────────────────────

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")


def _label(date_iso: str) -> str:
    import datetime as _dt
    d = _dt.date.fromisoformat(date_iso)
    suffix = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(
        d.day % 10, "th")
    return f"{_WEEKDAYS[d.weekday()]} {d.day}{suffix} {d.strftime('%B')}"


def _day(date_iso: str, times: List[str], not_shown: int = 0) -> Dict[str, Any]:
    return {
        "date": date_iso,
        "day_label": _label(date_iso),
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t) for t in times],
        "slots": [{"start": f"{date_iso}T{t}:00"} for t in times],
        "times_not_shown": not_shown,
    }


def _grid(start="08:00", step=50, n=11) -> List[str]:
    h, m = (int(x) for x in start.split(":"))
    out = []
    for _ in range(n):
        out.append("%02d:%02d" % (h, m))
        m += step
        h, m = h + m // 60, m % 60
    return out


#: 2026-09-14 is a Monday.
MON, TUE, WED, THU = "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"

SHAPES: Dict[str, Callable[[], List[Dict[str, Any]]]] = {
    # northgate, and the only shape any phone verification has ever used.
    "uniform50": lambda: [_day(d, _grid()) for d in (MON, TUE, WED, THU)],
    # Redditch: two days a week. The rota is the reason a caller cannot be seen
    # soon, not the demand -- a different sentence, and a different selection.
    "sparse": lambda: [_day(MON, ["09:00", "17:00"]),
                       _day(THU, ["09:00", "17:00"])],
    # A day with one time cannot be numbered, cannot be spread, and cannot
    # honour "two times that span the day".
    "single_slot": lambda: [_day(MON, ["10:30"])],
    # Twelve is what CA91020004 actually held when the caller was told "the
    # slots I have that day are eight in the morning or ten past five".
    "twelve": lambda: [_day(MON, ["%02d:00" % h for h in range(8, 20)])],
    # A band-filtered VIEW: the payload shows four, the diary holds eight.
    # Every completeness claim has to survive this.
    "band_filtered": lambda: [_day(MON, ["08:00", "09:00", "10:00", "11:00"],
                                   not_shown=8)],
    # One day, all morning: there is no second part of the day to spread into.
    "one_band": lambda: [_day(MON, ["08:00", "08:50", "09:40", "10:30"])],
}


def _session(days: Any, clinic: str = "northgate") -> Dict[str, Any]:
    return {"clinic_id": clinic, "available_days": days}


def _spoken_times_in(text: str, days: Any) -> List[str]:
    """Which payload times a sentence actually named, in the order named.

    Built off the payload's own spoken labels rather than a parser, so the
    scorer cannot disagree with the generator about what a time is called.
    """
    out: List[Tuple[int, str]] = []
    low = (text or "").lower()
    for slot in sf.flatten_bookable_slots(days):
        label = str(slot.get("spoken") or "").lower()
        if label and label in low:
            out.append((low.index(label), slot["time"]))
    return [t for _, t in sorted(out)]


# ───────────────────────────────────────────────────────────────────────────
# Rows
#
# Each returns (status, detail). A row raising is a FAIL with the exception --
# an engine that cannot answer the question is not passing it.
# ───────────────────────────────────────────────────────────────────────────

ROWS: Dict[str, Dict[str, Any]] = {}


def row(rid: str, what: str, shapes: Optional[List[str]] = None,
        invariant: str = "") -> Callable:
    def deco(fn: Callable) -> Callable:
        ROWS[rid] = {"id": rid, "what": what, "fn": fn,
                     "shapes": shapes or list(SHAPES), "invariant": invariant}
        return fn
    return deco


@row("DT-1", "first ask: up to 3 days, 2 times each, numbered",
     invariant="inv 10, 11")
def dt1(days, shape):
    offer = build_slot_offer(days[:3], pretrimmed=False)
    if offer is None:
        return UNREACHABLE, "build_slot_offer declined (no bookable slot)"
    if offer.mode != "multi_day":
        if len(days) == 1:
            return PASS, f"one day in the payload -> {offer.mode}, correct"
        return FAIL, f"mode={offer.mode} on {len(days)} days"
    n_days = len({s["date"] for s in offer.slots})
    per_day = [sum(1 for s in offer.slots if s["date"] == d)
               for d in {s["date"] for s in offer.slots}]
    if n_days > 3:
        return FAIL, f"{n_days} days spoken, cap is 3"
    if any(p > 2 for p in per_day):
        return FAIL, f"times per day {per_day}, cap is 2"
    if n_days >= 2 and "Number 1" not in offer.text:
        return FAIL, "two or more options and no numbering -- not keypad-selectable"
    return PASS, f"{n_days} days, {per_day} times"


@row("DT-3", "a named day is answered with 3 numbered times from that day")
def dt3(days, shape):
    s = _session(days)
    text = sf.speak_one_day_from_payload(s, days, days[0]["date"], why="scorer")
    if not text:
        return UNREACHABLE, "speak_one_day_from_payload declined"
    times = _spoken_times_in(text, [days[0]])
    held = len(days[0]["slot_times"])
    want = min(3, held)
    if len(times) != want:
        return FAIL, f"{len(times)} times spoken, day holds {held}, want {want}"
    if want >= 2 and "Number 1" not in text:
        return FAIL, "not numbered"
    return PASS, f"{times}"


@row("DT-4", "a named day KEEPS the times already offered for it (N1)",
     invariant="inv 7")
def dt4(days, shape):
    """The caller heard two times for Monday, then asked "what about Monday".
    B-116's novelty rule guaranteed that the two times which PROMPTED the
    question were the two withheld -- 6 of 6 on the exhibit.

    SCORED WITHIN N1'S STATED BOUNDARY. `_keep_times_heard_on_named_day`
    declines, by documented design, when `limit` (3) or more times have already
    been heard on the day: at that point the caller has had a full readout, so
    the rule treats the question as "what else" rather than "tell me about it".
    Whether that is the right reading of "what about Monday" is a SPEC question,
    not a bug, and it is DT-4b below -- the first version of this row asserted
    the strong form and reported three shapes as failures, which they are not.
    """
    if len(days[0]["slot_times"]) < 3:
        return UNREACHABLE, "needs a day holding 3+ times to withhold any"
    s = _session(days)
    first = build_slot_offer(days[:3], pretrimmed=False)
    if first is None:
        return UNREACHABLE, "no first offer"
    heard = [x["time"] for x in first.slots if x["date"] == days[0]["date"]]
    if not heard:
        return UNREACHABLE, "the first readout named no time on day 0"
    if len(heard) >= sf.SINGLE_DAY_MAX_TIMES if hasattr(
            sf, "SINGLE_DAY_MAX_TIMES") else len(heard) >= 3:
        return UNREACHABLE, (f"{len(heard)} heard on the day, limit is 3 -- N1 "
                             f"declines by design here; scored as DT-4b")
    sf.record_spoken_slots(s, first.slots)
    text = sf.speak_one_day_from_payload(
        s, days, days[0]["date"], why="scorer", user_text="what about monday")
    if not text:
        return FAIL, "no answer to a named day"
    kept = _spoken_times_in(text, [days[0]])
    missing = [t for t in heard if t not in kept]
    if missing:
        return FAIL, f"heard {heard} on that day, re-read {kept}, dropped {missing}"
    return PASS, f"heard {heard}, kept in {kept}"


@row("DT-4b", "after a FULL day readout, 'what about <day>' reads new times",
     invariant="inv 7 boundary")
def dt4b(days, shape):
    """The other side of N1's boundary, reported as a DIVERGENCE rather than a
    pass or a fail, because which answer is right is the owner's call.

    Today: a caller who has heard all three of Monday's offered times and then
    says "what about Monday" is read three DIFFERENT times -- zero overlap with
    what prompted the question, which is N1's own complaint one condition
    further along. The code's reasoning is that re-reading the same three
    carries no new time. The caller's reading of "what about Monday" may well be
    "tell me about Monday", in which case overlap is the point.

    §9.2 of the spec. No behaviour change is proposed here.
    """
    if len(days[0]["slot_times"]) < 6:
        return UNREACHABLE, "needs 6+ times so a full readout still leaves 3"
    s = _session(days)
    day = days[0]
    heard = day["slot_times"][:3]
    sf.record_spoken_slots(s, [
        {"start": f"{day['date']}T{t}:00", "date": day["date"]} for t in heard])
    text = sf.speak_one_day_from_payload(
        s, days, day["date"], why="scorer", user_text="what about monday")
    if not text:
        return UNREACHABLE, "no answer"
    kept = _spoken_times_in(text, [day])
    overlap = [t for t in kept if t in heard]
    return (PASS if overlap else UNREACHABLE), (
        f"heard {heard} -> re-read {kept}; overlap {overlap or 'NONE'}"
        + ("" if overlap else "  <-- divergence, owner call (spec §9.2)"))


@row("DT-7", "a named time the diary HOLDS is spoken", invariant="inv 4")
def dt7(days, shape):
    times = days[0]["slot_times"]
    if len(times) < 4:
        return UNREACHABLE, "needs 4+ times so the pin can displace something"
    target = times[len(times) // 2]          # mid-day, so it is not the head
    s = _session(days)
    s[sf.REQUESTED_TIMES_KEY] = [target]
    idx = sf.choose_presented_indices(s, days[0], 3)
    chosen = [times[i] for i in idx]
    if target not in chosen:
        return FAIL, f"asked for {target}, spoken {chosen}"
    return PASS, f"asked {target}, spoken {chosen}"


@row("DT-8", "a named time the diary LACKS gets the nearest bookable one",
     invariant="inv 4")
def dt8(days, shape):
    """A ROUND time off the grid, because that is what callers say and what the
    engine is allowed to drift from.

    `nearest_time_index` applies its tolerance to round times ONLY -- invariant
    18's "no drift for non-round times" -- so probing with 09:41 measures the
    rule declining exactly as designed. The first version of this row did that
    and reported four shapes as failures, which they were not. A caller says
    "around ten", never "nine forty-one".
    """
    times = days[0]["slot_times"]
    if len(times) < 3:
        return UNREACHABLE, "needs 3+ times"
    grid = set(times)
    asked = next((f"{h:02d}:00" for h in range(8, 20)
                  if f"{h:02d}:00" not in grid
                  and any(abs((int(t[:2]) * 60 + int(t[3:])) - h * 60)
                          <= sf.NEAREST_TIME_TOLERANCE_MIN for t in times)), None)
    if asked is None:
        return UNREACHABLE, (f"no round hour off this grid within "
                             f"{sf.NEAREST_TIME_TOLERANCE_MIN}min of a slot")
    # A LIST, not a string: `wanted` carries the 12-hour twin and the function
    # declines a bare string by contract. Passing `asked` alone made every shape
    # look like a failure -- the third time this scorer manufactured one.
    idx = sf.nearest_time_index(times, [asked])
    if idx is None:
        return FAIL, f"asked {asked}, nearest_time_index declined over {times}"
    best = min(times, key=lambda t: abs(
        (int(t[:2]) * 60 + int(t[3:])) - (int(asked[:2]) * 60 + int(asked[3:]))))
    if times[idx] != best:
        return FAIL, f"asked {asked}, picked {times[idx]}, nearest is {best}"
    return PASS, f"asked {asked} -> {times[idx]}"


@row("DT-8b", "a named time DURING SELECTION reaches a producer on the day "
              "under discussion", invariant="inv 4, inv 20")
def dt8b(days, shape):
    """The dispatcher, driven the way CA34942aee (11 Sep 08:51) drove it: a
    week readout, "tell me about <day 0>", then "anything around <round hour>".
    Before 2026-09-11 the resolver ran over the whole sweep, the same clock
    time on every day made the hit non-unique, and the turn went to the
    model. DT-7/8 above score the selection pieces; this scores whether the
    act reaches them."""
    from app.tools.slot_offer import apply_offer_to_session, offer_as_record
    times = days[0]["slot_times"]
    if len(times) < 2:
        return UNREACHABLE, "needs 2+ times so one can be unheard"
    s = _session(days)
    first = build_slot_offer(days[:3], pretrimmed=False)
    if first is None:
        return UNREACHABLE, "no offer"
    apply_offer_to_session(s, offer_as_record(first), first.chunks)
    s["_slot_presentation_mode"] = first.mode
    if first.mode == "multi_day":
        if not sf.try_unspoken_followup_speech(s, f"tell me about {_label(days[0]['date']).split()[0].lower()}"):
            return UNREACHABLE, "the named-day producer declined"
    # A round hour within tolerance of an UNHEARD time on day 0.
    heard = set(s.get(sf._SPOKEN_KEY) or [])
    unheard = [t for t in times if f"{days[0]['date']}T{t}:00" not in heard]
    asked = None
    for t in unheard:
        h = round((int(t[:2]) * 60 + int(t[3:])) / 60)
        cand = f"{h:02d}:00"
        near = [x for x in times if abs((int(x[:2]) * 60 + int(x[3:])) - h * 60)
                <= sf.NEAREST_TIME_TOLERANCE_MIN]
        if 8 <= h <= 19 and near == [t]:
            asked = (cand, t)
            break
    if asked is None:
        return UNREACHABLE, "no unheard time is the unique nearest to a round hour"
    said = f"have you got anything around {int(asked[0][:2])}"
    out = sf.try_unspoken_followup_speech(s, said)
    if not out:
        return FAIL, f"{said!r} reached no producer (the model would answer it)"
    want = sf._spoken_slot_time(asked[1]) if hasattr(sf, "_spoken_slot_time") else _spoken_slot_time(asked[1])
    if want not in out:
        return FAIL, f"asked {asked[0]}, said {out!r}"
    if asked[0] != asked[1] and "nearest" not in out.lower():
        return FAIL, f"offered {asked[1]} for {asked[0]} without saying so: {out!r}"
    return PASS, f"{said!r} -> {out[:70]!r}"


@row("DT-9", "a tie for nearest declines rather than guessing",
     shapes=["uniform50"], invariant="inv 4")
def dt9(days, shape):
    """Exactly between two bookable times, `nearest_time_index` must decline.
    Picking either is a 50% chance of answering a question the caller did not
    ask, and the cost of declining is one more turn."""
    times = days[0]["slot_times"]
    a, b = times[0], times[1]
    mid = ((int(a[:2]) * 60 + int(a[3:])) + (int(b[:2]) * 60 + int(b[3:]))) // 2
    asked = "%02d:%02d" % divmod(mid, 60)
    idx = sf.nearest_time_index([a, b], asked)
    if idx is None:
        return PASS, f"{asked} is equidistant from {a} and {b} -- declined"
    return FAIL, f"{asked} equidistant from {a}/{b}, picked {[a, b][idx]}"


@row("DT-12", "'what else' brings UNHEARD times and never pads with heard ones",
     invariant="inv 6")
def dt12(days, shape):
    times = days[0]["slot_times"]
    if len(times) < 4:
        return UNREACHABLE, "needs 4+ times to have any unheard"
    s = _session(days)
    first = sf.choose_presented_indices(s, days[0], 2)
    heard = [times[i] for i in first]
    sf.record_spoken_slots(s, [
        {"start": f"{days[0]['date']}T{t}:00", "date": days[0]["date"]}
        for t in heard])
    second = sf.choose_presented_indices(s, days[0], 2)
    again = [times[i] for i in second]
    repeated = [t for t in again if t in heard]
    unheard_left = [t for t in times if t not in heard]
    if repeated and unheard_left:
        return FAIL, f"heard {heard}, unheard {unheard_left}, re-read {repeated}"
    return PASS, f"heard {heard} -> {again}"


@row("DT-13", "'what else' after a multi-day readout brings MORE DAYS",
     shapes=["uniform50"])
def dt13(days, shape):
    if len(days) < 4:
        return UNREACHABLE, "needs 4+ days so a second set exists"
    s = _session(days)
    first = sf.choose_presented_days(s, days, 3)
    if not first:
        return UNREACHABLE, "choose_presented_days declined"
    offered = [d.get("date") for d in first]
    # `choose_presented_days` reads the SPOKEN record via
    # `spoken_starts_for_offer`, not a separate offered-days key. The first
    # version of this row invented three key names and measured nothing but its
    # own guess -- which is how a scorer manufactures a finding.
    sf.record_spoken_slots(s, [
        sl for d in first for sl in (d.get("slots") or [])])
    second = sf.choose_presented_days(s, days, 3)
    got = [d.get("date") for d in second]
    if got == offered and len(days) > len(offered):
        return FAIL, (f"offered {offered}, asked again, got the same days -- "
                      f"{[d['date'] for d in days if d['date'] not in offered]} "
                      f"never offered")
    return PASS, f"{offered} -> {got}"


@row("DT-14", "'what else on <day>' is answered ON THAT DAY (N6)",
     invariant="inv 4")
def dt14(days, shape):
    """N6, OPEN at the time of writing -- and THIS SCORER CANNOT SEE IT.

    Read this before trusting a PASS here. N6 is a ROUTING defect: "what else
    have you got on Monday" never reaches `speak_one_day_from_payload` at all,
    it is taken by B-137's "lead with unheard days" and answered with
    Thursday-Saturday. The row below calls the named-day producer DIRECTLY, so
    it measures what that producer does once reached, and says nothing about
    whether it is reached.

    That is the scorer's structural limit, and it is the same blind spot as
    every existing harness (`replay_slot_decisions` cannot see selection;
    `replay_multi_day_spread` cannot see the named-day producers). The
    dispatcher is `handle_transcript`, a single 15,734-line method, and nothing
    can drive it offline today.

    So this returns UNREACHABLE whatever the producer says, with the producer's
    answer in the detail for information. Reporting a PASS would be worse than
    reporting nothing: it would retire the only thing currently catching N6,
    which is a phone call.
    """
    times = days[0]["slot_times"]
    if len(times) < 3 or len(days) < 2:
        return UNREACHABLE, "needs a multi-day payload with 3+ times on day 0"
    s = _session(days)
    first = build_slot_offer(days[:3], pretrimmed=False)
    if first is None:
        return UNREACHABLE, "no first offer"
    sf.record_spoken_slots(s, first.slots)
    text = sf.speak_one_day_from_payload(
        s, days, days[0]["date"], why="scorer",
        user_text="what else have you got on monday")
    if not text:
        return FAIL, "no answer"
    other = [d for d in days[1:]
             if str(d["day_label"]).split()[0].lower() in text.lower()]
    verdict = ("names " + str([d["day_label"] for d in other])) if other \
        else "answered on the named day"
    return UNREACHABLE, (
        f"the named-day PRODUCER {verdict}; but N6 is a ROUTING defect and this "
        f"row calls the producer directly -- only a call can see it")


@row("DT-17", "'nothing sooner' says what the earliest IS", invariant="inv 5")
def dt17(days, shape):
    s = _session(days)
    first = days[0]
    sf.record_spoken_slots(s, [
        {"start": f"{first['date']}T{first['slot_times'][0]}:00",
         "date": first["date"]}])
    # `nothing_sooner_speech(session, user_text)`: the ask must be on THIS turn,
    # deliberately -- the sentence is a claim, and a claim made off a stale
    # session key is one of the ways it becomes a lie.
    s["day_preference"] = "asap"
    text = sf.nothing_sooner_speech(s, "have you got anything sooner")
    if not text:
        return UNREACHABLE, "nothing_sooner_speech declined (no earlier slot heard)"
    if _spoken_slot_time(first["slot_times"][0]).split(" in ")[0] not in text.lower():
        return FAIL, f"does not name the earliest ({first['slot_times'][0]}): {text!r}"
    return PASS, text[:80]


@row("DT-19", "an ordinal pick resolves against the LAST spoken list",
     invariant="inv 10")
def dt19(days, shape):
    offer = build_slot_offer(days[:3], pretrimmed=False)
    if offer is None or len(offer.dtmf_map) < 2:
        return UNREACHABLE, "needs a numbered offer of 2+ options"
    spoken_order = [k for k in sorted(offer.dtmf_map)]
    for n in spoken_order:
        mapped = offer.dtmf_map[n]
        if mapped is None:
            return FAIL, f"option {n} maps to nothing"
    # The map must describe the sentence, not the payload.
    if len(offer.dtmf_map) != len({s["date"] for s in offer.slots}) and \
            len(offer.dtmf_map) != len(offer.slots):
        return FAIL, (f"map has {len(offer.dtmf_map)} entries for "
                      f"{len(offer.slots)} slots over "
                      f"{len({s['date'] for s in offer.slots})} days")
    return PASS, f"{len(offer.dtmf_map)} options, all mapped"


@row("DT-21", "an accept naming NO offered time is a question, never a "
              "confirmation", invariant="inv 2")
def dt21(days, shape):
    """D-p, from CA7ebc0083 (11 Sep 00:04): "10 to 12 works" against
    10:30 / 11:20 / 12:10 was confirmed as "twenty to twelve", three times.

    Drives the dispatcher with the to/past mirror of one offered time
    ("ten TO twelve" for an offered ten PAST twelve), or five past it when
    the offered time is on the hour. Passes only when the reply names the
    ONE offered time it could be as a question, never speaks the time the
    caller mis-said, and leaves the offer narrowed to that one slot so a
    "yes" books it (V5)."""
    from app.tools import slot_fact_guard as guard
    from app.tools.slot_offer import apply_offer_to_session, offer_as_record
    s = _session(days)
    first = build_slot_offer(days[:3], pretrimmed=False)
    if first is None:
        return UNREACHABLE, "no offer"
    apply_offer_to_session(s, offer_as_record(first), first.chunks)
    s["_slot_presentation_mode"] = first.mode
    if first.mode == "multi_day":
        # The 00:04 offer was one day's three times. On a week menu the same
        # clock time sits on several days, so a slip is plausible for all of
        # them and the honest answer is the re-read -- a different branch.
        if not sf.try_unspoken_followup_speech(
            s, f"tell me about {_label(days[0]['date']).split()[0].lower()}"
        ):
            return UNREACHABLE, "the named-day producer declined"
    held = {t for d in days for t in d["slot_times"]}
    offered = [(o["start"][:10], o["start"][11:16]) for o in s["last_offered_slots"]]
    pick = None
    for date, t in offered:
        h, m = int(t[:2]), int(t[3:])
        slip = "%02d:%02d" % (h - 1, 60 - m) if 0 < m < 30 else \
               ("%02d:05" % h if m == 0 else None)
        if slip is None or slip in held:
            continue                       # a diary time is DT-7's, not this row's
        alone = [x for _, x in offered if sf._plausible_slip(slip, x)]
        if alone == [t]:
            pick = (date, t, slip)
            break
    if pick is None:
        return UNREACHABLE, "no offered time has a slip that is plausible for it alone"
    date, t, slip = pick
    said = f"{_spoken_slot_time(slip)} works"
    guard.note_caller_speech(s, said)
    out = sf.try_unspoken_followup_speech(s, said)
    if not out:
        return FAIL, f"{said!r} reached no producer (the model would answer it)"
    want = _spoken_slot_time(t)
    if not out.startswith(f"Just to check — did you mean {want}"):
        return FAIL, f"{said!r} -> {out!r}"
    if _spoken_slot_time(slip) in out:
        return FAIL, f"the mis-said time was spoken back: {out!r}"
    if out.lower().startswith(("so that's", "yes")) or not out.rstrip().endswith("?"):
        return FAIL, f"not a question: {out!r}"
    narrowed = [o["start"][:16] for o in s.get("last_offered_slots") or []]
    if narrowed != [f"{date}T{t}"]:
        return FAIL, f"the offer did not narrow to {t}: {narrowed}"
    v = guard.check_outgoing(s, out)
    if not v.clean:
        return FAIL, f"guard: {v.reason}"
    return PASS, f"{said!r} -> {out[:60]!r}"


@row("DT-24", "an accepted slot is pinned back into a later readout",
     invariant="inv 3")
def dt24(days, shape):
    times = days[0]["slot_times"]
    if len(times) < 4:
        return UNREACHABLE, "needs 4+ times so a readout can drop one"
    s = _session(days)
    accepted = times[-1]                      # last of the day: novelty drops it
    s["chosen_slot"] = {"start": f"{days[0]['date']}T{accepted}:00"}
    s["selected_slot_iso"] = f"{days[0]['date']}T{accepted}:00"
    idx = sf.choose_presented_indices(s, days[0], 2)
    chosen = [times[i] for i in idx]
    if accepted not in chosen:
        return FAIL, f"accepted {accepted}, re-read {chosen} without it"
    return PASS, f"accepted {accepted} kept in {chosen}"


@row("DT-26", "a refused day is not offered again", shapes=["uniform50"],
     invariant="inv 8")
def dt26(days, shape):
    """The refusal door resolves the weekday against the OFFER on the session,
    so the offer has to be applied first -- through `apply_offer_to_session`,
    which is the one place every record of an offer is written ("anything that
    speaks an offer calls this")."""
    from app.tools.slot_offer import apply_offer_to_session, offer_as_record
    if len(days) < 2:
        return UNREACHABLE, "needs 2+ days so a refusal leaves something"
    s = _session(days)
    offer = build_slot_offer(days[:3], pretrimmed=False)
    if offer is None:
        return UNREACHABLE, "no offer to refuse"
    apply_offer_to_session(s, offer_as_record(offer), offer.chunks)
    weekday = days[0]["day_label"].split()[0].lower()
    refused = sf.day_refused_by_caller(s, f"{weekday} doesn't work")
    if refused is None:
        return FAIL, (f"'{weekday} doesn't work' was not read as a refusal of "
                      f"{days[0]['date']}")
    if refused != days[0]["date"]:
        return FAIL, f"refused {refused}, caller named {days[0]['date']}"
    return PASS, f"refusal of {refused} read"


@row("DT-30", "REPEAT re-reads the same offer verbatim", invariant="inv 10")
def dt30(days, shape):
    """Rebuilding instead of replaying applies novelty (precedence level 4) and
    silently changes the times, which is how "say that again" became a new
    readout (N3).

    Until 2026-09-11 this row called `build_slot_offer` twice, which scores
    the FORMATTER's determinism and nothing about the act -- on both demo
    calls that morning the act itself went to the model. Now it drives the
    dispatcher with the caller's words, after the offer has been recorded as
    heard, so that a selector re-run WOULD change the times: verbatim equality
    is then evidence that nothing selected (D-o)."""
    from app.tools.slot_offer import apply_offer_to_session, offer_as_record
    s = _session(days)
    first = build_slot_offer(days[:3], pretrimmed=False)
    if first is None:
        return UNREACHABLE, "no offer"
    apply_offer_to_session(s, offer_as_record(first), first.chunks)
    keypad = dict(s.get("v3_dtmf_slot_map") or {})
    heard = list(s.get(sf._SPOKEN_KEY) or [])
    again = sf.try_unspoken_followup_speech(s, "sorry, say that again")
    if not again:
        return FAIL, "REPEAT reached no producer (the model would answer it)"
    if again != first.text:
        return FAIL, f"repeat differs:\n    first: {first.text!r}\n    again: {again!r}"
    if dict(s.get("v3_dtmf_slot_map") or {}) != keypad:
        return FAIL, "the keypad map changed on a repeat (inv 10)"
    if list(s.get(sf._SPOKEN_KEY) or []) != heard:
        return FAIL, "a repeat recorded something new as heard (inv 16)"
    return PASS, "verbatim, keypad and heard record untouched"


@row("DT-31", "REPEAT with the offer cleared re-speaks the last SPOKEN list",
     invariant="inv 10")
def dt31(days, shape):
    """D-o: never the selector re-run, never a re-query. `last_offered_slots`
    is wiped by several turn types (B-78/B-80 family); the words survive."""
    from app.tools.slot_offer import apply_offer_to_session, offer_as_record
    s = _session(days)
    first = build_slot_offer(days[:3], pretrimmed=False)
    if first is None:
        return UNREACHABLE, "no offer"
    apply_offer_to_session(s, offer_as_record(first), first.chunks)
    s.pop("last_offered_slots", None)
    s.pop("_slot_readout_chunks", None)        # a watchdog re-ask pops this
    again = sf.try_unspoken_followup_speech(s, "what were those again")
    if not again:
        return FAIL, "REPEAT with an empty offer reached no producer"
    if again != first.text:
        return FAIL, f"rebuilt differs from spoken:\n    {first.text!r}\n    {again!r}"
    return PASS, "the spoken words, not a rebuild"


@row("INV-11", "two times in one readout span the day", invariant="inv 11")
def inv11(days, shape):
    offer = build_slot_offer(days[:3], pretrimmed=False)
    if offer is None:
        return UNREACHABLE, "no offer"
    worst = None
    for date in {s["date"] for s in offer.slots}:
        ts = sorted(s["time"] for s in offer.slots if s["date"] == date)
        if len(ts) < 2:
            continue
        held = [d for d in days if d["date"] == date][0]["slot_times"]
        if len(held) < 3:
            continue                          # nothing to spread into
        gap = (int(ts[-1][:2]) * 60 + int(ts[-1][3:])) - \
              (int(ts[0][:2]) * 60 + int(ts[0][3:]))
        span = (int(held[-1][:2]) * 60 + int(held[-1][3:])) - \
               (int(held[0][:2]) * 60 + int(held[0][3:]))
        if span and gap < span / 3:
            worst = (date, ts, gap, span)
    if worst:
        return FAIL, (f"{worst[0]}: {worst[1]} are {worst[2]}min apart over a "
                      f"{worst[3]}min day")
    return PASS, "spread"


@row("INV-1", "nothing spoken names a time the payload lacks", invariant="inv 1")
def inv1(days, shape):
    """The guard, run over the engine's OWN output. A violation here is not a
    model hallucination -- it is a producer disagreeing with its own payload,
    which is the B-102 shape."""
    from app.tools import slot_fact_guard as guard
    s = _session(days)
    sentences = []
    offer = build_slot_offer(days[:3], pretrimmed=False)
    if offer is not None:
        sentences.append(offer.text)
    one = sf.speak_one_day_from_payload(s, days, days[0]["date"], why="scorer")
    if one:
        sentences.append(one)
    if not sentences:
        return UNREACHABLE, "no producer spoke"
    bad = []
    for text in sentences:
        v = guard.check_outgoing(_session(days), text)
        bad.extend(v.violations)
    if bad:
        return FAIL, "; ".join(
            f"{b['phrase']} ({','.join(b['candidates'])})" for b in bad)
    return PASS, f"{len(sentences)} producer sentence(s) clean"


# ───────────────────────────────────────────────────────────────────────────

def run(only_row: Optional[str], only_shape: Optional[str]
        ) -> List[Tuple[str, str, str, str]]:
    results = []
    for rid, spec in ROWS.items():
        if only_row and rid != only_row:
            continue
        for shape in spec["shapes"]:
            if only_shape and shape != only_shape:
                continue
            days = SHAPES[shape]()
            try:
                status, detail = spec["fn"](days, shape)
            except Exception as exc:      # an engine that cannot answer is failing
                status, detail = FAIL, f"raised {type(exc).__name__}: {exc}"
            results.append((rid, shape, status, detail))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--row")
    ap.add_argument("--shape", choices=sorted(SHAPES))
    ap.add_argument("--md", action="store_true", help="markdown table")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    results = run(args.row, args.shape)
    n = {PASS: 0, FAIL: 0, UNREACHABLE: 0}
    for _, _, status, _ in results:
        n[status] += 1

    if args.md:
        print("| row | what | shape | result | detail |")
        print("|---|---|---|---|---|")
        for rid, shape, status, detail in results:
            if status == PASS and not args.verbose:
                continue
            print(f"| {rid} | {ROWS[rid]['what']} | `{shape}` | **{status}** | "
                  f"{detail.replace('|', '/')} |")
    else:
        width = max((len(r[0]) for r in results), default=6)
        last = None
        for rid, shape, status, detail in results:
            if rid != last:
                print(f"\n{rid:<{width}}  {ROWS[rid]['what']}"
                      + (f"   [{ROWS[rid]['invariant']}]"
                         if ROWS[rid]["invariant"] else ""))
                last = rid
            if status == PASS and not args.verbose:
                print(f"    {shape:<14} PASS")
            else:
                print(f"    {shape:<14} {status}  {detail}")

    print(f"\n{len(results)} checks: {n[PASS]} pass, {n[FAIL]} FAIL, "
          f"{n[UNREACHABLE]} unreachable")
    if n[FAIL]:
        print("\nThe FAIL rows are the backlog. Weigh them by shape: a row that "
              "\nfails only on a diary no clinic runs is worth less than one "
              "that\nfails on uniform50, which is northgate and the demo line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
