"""The harness fence must cover every transport the engine can actually use.

WHY THIS FILE EXISTS

`netfence.py` argues, correctly, that an egress fence beats a list of patched
provider functions "because enumerating providers is a guessing game that
silently stops covering a path the moment someone adds one". It then shipped
with two paths uncovered:

* **Google Calendar.** `calendar_google.py` builds its client with
  `build("calendar", "v3", credentials=creds)`, which google-api-python-client
  routes over **httplib2** -- not httpx, not requests. The one provider that
  writes into a practitioner's real diary was the one outside the fence.
* **AssemblyAI and ElevenLabs.** 11 `import websockets` in `app/`, all of them
  paid, none of them fenced.

Neither leaked, because `driver.py` replaces `TOOL_EXECUTORS` wholesale. That
is exactly why they went unnoticed for two weeks, and exactly why the fence
needs its own test rather than the absence of a disaster.

So the last test here does not check a transport. It checks the ARGUMENT: that
`app/` imports no HTTP or WebSocket client the fence does not close. Add
aiohttp to the engine and that test fails, which is the only mechanism that
turns "we enumerated the providers correctly" from a hope into a fact.

Deterministic: no network, no model. The guard raises before any socket is
opened, so a blocked call never leaves the process.
"""
from __future__ import annotations

import re
from contextlib import ExitStack
from pathlib import Path

import pytest

from tests.harness import netfence

APP = Path(__file__).resolve().parents[2] / "app"

BLOCKED_HTTP = "https://www.googleapis.com/calendar/v3/calendars/x/events"
BLOCKED_WS = "wss://api.assemblyai.com/v2/realtime/ws"


def test_httpx_is_fenced():
    """The transport Acuity, ElevenLabs, AssemblyAI and Twilio all use."""
    import httpx

    with ExitStack() as stack:
        netfence.install(stack)
        with pytest.raises(netfence.EgressBlocked) as e:
            httpx.Client().send(httpx.Request("GET", BLOCKED_HTTP))
    assert "googleapis.com" in str(e.value)


def test_requests_is_fenced():
    """google.auth refreshes its token over requests."""
    import requests

    with ExitStack() as stack:
        netfence.install(stack)
        with pytest.raises(netfence.EgressBlocked):
            requests.get(BLOCKED_HTTP)


def test_google_calendar_is_fenced():
    """THE REGRESSION. httplib2 is how googleapiclient actually talks.

    Fails on every build before 2026-08-28: the fence patched httpx and
    requests, and the Google client used neither, so a harness conversation
    that reached a real executor could have written a real appointment into a
    real diary while the fence reported everything was contained.

    Note the shape of the failure, because it is the proof: on unfixed code
    this line does not raise, it goes to the network. That is the defect. Once
    fenced the guard raises before a socket is opened, so the passing test
    makes no request at all.
    """
    import httplib2

    with ExitStack() as stack:
        netfence.install(stack)
        with pytest.raises(netfence.EgressBlocked) as e:
            httplib2.Http().request(BLOCKED_HTTP)
    assert "googleapis.com" in str(e.value)


def test_the_authorised_google_wrapper_is_fenced_too():
    """`AuthorizedHttp` delegates to `self.http.request`, so it inherits it.

    Pinned separately because that delegation is the only reason one patch on
    `httplib2.Http` covers the credentialed client the engine really builds.
    """
    google_auth_httplib2 = pytest.importorskip("google_auth_httplib2")
    import httplib2

    class _Creds:
        def before_request(self, *a, **kw):
            pass

    authed = google_auth_httplib2.AuthorizedHttp(_Creds(), http=httplib2.Http())
    with ExitStack() as stack:
        netfence.install(stack)
        with pytest.raises(netfence.EgressBlocked):
            authed.request(BLOCKED_HTTP)


def test_websockets_are_fenced():
    """AssemblyAI and ElevenLabs bill per stream; both connect this way."""
    import websockets

    with ExitStack() as stack:
        netfence.install(stack)
        with pytest.raises(netfence.EgressBlocked) as e:
            websockets.connect(BLOCKED_WS)
    assert "assemblyai" in str(e.value)


def test_the_model_is_the_only_thing_allowed_through():
    with ExitStack() as stack:
        netfence.install(stack)
        assert netfence.DEFAULT_ALLOWED_HOSTS == frozenset({"api.anthropic.com"})
        # An explicitly allowed host is checked and passes; anything else does
        # not. Asserted on _check so no socket is opened either way.
        netfence._check(__import__("httpx").URL("https://api.anthropic.com/v1"),
                        netfence.DEFAULT_ALLOWED_HOSTS)


def test_install_never_short_circuits(monkeypatch):
    """One missing import must not disarm the fences registered after it.

    `install` used to bail out of the whole function with a bare `return` when
    `requests` was absent, which would have silently dropped everything after
    it -- including, once it existed, the Google Calendar fence. Each fence now
    swallows its own ImportError, and this pins that install visits all of them
    rather than stopping at the first.
    """
    called: list[str] = []

    def _recorder(name):
        def _fence(stack, allowed):
            called.append(name)
        return _fence

    monkeypatch.setattr(
        netfence, "FENCES",
        tuple(_recorder(f.__name__) for f in netfence.FENCES),
    )
    with ExitStack() as stack:
        netfence.install(stack)

    assert called == ["_fence_httpx", "_fence_requests",
                      "_fence_httplib2", "_fence_websockets"]


# ---------------------------------------------------------------------------
# The invariant the whole module rests on
# ---------------------------------------------------------------------------

# Clients whose traffic one of the FENCES actually stops. googleapiclient and
# google.auth.transport.requests are listed because they are wrappers that come
# out over httplib2 and requests respectively.
FENCED_IMPORTS = frozenset({
    "httpx", "requests", "httplib2", "websockets",
    "googleapiclient", "google_auth_httplib2", "google.auth.transport.requests",
})

# Clients nothing in FENCES would stop. Importing one of these into app/ opens
# a hole that no existing test would notice.
UNFENCED_IMPORTS = frozenset({
    "aiohttp", "urllib3", "urllib.request", "http.client", "httplib",
    "pycurl", "tornado", "websocket", "socket", "asyncio.open_connection",
})

_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+([a-zA-Z_][\w.]*)", re.M)


def test_app_imports_no_transport_the_fence_cannot_close():
    """Adding aiohttp to the engine must fail HERE, not in production.

    This is the test that makes netfence's own argument true. Without it the
    module is a list of providers someone once enumerated correctly, which is
    the thing its docstring says not to rely on.
    """
    offenders: dict[str, set[str]] = {}
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for mod in _IMPORT_RE.findall(text):
            root = mod.split(".")[0]
            if mod in UNFENCED_IMPORTS or root in UNFENCED_IMPORTS:
                offenders.setdefault(mod, set()).add(
                    str(path.relative_to(APP.parent)))
    assert not offenders, (
        "app/ imports a transport the harness fence does not close:\n"
        + "\n".join(f"  {m}: {sorted(f)}" for m, f in sorted(offenders.items()))
        + "\n\nEither add a fence for it in netfence.FENCES, or stub the caller. "
          "Do NOT move it to FENCED_IMPORTS to make this pass - the point of "
          "this test is that the engine cannot quietly grow a new way out."
    )
