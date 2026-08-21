# tests/regression/test_transfer_is_distinguishable_from_a_hangup.py
"""
A caller who was put through must not look like a caller who hung up.

`transfer_attempted` had exactly one writer: `app/routes/twilio.py`'s
`/transfer-status` handler. Two things were wrong with that.

1. It sits on the **failure** branch — `completed`/`canceled` returns before it
   — so the field meant "a transfer was attempted AND nobody answered", which is
   not what `app/obs/judge.py` and `app/obs/show.py` read it as.
2. `/transfer-status` belongs to the **legacy HTTP flow**. Media streams wire
   their `<Dial>` to `/transfer-miss`, which never touched the field. Every live
   clinic runs media streams, so on the live path the flag had **no writer at
   all** — on success or on failure.

Measured consequence (2026-08-21): `transfer_attempted` was False on all 40
captured `theorem_v3` calls, including three whose stored transcript contains
Susie saying "Transferring you to Mark now — one moment." A transferred caller
and an abandoning caller produced identical rows — `transfer_attempted=False`,
`outcome='resolved'`, `reason='caller_hung_up'`, `graceful_exit=False`. On
Theorem, where press-1 is the dominant path, that reported 0% transfers.

The flag is now set at the ONLY point a leg is actually placed: after the Twilio
redirect succeeds. The four ways a transfer can be announced-but-not-placed must
still leave it False, because in each of those the caller was never put through.

See `B-72` in `docs/plan/REGISTER_B_U.md`.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.call_logger import CallLogger


def _record(session: dict) -> dict:
    session.setdefault("clinic_id", "jv_v1")
    return CallLogger("CAtest0000000000000000000000000072", session)._build_record()


# ---------------------------------------------------------------------------
# The record must separate the two shapes
# ---------------------------------------------------------------------------

def test_a_hangup_during_the_greeting_records_no_transfer():
    """The 17 Aug shape: 0s, hung up at the greeting, nothing else set."""
    rec = _record({})
    assert rec["transfer_attempted"] is False
    assert rec["transfer"]["requested_by_caller"] is False
    assert rec["transfer"]["unavailable"] is False


def test_a_press_1_transfer_is_visibly_different_from_that_hangup():
    """The 14 Aug 08:16 shape: pressed 1, Susie put them through."""
    rec = _record({
        "transfer_requested_by_caller": True,
        "transfer_attempted": True,
    })
    assert rec["transfer_attempted"] is True
    assert rec["transfer"]["requested_by_caller"] is True
    # The whole point: this row and the hang-up row are no longer identical.
    assert rec["transfer_attempted"] is not _record({})["transfer_attempted"]


def test_the_promised_but_impossible_transfer_is_recorded_as_neither():
    """
    Susie says she is transferring, there is no dial target, and she recovers
    in-line. The caller was not transferred and did not hang up. Before B-72
    this outcome was invisible.
    """
    rec = _record({
        "transfer_requested_by_caller": True,
        "transfer_unavailable": True,
    })
    assert rec["transfer_attempted"] is False
    assert rec["transfer"]["unavailable"] is True


def test_the_record_says_why_the_transfer_happened():
    for key, field in (
        ("transfer_requested_by_caller", "requested_by_caller"),
        ("medical_emergency_detected",   "medical_emergency"),
        ("request_transfer",             "requested_by_tool"),
        ("silence_transfer",             "after_silence"),
    ):
        rec = _record({key: True, "transfer_attempted": True})
        assert rec["transfer"][field] is True, f"{key} lost on the way to the record"


def test_a_seeded_none_records_false_not_null():
    # media_streams/session.py seeds these; a NULL here reads as "capture did
    # not populate it", which cost an hour of diagnosis once already.
    rec = _record({"transfer_attempted": None, "request_transfer": None})
    assert rec["transfer_attempted"] is False
    assert rec["transfer"]["requested_by_tool"] is False


# ---------------------------------------------------------------------------
# The flag must be set where the leg is actually placed — and nowhere else
# ---------------------------------------------------------------------------

def _twilio_stub(status: str = "in-progress", update_raises: bool = False):
    """Minimal stand-in for twilio.rest.Client used by _handle_transfer."""
    client = MagicMock()
    call = MagicMock()
    call.fetch.return_value = MagicMock(status=status)
    if update_raises:
        call.update.side_effect = RuntimeError("Twilio 400")
    client.calls.return_value = call
    return client


@pytest.fixture
def transferable(monkeypatch):
    """A session for a clinic that has a real dial target."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "TRANSFER_DISABLED", False, raising=False)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    return {"clinic_id": "jv_v1"}


async def test_a_placed_leg_sets_the_flag(monkeypatch, transferable):
    """The regression: this was False on every live transfer before B-72."""
    import twilio.rest
    from app.routes.realtime import _handle_transfer, resolve_transfer_target

    assert resolve_transfer_target(transferable), "fixture clinic must be dialable"
    client = _twilio_stub()
    monkeypatch.setattr(twilio.rest, "Client", lambda *a, **k: client)

    await _handle_transfer("CAtest72", transferable)

    assert client.calls.return_value.update.called, "no redirect was issued"
    assert transferable.get("transfer_attempted") is True


async def test_the_kill_switch_places_no_leg_and_sets_nothing(monkeypatch, transferable):
    import app.config as cfg
    import twilio.rest
    from app.routes.realtime import _handle_transfer

    monkeypatch.setattr(cfg, "TRANSFER_DISABLED", True, raising=False)
    client = _twilio_stub()
    monkeypatch.setattr(twilio.rest, "Client", lambda *a, **k: client)

    await _handle_transfer("CAtest72", transferable)

    assert not client.calls.return_value.update.called
    assert transferable.get("transfer_attempted") is not True


async def test_no_dial_target_sets_nothing(monkeypatch):
    """Theorem's exposure: no transfer_phone and an empty fallback."""
    import app.config as cfg
    import twilio.rest
    from app.routes.realtime import _handle_transfer

    monkeypatch.setattr(cfg, "TRANSFER_DISABLED", False, raising=False)
    monkeypatch.setattr(cfg, "TRANSFER_FALLBACK_NUMBER", "", raising=False)
    client = _twilio_stub()
    monkeypatch.setattr(twilio.rest, "Client", lambda *a, **k: client)

    session = {"clinic_id": "__no_such_clinic__"}
    await _handle_transfer("CAtest72", session)

    assert not client.calls.return_value.update.called
    assert session.get("transfer_attempted") is not True


async def test_a_call_that_already_ended_sets_nothing(monkeypatch, transferable):
    """Twilio refuses to redirect a call that is not in-progress."""
    import twilio.rest
    from app.routes.realtime import _handle_transfer

    client = _twilio_stub(status="completed")
    monkeypatch.setattr(twilio.rest, "Client", lambda *a, **k: client)

    await _handle_transfer("CAtest72", transferable)

    assert not client.calls.return_value.update.called
    assert transferable.get("transfer_attempted") is not True


async def test_a_failed_redirect_sets_nothing(monkeypatch, transferable):
    """The REST call raised — no leg, so no transfer."""
    import twilio.rest
    from app.routes.realtime import _handle_transfer

    client = _twilio_stub(update_raises=True)
    monkeypatch.setattr(twilio.rest, "Client", lambda *a, **k: client)

    await _handle_transfer("CAtest72", transferable)

    assert transferable.get("transfer_attempted") is not True
