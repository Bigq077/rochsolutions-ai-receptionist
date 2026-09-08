"""Score the P6b stand-down against every stored call, not one phone call.

`accepted_slot_is_named_in` decides whether Gate 5 suppresses the deterministic
slot offer and speaks the MODEL instead. It is the highest-consequence boolean
in the slot layer: a wrong True makes the caller listen to a list they did not
ask for (CA4215ab7f, 16.1 seconds, abandoned), and a wrong False throws away the
model's correct confirmation and reads the list a second time (CA5a126fe4, the
call P6b was written for). Both shapes have abandoned a real call.

`replay_slot_decisions.py` cannot cover it: every function it scores takes
CALLER text, and this one takes the MODEL's. So this harness walks the same
corpus from the other side.

    python scripts/replay_p6b_stand_downs.py --out CAND.json
    # in a worktree at the baseline commit
    python scripts/replay_p6b_stand_downs.py --out BASE.json
    python scripts/replay_p6b_stand_downs.py --diff BASE.json CAND.json

WHAT IT REPLAYS
---------------
For each call: rebuild the payload from a numbered readout exactly as
`replay_slot_decisions` does, find the caller turn that `slot_accepted_by_caller`
resolves, pin that slot as `_accepted_slot_iso`, and ask
`accepted_slot_is_named_in` of the bot block that FOLLOWED it. That is precisely
the question Gate 5 asks, with both sides taken from what was really said.

WHAT IT DOES NOT
----------------
It cannot tell whether the model's next sentence was a confirmation or a fresh
list -- that judgement is what is under test. So the diff is the product, not
the absolute count: a turn whose verdict CHANGED is a turn a human should read.
The `sentence_numbers_options` column is the cheap discriminator to read them
by: a stand-down on a numbered multi-day block is almost always the defect, and
a stand-down on an unnumbered short sentence is almost always correct.

It also inherits every limit of the payload rebuild -- weekday-only readouts
collapse onto a fixed reference week, and unparsable readouts are skipped and
counted. Both lose turns rather than invent verdicts.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.tools.slot_followup import (  # noqa: E402
    ACCEPTED_SLOT_KEY,
    accepted_slot_is_named_in,
    slot_accepted_by_caller,
)

from scripts.replay_slot_decisions import (  # noqa: E402
    NUMBERED_RE,
    _arm_session,
    _iter_turns,
    _payload_from_readout,
)


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
        for call_sid, clinic_id, transcript in conn.execute(
                sql(q), {"clinic": clinic} if clinic else {}):
            if isinstance(transcript, str):
                try:
                    transcript = json.loads(transcript)
                except Exception:
                    continue
            rows.append((call_sid, clinic_id, transcript))

    report, stats = [], Counter()
    for call_sid, clinic_id, transcript in rows:
        stats["calls"] += 1
        # The obs store records speech at the TTS loop, so one readout arrives
        # as several consecutive bot turns. Same joining rule as the sibling
        # harness, for the same reason.
        turns = list(_iter_turns(transcript))
        pending, last_readout = [], None
        pending_pick = None          # (session, iso, utterance)
        for role, body in turns:
            if role == "bot":
                pending.append(body)
                continue

            block = " ".join(pending)
            if pending:
                if pending_pick is not None:
                    # THE MEASUREMENT: the caller accepted a slot, and this is
                    # what the model said next.
                    session, iso, said = pending_pick
                    session[ACCEPTED_SLOT_KEY] = iso
                    stats["scored"] += 1
                    try:
                        verdict = bool(accepted_slot_is_named_in(session, block))
                    except Exception as exc:
                        verdict = "RAISED:%s" % type(exc).__name__
                        stats["raised"] += 1
                    if verdict is True:
                        stats["stood_down"] += 1
                    report.append({
                        "call": call_sid,
                        "clinic": clinic_id,
                        "accepted": iso,
                        "pick": "<redacted>" if redact else said[:120],
                        "sentence": "<redacted>" if redact else block[:300],
                        "sentence_numbers_options": bool(
                            NUMBERED_RE.search(block)),
                        "stands_down": verdict,
                    })
                    pending_pick = None
                if NUMBERED_RE.search(block):
                    last_readout = block
            pending = []

            if not last_readout:
                continue
            payload, mode = _payload_from_readout(last_readout)
            if not payload:
                stats["unparsable_readout"] += 1
                continue
            try:
                session = _arm_session(payload, mode)
                iso = slot_accepted_by_caller(session, body)
            except Exception:
                stats["arm_failed"] += 1
                continue
            if iso:
                stats["picks"] += 1
                pending_pick = (session, iso, body)
    return report, stats


def _key(e):
    return (e.get("call"), e.get("accepted"), (e.get("pick") or "")[:60])


def do_diff(a_path, b_path):
    a = {_key(e): e for e in json.load(open(a_path))["report"]}
    b = {_key(e): e for e in json.load(open(b_path))["report"]}
    shared = set(a) & set(b)
    changed = [k for k in shared if a[k]["stands_down"] != b[k]["stands_down"]]
    print("baseline %d  candidate %d  shared %d  CHANGED %d"
          % (len(a), len(b), len(shared), len(changed)))
    if set(a) ^ set(b):
        print("!! %d turn(s) present in only one report -- the corpus or the "
              "payload rebuild moved, so this diff is not clean"
              % len(set(a) ^ set(b)))
    to_false = [k for k in changed if a[k]["stands_down"] is True]
    to_true = [k for k in changed if a[k]["stands_down"] is not True]
    print("  stand-down REMOVED (was True, now False): %d" % len(to_false))
    print("  stand-down ADDED   (was False, now True): %d" % len(to_true))
    for label, keys in (("REMOVED", to_false), ("ADDED", to_true)):
        for k in keys:
            e = b[k]
            print("\n-- %s  %s  %s  numbered=%s" % (
                label, e["call"][:12], e["accepted"],
                e["sentence_numbers_options"]))
            print("   pick: %s" % e["pick"])
            print("   said: %s" % e["sentence"][:220])
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--clinic")
    ap.add_argument("--redact", action="store_true")
    ap.add_argument("--diff", nargs=2)
    args = ap.parse_args()

    if args.diff:
        return do_diff(*args.diff)

    report, stats = collect(args.limit, args.clinic, args.redact)
    print(json.dumps(dict(stats), indent=2))
    numbered_standdowns = [
        e for e in report
        if e["stands_down"] is True and e["sentence_numbers_options"]]
    print("stand-downs on a NUMBERED sentence: %d  <- read these"
          % len(numbered_standdowns))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"stats": dict(stats), "report": report}, fh, indent=1)
        print("wrote %s (%d turns)" % (args.out, len(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
