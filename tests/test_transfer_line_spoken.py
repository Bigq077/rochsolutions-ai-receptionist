"""
tests/test_transfer_line_spoken.py
----------------------------------
F17 — the verbatim G18 hand-off line must be SPOKEN on every authorised transfer.

Sweep finding F17 (docs/sweep_findings.md, Call 6): on "can I just speak to
someone" the LLM called transfer_to_human and the bridge fired with NO spoken
line. The model's prose ("...just bear with me") is stripped by gate5's
`bear_with_me` pattern, and the only deterministic line — the TwiML <Say> in
realtime._handle_transfer — is suppressed on staging by TRANSFER_DISABLED and
only plays post-redirect in prod. The DTMF path works because it queues a TTS
line before dialing; the LLM path had no equivalent.

Fix: `_on_transfer_request` (the single choke point for ALL transfer paths —
LLM tool, DTMF press-1, silence, emergency) must queue the verbatim line to
`tts_text_queue` before `_handle_transfer`. TTS on the live stream bypasses gate5
AND plays even when the dial is suppressed on staging → phone-verifiable.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_streams.connection import WebSocketCallHandler

G18_LINE = "Putting you through now — please stay on the line."


def _handler(session: dict) -> WebSocketCallHandler:
    """Minimal handler — bypass the heavy constructor; set only what
    _on_transfer_request touches (session, call_sid, tts_text_queue)."""
    h = object.__new__(WebSocketCallHandler)
    h.session = session
    h.call_sid = "CAtest"
    h.tts_text_queue = asyncio.Queue()
    return h


def _drain(q: asyncio.Queue) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.mark.asyncio
async def test_authorised_transfer_speaks_g18_line():
    """An authorised transfer enqueues the verbatim G18 line, then dials."""
    h = _handler({"request_transfer": True, "clinic_id": "theorem_v3"})
    with patch("app.routes.realtime._handle_transfer", new_callable=AsyncMock) as mock_ht:
        await h._on_transfer_request()
    queued = _drain(h.tts_text_queue)
    assert any(G18_LINE in q for q in queued), queued
    mock_ht.assert_awaited_once()


@pytest.mark.asyncio
async def test_blocked_transfer_speaks_nothing():
    """Guard fails (no transfer flag) → no line spoken, no dial (control)."""
    h = _handler({"clinic_id": "theorem_v3"})
    with patch("app.routes.realtime._handle_transfer", new_callable=AsyncMock) as mock_ht:
        await h._on_transfer_request()
    assert _drain(h.tts_text_queue) == []
    mock_ht.assert_not_awaited()
