"""
T-2 — one call must not produce two summary rows, two SMS and an operator page.

Reproduced five times on the Theorem acceptance run (2026-08-04), raised from
medium to HIGH on call 6 — a call that *succeeded*:

    📊 Row built — outcome=abandoned            name=None         phone=no
    📊 Row built — outcome=reached_confirmation name=Quentin Rook phone=yes

Two paths summarise every call. `/twilio/status` fires first, against a session
the media-streams connection has not written back yet, reads an empty session
and concludes the call was abandoned. Connection cleanup then writes the truth.

The damage is not a tidy-up issue:
  * Mark's sheet shows every call twice, once as abandoned
  * the caller receives a second, wrong follow-up SMS
  * `abandoned_call` is IMMEDIATE/sms in app/obs/alerts.py, so an operator is
    paged about a successful booking

It was invisible while `SHEETS_ENABLED` was off. It is on now.

The rule these pin: **absence of data is not evidence of abandonment.** On a
completed call the media-streams path owns the record. Dropped statuses are
deliberately still logged here — no WebSocket opened, no cleanup will run, and
that row is the only record of the missed call there will ever be.
"""

import pytest

from app.routes import twilio as twilio_routes


class _Form(dict):
    """Minimal stand-in for the Starlette form mapping."""


def _post(monkeypatch, *, session, call_status="completed"):
    """Drive /twilio/status and report whether it wrote a summary row."""
    wrote = []

    async def _fake_get_session(_sid):
        return dict(session)

    async def _fake_save_session(_sid, _sess):
        return None

    async def _fake_lock(*_a, **_kw):
        return True

    monkeypatch.setattr(twilio_routes, "get_session", _fake_get_session)
    monkeypatch.setattr(twilio_routes, "save_session", _fake_save_session)
    monkeypatch.setattr(twilio_routes, "acquire_once_lock", _fake_lock)
    monkeypatch.setattr(
        "app.tools.handoff.fire_and_forget_append_summary_row",
        lambda row, **kw: wrote.append(row),
    )

    form = _Form({
        "CallSid": "CAtest0000000000000000000000000001",
        "CallStatus": call_status,
        "From": "+447700900123",
        "To": "+447380841468",
        "CallDuration": "94",
    })

    class _Req:
        async def form(self_inner):
            return form

    import asyncio
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        twilio_routes.status(_Req())
    )
    return wrote


# ── the defect ──────────────────────────────────────────────────────────────

def test_a_completed_call_with_no_session_writes_no_row(monkeypatch):
    """The exact T-2 shape: /status arrives first and sees nothing."""
    assert _post(monkeypatch, session={}) == [], (
        "an empty session was summarised as an abandoned call — T-2 is back"
    )


def test_a_call_already_logged_by_cleanup_is_not_logged_twice(monkeypatch):
    """The reverse ordering. This is the guard connection.py's comment always
    claimed existed — /status set `call_summary_logged` and never read it."""
    session = {
        "call_summary_logged": True,
        "collected": {"name": "Quentin Roch", "phone": "07502211207"},
    }
    assert _post(monkeypatch, session=session) == []


# ── what must KEEP working ──────────────────────────────────────────────────

def test_a_missed_call_is_still_recorded(monkeypatch):
    """No WebSocket opens for busy/no-answer, so no cleanup will ever run. If
    this route stands down too, a missed call vanishes entirely — which is the
    one outcome a clinic most needs to see."""
    for status in ("no-answer", "busy", "failed"):
        assert _post(monkeypatch, session={}, call_status=status), (
            f"{status} produced no row — the missed call is now invisible"
        )


def test_a_real_session_is_still_summarised(monkeypatch):
    """A completed call whose session this route CAN see is genuine data and
    must still be written — the guard keys on absence, not on the route."""
    session = {
        "collected": {"name": "Quentin Roch", "phone": "07502211207"},
        "conversation_history": [{"role": "assistant", "content": "hello"}],
    }
    assert _post(monkeypatch, session=session)
