"""Every stored caller turn, against every stored offer, through the resolver.

A DIFFERENTIAL harness. It answers one question:

    does this branch resolve any real caller's words differently from another?

Run it on two checkouts and diff the output. Identical output means no stored
caller's pick changed; a diff is the exact list of callers whose outcome moved,
with the words they said.

    git worktree add --detach /tmp/base <sha>
    cp .env /tmp/base/.env
    (cd /tmp/base && python scripts/replay_slot_resolutions.py > /tmp/base.txt)
    python scripts/replay_slot_resolutions.py > /tmp/head.txt
    diff /tmp/base.txt /tmp/head.txt

── WHY IT EXISTS ──────────────────────────────────────────────────────────────
`scripts/replay_day_picks.py` (gate 1a) classifies day-naming turns with its own
scorer; it does not call `slot_accepted_by_caller`, so a change to the resolver
can leave its numbers untouched. B-138 did exactly that -- its 835-call
classification was byte-identical with the fix on and off -- while the defect it
fixed had booked a live caller onto the wrong day the same morning.

This harness closes that gap by calling the real function, with a session built
by the real `apply_offer_to_session`, from the real payload the engine stored.

── WHAT IT IS NOT ─────────────────────────────────────────────────────────────
It does NOT reconstruct the call's timeline. Every caller turn in a call is run
against every offer in that call, including turns that came before the offer was
made. That over-approximates on purpose: as a differential the only requirement
is that the inputs are deterministic and cover the interesting states, and a
turn that could never have followed an offer still exercises the resolver
exactly as a turn that could. Do not read an individual line as "this is what
happened on the call" -- read the DIFF.

Coverage is bounded by the `slot_offers` column, which the engine only began
writing recently. Print the header to see how many calls carry one; when that
number is small, this is a sharp instrument on a narrow corpus, not a survey.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tools.slot_followup import slot_accepted_by_caller  # noqa: E402
from app.tools.slot_offer import apply_offer_to_session  # noqa: E402

try:                                    # B-139; absent on older checkouts
    from app.tools.slot_followup import payload_slots_named_in
except ImportError:                     # pragma: no cover
    payload_slots_named_in = None


def _load():
    url = os.getenv("OBS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("OBS_DATABASE_URL is not set", file=sys.stderr)
        raise SystemExit(2)
    from sqlalchemy import create_engine, text as _sql

    engine = create_engine(url)
    with engine.connect() as conn:
        return conn.execute(_sql(
            "select call_sid, clinic_id, transcript, slot_offers "
            "from calls where slot_offers is not null "
            "order by start_utc, call_sid"
        )).fetchall()


def _as_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    return value if isinstance(value, list) else []


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    rows = _load()
    calls = offers = turns = resolved = 0
    narrowed_states = 0
    lines: list[str] = []
    narrow_lines: list[str] = []

    for sid, clinic, transcript, slot_offers in rows:
        stored = _as_list(slot_offers)
        script = _as_list(transcript)
        if not stored or not script:
            continue
        said = [
            str(t.get("text") or "").strip()
            for t in script
            if isinstance(t, dict) and (t.get("role") or "") == "user"
        ]
        said = [s for s in said if s]
        spoke = [
            str(t.get("text") or "").strip()
            for t in script
            if isinstance(t, dict) and (t.get("role") or "") == "assistant"
        ]
        spoke = [s for s in spoke if s]
        if not said:
            continue
        calls += 1

        for record in stored:
            if not isinstance(record, dict):
                continue
            payload = record.get("payload")
            offer = record.get("offer")
            if not isinstance(payload, list) or not isinstance(offer, dict):
                continue
            offers += 1
            seq = record.get("seq")

            for utterance in said:
                # A fresh session per turn: the resolver is pure with respect to
                # the session, but `apply_offer_to_session` is not, and a shared
                # session would make each line depend on the ones before it --
                # which would make the DIFF depend on them too.
                session = {"clinic_id": clinic, "available_days": payload}
                apply_offer_to_session(session, offer, offer.get("chunks") or [])
                turns += 1
                try:
                    got = slot_accepted_by_caller(session, utterance)
                except Exception as exc:                      # pragma: no cover
                    got = "RAISED %s: %s" % (type(exc).__name__, exc)
                if got:
                    resolved += 1
                lines.append("%s seq=%s %-64r -> %s" % (
                    sid[:14], seq, utterance[:64], got,
                ))

            # ── PASS B: the NARROWED state ────────────────────────────────
            # The offer above is what she read out, and it always spans several
            # days. The state that matters most never appears there: after a P6
            # stand-down narrows the record to ONE day, the resolver's
            # last-resort branch becomes reachable, and that branch is what put
            # a live caller on the wrong day (B-138).
            #
            # Reached here the same way the engine reaches it -- every sentence
            # she actually spoke, through payload_slots_named_in, and whatever
            # it names applied by apply_offer_to_session with an empty keypad
            # map, exactly as the stand-down branch does.
            #
            # Without this pass the harness is BLIND to B-138: verified by
            # neutering that guard and diffing, which moved nothing at all.
            if payload_slots_named_in is None:
                continue
            for sentence in spoke:
                probe = {"clinic_id": clinic, "available_days": payload}
                apply_offer_to_session(probe, offer, offer.get("chunks") or [])
                named = payload_slots_named_in(probe, sentence)
                if not named:
                    continue
                narrowed_states += 1
                for utterance in said:
                    session = {"clinic_id": clinic, "available_days": payload}
                    apply_offer_to_session(
                        session, offer, offer.get("chunks") or [])
                    apply_offer_to_session(
                        session,
                        {"slots": named, "dtmf_map": {}, "more_times": False,
                         "mode": "single_day"},
                        [sentence],
                    )
                    turns += 1
                    try:
                        got = slot_accepted_by_caller(session, utterance)
                    except Exception as exc:              # pragma: no cover
                        got = "RAISED %s: %s" % (type(exc).__name__, exc)
                    if got:
                        resolved += 1
                    narrow_lines.append("%s seq=%s narrowed=%s %-52r -> %s" % (
                        sid[:14], seq,
                        ",".join(sorted({str(s.get("start"))[:10]
                                         for s in named})),
                        utterance[:52], got,
                    ))

    print("=" * 78)
    print("SLOT RESOLUTION DIFFERENTIAL")
    print("=" * 78)
    print("calls carrying a stored offer : %5d" % calls)
    print("offers replayed               : %5d" % offers)
    print("narrowed states reached       : %5d%s" % (
        narrowed_states,
        "" if payload_slots_named_in else "   (payload_slots_named_in absent)",
    ))
    print("caller turns resolved          : %5d" % turns)
    print("resolved to a slot            : %5d" % resolved)
    print("-" * 78)
    print("Every line below. Diff this against another checkout; the lines that")
    print("differ are the callers whose outcome moved.")
    print("-" * 78)
    print("-- PASS A: the offer as she read it out " + "-" * 38)
    for line in lines:
        print(line)
    print("-- PASS B: after a stand-down narrowed it " + "-" * 36)
    for line in narrow_lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
