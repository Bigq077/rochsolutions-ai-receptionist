#!/usr/bin/env python
"""Drive every availability reader against its REAL backend, and print what the
deterministic-offer gate in llm_stream would decide.

    python scripts/verify_availability_readonly.py            # theorem_v3
    python scripts/verify_availability_readonly.py northgate

READ-ONLY, and that is a property of the code path rather than a promise here:
`_exec_check_availability` performs availability READS only. It has no branch
that creates, moves or cancels an appointment, and it sends no SMS. Contrast
`tests/auto`, which reaches the booking path and once created 60 real
appointments — this is not that, and must never be extended into that. If you
add a write to this file you have broken its only guarantee.

WHY IT EXISTS. Theorem is the one clinic on the Acuity path, and Theorem runs
on `production`. Before this script, checking anything about its slot
presentation meant deploying to a live patient line and ringing it. This gets
the same answer from a terminal, so a phone call becomes CONFIRMATION rather
than the experiment.

What it cannot tell you: whether llm_stream turns `presented_days` into the
right sentence, and whether TTS says it properly. Those are shared with the
Google path and heavily exercised there — but the last mile is still a call.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values  # noqa: E402

for _k, _v in dotenv_values(Path(__file__).resolve().parents[1] / ".env").items():
    if _v:
        os.environ.setdefault(_k, _v)

from app.tools.receptionist_tools import (  # noqa: E402
    _exec_check_availability,
    uses_acuity,
)

CLINIC = sys.argv[1] if len(sys.argv) > 1 else "theorem_v3"
LOCATION = sys.argv[2] if len(sys.argv) > 2 else "alcester"
SERVICE = "physiotherapy assessment" if CLINIC.startswith("theorem") else "initial_assessment"

#: (date_hint, what the reader should decide, why)
CASES = [
    ("any time next week",  "multi_day",  "open request"),
    ("as soon as possible", "single_day", "ASAP — Acuity decides from the REQUEST"),
]


def _session() -> dict:
    return {
        "call_sid": "CAverify_readonly",
        "clinic_id": CLINIC,
        "selected_location": LOCATION,
        "collected": {},
        "v3_location_confirmed": True,
    }


async def main() -> int:
    print(f"clinic={CLINIC}  location={LOCATION}  uses_acuity={uses_acuity(_session())}\n")
    failures = 0
    for hint, expect, why in CASES:
        out = await _exec_check_availability(
            {"service": SERVICE, "location": LOCATION, "date_hint": hint},
            _session(),
        )
        if out.get("error"):
            print(f"{hint!r} ({why})\n    error={out['error']} — cannot judge\n")
            continue
        mode = out.get("presentation_mode")
        pd, fd = out.get("presented_days"), out.get("first_day")
        # This is exactly what llm_stream's gate tests.
        ok = (mode == "multi_day" and pd) or (mode == "single_day" and fd)
        print(f"{hint!r} ({why})")
        print(f"    presentation_mode = {mode!r}   expected {expect!r}")
        print(f"    presented_days    = {len(pd) if pd else None}"
              f"   times/day={[len(d['slot_times']) for d in pd] if pd else '-'}")
        print(f"    first_day         = {'yes' if fd else 'no'}")
        print(f"    available_days    = {len(out.get('available_days') or [])} (bookable, never trimmed)")
        print(f"    gate              : {'OK' if ok else '*** branch=none-matched — WOULD FAIL ***'}")
        print()
        if not ok:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
