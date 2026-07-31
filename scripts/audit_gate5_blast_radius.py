#!/usr/bin/env python3
"""Measure a Gate 5 change's blast radius against every call we have recorded.

WHY THIS EXISTS
---------------
Gate 5 (`sanitise_response`) is the last thing between the model and the
caller's ear. Its dangerous failure is not letting something through — it is
taking something away: a stripped confirmation abandoned a completed booking in
June 2026, and a chunk-level reasoning drop swallowed real slot times in
another incident. Both were caught only after a live call went wrong.

A handful of hand-written "this line must survive" tests is a sample. This is
the population: every assistant turn the system has actually produced.

TWO BLIND SPOTS, BOTH FIXED 2026-07-31
--------------------------------------
This script reported "5 changed turns of 740, 0 emptied — clean" on the same day
Gate 5 was rewriting the caller's chosen booking day back to one they had
abandoned, on every call where anyone changed their mind. It had been doing that
for three weeks (CAb81fe651, CA42486ff4, CAec93b032, CA6dce36c8). Two independent
reasons, and the clean run was false under both:

1. EMPTY SESSION. Every turn was replayed with `session = {}`, so any gate that
   READS session state never executed in either arm. The booking-readback date
   enforcement needs v3_confirmed_slot_phrase and phone_confirmed; with neither
   set it was dead code here. This script only ever measured the stateless
   strip/pattern rules, while reporting as though it covered Gate 5.

   The session is now reconstructed progressively across a call's turns, from the
   transcript, so each turn is sanitised with roughly the state it really had.

2. DIFF-ONLY REPORTING. obs stores the SPOKEN text — post-Gate-5. Feeding that
   back through a REWRITE rule re-applies a change already baked in, so output
   equals input and the diff is empty. The day-rewriting gate is invisible to a
   before/after comparison however good the session state is: live it turned
   "Wednesday the 5th" into "Tuesday the 4th"; on replay it turns "Tuesday the
   4th" into "Tuesday the 4th".

   So it now also reports WHICH GATES FIRE, counted from the log records
   sanitise_response emits. A rewrite that changes nothing on replay is still
   visible as a firing. That is the only signal that can catch this class.

Read the FIRINGS table as carefully as the diffs. A strip rule firing more often
is usually the intent. A rewrite rule firing on turns where the caller had
changed their mind is the 30 Jul defect.

USAGE
-----
    python scripts/audit_gate5_blast_radius.py
    python scripts/audit_gate5_blast_radius.py --limit 20
    python scripts/audit_gate5_blast_radius.py --gate readback

Needs OBS_DATABASE_URL (or a .env containing it).

READING THE OUTPUT
------------------
    turns emptied completely > 0    -> the change is wrong. A caller hears the
                                       "sorry, I didn't catch that" fallback, or
                                       silence, where speech belonged.
    turns changed >> intended       -> the change is catching legitimate speech.
                                       Read the diffs before going further.
    firings differ unexpectedly     -> a state-dependent gate moved. Explain it
                                       before shipping; the diff may be empty and
                                       the behaviour still changed.
    turns changed == intended       -> proceed.

ADDING A NEW RULE TO THE COMPARISON
-----------------------------------
`old_sanitise` below defines what "OLD" means: the rule names it removes from
_BANNED_SENTENCE_RE, plus the gate functions it neutralises. When you add a Gate
5 rule, add it there so the audit can still see a before/after. Leaving it out
silently turns this script into a no-op that always reports 0 — which is exactly
what happened to the readback date gate.

SESSION RECONSTRUCTION IS APPROXIMATE
-------------------------------------
It is rebuilt from what was SPOKEN, because that is all obs keeps. It cannot
reproduce flags the engine set from tool results or DTMF. It is close enough to
put the state-dependent gates on the same code path they take live, which is the
difference between measuring them and not measuring them at all.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Rules added after the baseline this audit compares against. Extend as Gate 5
# grows -- see the note in the module docstring.
NEW_BANNED_RULES = ("markdown_emphasis", "internal_identifier_token")

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}

# "Wednesday 5th August — Number 1, half past five in the evening. Number 2, …"
_SLOT_LIST = re.compile(
    r"([A-Za-z]+day)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?"
    r"(" + "|".join(_MONTHS) + r")", re.I)
_HAS_OPTIONS = re.compile(r"number\s+1\b|number\s+one\b", re.I)
# The name-request readback — where connection.py captures the confirmed phrase.
_NAME_REQUEST = re.compile(r"could i take your|and your surname", re.I)
_SLOT_PHRASE = re.compile(
    r"((?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+"
    r"(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?"
    r"(?:" + "|".join(_MONTHS) + r")(?:\s+at\s+[^—\-.?!]{1,60})?)", re.I)
_PHONE_STEP = re.compile(r"i'?ve got you on|best number|use this number", re.I)
_PHONE_YES = re.compile(r"\b(yes|yeah|yep|correct|it is)\b|that'?s the best", re.I)


def load_calls():
    from sqlalchemy import create_engine, text
    url = os.environ.get("OBS_DATABASE_URL")
    if not url:
        env = REPO / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("OBS_DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
    if not url:
        sys.exit("OBS_DATABASE_URL not set (and no .env)")
    eng = create_engine(url, connect_args={"connect_timeout": 30})
    with eng.connect() as c:
        return list(c.execute(text(
            "SELECT call_sid, start_utc, transcript FROM calls ORDER BY start_utc"
        )).mappings())


def advance_session(session: dict, role: str, text_: str) -> None:
    """Update the reconstructed session as one turn goes by.

    Mirrors, approximately, what connection.py and llm_stream.py set live. Only
    the state the gates actually read is modelled; anything more would be
    invented precision.
    """
    if role == "user":
        # The phone step, once answered, sets phone_confirmed — the switch the
        # readback date enforcement is gated on.
        if session.pop("_phone_asked", False) and _PHONE_YES.search(text_):
            session["phone_confirmed"] = True
        return

    if _PHONE_STEP.search(text_):
        session["_phone_asked"] = True

    m = _SLOT_LIST.search(text_)
    if m and _HAS_OPTIONS.search(text_):
        # A numbered batch was offered: this is the day now on the table, and it
        # is what staleness is judged against.
        try:
            day, month = int(m.group(2)), _MONTHS[m.group(3).lower()]
            session["v3_last_offered_day_iso"] = f"2026-{month:02d}-{day:02d}"
            session["last_offered_slots"] = [
                {"start": f"2026-{month:02d}-{day:02d}T00:00:00"}]
            session["v3_awaiting_slot_selection"] = True
            session["v3_dtmf_slot_map"] = {"1": "", "2": ""}
        except (ValueError, KeyError):
            pass

    if _NAME_REQUEST.search(text_):
        # connection.py captures v3_confirmed_slot_phrase off this turn and never
        # refreshes it — the staleness the gate has to cope with.
        sp = _SLOT_PHRASE.search(text_)
        if sp and "v3_confirmed_slot_phrase" not in session:
            session["v3_confirmed_slot_phrase"] = " ".join(sp.group(1).split())


class GateRecorder(logging.Handler):
    """Counts which gates fire, from the records sanitise_response emits.

    A rewrite that produces identical output on replay still logs, so this sees
    what a before/after diff structurally cannot.
    """

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.counts: Counter = Counter()

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return
        if "[ms_gate5]" not in msg:
            return
        key = msg.split("[ms_gate5]", 1)[1].strip()
        key = re.split(r"[:(]", key, maxsplit=1)[0].strip()[:52]
        self.counts[key] += 1


def speak(raw: str, sanitise, session: dict) -> str:
    """What the caller hears: raw tokens -> ResponseChunker -> gate -> TTS queue.

    Mirrors the live streaming loop in llm_stream.py. The session is passed in
    and shared across a call's turns; isolating it per turn is what hid every
    state-dependent gate.
    """
    from app.media_streams.chunker import ResponseChunker
    chunker, spoken = ResponseChunker(), []
    for token in re.findall(r"\S+\s*", raw):
        chunk = chunker.add_token(token)
        if chunk:
            out = sanitise(chunk, session)
            if out:
                spoken.append(out)
    tail = chunker.flush()
    if tail:
        out = sanitise(tail, session)
        if out:
            spoken.append(out)
    return " ".join(spoken)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40,
                    help="max diffs to print (default 40)")
    ap.add_argument("--gate", help="print only firings whose label contains this")
    args = ap.parse_args()

    calls = load_calls()
    from app.media_streams import turn_handler as th

    old_banned = [(d, p) for d, p in th._BANNED_SENTENCE_RE
                  if d not in NEW_BANNED_RULES]

    def new_sanitise(text, session):
        return th.sanitise_response(text, session)

    def old_sanitise(text, session):
        """The gate as it was: new pattern rules removed, Gate 5g neutralised,
        and the readback-date stand-down reverted to always-correct (2026-07-31).
        """
        saved_list = th._BANNED_SENTENCE_RE
        saved_fn = th._strip_self_narration
        saved_stale = th._confirmed_slot_is_stale
        th._BANNED_SENTENCE_RE = old_banned
        th._strip_self_narration = lambda t: t
        th._confirmed_slot_is_stale = lambda *a, **k: False
        try:
            return th.sanitise_response(text, session)
        finally:
            th._BANNED_SENTENCE_RE = saved_list
            th._strip_self_narration = saved_fn
            th._confirmed_slot_is_stale = saved_stale

    rec_old, rec_new = GateRecorder(), GateRecorder()
    gate_log = logging.getLogger("app.media_streams.turn_handler")
    gate_log.setLevel(logging.INFO)

    n_turns, changes = 0, []
    for call in calls:
        # One session per ARM per call, advanced turn by turn.
        s_old, s_new = {}, {}
        for turn in (call["transcript"] or []):
            role = "user" if (turn.get("role") or "") == "user" else "bot"
            raw = turn.get("text") or ""
            if role == "user" or not raw.strip():
                advance_session(s_old, role, raw)
                advance_session(s_new, role, raw)
                continue
            n_turns += 1

            gate_log.addHandler(rec_old)
            try:
                before = speak(raw, old_sanitise, s_old)
            finally:
                gate_log.removeHandler(rec_old)

            gate_log.addHandler(rec_new)
            try:
                after = speak(raw, new_sanitise, s_new)
            finally:
                gate_log.removeHandler(rec_new)

            if before != after:
                changes.append((call["call_sid"], before, after))
            advance_session(s_old, role, raw)
            advance_session(s_new, role, raw)

    emptied = [c for c in changes if c[1].strip() and not c[2].strip()]

    print(f"calls: {len(calls)}   assistant turns replayed: {n_turns}")
    print(f"turns whose AUDIBLE output changed: {len(changes)}")
    print(f"turns emptied completely: {len(emptied)}")
    if emptied:
        print("\n*** OVER-STRIP — the change is wrong, do not ship it ***")
        for sid, before, _ in emptied[: args.limit]:
            print(f"  {sid[:14]}  silenced: {before[:160]!r}")

    print("\n=== GATE FIRINGS (a rewrite can fire and change nothing on replay) ===")
    print(f"{'old':>7}{'new':>7}  gate")
    print("-" * 78)
    for key in sorted(set(rec_old.counts) | set(rec_new.counts)):
        if args.gate and args.gate.lower() not in key.lower():
            continue
        o, n = rec_old.counts.get(key, 0), rec_new.counts.get(key, 0)
        mark = "   <-- CHANGED" if o != n else ""
        print(f"{o:>7}{n:>7}  {key}{mark}")

    print("\n=== DIFFS (review every one) ===")
    for sid, before, after in changes[: args.limit]:
        print(f"\n{sid[:14]}")
        print(f"  BEFORE: {before[:240]}")
        print(f"  AFTER : {after[:240]}")
    if len(changes) > args.limit:
        print(f"\n... {len(changes) - args.limit} more (raise --limit)")

    return 1 if emptied else 0


if __name__ == "__main__":
    sys.exit(main())
