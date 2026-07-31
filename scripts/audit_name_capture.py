#!/usr/bin/env python3
"""Audit how the caller's name is asked for, answered, and stored.

WHY THIS EXISTS
---------------
The name is the only free-text field that reaches a real calendar entry. A wrong
day is caught by the write-guard; a wrong surname is caught by nothing — it looks
exactly like a right one. CA6dce36c8 (31 Jul 2026) stored "Sara Six" because the
caller's answer to a TIME question ("six") was back-filled as her surname, and
the A3 detector scored 0 on it.

The reported symptom is inconsistency: sometimes she asks for the surname, other
times she goes straight to the phone step. This measures that rather than
inferring it from the last call anyone happened to listen to.

USAGE
-----
    python scripts/audit_name_capture.py                 # since Mon 27 Jul
    python scripts/audit_name_capture.py --since 2026-07-30
    python scripts/audit_name_capture.py --show SKIPPED_SURNAME
    python scripts/audit_name_capture.py --detail CA6dce36c8

Needs OBS_DATABASE_URL (read from .env if unset), like the other obs scripts.

WHAT IT CANNOT SEE
------------------
obs stores the SPOKEN text, i.e. post-Gate-5. A name that a gate rewrote looks
like a name the model produced. It also stores no tool-call trace, so "which code
path stored this" is a Render log question.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# How she asks. Split deliberately: the COMBINED ask expects both parts in one
# answer; the SURNAME-only ask is a follow-up and means the first answer was
# incomplete. Which one runs, and whether the follow-up is honoured, is the
# inconsistency being measured.
ASK_BOTH = re.compile(r"first name and surname|your full name", re.I)
ASK_SURNAME = re.compile(r"\band your surname\b|\bcould i take your surname\b"
                         r"|\byour surname\?", re.I)
ASK_FIRST = re.compile(r"first name as well|your first name\?", re.I)
ASK_REPEAT = re.compile(r"say your name again", re.I)
# The phone step — Step 8. Reaching this is what "goes straight to the phone
# number" means.
PHONE_STEP = re.compile(r"i'?ve got you on|best number|use this number", re.I)
THANKS = re.compile(r"\bthanks,?\s+([A-Za-z][a-z]+)", re.I)

# Words that are never a surname. "six" became one on CA6dce36c8; the rest are
# the other one-word answers a caller gives around the name step.
NOT_A_NAME = {
    "yes", "yeah", "yep", "no", "nope", "ok", "okay", "sure", "please",
    "thanks", "thank", "correct", "right", "fine", "one", "two", "three",
    "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "morning", "afternoon", "evening", "tuesday", "wednesday", "thursday",
    "monday", "friday", "saturday", "sunday", "today", "tomorrow", "past",
    "quarter", "half", "number", "that", "this", "it", "is", "the",
}


def _turns(call):
    """(index, role, text) for every turn, assistant roles normalised."""
    out = []
    for i, t in enumerate(call["transcript"] or []):
        role = "user" if (t.get("role") or "") == "user" else "bot"
        out.append((i, role, t.get("text") or ""))
    return out


def _user_words(call) -> set:
    w = set()
    for _, role, tx in _turns(call):
        if role == "user":
            w |= set(re.findall(r"[a-z']+", tx.lower()))
    return w


def classify(call) -> dict:
    """One row per call: what was asked, what was stored, and what went wrong."""
    turns = _turns(call)
    stored = ((call["collected"] or {}).get("name") or "").strip()
    tokens = [t for t in stored.split() if t]

    ask_both = next((i for i, r, tx in turns if r == "bot" and ASK_BOTH.search(tx)), None)
    ask_sur = next((i for i, r, tx in turns if r == "bot" and ASK_SURNAME.search(tx)), None)
    ask_first = next((i for i, r, tx in turns if r == "bot" and ASK_FIRST.search(tx)), None)
    repeats = sum(1 for _, r, tx in turns if r == "bot" and ASK_REPEAT.search(tx))
    phone = next((i for i, r, tx in turns if r == "bot" and PHONE_STEP.search(tx)), None)
    asked_at = min([i for i in (ask_both, ask_sur, ask_first) if i is not None], default=None)

    flags = []
    if asked_at is None and phone is not None:
        flags.append("NEVER_ASKED")
    if repeats:
        flags.append(f"REASKED_x{repeats}")

    # "Asks for the surname, then goes straight to the phone number" — the
    # follow-up ask is issued and the very next thing she says is Step 8.
    if ask_sur is not None and phone is not None and phone > ask_sur:
        between = [tx for i, r, tx in turns if r == "bot" and ask_sur < i < phone]
        if not between:
            answered = [tx for i, r, tx in turns if r == "user" and ask_sur < i < phone]
            if not answered:
                flags.append("SURNAME_ASK_UNANSWERED")

    if not stored:
        flags.append("NOTHING_STORED")
    elif len(tokens) == 1:
        flags.append("FIRST_ONLY")
    else:
        # Every stored token should be a word the caller actually said. A token
        # that is not is either invented or back-filled from another answer.
        spoken = _user_words(call)
        for tok in tokens:
            low = tok.lower()
            if low in NOT_A_NAME:
                flags.append(f"JUNK_TOKEN:{tok}")
            elif low not in spoken:
                flags.append(f"UNSPOKEN_TOKEN:{tok}")

    return {
        "sid": call["call_sid"],
        "when": call["start_utc"],
        "stored": stored or "-",
        "asked": ("both" if ask_both is not None else
                  "surname_only" if ask_sur is not None else
                  "first_only" if ask_first is not None else "none"),
        "followed_up": ask_sur is not None and ask_both is not None,
        "booked": bool(call.get("booking_confirmed")),
        "flags": flags,
        "build": (call.get("build_sha") or "")[:12],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-27")
    ap.add_argument("--show", help="print only calls carrying this flag prefix")
    ap.add_argument("--detail", help="print the name turns of one call (SID prefix)")
    args = ap.parse_args()

    from detect_defects import load_calls

    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    calls = load_calls(since)

    if args.detail:
        for c in calls:
            if not c["call_sid"].startswith(args.detail):
                continue
            print(f"=== {c['call_sid']}  stored={(c['collected'] or {}).get('name')!r}")
            for i, role, tx in _turns(c):
                print(f"  [{role}] {tx[:150]}")
        return 0

    rows = [classify(c) for c in calls]
    if args.show:
        rows_p = [r for r in rows if any(f.startswith(args.show) for f in r["flags"])]
    else:
        rows_p = rows

    print(f"{len(calls)} calls since {since:%a %d %b}\n")
    print(f"{'when':13}{'stored':24}{'asked':13}{'bk':4}flags")
    print("-" * 104)
    for r in sorted(rows_p, key=lambda r: r["when"]):
        print(f"{r['when']:%d %b %H:%M}  {r['stored'][:22]:24}{r['asked']:13}"
              f"{'Y' if r['booked'] else '-':4}{','.join(r['flags'])}")

    print("\n=== how the name was asked ===")
    for k, n in Counter(r["asked"] for r in rows).most_common():
        print(f"  {n:4}  {k}")
    print("\n=== defects ===")
    fl = Counter(f.split(":")[0] for r in rows for f in r["flags"])
    clean = sum(1 for r in rows if not r["flags"])
    for k, n in fl.most_common():
        print(f"  {n:4}  {k}")
    print(f"  {clean:4}  CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
