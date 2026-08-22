"""
A delivery log must name the destination actually dialled.

Call CA6711a434 (22 Aug), latency-eval line. The missed-transfer handler logged

    transfer-miss: clinic notified at +447586605462

for a text that redirect_staff_sms had already diverted, two lines earlier, to a
different number. The clinic was told nothing. Both statements were "true" in
their own frame — the handler asked for that number, send_sms sent somewhere else
— and the log recorded only the first.

`send_sms` was no better: it logged a bare "SMS sent successfully" and put the
destination in `extra`, which the deployed log format drops. So the one component
that knew the truth did not say it, and the one that said something did not know.

A delivery claim nobody can check is worse than no claim: it survives an audit.
The redirect happens inside send_sms, so send_sms is the only place that can name
the destination, and it now does.
"""

import logging

import pytest

from app.notifications import sms as sms_mod


class _FakeMessage:
    sid = "SM_test"
    status = "queued"


class _FakeClient:
    def __init__(self):
        self.sent = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, body, from_, to):
            self._outer.sent.append(to)
            return _FakeMessage()

    @property
    def messages(self):
        return _FakeClient._Messages(self)


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("SMS_ENABLED", "true")
    monkeypatch.delenv("EVAL_STAFF_SMS_TO", raising=False)
    s = sms_mod.SMSService.__new__(sms_mod.SMSService)
    s.client = _FakeClient()
    s.from_number = "+447366263180"
    return s


async def _send(svc, to, caplog):
    with caplog.at_level(logging.INFO, logger=sms_mod.__name__):
        await svc.send_sms(to=to, message="hello")
    return [r.getMessage() for r in caplog.records]


async def test_the_success_line_names_the_destination(svc, caplog):
    msgs = await _send(svc, "+447586605462", caplog)
    line = next(m for m in msgs if "SMS sent successfully" in m)
    assert "5462" in line, "the success line does not say where it went: " + line


async def test_a_diverted_send_says_so_and_names_the_real_number(
    svc, caplog, monkeypatch
):
    """THE regression."""
    monkeypatch.setenv("EVAL_STAFF_SMS_TO", "+447502211207")
    monkeypatch.setattr(
        sms_mod, "_staff_numbers", lambda: frozenset({"+447586605462"})
    )

    msgs = await _send(svc, "+447586605462", caplog)
    line = next(m for m in msgs if "SMS sent successfully" in m)

    assert "1207" in line, "the line names the intended number, not the real one: " + line
    assert "REDIRECTED" in line, "a diverted send is not flagged: " + line
    assert "5462" in line, "the intended recipient is not named: " + line
    assert svc.client.sent == ["+447502211207"], svc.client.sent


async def test_an_undiverted_send_is_not_flagged(svc, caplog):
    """The note must mark a real diversion only — a flag on every send is noise
    and gets filtered out, which is how the next one goes unnoticed."""
    msgs = await _send(svc, "+447586605462", caplog)
    line = next(m for m in msgs if "SMS sent successfully" in m)
    assert "REDIRECTED" not in line, line


def test_no_call_site_claims_a_delivery_it_cannot_see():
    """The structural guard.

    Only send_sms knows the destination. Any other site asserting that someone
    *was notified* is asserting something it cannot observe — that is exactly
    what produced the CA6711a434 line.
    """
    import inspect

    from app.routes import twilio as twilio_routes

    src = inspect.getsource(twilio_routes.transfer_miss)
    assert "clinic notified at" not in src, (
        "transfer-miss claims a delivery again; send_sms may divert it and this "
        "call site cannot tell"
    )
    assert "notify requested" in src, (
        "the intent log went missing entirely — it should say what it ASKED for"
    )
