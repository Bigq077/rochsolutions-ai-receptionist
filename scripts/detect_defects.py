#!/usr/bin/env python3
"""Score every recorded call against the known defect set.

WHY THIS EXISTS
---------------
On 2026-07-29 an audit of 95 calls found five defects still live that four
planning documents had recorded as fixed, and two "defects" that were not real.
The documents were not lying — they were written from what a human heard on a
call, and the worst defects in this system are inaudible: a wrong surname
written to a real calendar event sounds exactly like a correct one.

The demo is 2026-08-05 and the constraint on the work between now and then is
"fix these without introducing a regression". A document cannot enforce that.
This can.

Run it after every fix and after every block of test calls. It reads obs (not
logs), scores each call against every known defect, buckets by the build that
was live when the call happened, and exits non-zero if a defect marked FIXED
fires again.

USAGE
-----
    python scripts/detect_defects.py                  # score everything
    python scripts/detect_defects.py --since 2026-07-28T02:09
    python scripts/detect_defects.py --write-baseline # freeze current counts
    python scripts/detect_defects.py --check          # CI mode, exit 1 on regression

Needs OBS_DATABASE_URL. Run it in the Render shell, or locally with .env.

ADDING A DEFECT
---------------
Add a Detector to DETECTORS with a real SID in `evidence`. A detector with no
evidence is a hypothesis, not a defect — keep those out of here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

BASELINE = Path(__file__).resolve().parent.parent / "docs" / "plan" / "defect_baseline.json"

# --- build boundaries, UTC -------------------------------------------------
# Commit times normalised to UTC (authors commit from +01:00 and +02:00).
# Commit time is not deploy time: Render takes ~3-6 min, so a call within
# AMBIGUOUS_S of a boundary is reported but never counted as a regression.
AMBIGUOUS_S = 360

_D = lambda *a: datetime(*a, tzinfo=timezone.utc)
BUILDS = [
    # 2026-07-31 01:18Z — cdc2177 (the booking readback quotes the slot the caller
    # last agreed to, instead of being told to infer it from a conversation that
    # held two agreements). CA42486ff4 is the call it comes from: the readback
    # composed Tuesday's date with Wednesday's time and the write-guard refused.
    # This is upstream of the guard, so on this build and later
    # c1_write_guard_fired should be 0 on a clean day-change call — a non-zero
    # count here means the wrong day is still being spoken somewhere else.
    (_D(2026, 7, 31, 1, 18), "cdc2177"),
    # 2026-07-31 00:57Z — 6f63057 (Bug B: a different-day request steers to
    # check_availability instead of being answered from the previous day's slots)
    # + 27c59a5 (the C1 write-guard's refusal names the day the slot is really
    # on) + 3c8f3fc (guard counters land on the call record). Pushed at 00:56Z.
    #
    # Set from EVIDENCE, not from push time + assumed lag. First written as 01:00Z
    # on the usual 3-6 min Render estimate, which mislabelled CA42486ff4 (00:57Z)
    # as ad09f3e — the same mislabel this list has now produced three times.
    # That call's guards hold `different_day_steer_fired` and
    # `c1_write_guard_fired`, keys that exist only on 3c8f3fc, so it demonstrably
    # ran the new build ~1 min after the push. The deploy was faster than the
    # estimate; the counters proved it and the estimate did not.
    #
    # Where a call carries a build fingerprint, prefer it over arithmetic.
    (_D(2026, 7, 31, 0, 57), "6f63057"),
    # 2026-07-30 22:00Z — ad09f3e (phone_confirmed is set on the LLM path).
    # CA3145c15f looped the phone question on d88e0da until the caller hung up:
    # book_appointment's A1 gate cannot be cleared by the model, and the branch
    # that sets the flag had been unreachable since the 26 Jul Step 8 reword.
    # Calls before this boundary can loop that way; calls after must not.
    (_D(2026, 7, 30, 22, 0), "ad09f3e"),
    # 2026-07-30 19:35Z — 5b0c9c2 (Bug B: caller can change day after name+phone)
    # + d88e0da (C1 write-guard: refuse a booking on a day the caller was not
    # told). Pushed together at 19:31Z; AMBIGUOUS_S covers the deploy lag.
    (_D(2026, 7, 30, 19, 35), "d88e0da"),
    # 2026-07-30 11:30Z — 1b87b99 (C1 detector) + 7c140f4 (Fix A: a follow-up
    # batch can no longer straddle two days). Pushed together.
    (_D(2026, 7, 30, 11, 30), "7c140f4"),
    # 2026-07-29 20:20Z — docs + scripts/audit_gate5_blast_radius.py only.
    # Runtime-identical to 4cb7273; listed so labels stay precise.
    (_D(2026, 7, 29, 20, 20), "ce45ea8"),
    # 2026-07-29 20:10Z — 554ebb4, 503a06f, 801152a and 4cb7273 were authored at
    # different times but PUSHED AS ONE DEPLOY, so none of the first three ever
    # ran on its own. One boundary, labelled by the tip commit. Boundaries are
    # DEPLOY times, not commit times — a missing entry here silently mislabels
    # every later call as the previous build (found by Jules, 30 Jul).
    (_D(2026, 7, 29, 20, 10), "4cb7273"),
    (_D(2026, 7, 28, 2, 9), "b405017"),
    (_D(2026, 7, 28, 1, 44), "368b4e0"),
    (_D(2026, 7, 27, 19, 16), "2d553b6"),
    (_D(2026, 7, 27, 18, 58), "83699c3"),
    (_D(2026, 7, 27, 4, 51), "29e3f9b"),
    (_D(2026, 7, 27, 4, 14), "41b8b97"),
    (_D(2026, 7, 27, 1, 58), "17d90e7"),
    (_D(2026, 7, 27, 1, 49), "restored-4"),
    (_D(2026, 7, 27, 1, 26), "REVERT-WINDOW"),
    (_D(2026, 7, 27, 0, 58), "073e563"),
    (_D(2026, 7, 26, 14, 38), "de426a6"),
    (_D(2026, 7, 26, 11, 39), "0fd1961"),
    (_D(2026, 7, 25, 16, 48), "2485229"),
    (_D(2000, 1, 1), "baseline"),
]


def build_at(when: datetime) -> tuple[str, bool]:
    """Return (build_label, is_ambiguous)."""
    for cut, label in BUILDS:
        if when >= cut:
            return label, (when - cut).total_seconds() < AMBIGUOUS_S
    return "?", False


# --- helpers ---------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}

_DATE_PHRASE = re.compile(
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+the\s+"
    r"(\d{1,2})(?:st|nd|rd|th)\s+of\s+(" + "|".join(_MONTHS) + r")", re.I)

_LEAK = re.compile(
    r"(looking at the call state|let me now book|i need to book this in now"
    r"|that's a soft affirmative|i have everything i need|wait[,—-]"
    r"|i don't have the|i need to ask for the reason|i should not guess"
    r"|but i'm missing|\*\*[A-Za-z])", re.I)

_ASK = re.compile(r"(shall i (go ahead and )?book|book that in|get that booked)", re.I)

# C1 — the spoken date on a CONFIRMATION turn, compared against the slot we booked.
# Weekday prefix optional and deliberately ignored: the question is not whether the
# weekday matches the date (that is A2), it is whether the DATE the caller agreed to
# is the date that exists in the calendar.
_SPOKEN_DATE = re.compile(
    r"(?:(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+)?"
    r"(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\s+(?:of\s+)?(" + "|".join(_MONTHS) + r")",
    re.I,
)
# Only turns where the caller is being asked to agree, or told it is done. A date
# mentioned while browsing availability is not something the caller acted on.
_CONFIRM_TURN = re.compile(r"all booked|you'?re in for|shall i go ahead", re.I)
_CAUDA = re.compile(r"saddle area|bladder or bowel", re.I)
_BACKISH = re.compile(r"\b(back|spine|lumbar|sciatic|leg|legs|buttock)\b", re.I)
_SCREEN_Q = re.compile(r"can i (just )?(ask|check)|do you (get|have any)", re.I)
_THANKS = re.compile(r"\bthanks,?\s+([A-Z][a-z]+)", re.I)


def _bot(call) -> list[str]:
    return [t.get("text") or "" for t in (call["transcript"] or [])
            if (t.get("role") or "") != "user"]


# --- detectors -------------------------------------------------------------
@dataclass
class Detector:
    id: str
    what: str
    status: str          # "open" | "fixed" | "withdrawn"
    fn: Callable
    evidence: str        # a real SID this fired on
    fixed_by: str = ""   # build label the fix landed in
    hits: list = field(default_factory=list)


def d_reasoning_leak(call):
    m = _LEAK.search(" ".join(_bot(call)))
    return m.group(0)[:44] if m else None


def d_day_date_mismatch(call):
    for mm in _DATE_PHRASE.finditer(" ".join(_bot(call))):
        try:
            actual = datetime(2026, _MONTHS[mm.group(3).lower()],
                              int(mm.group(2))).strftime("%A").lower()
        except ValueError:
            continue
        if actual != mm.group(1).lower():
            return f"said '{mm.group(0)}' - that is a {actual.title()}"
    return None


def d_confirmation_loop(call):
    n = sum(1 for t in _bot(call) if _ASK.search(t))
    return f"{n} booking confirmations" if n > 1 else None


def d_wrong_screen(call):
    tx = call["transcript"] or []
    if not _CAUDA.search(" ".join(_bot(call))):
        return None
    said = (call["collected"] or {}).get("reason") or ""
    said += " " + " ".join(t.get("text") or "" for t in tx
                           if (t.get("role") or "") == "user")
    if _BACKISH.search(said):
        return None
    return f"cauda screen on reason={(call['collected'] or {}).get('reason')!r}"


def d_spoken_slot_not_booked_slot(call):
    """The caller agreed to one date and a DIFFERENT date exists in the calendar.

    The worst failure this system has, because the call sounds perfect: she
    confirms, the caller says yes, she says "all booked", and the appointment is
    on another day. The caller arrives to nothing.

    Distinct from A2 and NOT covered by it. A2 is an internally inconsistent
    phrase ("Friday the 1st of August" when the 1st is a Saturday) — the date is
    right, the weekday label is wrong. Here the phrase is perfectly consistent
    and simply names the wrong day: CA5c4fb14f said "Tuesday the 4th of August"
    (4 Aug IS a Tuesday) and booked 2026-08-05. A2's detector returns 0 on it,
    correctly. Nothing caught this class until 30 Jul.

    Only confirmation turns count. A date spoken while browsing availability is
    not one the caller acted on, so counting it would bury the real hits.

    KNOWN LIMITATION: date only, not time. CAc64a05f1 also had the wrong TIME
    (spoke half past six, booked 17:30). Spoken times are words ("quarter to
    six in the evening") and parsing them is a second piece of work; a
    date-level check already catches both known instances. A same-day wrong-time
    booking would slip through this as written.
    """
    collected = call["collected"] or {}
    iso = collected.get("selected_slot")
    if not iso:
        return None
    try:
        booked = datetime.fromisoformat(str(iso)).date()
    except (ValueError, TypeError):
        return None

    for turn in (call["transcript"] or []):
        if (turn.get("role") or "") == "user":
            continue
        text_ = turn.get("text") or ""
        if not _CONFIRM_TURN.search(text_):
            continue
        for mm in _SPOKEN_DATE.finditer(text_):
            try:
                spoke = datetime(booked.year, _MONTHS[mm.group(2).lower()],
                                 int(mm.group(1))).date()
            except ValueError:
                continue
            if spoke != booked:
                ev = call["calendar_event_id"]
                return (f"agreed {mm.group(0)!r} but booked {booked} "
                        f"({'event ' + str(ev)[:12] if ev else 'NO EVENT'})")
    return None


def d_screen_after_confirm(call):
    tx = call["transcript"] or []
    ask = next((i for i, t in enumerate(tx) if (t.get("role") or "") != "user"
                and _ASK.search(t.get("text") or "")), None)
    scr = next((i for i, t in enumerate(tx) if (t.get("role") or "") != "user"
                and _SCREEN_Q.search(t.get("text") or "")), None)
    if ask is not None and scr is not None and scr > ask:
        return f"confirm@{ask}, screen@{scr}"
    return None


# Calls where a detector fires for a reason that is not the defect. Each entry
# needs a reason — an unexplained silence here is how a real defect gets hidden.
KNOWN_FALSE_POSITIVES = {
    ("A3", "CAdd3373ad0bc4404401b470c7c3dadb93"):
        "caller opened as 'Vince' then gave 'John Smith' at the name step; "
        "the stored value is the correct one",
}


def d_name_divergence(call):
    """She SPOKE one first name and STORED another. Cannot see surnames.

    Blind to the worse half of this defect: the surname is never read back, so
    there is nothing in the record to compare it against. Shipping the surname
    read-back is what makes that half measurable.
    """
    stored = ((call["collected"] or {}).get("name") or "").strip()
    if not stored:
        return None
    for t in _bot(call):
        m = _THANKS.search(t)
        if m:
            if m.group(1).lower() != stored.split()[0].lower():
                return f"spoke {m.group(1)!r}, stored {stored!r}"
            return None
    return None


DETECTORS = [
    # C1 first: it is the only defect here that sends a real patient to the clinic
    # on the wrong day, and it is the only one the caller cannot possibly notice.
    Detector("C1", "agreed one date, booked another", "open",
             d_spoken_slot_not_booked_slot,
             "CA5c4fb14fb555756f3f64952ad945788d"),
    Detector("A1", "model reasoning spoken aloud", "open", d_reasoning_leak,
             "CA76bc921fe665dbf01a75317913c87e01"),
    Detector("A2", "day-name does not match date", "open", d_day_date_mismatch,
             "CAfe6a41626d0b69eb27f7869e0152c8ff"),
    Detector("A3", "spoke one name, stored another", "open", d_name_divergence,
             "CA325372e5d833bd2a8c38de9f7b7167b7"),
    Detector("A4", "confirmation loop", "open", d_confirmation_loop,
             "CAa4942bcea465e89b9b45d9a3b9d9a03b"),
    Detector("B1", "wrong clinical screen for the complaint", "open", d_wrong_screen,
             "CAdd3373ad0bc4404401b470c7c3dadb93"),
    Detector("B2", "screening fires after confirmation", "open", d_screen_after_confirm,
             "CA61d1c0c38ac4ef0fe2c1271a1ac1e9c0"),
]


def load_calls(since: datetime | None):
    from sqlalchemy import create_engine, text
    url = os.environ.get("OBS_DATABASE_URL")
    if not url:
        env = Path(".env")
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("OBS_DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
    if not url:
        sys.exit("OBS_DATABASE_URL not set (and no .env)")
    eng = create_engine(url, connect_args={"connect_timeout": 30})
    q = "SELECT * FROM calls"
    if since:
        q += f" WHERE start_utc >= '{since.isoformat()}'"
    q += " ORDER BY start_utc"
    with eng.connect() as c:
        return list(c.execute(text(q)).mappings())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", help="ISO timestamp, UTC")
    p.add_argument("--write-baseline", action="store_true")
    p.add_argument("--check", action="store_true",
                   help="exit 1 if a FIXED defect fires again")
    a = p.parse_args()

    since = datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc) if a.since else None
    calls = load_calls(since)
    if not calls:
        print("no calls in range")
        return 0

    for call in calls:
        when = call["start_utc"]
        when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
        label, ambiguous = build_at(when)
        for det in DETECTORS:
            try:
                detail = det.fn(call)
            except Exception as exc:                      # a detector must never
                detail = f"DETECTOR ERROR: {exc!r}"       # hide a call from view
            if detail and (det.id, call["call_sid"]) not in KNOWN_FALSE_POSITIVES:
                det.hits.append((when, label, ambiguous, call["call_sid"], detail))

    print(f"{len(calls)} calls  "
          f"{calls[0]['start_utc']:%d %b %H:%M}Z -> {calls[-1]['start_utc']:%d %b %H:%M}Z\n")
    print(f"{'id':4}{'defect':38}{'n':>4}  {'last seen':22}build")
    print("-" * 88)
    for det in DETECTORS:
        if not det.hits:
            print(f"{det.id:4}{det.what:38}{0:>4}  {'-- not observed --':22}")
            continue
        last = sorted(det.hits)[-1]
        print(f"{det.id:4}{det.what:38}{len(det.hits):>4}  "
              f"{last[0]:%d %b %H:%M}Z{'':10}{last[1]}")

    newest = build_at(max(
        (c["start_utc"].replace(tzinfo=timezone.utc)
         if c["start_utc"].tzinfo is None else c["start_utc"]) for c in calls))[0]
    print(f"\n=== occurrences on the newest build seen ({newest}) ===")
    any_live = False
    for det in DETECTORS:
        live = [h for h in det.hits if h[1] == newest and not h[2]]
        for when, _, _, sid, detail in live:
            any_live = True
            print(f"  {det.id}  {when:%d %b %H:%M}Z  {sid[:20]}  {detail}")
    if not any_live:
        print("  none")

    if a.write_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({
            "frozen_utc": datetime.now(timezone.utc).isoformat(),
            "calls_scored": len(calls),
            "detectors": {
                d.id: {"what": d.what, "status": d.status, "count": len(d.hits),
                       "last_build": (sorted(d.hits)[-1][1] if d.hits else None),
                       "evidence": d.evidence}
                for d in DETECTORS},
        }, indent=2) + "\n", encoding="utf-8")
        print(f"\nbaseline written -> {BASELINE}")

    if a.check:
        regressions = [d for d in DETECTORS if d.status == "fixed" and d.hits]
        for d in regressions:
            print(f"\nREGRESSION: {d.id} ({d.what}) is marked fixed but fired "
                  f"{len(d.hits)}x")
        if regressions:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
