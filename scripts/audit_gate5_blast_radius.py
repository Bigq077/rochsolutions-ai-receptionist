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
the population: every assistant turn the system has actually produced. It
replays each one through the OLD gate and the NEW gate and reports only what
changed, so an over-strip is visible before a caller hears it.

Written 2026-07-29 for the A1 class rules. That change reports 5 changed turns
of 656 and 0 emptied; an earlier revision of it reported 30, because a
split/rejoin was normalising whitespace on every multi-sentence turn. Nothing
but this script would have shown that.

USAGE
-----
    python scripts/audit_gate5_blast_radius.py
    python scripts/audit_gate5_blast_radius.py --limit 20

Needs OBS_DATABASE_URL (or a .env containing it).

READING THE OUTPUT
------------------
    turns emptied completely > 0    -> the change is wrong. A caller hears the
                                       "sorry, I didn't catch that" fallback, or
                                       silence, where speech belonged.
    turns changed >> intended       -> the change is catching legitimate speech.
                                       Read the diffs before going further.
    turns changed == intended       -> proceed.

ADDING A NEW RULE TO THE COMPARISON
-----------------------------------
`_disable_new_rules` below defines what "OLD" means: the rule names it removes
from _BANNED_SENTENCE_RE, plus the extra gate functions it neutralises. When you
add a Gate 5 rule, add its name here so the audit can still see a before/after.
Leaving it out silently turns this script into a no-op that always reports 0.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Rules added after the baseline this audit compares against. Extend as Gate 5
# grows -- see the note in the module docstring.
NEW_BANNED_RULES = ("markdown_emphasis", "internal_identifier_token")


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


def speak(raw: str, sanitise) -> str:
    """What the caller hears: raw tokens -> ResponseChunker -> gate -> TTS queue.

    Mirrors the live streaming loop in llm_stream.py. A fresh session per turn,
    so per-session counters cannot leak between turns and skew the comparison.
    """
    from app.media_streams.chunker import ResponseChunker
    session, chunker, spoken = {}, ResponseChunker(), []
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
    args = ap.parse_args()

    calls = load_calls()
    from app.media_streams import turn_handler as th

    new_sanitise = th.sanitise_response
    old_banned = [(d, p) for d, p in th._BANNED_SENTENCE_RE
                  if d not in NEW_BANNED_RULES]

    def old_sanitise(text, session):
        """The gate as it was: new pattern rules removed, Gate 5g neutralised."""
        saved_list, saved_fn = th._BANNED_SENTENCE_RE, th._strip_self_narration
        th._BANNED_SENTENCE_RE = old_banned
        th._strip_self_narration = lambda t: t
        try:
            return th.sanitise_response(text, session)
        finally:
            th._BANNED_SENTENCE_RE = saved_list
            th._strip_self_narration = saved_fn

    n_turns, changes = 0, []
    for call in calls:
        for turn in (call["transcript"] or []):
            if (turn.get("role") or "") == "user":
                continue
            raw = turn.get("text") or ""
            if not raw.strip():
                continue
            n_turns += 1
            before, after = speak(raw, old_sanitise), speak(raw, new_sanitise)
            if before != after:
                changes.append((call["call_sid"], before, after))

    emptied = [c for c in changes if c[1].strip() and not c[2].strip()]

    print(f"calls: {len(calls)}   assistant turns replayed: {n_turns}")
    print(f"turns whose AUDIBLE output changed: {len(changes)}")
    print(f"turns emptied completely: {len(emptied)}")
    if emptied:
        print("\n*** OVER-STRIP — the change is wrong, do not ship it ***")
        for sid, before, _ in emptied[: args.limit]:
            print(f"  {sid[:14]}  silenced: {before[:160]!r}")

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
