"""
Every inbound text to the clinic line is copied to an oversight number (2026-08-07).

Before this, an inbound text reached exactly one person and only on one of the
two branches of /twilio/sms/inbound: a general message was forwarded to the
practitioner's `transfer_phone`, while a reply to a name-confirmation request
was silently consumed to PUT a surname into Acuity and shown to nobody. There
was no way to see what patients were actually texting the line.

These pin the relay:
  * it fires on BOTH branches, from one call site, so nothing is invisible;
  * it is a copy, never a substitute — the practitioner forward and the patient
    ack are unchanged;
  * it does not echo itself. The copy is sent FROM the clinic's Twilio line, so
    a reply to that thread lands back on this same webhook. Without the guards,
    that reply would be relayed to the operator again AND pushed at the
    patient's clinician as if a patient had sent it.
"""

import pytest

import app.routes.twilio as tw


RELAY = "+447502211207"
THEOREM_TO = "+447380841468"   # the theorem_v3 patient line
MARK = "+447870166861"         # transfer_phone
PATIENT = "+447700900123"


class _FakeRequest:
    """sms_inbound only ever touches request.form()."""

    def __init__(self, form: dict):
        self._form = form

    async def form(self):
        return self._form


@pytest.fixture
def sent(monkeypatch):
    """Record every outbound SMS, and neutralise Redis/Acuity side effects."""
    import app.notifications.sms as sms_mod
    import app.storage.redis_store as redis_mod
    import app.routes.twilio as tw_mod

    outbox: list[tuple[str, str]] = []

    async def _send_sms(to, message, **_kw):
        outbox.append((to, message))
        return "SM_fake"

    async def _lock(*_a, **_kw):
        return True

    async def _no_pending(_phone):
        return None

    async def _no_ctx(_phone):
        return None

    import app.tools.handoff as handoff_mod
    monkeypatch.setattr(handoff_mod, "send_to_sheet", lambda *a, **k: None)

    monkeypatch.setattr(sms_mod, "send_sms", _send_sms)
    monkeypatch.setattr(tw_mod, "acquire_once_lock", _lock)
    monkeypatch.setattr(redis_mod, "get_pending_name_confirmation", _no_pending)
    monkeypatch.setattr(redis_mod, "get_recent_booking_context", _no_ctx)
    # The relay target must come from clinic config, not from whatever this
    # machine happens to have exported.
    monkeypatch.delenv("SMS_RELAY_TO", raising=False)
    return outbox


async def _post(form: dict):
    return await tw.sms_inbound(_FakeRequest(form))


def _relayed(outbox):
    return [m for to, m in outbox if to == RELAY]


# ── the relay fires, on both branches ───────────────────────────────────────

async def test_general_inbound_text_is_relayed(sent):
    await _post({"From": PATIENT, "To": THEOREM_TO,
                 "Body": "Can I move Tuesday?", "MessageSid": "SM1"})

    copies = _relayed(sent)
    assert len(copies) == 1, f"expected exactly one relay copy, got {sent!r}"
    assert "Can I move Tuesday?" in copies[0], "the relay dropped the message body"
    assert PATIENT in copies[0], "the relay did not say who texted"


async def test_name_confirmation_reply_is_also_relayed(sent, monkeypatch):
    """The branch that used to consume the text and show it to nobody."""
    import app.storage.redis_store as redis_mod

    async def _pending(_phone):
        return {"appointment_id": "12345"}

    async def _complete(_phone):
        return None

    monkeypatch.setattr(redis_mod, "get_pending_name_confirmation", _pending)
    monkeypatch.setattr(redis_mod, "complete_pending_name_confirmation", _complete)

    await _post({"From": PATIENT, "To": THEOREM_TO,
                 "Body": "Sarah Whitfield", "MessageSid": "SM2"})

    copies = _relayed(sent)
    assert len(copies) == 1, (
        "a name-confirmation reply is still invisible — the relay must sit "
        "before the pending-name branch, not inside the general handler"
    )
    assert "Sarah Whitfield" in copies[0]


# ── it is a copy, not a replacement ─────────────────────────────────────────

async def test_practitioner_forward_and_patient_ack_survive(sent):
    await _post({"From": PATIENT, "To": THEOREM_TO,
                 "Body": "Running late", "MessageSid": "SM3"})

    recipients = [to for to, _ in sent]
    assert MARK in recipients, "the relay displaced the practitioner forward"
    assert PATIENT in recipients, "the relay displaced the patient acknowledgement"
    assert RELAY in recipients


# ── it does not feed on itself ──────────────────────────────────────────────

async def test_reply_from_the_relay_number_is_not_echoed_or_forwarded(sent):
    """The operator replies to a copy. That reply lands right back here."""
    await _post({"From": RELAY, "To": THEOREM_TO,
                 "Body": "noted, ignore", "MessageSid": "SM4"})

    recipients = [to for to, _ in sent]
    assert RELAY not in recipients, "the relay echoed its own thread back"
    assert MARK not in recipients, (
        "an operator's aside was pushed at the patient's clinician as if a "
        "patient had sent it"
    )


def test_relay_is_suppressed_when_it_is_the_practitioner_number(monkeypatch):
    """Same person, one message — the richer labelled forward wins."""
    monkeypatch.delenv("SMS_RELAY_TO", raising=False)
    clinic = {"transfer_phone": MARK, "sms_relay_to": MARK}
    assert tw._sms_relay_target(clinic) == MARK

    outbox: list[str] = []

    async def _send(to, message, **_kw):
        outbox.append(to)

    import asyncio
    asyncio.run(tw._relay_inbound_sms(
        sender=PATIENT, body="hello", clinic=clinic, send_sms=_send,
    ))
    assert outbox == []


# ── configuration ───────────────────────────────────────────────────────────

def test_theorem_config_carries_the_relay_number(monkeypatch):
    from app.clinic_config import get_clinic, clinic_id_from_twilio_to

    monkeypatch.delenv("SMS_RELAY_TO", raising=False)
    clinic = get_clinic(clinic_id_from_twilio_to(THEOREM_TO))
    assert tw._sms_relay_target(clinic) == RELAY


def test_env_var_overrides_and_can_disable(monkeypatch):
    clinic = {"sms_relay_to": RELAY}

    monkeypatch.setenv("SMS_RELAY_TO", "+447000000001")
    assert tw._sms_relay_target(clinic) == "+447000000001"

    monkeypatch.setenv("SMS_RELAY_TO", "")
    assert tw._sms_relay_target(clinic) == "", (
        "an explicitly empty SMS_RELAY_TO must switch the relay off, not fall "
        "back to the clinic default"
    )


def test_spaces_in_a_configured_number_do_not_break_the_loop_guard(monkeypatch):
    """'+44 7502 211207' pasted into config must still match the E.164 sender."""
    monkeypatch.setenv("SMS_RELAY_TO", "+44 7502 211207")
    assert tw._sms_relay_target({}) == RELAY
