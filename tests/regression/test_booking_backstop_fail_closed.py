# tests/regression/test_booking_backstop_fail_closed.py
"""
P1 #4 — the clinical-screening booking backstop failed OPEN.

`_exec_book_appointment` runs a Layer-3 safety gate before booking: if a red-flag
screen is pending or positive, `booking_blocked_reason` returns a reason and the
tool refuses. But the whole gate was wrapped in:

    except Exception:
        logger.exception("... failing open")

so if `get_clinic` or `booking_blocked_reason` raised — an import error, a
transient config-load failure — the booking proceeded UNSCREENED. For a safety
backstop, an error must not open the gate.

The fix must fail closed WITHOUT becoming an outage. The block condition lives in
the SESSION (`pending_screen` / `screen_red_flag`), not in the clinic config, so
the handler can honour it even when the clinic lookup is what threw. If no screen
was ever armed there is nothing to guard, and blocking every booking on any
transient error — including for clinics that have no screening at all — would be
a worse bug. So:

    backstop errors AND a screen is pending/positive  -> fail CLOSED (refuse)
    backstop errors AND no screen was ever armed      -> proceed (nothing to guard)

Tests inject a fault into `booking_blocked_reason` and use `_resolve_clinic_id`
(the first statement after the backstop) as a tripwire: reaching it proves the
booking proceeded past the gate. No calendar or network is touched.
"""
from __future__ import annotations

import pytest

from app.tools import receptionist_tools as rt
from app.media_streams import clinical_screening as cs


class _ProceededPastBackstop(Exception):
    """Raised by the patched _resolve_clinic_id to prove the gate was passed."""


@pytest.fixture()
def backstop_raises(monkeypatch):
    """Force the screening lookup to throw, exercising the except path."""
    def boom(*_a, **_k):
        raise RuntimeError("simulated clinic-config load failure")
    monkeypatch.setattr(cs, "booking_blocked_reason", boom)


@pytest.fixture()
def tripwire_after_backstop(monkeypatch):
    """Anything that reaches real booking raises the sentinel instead."""
    def sentinel(*_a, **_k):
        raise _ProceededPastBackstop()
    monkeypatch.setattr(rt, "_resolve_clinic_id", sentinel)


# ── FAIL CLOSED: backstop errors while a screen is unresolved ─────────────
async def test_fails_closed_when_pending_screen_and_backstop_errors(
    backstop_raises, tripwire_after_backstop
):
    session = {"clinic_id": "jv_v1", "pending_screen": "cauda_equina"}
    result = await rt._exec_book_appointment({}, session)
    assert result.get("success") is False, "must refuse — a screen is unresolved"


async def test_fails_closed_when_red_flag_and_backstop_errors(
    backstop_raises, tripwire_after_backstop
):
    session = {"clinic_id": "jv_v1", "screen_red_flag": "dvt"}
    result = await rt._exec_book_appointment({}, session)
    assert result.get("success") is False, "must refuse — a positive red flag stands"


# ── PROCEED: backstop errors but no screen was ever armed ─────────────────
# Blocking here would be an outage for clinics that have no screening at all.
async def test_proceeds_when_no_screen_and_backstop_errors(
    backstop_raises, tripwire_after_backstop
):
    session = {"clinic_id": "jv_v1"}  # no pending_screen, no screen_red_flag
    with pytest.raises(_ProceededPastBackstop):
        await rt._exec_book_appointment({}, session)


# ── Existing behaviour preserved (no fault injected into the gate) ────────
async def test_normal_block_still_returns_success_false(monkeypatch, tripwire_after_backstop):
    monkeypatch.setattr(cs, "booking_blocked_reason", lambda *_a, **_k: "ask the screen question")
    result = await rt._exec_book_appointment({}, {"clinic_id": "jv_v1", "pending_screen": "x"})
    assert result.get("success") is False
    assert "ask the screen question" in (result.get("error") or "")


async def test_normal_clear_proceeds(monkeypatch, tripwire_after_backstop):
    monkeypatch.setattr(cs, "booking_blocked_reason", lambda *_a, **_k: None)
    with pytest.raises(_ProceededPastBackstop):
        await rt._exec_book_appointment({}, {"clinic_id": "jv_v1"})
