"""
CA166de2a9 (Theorem, 10 Aug 2026) — Mark got four identical alerts about one
caller, and the caller was booked.

    15:00:12 → 15:02:05   four book_appointment calls, all Acuity 400
                          'The time "2026-08-12T16:00:00+01:00" is not an
                          available time slot.'
    ...each one                    → owner_alert [manual_followup] → SMS

    15:03:20  booked, on a Thursday the caller proposed himself

So four "⚠️ Booking needs manual entry: Jack" texts in under two minutes, all
saying the same thing, none of them still true by the end of the call.

The alert channel is load-bearing: with Sheets off it is the ONLY route a failed
booking has to a human, and CLAUDE.md's bar is "every booking that fails is
escalated to a human within minutes". Four copies of one failure does not raise
the odds Mark acts — it lowers them, because a channel that repeats itself is a
channel you learn to swipe away. See test_theorem_owner_alerts for why this path
exists at all.

The signature is the MESSAGE, not the event. The message is exactly what Mark
reads, so two alerts that read identically cannot be two pieces of news, and two
that read differently might be — a booking that moved to another slot carries a
different when_label and must still go through.

NOT FIXED HERE: an alert is still not retracted when the call later succeeds.
Mark's four texts would have become one text, still telling him to chase a
patient who is already in the diary. That needs a new outbound message to a real
person and is the owner's call, not a bug fix.
"""
from __future__ import annotations

import pytest

from app.notifications.owner_alert import notify_owner


# The clinic is SYNTHETIC and get_clinic is patched to return it.
#
# The first version of this file used the real "theorem_v3" config. That passed
# here and failed all six ways the moment the fix was cherry-picked to
# latency-eval, jv_v2 or vitaledge — the `owner_alerts` block exists ONLY on
# theorem-onboarding, so `owner_alerts_enabled` returned False, notify_owner
# no-opped, and every assertion below read as a broken port when nothing was
# broken at all.
#
# What is under test is the suppression, which is clinic-independent. Binding it
# to a branch's config makes the test measure the config instead.
_CLINIC = {
    "owner_alerts": {
        "enabled": True,
        "phone": "+447000000000",
        "events": ["manual_followup", "booking"],
    }
}


@pytest.fixture(autouse=True)
def _clinic(monkeypatch):
    monkeypatch.setattr(
        "app.notifications.owner_alert.get_clinic", lambda _cid: _CLINIC
    )


def _session(**extra) -> dict:
    s = {"clinic_id": "any_clinic"}
    s.update(extra)
    return s


# ── 1. The defect ───────────────────────────────────────────────────────────

async def test_the_same_failure_is_reported_once(block_outbound_sms):
    """Four retries against one rejected slot — the live sequence."""
    session = _session()
    results = []
    for _ in range(4):
        results.append(
            await notify_owner(
                session,
                event="manual_followup",
                patient_name="Jack",
                when_label="Wed 12 Aug at 4:00pm",
                service="Physiotherapy",
            )
        )

    assert results == [True, False, False, False]
    assert len(block_outbound_sms) == 1, (
        f"Mark received {len(block_outbound_sms)} texts about one failure"
    )


# ── 2. What must still get through ──────────────────────────────────────────

async def test_a_different_slot_is_different_news(block_outbound_sms):
    """
    The caller moved to Thursday. If that had also failed, it is a second
    failure about a second slot and Mark needs to hear it.
    """
    session = _session()
    await notify_owner(
        session, event="manual_followup", patient_name="Jack",
        when_label="Wed 12 Aug at 4:00pm",
    )
    sent = await notify_owner(
        session, event="manual_followup", patient_name="Jack",
        when_label="Thu 13 Aug at 4:00pm",
    )
    assert sent is True
    assert len(block_outbound_sms) == 2


async def test_a_different_event_is_different_news(block_outbound_sms):
    """A booking alert and a failure alert are never each other."""
    session = _session()
    await notify_owner(
        session, event="manual_followup", patient_name="Jack",
        when_label="Thu 13 Aug at 4:00pm",
    )
    sent = await notify_owner(
        session, event="booking", patient_name="Jack",
        when_label="Thu 13 Aug at 4:00pm",
    )
    assert sent is True
    assert len(block_outbound_sms) == 2


async def test_a_different_caller_is_different_news(block_outbound_sms):
    """Suppression is per session, and the signature carries the name anyway."""
    session = _session()
    await notify_owner(session, event="manual_followup", patient_name="Jack")
    sent = await notify_owner(session, event="manual_followup", patient_name="Sarah")
    assert sent is True
    assert len(block_outbound_sms) == 2


async def test_another_call_is_not_suppressed(block_outbound_sms):
    """The record lives on the session, so it dies with the call."""
    await notify_owner(_session(), event="manual_followup", patient_name="Jack")
    sent = await notify_owner(_session(), event="manual_followup", patient_name="Jack")
    assert sent is True
    assert len(block_outbound_sms) == 2


# ── 3. A send that did not happen is not a send ─────────────────────────────

async def test_a_failed_send_is_retried(monkeypatch, block_outbound_sms):
    """
    Recorded on a confirmed SID only. If the record were written before the
    send, one Twilio hiccup would suppress the retry of the one message that
    most needs to arrive — and it would do it silently.
    """
    calls = {"n": 0}

    async def _flaky(*args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else "SM123"

    monkeypatch.setattr("app.notifications.owner_alert.send_sms", _flaky)

    session = _session()
    assert await notify_owner(session, event="manual_followup", patient_name="Jack") is False
    assert await notify_owner(session, event="manual_followup", patient_name="Jack") is True
    assert calls["n"] == 2


# ── 4. The record survives the session round trip ───────────────────────────

def test_the_record_is_json_serialisable():
    """
    session is persisted by save_session. A set would serialise to nothing
    useful and the suppression would silently stop working across the awaits
    that separate two retries.
    """
    import json

    session = _session(owner_alerts_sent=["x"])
    assert json.loads(json.dumps(session))["owner_alerts_sent"] == ["x"]


# ── 5. Unrelated behaviour, pinned ──────────────────────────────────────────

async def test_a_clinic_without_the_block_is_still_a_no_op(
    monkeypatch, block_outbound_sms
):
    """
    The module's self-gating contract — unaffected by the suppression. Not a
    hypothetical: `vital_edge` carries no owner_alerts block on ANY branch, so
    this is its live behaviour and a failed booking there reaches no one.
    """
    monkeypatch.setattr("app.notifications.owner_alert.get_clinic", lambda _cid: {})
    sent = await notify_owner(
        {"clinic_id": "no_alerts"}, event="manual_followup", patient_name="Jack"
    )
    assert sent is False
    assert len(block_outbound_sms) == 0
