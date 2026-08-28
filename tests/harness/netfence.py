"""Allowlist every byte of outbound HTTP; fail loudly on anything else.

The rule this enforces: a harness conversation may talk to the model and to
NOTHING else. Not Acuity, not Google Calendar, not Twilio, not Sheets.

This is deliberately an egress fence rather than a list of patched provider
functions. Enumerating providers is a guessing game that silently stops
covering a path the moment someone adds one — and the failure mode it guards
is a real appointment written into a real practitioner's calendar. Two of
those have already happened here (60 stray Acuity bookings from a plain
pytest, and active call-runner bookings that occupied real slots).

Anything not allow-listed raises EgressBlocked, which names the URL.
"""
from __future__ import annotations

from contextlib import ExitStack
from typing import Iterable
from unittest.mock import patch

DEFAULT_ALLOWED_HOSTS = frozenset({"api.anthropic.com"})


class EgressBlocked(RuntimeError):
    """Raised when harness code attempts a non-allow-listed network call."""


def _check(url: object, allowed: frozenset) -> None:
    host = getattr(url, "host", None) or ""
    if host not in allowed:
        raise EgressBlocked(
            f"harness blocked an outbound request to {url!r}. "
            f"Allowed hosts: {sorted(allowed)}. "
            "If this is a new provider call, stub it — do not widen the "
            "allowlist to make a test pass."
        )


def _fence_httpx(stack: ExitStack, allowed: frozenset) -> None:
    """Acuity, ElevenLabs, AssemblyAI, Twilio, and the model itself."""
    import httpx

    real_async_send = httpx.AsyncClient.send
    real_sync_send = httpx.Client.send

    async def guarded_async_send(self, request, *a, **kw):
        _check(request.url, allowed)
        return await real_async_send(self, request, *a, **kw)

    def guarded_sync_send(self, request, *a, **kw):
        _check(request.url, allowed)
        return real_sync_send(self, request, *a, **kw)

    stack.enter_context(patch.object(httpx.AsyncClient, "send", guarded_async_send))
    stack.enter_context(patch.object(httpx.Client, "send", guarded_sync_send))


def _fence_requests(stack: ExitStack, allowed: frozenset) -> None:
    """Anything on `requests`, including google.auth's token refresh."""
    try:
        import requests.adapters
    except ImportError:
        return

    real_adapter_send = requests.adapters.HTTPAdapter.send

    def guarded_adapter_send(self, request, *a, **kw):
        import httpx as _hx

        _check(_hx.URL(request.url), allowed)
        return real_adapter_send(self, request, *a, **kw)

    stack.enter_context(
        patch.object(requests.adapters.HTTPAdapter, "send", guarded_adapter_send)
    )


def _fence_httplib2(stack: ExitStack, allowed: frozenset) -> None:
    """GOOGLE CALENDAR. Added 2026-08-28; the fence shipped without it.

    `calendar_google.py` builds its client with
    `build("calendar", "v3", credentials=creds)`, and google-api-python-client
    routes that through `google_auth_httplib2.AuthorizedHttp` over **httplib2**
    — neither httpx nor requests. So for two weeks the one provider that writes
    into Jonathan's and Sam's real diaries was the one provider outside the
    fence, in a module whose whole argument is that enumerating providers by
    hand is a guessing game.

    Nothing leaked: `driver.py` replaces `TOOL_EXECUTORS` wholesale, so no real
    executor ever ran. That is the point — this is the layer that is supposed to
    hold when the layer above it is wrong.

    `AuthorizedHttp.request` delegates to `self.http.request`, so patching
    `httplib2.Http.request` covers the authorised wrapper too.
    """
    try:
        import httplib2
    except ImportError:
        return

    real_request = httplib2.Http.request

    def guarded_request(self, uri, *a, **kw):
        import httpx as _hx

        _check(_hx.URL(uri), allowed)
        return real_request(self, uri, *a, **kw)

    stack.enter_context(patch.object(httplib2.Http, "request", guarded_request))


def _fence_websockets(stack: ExitStack, allowed: frozenset) -> None:
    """AssemblyAI and ElevenLabs. Both are paid, and both bill per stream.

    Found alongside the httplib2 gap: `app/` has 11 `import websockets`, and
    every call site goes through the module-level `websockets.connect`, so one
    patch covers `router.py`, `stt_stream.py`, `tts_stream.py`, `realtime.py`
    and `admin.py`. `connect` is a class here rather than a function, but it is
    only ever *called*, so a plain wrapper serves both `await connect(...)` and
    `async with connect(...)`.
    """
    try:
        import websockets
    except ImportError:
        return

    real_connect = getattr(websockets, "connect", None)
    if real_connect is None:                      # a version that renamed it
        return

    def guarded_connect(uri, *a, **kw):
        import httpx as _hx

        _check(_hx.URL(uri), allowed)
        return real_connect(uri, *a, **kw)

    stack.enter_context(patch.object(websockets, "connect", guarded_connect))


# Every transport the fence knows how to close. `test_netfence.py` asserts that
# app/ imports nothing outside this set, so adding aiohttp to the engine fails
# a test instead of silently opening a hole.
FENCES = (_fence_httpx, _fence_requests, _fence_httplib2, _fence_websockets)


def install(stack: ExitStack, allowed_hosts: Iterable[str] = ()) -> None:
    """Install the fence for the lifetime of `stack`.

    Each transport is fenced independently. An earlier version bailed out of
    the whole function with a bare `return` when one import was missing, which
    would have disarmed every fence after it.
    """
    allowed = frozenset(allowed_hosts) | DEFAULT_ALLOWED_HOSTS
    for fence in FENCES:
        fence(stack, allowed)
