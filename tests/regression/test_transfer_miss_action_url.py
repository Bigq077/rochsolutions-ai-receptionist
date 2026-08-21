"""
The missed-transfer safety net must not depend on a hand-set env var.

_handle_transfer redirects the live call to <Say>+<Dial>, and hangs an
`action="…/twilio/transfer-miss"` attribute off the <Dial> so that a
no-answer/busy/failed leg comes back to us: that handler texts the clinic
"📵 Missed patient transfer" and offers the caller a voicemail.  With no
action attribute the <Dial> just ends and Twilio hangs up — the caller is
dropped in silence and the clinic is told nothing.

The attribute used to be built from BASE_URL alone.  BASE_URL is a manual
Render dashboard variable (it is not in render.yaml), and it was unset on
the Theorem service, so that safety net was dead in production without ever
logging anything.  Evidence, 2026-08-21: four consecutive live transfers to
Mark — CA82ec06, CA5eda55, CA9f7d8c, CAe057b1 — record only /ms/incoming,
the REST redirect and /twilio/status in their Twilio call events.  No
/twilio/transfer-miss request on any of them.  The same call history holds
three `no-answer` legs, each of which therefore dropped a caller.

Render sets RENDER_EXTERNAL_URL on every service automatically, so it is the
source that cannot be forgotten.  BASE_URL stays as the override.
"""

from unittest.mock import MagicMock, patch

import pytest


async def _emit_twiml(monkeypatch, *, render_url=None, base_url=None) -> str:
    """Run _handle_transfer with the env as given; return the TwiML it sent."""
    from app.routes import realtime

    for name, value in (("RENDER_EXTERNAL_URL", render_url), ("BASE_URL", base_url)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    monkeypatch.setattr("app.config.TRANSFER_DISABLED", False)
    # Keep this test about the action URL only — the dial target has its own
    # tests, and pinning a real clinic_id here would measure that branch's
    # config rather than this behaviour.
    monkeypatch.setattr(realtime, "resolve_transfer_target", lambda _s: "+447700900123")

    client = MagicMock()
    client.calls.return_value.fetch.return_value.status = "in-progress"

    with patch("twilio.rest.Client", return_value=client):
        await realtime._handle_transfer("CA_test", {})

    assert client.calls.return_value.update.called, "no redirect was issued"
    return client.calls.return_value.update.call_args.kwargs["twiml"]


async def test_render_external_url_alone_is_enough(monkeypatch):
    """THE regression. This is production on Theorem: no BASE_URL anywhere."""
    twiml = await _emit_twiml(
        monkeypatch, render_url="https://susie-theorem.onrender.com", base_url=None
    )
    assert 'action="https://susie-theorem.onrender.com/twilio/transfer-miss"' in twiml, (
        "a missed transfer will drop the caller silently: " + twiml
    )
    assert 'method="POST"' in twiml


async def test_base_url_still_works(monkeypatch):
    """The old source stays honoured — this must not be a swap."""
    twiml = await _emit_twiml(
        monkeypatch, render_url=None, base_url="https://example.onrender.com"
    )
    assert 'action="https://example.onrender.com/twilio/transfer-miss"' in twiml


async def test_render_wins_when_the_two_disagree(monkeypatch):
    """
    RENDER_EXTERNAL_URL takes precedence, matching media_streams/router.py's
    _validate_twilio_signature. That agreement is the point, not an accident:
    the callback we advertise here is signature-checked against the host that
    helper reconstructs. If this preferred BASE_URL while the validator
    preferred RENDER_EXTERNAL_URL, a service where the two differ would send
    every transfer-miss callback to a host that then rejects its own signature
    — the safety net would look wired up and still be dead.
    """
    twiml = await _emit_twiml(
        monkeypatch,
        render_url="https://auto.onrender.com",
        base_url="https://other.example.com",
    )
    assert 'action="https://auto.onrender.com/twilio/transfer-miss"' in twiml
    assert "other.example.com" not in twiml


async def test_neither_set_emits_no_action_at_all(monkeypatch):
    """
    Local dev / a bare container. A relative or empty action URL is worse than
    none — Twilio cannot reach it, and the <Dial> fails instead of ringing.
    """
    twiml = await _emit_twiml(monkeypatch, render_url=None, base_url=None)
    assert "action=" not in twiml
    assert "transfer-miss" not in twiml
    assert "<Dial" in twiml, "the caller must still be dialled: " + twiml


@pytest.mark.parametrize("host", ["susie.onrender.com", "https://susie.onrender.com"])
async def test_scheme_is_added_when_missing(monkeypatch, host):
    """
    RENDER_EXTERNAL_URL carries the scheme today, but a bare host must not
    produce action="susie.onrender.com/…", which Twilio reads as relative.
    """
    twiml = await _emit_twiml(monkeypatch, render_url=host, base_url=None)
    assert 'action="https://susie.onrender.com/twilio/transfer-miss"' in twiml


def test_the_action_url_has_a_handler():
    """
    An action URL pointing at a 404 fails the same way as no action URL, and
    silently. Assert the route this TwiML names actually exists.
    """
    from app.routes import twilio as twilio_routes

    paths = {getattr(r, "path", None) for r in twilio_routes.router.routes}
    assert "/twilio/transfer-miss" in paths, (
        "the <Dial> action URL names a route that is not mounted: " + str(sorted(p for p in paths if p))
    )
