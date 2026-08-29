"""Run the adaptive-caller suite against the live free-form turn loop.

    python -m scripts.run_call_suite --list
    python -m scripts.run_call_suite --only book_named_day --show
    python -m scripts.run_call_suite --clinic northgate --out logs/suite

WHAT THIS REPLACES
------------------
Picking up a phone. Each persona is a caller with a goal who reads what Susie
says and answers it, driven through `LLMStream.run_turn` in-process -- the same
turn loop every live clinic runs. No Twilio, no ngrok, no deployed server, no
STT, no TTS, and no calendar: the netfence permits `api.anthropic.com` and
nothing else, and bookings land in a FakeDiary.

WHAT IT COSTS
-------------
Two model calls per turn -- one for the caller, one for Susie -- so the whole
suite is real money, though a rounding error against an afternoon of phone
calls. `--only` runs one persona while you are iterating.

HOW TO READ A FAILURE
---------------------
A finding is a deterministic function of the transcript (`verdicts.py`), so it
reproduces from the saved JSON without re-running the call. `--out` writes one
file per call for exactly that reason: attach it to the bug rather than
describing it.

WHAT IT CANNOT FIND
-------------------
It types; it does not speak. Barge-in, endpointing, STT mishearing and prosody
are all outside this harness. A green suite is not a substitute for a live call
-- it is what makes the live calls worth placing.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv

    load_dotenv(".env")
except Exception:
    pass

from tests.harness.caller import AdaptiveCaller  # noqa: E402
from tests.harness.driver import ConversationDriver  # noqa: E402
from tests.harness.fake_clinic import FakeDiary  # noqa: E402
from tests.harness.personas import (  # noqa: E402
    NEEDS_EXISTING_BOOKING,
    SUITE,
    by_id,
)
from tests.harness.verdicts import judge  # noqa: E402


def _diary(now: datetime) -> FakeDiary:
    """A fortnight of ordinary clinic availability, weekdays plus Saturday.

    Deliberately generous: this suite is looking for defects in how Susie
    HANDLES availability, and a thin diary turns every persona into the same
    "nothing free" call.
    """
    return FakeDiary.weekly(
        start=now,
        days=14,
        times=["09:00", "09:30", "10:00", "14:00", "17:00", "18:00", "18:30"],
        weekdays=[0, 1, 2, 3, 4, 5],
    )


async def run_one(persona, clinic_id: str, now: datetime, show: bool = False):
    """Drive one whole call. Returns a record dict; never raises for a defect."""
    diary = _diary(now)
    initial = {}
    if persona.id in NEEDS_EXISTING_BOOKING:
        # You cannot cancel what was never booked. Seeded through the diary the
        # engine actually reads, not through session state, so the lookup path
        # is exercised rather than bypassed.
        seed = (now + timedelta(days=3)).replace(
            hour=10, minute=0, second=0, microsecond=0)
        # No hasattr guard. An earlier version had one, and because FakeDiary
        # had no seed_booking at the time it silently did nothing -- so the
        # cancel personas rang about an appointment that did not exist and the
        # suite reported them clean. A missing seed must be a crash.
        diary.seed_booking(
            name=persona.facts.get("full name", "Test Caller"),
            phone=persona.facts.get("phone", "07700 900141"),
            start=seed,
        )

    caller = AdaptiveCaller(persona)
    exchanges = []
    started = time.monotonic()
    error = None

    async with ConversationDriver(
        clinic_id=clinic_id, diary=diary, now=now, initial=initial,
        twilio_from="+447700900" + persona.facts.get("phone", "07700 900141")[-3:],
    ) as call:
        said = caller.opening()
        while said:
            try:
                turn = await call.say(said)
            except Exception as exc:  # a crash IS the finding
                error = f"{type(exc).__name__}: {exc}"
                exchanges.append((said, ""))
                break
            heard = turn.spoken
            exchanges.append((said, heard))
            if show:
                print(f"    caller : {said}")
                print(f"    susie  : {heard[:160]}")
            said = await caller.reply(exchanges)

        findings = judge(persona.id, exchanges, diary=diary,
                         tool_calls=call.tool_calls)
        record = {
            "persona": persona.id,
            "covers": persona.covers,
            "clinic": clinic_id,
            "turns": len(exchanges),
            "seconds": round(time.monotonic() - started, 1),
            "tools": [t.name for t in call.tool_calls],
            "bookings": len(getattr(diary, "bookings", []) or []),
            "error": error,
            "findings": [str(f) for f in findings],
            "transcript": [{"caller": s, "susie": h} for s, h in exchanges],
            "caller_tokens": sum(o for _i, o in caller.usage),
        }
    if error:
        record["findings"].insert(0, f"[defect] crashed: {error}")
    return record


async def main_async(args) -> int:
    personas = [by_id(p) for p in args.only] if args.only else list(SUITE)
    now = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for i, persona in enumerate(personas, 1):
        print(f"[{i}/{len(personas)}] {persona.id} … ", end="", flush=True)
        try:
            record = await run_one(persona, args.clinic, now, show=args.show)
        except Exception as exc:
            print(f"HARNESS ERROR {type(exc).__name__}: {exc}")
            records.append({"persona": persona.id, "findings": [f"[defect] harness: {exc}"],
                            "turns": 0, "transcript": [], "bookings": 0, "tools": []})
            continue
        records.append(record)
        n = len(record["findings"])
        print(f"{record['turns']} turns, {record['bookings']} booking(s), "
              f"{'CLEAN' if not n else str(n) + ' finding(s)'}")
        if out_dir:
            (out_dir / f"{persona.id}.json").write_text(
                json.dumps(record, indent=2, default=str), encoding="utf-8"
            )

    print("\n" + "=" * 74)
    clean = [r for r in records if not r["findings"]]
    print(f"calls: {len(records)}   clean: {len(clean)}   "
          f"with findings: {len(records) - len(clean)}")
    for record in records:
        if not record["findings"]:
            continue
        print(f"\n{record['persona']}  ({record.get('covers', '')[:60]})")
        for finding in record["findings"]:
            print(f"    {finding}")
    if out_dir:
        print(f"\ntranscripts written to {out_dir}")
    # Findings are reported, not fatal: this is an instrument first. Wire the
    # exit code into CI only once a clean baseline exists to compare against.
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clinic", default="northgate",
                        help="the demo tenant by default; never a patient line")
    parser.add_argument("--only", nargs="*", help="persona ids to run")
    parser.add_argument("--out", help="directory to write transcripts to")
    parser.add_argument("--show", action="store_true", help="print the dialogue")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for persona in SUITE:
            print(f"  {persona.id:28s} {persona.covers}")
        return 0
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
