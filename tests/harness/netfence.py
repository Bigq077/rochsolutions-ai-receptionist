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


def install(stack: ExitStack, allowed_hosts: Iterable[str] = ()) -> None:
    """Install the fence for the lifetime of `stack`."""
    allowed = frozenset(allowed_hosts) | DEFAULT_ALLOWED_HOSTS

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
