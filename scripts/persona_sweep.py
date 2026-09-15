#!/usr/bin/env python3
"""persona_sweep.py — drive every persona against a clinic and report findings.

    HARNESS_LIVE_LLM=1 python scripts/persona_sweep.py --clinic jv_v1
    ... --personas red_flag_cauda_equina,under_age     # just these
    ... --repeat 3                                      # 3 runs each

WHY THIS EXISTS
---------------
tests/harness/ has 16 personas and a deterministic verdict layer, and nothing
drives all of them in one go. The pytest files pin specific invariants; this
answers the broader question before a demo: *what breaks when sixteen different
callers ring this clinic?*

Everything is in-process against a FakeDiary — no Twilio, no Redis, no calendar
or SMS provider, and the driver's own netfence blocks any egress that is not the
model. Nothing here can write to a real diary.

The caller never decides the verdict. It generates the conversation; findings
come from tests/harness/verdicts.py, which is a deterministic function of the
transcript and the diary. A sweep cannot go green because the caller was in a
generous mood.

NOT A PASS/FAIL GATE. It drives a real model, so a single run is a sample, not
a proof — use --repeat and treat anything that recurs as real. Exit code is 0
unless the sweep itself failed to run.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness.caller import CALLER_MODEL, HANG_UP, AdaptiveCaller  # noqa: E402
from tests.harness.driver import ConversationDriver  # noqa: E402
from tests.harness.fake_clinic import FakeDiary  # noqa: E402
from tests.harness.personas import NEEDS_EXISTING_BOOKING, SUITE  # noqa: E402
from tests.harness.verdicts import judge  # noqa: E402

# Findings that mean a patient was misled, endangered, or lost — as against
# phrasing. Ranked the way docs/INCIDENT.md ranks severity, so the report leads
# with what would actually matter on a live line.
SEVERE = (
    "red_flag", "booking", "duplicate", "cancel", "moved", "surname",
    "no_booking", "silent", "loop", "repeated",
)


def _is_severe(finding) -> bool:
    text = f"{getattr(finding, 'check', '')} {getattr(finding, 'detail', '')}".lower()
    return any(k in text for k in SEVERE)


async def run_persona(persona, clinic_id: str, seed_booking: bool):
    """One call. Returns (findings, turns, booked, transcript)."""
    diary = FakeDiary.weekly(
        start=datetime.now() + timedelta(days=2), days=14,
        times=["09:00", "11:00", "14:00", "16:00"],
    )
    if seed_booking:
        diary.seed_booking(
            start=datetime.now() + timedelta(days=4, hours=10),
            name="Alex Delaney",
            # Ofcom drama-reserved range — never routable.
            phone="07700900228",
        )

    caller = AdaptiveCaller(persona)
    exchanges: list = []
    said = caller.opening()

    async with ConversationDriver(clinic_id=clinic_id, diary=diary) as call:
        for _ in range(persona.max_turns):
            spoken = (await call.say(said)).spoken
            exchanges.append((said, spoken))
            nxt = await caller.reply(exchanges)
            if nxt is None or nxt.strip() == HANG_UP:
                break
            said = nxt
        tool_calls = list(getattr(call, "tool_calls", []) or [])

    transcript = []
    for user, bot in exchanges:
        transcript.append(("user", user))
        transcript.append(("bot", bot))

    findings = judge(persona.id, transcript, diary=diary, tool_calls=tool_calls)
    return findings, len(exchanges), bool(diary.bookings), transcript


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clinic", default="jv_v1")
    ap.add_argument("--personas", help="comma-separated ids; default all 16")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--show-transcript", action="store_true")
    args = ap.parse_args()

    if not os.getenv("HARNESS_LIVE_LLM"):
        print("Set HARNESS_LIVE_LLM=1 — this sweep drives a real model.")
        return 2

    chosen = SUITE
    if args.personas:
        want = {p.strip() for p in args.personas.split(",")}
        chosen = [p for p in SUITE if p.id in want]
        missing = want - {p.id for p in chosen}
        if missing:
            print(f"unknown persona id(s): {', '.join(sorted(missing))}")
            return 2

    print(f"clinic={args.clinic}  caller={CALLER_MODEL}  "
          f"personas={len(chosen)}  repeat={args.repeat}")
    print("in-process, FakeDiary — no Twilio, Redis, calendar or SMS\n")

    by_persona: dict = defaultdict(list)
    by_check: Counter = Counter()
    t0 = time.time()

    for rep in range(args.repeat):
        for p in chosen:
            try:
                findings, turns, booked, transcript = await run_persona(
                    p, args.clinic, p.id in NEEDS_EXISTING_BOOKING
                )
            except Exception as exc:  # a crash IS the finding
                print(f"  {p.id:28} ERROR {type(exc).__name__}: {str(exc)[:70]}")
                by_persona[p.id].append(("ERROR", str(exc)[:120]))
                continue

            mark = "." if not findings else ("!" if any(map(_is_severe, findings)) else "?")
            print(f"  {mark} {p.id:28} turns={turns:2} booked={str(booked):5} "
                  f"findings={len(findings)}")
            for f in findings:
                check = getattr(f, "check", str(f))
                by_check[check] += 1
                by_persona[p.id].append((check, str(getattr(f, "detail", ""))[:120]))
            if args.show_transcript and findings:
                for role, text in transcript:
                    print(f"        {role:5} {text[:96]}")

    print(f"\n{'=' * 70}\nSWEEP — {args.clinic} — {time.time() - t0:.0f}s")
    runs = len(chosen) * args.repeat
    clean = runs - len({k for k, v in by_persona.items() if v})
    print(f"  calls: {runs}   personas clean: {clean}/{len(chosen)}")

    if not by_check:
        print("\n  No findings. That is a real result — record it.")
        return 0

    print("\nFINDINGS BY CHECK (most frequent first):")
    for check, n in by_check.most_common():
        sev = "SEVERE" if any(k in check.lower() for k in SEVERE) else "      "
        print(f"  {n:3}  {sev}  {check}")

    print("\nBY PERSONA:")
    for pid, items in sorted(by_persona.items()):
        print(f"  {pid}")
        for check, detail in items[:4]:
            print(f"       {check}: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
