#!/usr/bin/env python3
"""Cancel the two outstanding handover test bookings (Quentin 11 Aug).

    Jack Thompson  — Theorem/Acuity  Fri 14 Aug 11:00  id=1752726653
    Quentin Road   — VE Google diary Tue 18 Aug 12:00  id=21jornld8dvoqk5nov9jl74d3g

Run on the matching Render shell (creds differ per service):

    # Theorem:
    PYTHONPATH=. python scripts/cleanup_handover_test_bookings.py theorem

    # Vital Edge:
    PYTHONPATH=. python scripts/cleanup_handover_test_bookings.py vital_edge
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

JACK_ACUITY_ID = "1752726653"
QUENTIN_GCAL_ID = "21jornld8dvoqk5nov9jl74d3g"


async def _cancel_theorem() -> int:
    from app.tools.receptionist_tools import _get_acuity_adapter

    adapter = _get_acuity_adapter()
    if adapter is None:
        print("No Acuity adapter — check ACUITY_USER_ID / ACUITY_API_KEY", file=sys.stderr)
        return 1
    ok = await adapter.cancel_booking(JACK_ACUITY_ID)
    print(f"Jack Thompson Acuity {JACK_ACUITY_ID}: {'cancelled' if ok else 'FAILED'}")
    return 0 if ok else 1


async def _cancel_ve() -> int:
    from app.clinic_config import get_clinic
    from app.tools.calendar_google import delete_event
    from app.tools.receptionist_tools import _get_tokens, _resolve_calendar_id

    tokens = await _get_tokens("vital_edge")
    if not tokens:
        print("No Google tokens for vital_edge", file=sys.stderr)
        return 1
    clinic = get_clinic("vital_edge") or {}
    calendar_id = _resolve_calendar_id(clinic, "kingston")
    await asyncio.to_thread(delete_event, tokens, QUENTIN_GCAL_ID, calendar_id)
    print(f"Quentin Road gcal {QUENTIN_GCAL_ID} on {calendar_id}: deleted")
    return 0


async def main(which: str) -> int:
    which = which.strip().lower()
    if which in ("theorem", "theorem_v3", "jack"):
        return await _cancel_theorem()
    if which in ("vital_edge", "ve", "quentin"):
        return await _cancel_ve()
    print("Usage: cleanup_handover_test_bookings.py theorem|vital_edge", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "")))
