"""
The inbound-SMS webhook must never return a body Twilio will deliver (2026-08-20).

Twilio parses the BODY of a MESSAGING webhook response as TwiML and sends any
message it finds. `/twilio/sms/inbound` returned `PlainTextResponse("ok")` on
every path — a pattern copied from the VOICE `/status` callback, where the body
genuinely is ignored.

Live on the Joint Venture line: a patient texted "speak to marcus", received the
intended acknowledgement, and then a second text reading just **"ok"**. Nothing
in this repo sends that string — it was this webhook's own HTTP body coming back
at the patient.

Every reply on these paths is sent out-of-band via `send_sms`, so the webhook's
own answer must be an EMPTY TwiML document.

Two tests on purpose. The behavioural one pins the path the patient actually
took; the structural one pins EVERY return, including the branches that need
Redis or an authorised sender to reach — a new `return PlainTextResponse("ok")`
added later would otherwise ship unnoticed.
"""

import ast
import inspect

import pytest

import app.routes.twilio as tw


THEOREM_TO = "+447380841468"
PATIENT = "+447700900123"


class _FakeRequest:
    def __init__(self, form: dict):
        self._form = form

    async def form(self):
        return self._form


@pytest.fixture
def sent(monkeypatch):
    """Neutralise every side effect; we only care about the RETURNED response."""
    import app.notifications.sms as sms_mod
    import app.storage.redis_store as redis_mod
    import app.tools.handoff as handoff_mod

    outbox: list[tuple[str, str]] = []

    async def _send_sms(to, message, **_kw):
        outbox.append((to, message))
        return "SM_fake"

    async def _lock(*_a, **_kw):
        return True

    async def _none(_phone):
        return None

    monkeypatch.setattr(handoff_mod, "send_to_sheet", lambda *a, **k: None)
    monkeypatch.setattr(sms_mod, "send_sms", _send_sms)
    monkeypatch.setattr(tw, "acquire_once_lock", _lock)
    monkeypatch.setattr(redis_mod, "get_pending_name_confirmation", _none)
    monkeypatch.setattr(redis_mod, "get_recent_booking_context", _none)
    monkeypatch.delenv("SMS_RELAY_TO", raising=False)
    return outbox


def _assert_not_deliverable(resp):
    body = (resp.body or b"").decode("utf-8", "replace").strip()
    # The exact failure the patient saw.
    assert body.lower() != "ok", (
        "the webhook returned the bare string 'ok' — Twilio delivers this to "
        "the patient as a second text right after their acknowledgement"
    )
    assert resp.media_type == "application/xml", (
        f"a messaging webhook must answer with TwiML, got media_type="
        f"{resp.media_type!r} with body {body!r}"
    )
    # Empty TwiML: a <Response> carrying no verb. Anything with a <Message> in
    # it would be spoken at the patient.
    assert "<Message" not in body, f"webhook body would send a text: {body!r}"
    assert body.endswith("</Response>") and "<Response>" in body, (
        f"expected an empty TwiML document, got {body!r}"
    )


async def test_general_inbound_text_returns_empty_twiml(sent):
    """The exact shape of the JV report: an ordinary patient text."""
    resp = await tw.sms_inbound(
        _FakeRequest({"From": PATIENT, "To": THEOREM_TO,
                      "Body": "speak to marcus", "MessageSid": "SM_ok_1"})
    )
    _assert_not_deliverable(resp)

    # The acknowledgement itself must be untouched — this fix removes the
    # SECOND text, never the first.
    to_patient = [m for to, m in sent if to == PATIENT]
    assert len(to_patient) == 1, (
        f"expected exactly one text to the patient, got {to_patient!r}"
    )
    assert "received your message" in to_patient[0]


async def test_duplicate_webhook_retry_returns_empty_twiml(sent, monkeypatch):
    """Twilio retries; the idempotency early-return is its own `return`."""
    async def _already_seen(*_a, **_kw):
        return False

    monkeypatch.setattr(tw, "acquire_once_lock", _already_seen)
    resp = await tw.sms_inbound(
        _FakeRequest({"From": PATIENT, "To": THEOREM_TO,
                      "Body": "speak to marcus", "MessageSid": "SM_dupe"})
    )
    _assert_not_deliverable(resp)
    assert not sent, "a duplicate retry must not re-send anything"


@pytest.mark.parametrize("fn_name", ["sms_inbound", "_handle_call_mode_command"])
def test_every_inbound_sms_return_is_empty_twiml(fn_name):
    """Structural: no return on an inbound-SMS path may carry a plain body.

    Reaching some branches needs Redis or an authorised sender, so they are
    pinned by shape rather than by call. Allowed: `_twiml_noop()`, or delegating
    to another handler that is itself covered here.
    """
    src = inspect.getsource(getattr(tw, fn_name))
    tree = ast.parse(src.lstrip())
    fn = tree.body[0]

    offenders = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        v = node.value
        if isinstance(v, ast.Await):
            v = v.value
        if isinstance(v, ast.Call):
            name = getattr(v.func, "id", None) or getattr(v.func, "attr", None)
            if name in {"_twiml_noop", "_handle_call_mode_command"}:
                continue
        offenders.append(ast.unparse(node))

    assert not offenders, (
        f"{fn_name} returns a body Twilio would deliver to the patient: "
        f"{offenders} — use _twiml_noop()"
    )
