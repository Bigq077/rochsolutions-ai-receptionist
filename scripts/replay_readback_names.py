"""Score the booking read-back's NAME against every stored call.

The read-back is composed by the model from conversation history; the name that
gets WRITTEN comes from `collected`. Nothing reconciles the two, so this asks
the corpus how often they disagree, and what a change to the steer would do to
every read-back that has ever been spoken.

    python scripts/replay_readback_names.py              # summary
    python scripts/replay_readback_names.py --detail     # every changed case
    python scripts/replay_readback_names.py --redact     # no names in output

Needs OBS_DATABASE_URL (it is in .env).

WHAT IT CAN AND CANNOT SEE — read before trusting a number
----------------------------------------------------------
REPLAYABLE. The read-back sentence is in the transcript verbatim, and the
stored name is in `collected`. Both are facts about a call that happened, so
"did these two disagree" is answered exactly, not modelled.

NOT REPLAYABLE. Whether the steer would have CHANGED the model's output. This
scores what the steer INSTRUCTS against what was said; a model that ignores the
instruction is not visible here and only a live call can settle it. Read the
"CHANGES the name spoken" figure as the number of read-backs the steer targets,
not as a promise about the model.

The session state is also not stored, so the read-back moment is inferred from
the presence of a read-back rather than from `phone_confirmed`. That is sound
for this question — a call that produced the sentence was in the state that
produces it — but it means the state gating in `readback_name_steer` is covered
by tests, not by this.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.name_capture import name_is_plausible  # noqa: E402

#: The booking read-back, exactly: "So that's <Name>, <Weekday> the 9th ...".
#: The name must be capitalised and a weekday must follow the comma. A looser
#: matcher ("that's <anything>") counted nine instances of "That's reassuring"
#: as read-backs and inflated the disagreement rate by half.
READBACK_RE = re.compile(
    r"that(?:'|’)?s\s+"
    r"([A-Z][A-Za-z'\-]*(?:\s+[A-Z][A-Za-z'\-]*){0,2})"
    r"\s*,\s*(?:on\s+)?(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day"
)


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z\s]", " ", (s or "").lower()).split())


def collect():
    from sqlalchemy import create_engine, text as sql

    engine = create_engine(os.environ["OBS_DATABASE_URL"])
    q = ("select call_sid, clinic_id, collected, transcript, booking_confirmed "
         "from calls where transcript is not null and collected is not null")
    out = []
    with engine.connect() as conn:
        for sid, clinic, coll, tr, booked in conn.execute(sql(q)):
            if isinstance(tr, str):
                try:
                    tr = json.loads(tr)
                except Exception:
                    continue
            if isinstance(coll, str):
                try:
                    coll = json.loads(coll)
                except Exception:
                    continue
            stored = (coll or {}).get("full_name") or (coll or {}).get("name")
            if not stored or not isinstance(stored, str):
                continue
            for turn in (tr or []):
                if not isinstance(turn, dict) or turn.get("role") != "assistant":
                    continue
                txt = turn.get("text") or ""
                if not isinstance(txt, str):
                    continue
                m = READBACK_RE.search(txt)
                if m:
                    out.append({
                        "call_sid": sid, "clinic": clinic,
                        "stored": stored.strip(), "spoken": m.group(1).strip(),
                        "booked": bool(booked),
                    })
                    break
    return out


def score(rows):
    buckets = {"unchanged": [], "gains_surname": [], "changes_name": [],
               "stands_down": []}
    for r in rows:
        if not name_is_plausible(r["stored"]):
            buckets["stands_down"].append(r)
            continue
        ns, nk = _norm(r["stored"]), _norm(r["spoken"])
        if ns == nk:
            buckets["unchanged"].append(r)
        elif nk and ns.startswith(nk + " "):
            buckets["gains_surname"].append(r)
        else:
            buckets["changes_name"].append(r)
    return buckets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--redact", action="store_true")
    a = ap.parse_args()

    rows = collect()
    b = score(rows)
    fires = len(rows) - len(b["stands_down"])

    print("read-backs in corpus            : %d" % len(rows))
    print()
    print("  steer FIRES                   : %d" % fires)
    print("     - read-back unchanged      : %d" % len(b["unchanged"]))
    print("     - GAINS the surname        : %d" % len(b["gains_surname"]))
    print("     - CHANGES the name spoken  : %d" % len(b["changes_name"]))
    print("  steer STANDS DOWN             : %d  (implausible stored name)"
          % len(b["stands_down"]))
    print()
    booked = sum(1 for r in b["changes_name"] if r["booked"])
    print("of the changed read-backs, %d reached a real calendar" % booked)

    if not a.detail:
        return
    for label in ("changes_name", "stands_down", "gains_surname"):
        print()
        print("=== %s ===" % label)
        for r in b[label]:
            if a.redact:
                print("  [%-10s] %s" % (r["clinic"], r["call_sid"][:18]))
            else:
                print("  [%-10s] spoken=%-16r stored=%-18r booked=%s"
                      % (r["clinic"], r["spoken"], r["stored"], r["booked"]))


if __name__ == "__main__":
    main()
