"""Phase 1: score a slot-layer change against every stored call, not one phone call.

Why this exists
---------------
Every slot fix so far was validated by a test or a single demo call, one at a
time, and two defects (F2b, P11) only surfaced AFTER they shipped. The obs store
holds 800+ real calls. This runs the slot layer's PURE decision functions over
every caller turn that followed a numbered readout and writes a JSON report, so
a change can be diffed against a baseline before it reaches a phone:

    # in a clean worktree at the baseline commit
    python scripts/replay_slot_decisions.py --out BASE.json
    # in the candidate worktree
    python scripts/replay_slot_decisions.py --out CAND.json
    python scripts/replay_slot_decisions.py --diff BASE.json CAND.json

The diff prints N turns changed and the exact before/after for each.

What it can and cannot replay -- read before trusting a green report
--------------------------------------------------------------------
The obs store keeps TRANSCRIPTS, not availability payloads. That splits the
seven producers in two:

  REPLAYABLE (the caller-interpretation half). What the caller heard is exactly
  what the transcript records, so a payload rebuilt from the readout is faithful
  for `slot_accepted_by_caller`, `classify_intent`, `utterance_requests_*`,
  `day_named_by_caller` and `day_selected_by_position`. This is where P6, P6b,
  P9 and F1 all lived.

  NOT REPLAYABLE from this corpus. `remaining_unspoken_on_current_day`,
  `choose_presented_indices` and `all_remaining_on_next_day` decide from the
  FULL day's slot set, and the transcript only ever contains the subset that was
  spoken. Rebuilding a payload from the readout would hand them a diary that is
  by construction equal to what was already read out, and they would agree with
  themselves. A green report here says NOTHING about those three.

So this harness is not the whole Phase 1 gate. It is the half the corpus can
honestly support, and it covers the half that has produced the defects.

Sessions are armed through `apply_offer_to_session` -- the real single writer --
so the record under test is the one the engine would have had.

Run with --redact to drop caller utterance text from the report entirely.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.tools.slot_followup import (  # noqa: E402
    day_named_by_caller,
    day_selected_by_position,
    option_label_candidates,
    slot_accepted_by_caller,
    utterance_requests_different_day,
    utterance_requests_more_slots,
)
from app.tools.slot_offer import apply_offer_to_session  # noqa: E402

try:
    from app.hold_speech import classify_intent
except Exception:  # pragma: no cover - hold_speech is optional to this study
    classify_intent = None

NUMBERED_RE = re.compile(r"Number\s+\d", re.I)
_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december"]
_MONTHS = {m: i + 1 for i, m in enumerate(_MONTH_NAMES)}
_WEEKDAYS = {d: i for i, d in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
     "sunday"])}

DAY_PHRASE_RE = re.compile(
    r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b"
    r"(?:\s+(\d{1,2})(?:st|nd|rd|th)?)?"
    r"(?:\s+(January|February|March|April|May|June|July|August|September|"
    r"October|November|December))?", re.I)

_NUMWORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "twenty": 20,
    "twenty five": 25, "twentyfive": 25, "quarter": 15, "half": 30,
}
_BAND_SHIFT = {"morning": 0, "afternoon": 12, "evening": 12, "night": 12}


def _spoken_to_hhmm(label):
    """Parse a spoken clock label to HH:MM, or None.

    `_candidate_hhmm_from_text` in the engine is a CANDIDATE GENERATOR for
    matching against a payload that already holds the real times -- it returns
    every reading and does not apply "to"/"past" minutes, so "ten to nine"
    comes back as 09:00 and "twenty past four in the afternoon" leads with
    04:00. This harness has no payload to match against; it must build one, so
    it needs a real parser. Kept here rather than in `app/` because nothing in
    the engine wants it: the engine always has the true time already.
    """
    t = re.sub(r"[^a-z0-9: ]", " ", str(label).lower())
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    band = None
    for b in _BAND_SHIFT:
        if b in t:
            band = b
            break
    # Word boundaries matter: "afternoon" contains "noon", and a substring
    # test turned "twenty past four in the afternoon" into 12:00.
    if re.search(r"\b(midday|noon)\b", t):
        return "12:00"
    if re.search(r"\bmidnight\b", t):
        return "00:00"
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
    else:
        words = r"(twenty five|twenty|quarter|half|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})"
        m = re.search(words + r" (past|to) " + words, t)
        if m:
            a, rel, b2 = m.group(1), m.group(2), m.group(3)
            mins = _NUMWORDS.get(a, None)
            if mins is None:
                mins = int(a) if a.isdigit() else None
            hh = _NUMWORDS.get(b2, None)
            if hh is None:
                hh = int(b2) if b2.isdigit() else None
            if mins is None or hh is None:
                return None
            if rel == "past":
                h, mi = hh, mins
            else:
                h, mi = (hh - 1) % 24, (60 - mins) % 60
        else:
            m = re.search(r"\b" + words + r"\b", t)
            if not m:
                return None
            a = m.group(1)
            h = _NUMWORDS.get(a, int(a) if a.isdigit() else None)
            mi = 0
            if h is None:
                return None
    if band and h < 12 and _BAND_SHIFT[band]:
        h += 12
    if band == "morning" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        return None
    return "%02d:%02d" % (h, mi)


BOT_ROLES = ("assistant", "bot", "susie", "agent")
CALLER_ROLES = ("user", "caller", "customer", "human")


def _iter_turns(transcript):
    for t in transcript or []:
        if not isinstance(t, dict):
            continue
        role = str(t.get("role") or "").lower()
        body = str(t.get("text") or "").strip()
        if not body:
            continue
        if role in BOT_ROLES:
            yield "bot", body
        elif role in CALLER_ROLES:
            yield "caller", body


def _day_from_phrase(match, year=2026):
    """A concrete ISO date for a spoken day phrase, or None.

    The real date is not in the transcript in machine form, so it is rebuilt
    from the words. Only the SHAPE matters to the functions under test -- they
    compare dates as strings -- but it must be internally consistent across a
    readout, which is why the same phrase always yields the same date.
    """
    dom, month = match.group(2), match.group(3)
    if dom and month:
        mi = _MONTHS.get(month.lower())
        if mi:
            return "%04d-%02d-%02d" % (year, mi, int(dom))
    # Many readouts name only the weekday ("Tuesday morning I have ..."). The
    # true date is unrecoverable, but these functions only ever compare dates as
    # opaque strings, so a weekday pinned to a fixed reference week is enough --
    # and pinning it makes the SAME weekday yield the SAME date across a
    # readout, which is the only property the record needs. 2026-09-07 is a
    # Monday. Two different real dates sharing a weekday collapse into one; that
    # loses turns rather than inventing verdicts, which is the safe direction.
    wd = _WEEKDAYS.get(match.group(1).lower())
    if wd is None:
        return None
    return "2026-09-%02d" % (7 + wd)


def _payload_from_readout(body):
    """Rebuild an available_days payload from what Susie actually said.

    Faithful for the caller-interpretation functions: the caller could only ever
    have picked from what was spoken, and that is precisely what is here.
    """
    opts = option_label_candidates(body) or {}
    if not opts:
        return None, None
    day_marks = [(m.start(), m) for m in DAY_PHRASE_RE.finditer(body)]
    if not day_marks:
        return None, None

    def day_for(pos):
        best = day_marks[0][1]
        for start, m in day_marks:
            if start <= pos:
                best = m
            else:
                break
        return best

    days, order = {}, []
    for _key_, labels in opts.items():
        label = (list(labels) or [None])[0]
        if not label:
            continue
        # The candidate label can run to the end of the sentence ("ten past
        # five in the evening. Any of those work?"). Only the clock phrase is
        # the label -- the tail would otherwise be matched against caller text.
        label = re.split(r"[.?!]", str(label))[0].strip()
        if not label:
            continue
        pos = body.find(label)
        m = day_for(pos if pos >= 0 else 0)
        date = _day_from_phrase(m)
        if not date:
            return None, None
        hhmm = _spoken_to_hhmm(label)
        if not hhmm:
            continue
        hhmm = [hhmm]
        day = days.setdefault(date, {
            "date": date,
            "day_label": m.group(0).strip(),
            "slot_times": [], "slot_times_spoken": [], "slots": [],
        })
        if date not in order:
            order.append(date)
        day["slot_times"].append(hhmm[0])
        day["slot_times_spoken"].append(str(label))
        day["slots"].append({"start": "%sT%s:00" % (date, hhmm[0])})
    payload = [days[d] for d in order if days[d]["slots"]]
    if not payload:
        return None, None
    mode = "multi_day" if len(payload) > 1 else "single_day"
    return payload, mode


def _arm_session(payload, mode):
    """A session holding this offer, written by the real single writer."""
    slots, dtmf, n = [], {}, 0
    for day in payload:
        for i, s in enumerate(day["slots"]):
            slot = {
                "start": s["start"],
                "date": day["date"],
                "day_label": day["day_label"],
                "time": day["slot_times"][i],
                "spoken": day["slot_times_spoken"][i],
            }
            slots.append(slot)
            n += 1
            dtmf[n] = slot["spoken"]
    session = {"available_days": payload}
    apply_offer_to_session(
        session, {"slots": slots, "dtmf_map": dtmf, "mode": mode}, ["readout"]
    )
    session["available_days"] = payload
    return session


def _decide(session, payload, utterance, readout):
    """Every pure verdict this utterance produces. Order is the report's shape."""
    out = {}

    def safe(name, fn):
        try:
            out[name] = fn()
        except Exception as exc:
            out[name] = "RAISED:%s" % type(exc).__name__

    safe("accepted", lambda: slot_accepted_by_caller(session, utterance))
    safe("more_slots", lambda: bool(utterance_requests_more_slots(utterance)))
    safe("different_day",
         lambda: bool(utterance_requests_different_day(utterance)))
    safe("day_named", lambda: day_named_by_caller(payload, utterance))
    safe("day_by_position", lambda: day_selected_by_position(
        session.get("last_offered_slots"), utterance))
    if classify_intent is not None:
        safe("intent", lambda: [str(i) for i in (classify_intent(
            utterance, readout,
            slot_selection=bool(out.get("accepted"))) or [])])
    return out


def collect(limit=0, clinic=None, redact=False):
    from sqlalchemy import create_engine, text as sql

    engine = create_engine(os.environ["OBS_DATABASE_URL"])
    q = ("select call_sid, clinic_id, transcript from calls "
         "where transcript is not null")
    if clinic:
        q += " and clinic_id = :clinic"
    q += " order by call_sid"
    if limit:
        q += " limit %d" % limit

    rows = []
    with engine.connect() as conn:
        res = conn.execute(sql(q), {"clinic": clinic} if clinic else {})
        for call_sid, clinic_id, transcript in res:
            if isinstance(transcript, str):
                try:
                    transcript = json.loads(transcript)
                except Exception:
                    continue
            rows.append((call_sid, clinic_id, transcript))

    report, stats = [], Counter()
    for call_sid, clinic_id, transcript in rows:
        stats["calls"] += 1
        # A readout is not one turn. The obs store records speech at the TTS
        # loop, so "The available slots for Tuesday 8th September are —" and
        # "Number 2, ten to twelve in the morning." arrive as separate bot
        # turns. Taking the last numbered turn alone threw away the day header
        # and made a third of the corpus unparsable. So the readout is every
        # consecutive bot turn since the caller last spoke, joined.
        pending, last_readout = [], None
        for role, body in _iter_turns(transcript):
            if role == "bot":
                pending.append(body)
                continue
            block = " ".join(pending)
            if pending and NUMBERED_RE.search(block):
                last_readout = block
            pending = []
            if not last_readout:
                continue
            stats["turns_after_readout"] += 1
            payload, mode = _payload_from_readout(last_readout)
            if not payload:
                stats["unparsable_readout"] += 1
                continue
            try:
                session = _arm_session(payload, mode)
            except Exception as exc:
                stats["arm_failed"] += 1
                report.append({"call": call_sid,
                               "error": "arm:%s" % type(exc).__name__})
                continue
            stats["scored"] += 1
            entry = {
                "call": call_sid,
                "clinic": clinic_id,
                "mode": mode,
                "utterance": "<redacted>" if redact else body[:160],
                "decisions": _decide(session, payload, body, last_readout),
            }
            if entry["decisions"].get("accepted"):
                stats["resolved_a_pick"] += 1
            report.append(entry)
    return report, stats


def _key(e):
    return (e.get("call"), e.get("utterance"), e.get("mode"))


def do_diff(a_path, b_path):
    a = json.load(open(a_path, encoding="utf-8"))
    b = json.load(open(b_path, encoding="utf-8"))
    A = {_key(e): e for e in a["report"]}
    B = {_key(e): e for e in b["report"]}
    changed = []
    for k in sorted(set(A) | set(B), key=lambda t: tuple(str(x) for x in t)):
        da = (A.get(k) or {}).get("decisions", {})
        db = (B.get(k) or {}).get("decisions", {})
        if da != db:
            fields = sorted(set(da) | set(db))
            changed.append((k, {f: (da.get(f), db.get(f))
                                for f in fields if da.get(f) != db.get(f)}))
    print("baseline turns: %d   candidate turns: %d" % (len(A), len(B)))
    print("CHANGED: %d" % len(changed))
    for (call, utt, mode), fields in changed:
        print("\n  %s  [%s]" % (call, mode))
        print("    utterance: %s" % utt)
        for f, (x, y) in fields.items():
            print("    %-16s %r  ->  %r" % (f, x, y))
    if not changed:
        print("\nNo decision changed on any scored turn.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--clinic")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--redact", action="store_true")
    ap.add_argument("--diff", nargs=2, metavar=("BASE", "CAND"))
    args = ap.parse_args()

    if args.diff:
        return do_diff(*args.diff)

    report, stats = collect(args.limit, args.clinic, args.redact)
    for k in ("calls", "turns_after_readout", "scored", "unparsable_readout",
              "arm_failed", "resolved_a_pick"):
        print("%-22s %d" % (k, stats[k]))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"stats": dict(stats), "report": report}, fh,
                      indent=1, sort_keys=True, default=str)
        print("\nwrote %s (%d turns)" % (args.out, len(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
