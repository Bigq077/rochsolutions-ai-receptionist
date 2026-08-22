"""
The callback host we advertise must be the host we validate against.

22 Aug, call CA3b018519 on the latency-eval line. The missed-transfer callback
finally fired — ccd765f had given the <Dial> its action URL — and the app
refused it:

    POST /twilio/transfer-miss  403 Forbidden
    Twilio signature INVALID: url=https://rochsolutions-ai-receptionist-1.onrender.com/twilio/transfer-miss

while the same call's stream URL was
wss://low-latency-joint-venture.onrender.com/ms/stream. Two hostnames for one
service. `_handle_transfer` advertised the callback at RENDER_EXTERNAL_URL, so
Twilio signed THAT url; the validator in app/routes/twilio.py reconstructed from
x-forwarded-host and got the other one. HMAC over a different string, so the
signature could never match.

The net went from never being called to being called and turned away — the
caller still got no voicemail and the clinic still got no SMS. Note ccd765f's
own message claimed "the advertised callback host now agrees with the host the
validator checks it against". That was true of `_verify_twilio_signature_ms`
(/ms/incoming, which validated fine all along) and false of the twilio.py
validator that actually guards this route.

One resolver now: app.config.public_base_url().
"""

import importlib

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("RENDER_EXTERNAL_URL", "BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    yield


def _reload():
    import app.config as cfg
    return importlib.reload(cfg)


def test_render_external_url_wins_over_base_url(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://auto.onrender.com")
    monkeypatch.setenv("BASE_URL", "https://manual.example.com")
    assert _reload().public_base_url() == "https://auto.onrender.com"


def test_base_url_is_the_fallback(monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://manual.example.com")
    assert _reload().public_base_url() == "https://manual.example.com"


def test_a_bare_host_gets_a_scheme(monkeypatch):
    """RENDER_EXTERNAL_URL carries one today; a bare host must not yield a URL
    Twilio reads as relative."""
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "susie.onrender.com")
    assert _reload().public_base_url() == "https://susie.onrender.com"


def test_neither_set_is_empty(monkeypatch):
    assert _reload().public_base_url() == ""


def test_the_advertised_host_is_the_validated_host():
    """THE regression.

    Both sides must resolve the origin through the one resolver. Asserting the
    two agree for a sample of values would pass while each kept its own copy and
    drifted again; assert they call the same function.
    """
    import inspect

    from app.routes import realtime, twilio as twilio_routes

    advertised = inspect.getsource(realtime._public_base_url)
    assert "public_base_url()" in advertised, (
        "the <Dial> action URL no longer uses the shared resolver"
    )

    validator_src = ""
    for name, obj in vars(twilio_routes).items():
        if not callable(obj) or not hasattr(obj, "__code__"):
            continue
        try:
            src = inspect.getsource(obj)
        except (OSError, TypeError):
            continue
        if "X-Twilio-Signature" in src and "RequestValidator" in src:
            validator_src = src
            break

    assert validator_src, "could not find the twilio.py signature validator"
    assert "public_base_url()" in validator_src, (
        "the signature validator resolves the host its own way again — Twilio "
        "will sign one host and this will check another, and every callback to "
        "this router 403s"
    )
    assert 'os.getenv("BASE_URL"' not in validator_src, (
        "BASE_URL read directly in the validator: it is the FALLBACK, and "
        "preferring it over RENDER_EXTERNAL_URL is the 403"
    )


def test_ms_incoming_agrees_with_both():
    """/ms/incoming validated fine throughout because it already preferred
    RENDER_EXTERNAL_URL. Pin that it keeps the same precedence, so the three
    cannot drift apart again from the other direction."""
    import inspect

    from app.media_streams import router as ms_router

    src = inspect.getsource(ms_router._verify_twilio_signature_ms)
    i_render = src.index("RENDER_EXTERNAL_URL")
    i_base = src.index("BASE_URL")
    assert i_render < i_base, "/ms/incoming no longer prefers RENDER_EXTERNAL_URL"
