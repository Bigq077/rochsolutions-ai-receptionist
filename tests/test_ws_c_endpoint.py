"""WS-C — phase-aware endpointing + capture-phase HARD GATE (RC-1).

The lever raises AssemblyAI's turn-detection silence thresholds during name/phone
capture (via a mid-session UpdateConfiguration) so the endpointer can no longer
fire aggressively and clip a spelled name / read-out number — the RC-1 failure
(endpoint_wait_ms=41 on a name turn). Default OFF => byte-identical to live.

These tests cover what is deterministically checkable off a phone call:
  1. the pure phase→profile helper, incl. the hard-gate floor;
  2. the handler's apply logic — pushes on a phase change, dedups otherwise,
     no-ops when OFF;
  3. the STT UpdateConfiguration message shape.
The acoustic outcome (fewer capture cutoffs, no new ones) is the WS-C Phase-1/2
live A/B and cannot be asserted here.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from app.media_streams import connection as conn
from app.media_streams import config as cfg
from app.media_streams.stt_stream import STTStream


# ---------------------------------------------------------------------------
# 1. Pure phase → profile helper
# ---------------------------------------------------------------------------

def test_profile_off_returns_none(monkeypatch):
    monkeypatch.setattr(cfg, "WS_C_SEMANTIC_ENDPOINT", False)
    assert cfg.ws_c_profile_for_phase("name") is None
    assert cfg.ws_c_profile_for_phase("phone") is None
    assert cfg.ws_c_profile_for_phase("conversation") is None


def test_profile_on_conversation_matches_live_defaults(monkeypatch):
    monkeypatch.setattr(cfg, "WS_C_SEMANTIC_ENDPOINT", True)
    monkeypatch.setattr(cfg, "WS_C_CONV_MIN_SILENCE", 600)
    monkeypatch.setattr(cfg, "WS_C_CONV_MAX_SILENCE", 1280)
    assert cfg.ws_c_profile_for_phase("conversation") == (600, 1280)


@pytest.mark.parametrize("phase", ["name", "phone"])
def test_profile_on_capture_is_more_conservative(monkeypatch, phase):
    monkeypatch.setattr(cfg, "WS_C_SEMANTIC_ENDPOINT", True)
    monkeypatch.setattr(cfg, "WS_C_CONV_MIN_SILENCE", 600)
    monkeypatch.setattr(cfg, "WS_C_CAP_MIN_SILENCE", 800)
    monkeypatch.setattr(cfg, "WS_C_CAP_MAX_SILENCE", 1600)
    cmin, cmax = cfg.ws_c_profile_for_phase(phase)
    assert (cmin, cmax) == (800, 1600)
    # Capture must never be more aggressive than conversation.
    assert cmin >= cfg.WS_C_CONV_MIN_SILENCE


def test_hard_gate_floors_misconfigured_capture(monkeypatch):
    """Even if the env sets capture LOWER than conversation, the hard gate floors
    it back up — an elderly caller reading a number is never clipped to save ms."""
    monkeypatch.setattr(cfg, "WS_C_SEMANTIC_ENDPOINT", True)
    monkeypatch.setattr(cfg, "WS_C_CONV_MIN_SILENCE", 700)
    monkeypatch.setattr(cfg, "WS_C_CAP_MIN_SILENCE", 300)   # misconfigured aggressive
    monkeypatch.setattr(cfg, "WS_C_CAP_MAX_SILENCE", 500)
    cmin, cmax = cfg.ws_c_profile_for_phase("name")
    assert cmin >= 700, "hard gate let capture be more aggressive than conversation"
    assert cmax >= cmin


# ---------------------------------------------------------------------------
# 2. Handler apply logic (dedup / transition / OFF)
# ---------------------------------------------------------------------------

class _RecStt:
    """Records the (min, max) pushed to AssemblyAI."""
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def request_config_update(self, mn: int, mx: int) -> bool:
        self.calls.append((mn, mx))
        return True


def _make_handler(session: dict) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        session=session,
        _ws_c_last_phase=None,
        _stt_stream=_RecStt(),
    )


_NAME_Q = "could I take your first name and surname?"
_CONV_Q = "any of those suit you?"

# Deterministic profile so these tests isolate the apply logic from config env.
_FAKE_PROFILE = lambda p: (800, 1600) if p in ("name", "phone") else (600, 1280)


async def test_apply_off_pushes_nothing(monkeypatch):
    monkeypatch.setattr(conn, "WS_C_SEMANTIC_ENDPOINT", False)
    h = _make_handler({"last_bot_prompt": _NAME_Q})
    conn.WebSocketCallHandler._ws_c_apply_endpoint_profile(h)
    await asyncio.sleep(0)
    assert h._stt_stream.calls == []
    assert h._ws_c_last_phase is None


async def test_apply_pushes_capture_profile_on_name(monkeypatch):
    monkeypatch.setattr(conn, "WS_C_SEMANTIC_ENDPOINT", True)
    monkeypatch.setattr(conn, "ws_c_profile_for_phase", _FAKE_PROFILE)
    h = _make_handler({"last_bot_prompt": _NAME_Q})
    conn.WebSocketCallHandler._ws_c_apply_endpoint_profile(h)
    await asyncio.sleep(0)
    assert h._stt_stream.calls == [(800, 1600)]
    assert h._ws_c_last_phase == "name"


async def test_apply_dedups_unchanged_phase(monkeypatch):
    monkeypatch.setattr(conn, "WS_C_SEMANTIC_ENDPOINT", True)
    monkeypatch.setattr(conn, "ws_c_profile_for_phase", _FAKE_PROFILE)
    h = _make_handler({"last_bot_prompt": _NAME_Q})
    conn.WebSocketCallHandler._ws_c_apply_endpoint_profile(h)
    conn.WebSocketCallHandler._ws_c_apply_endpoint_profile(h)  # same phase again
    await asyncio.sleep(0)
    assert h._stt_stream.calls == [(800, 1600)], "redundant push on unchanged phase"


async def test_apply_transitions_conversation_name_conversation(monkeypatch):
    monkeypatch.setattr(conn, "WS_C_SEMANTIC_ENDPOINT", True)
    monkeypatch.setattr(conn, "ws_c_profile_for_phase", _FAKE_PROFILE)
    h = _make_handler({"last_bot_prompt": _CONV_Q})
    conn.WebSocketCallHandler._ws_c_apply_endpoint_profile(h)      # conversation
    h.session["last_bot_prompt"] = _NAME_Q
    conn.WebSocketCallHandler._ws_c_apply_endpoint_profile(h)      # → name
    h.session["last_bot_prompt"] = "all booked — anything else?"
    conn.WebSocketCallHandler._ws_c_apply_endpoint_profile(h)      # → conversation
    await asyncio.sleep(0)
    assert h._stt_stream.calls == [(600, 1280), (800, 1600), (600, 1280)]


async def test_apply_phone_dtmf_flag_selects_capture(monkeypatch):
    monkeypatch.setattr(conn, "WS_C_SEMANTIC_ENDPOINT", True)
    monkeypatch.setattr(conn, "ws_c_profile_for_phase", _FAKE_PROFILE)
    # capture_phase() returns "phone" from this flag alone.
    h = _make_handler({"v3_phone_dtmf_active": True})
    conn.WebSocketCallHandler._ws_c_apply_endpoint_profile(h)
    await asyncio.sleep(0)
    assert h._stt_stream.calls == [(800, 1600)]
    assert h._ws_c_last_phase == "phone"


# ---------------------------------------------------------------------------
# 3. STT UpdateConfiguration message shape
# ---------------------------------------------------------------------------

class _RecWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, m: str) -> None:
        self.sent.append(m)


async def test_stt_update_configuration_message_shape():
    s = STTStream()
    s._ws = _RecWs()
    ok = await s.request_config_update(800, 1600)
    assert ok is True
    assert len(s._ws.sent) == 1
    payload = json.loads(s._ws.sent[0])
    assert payload["type"] == "UpdateConfiguration"
    assert payload["min_turn_silence"] == 800
    assert payload["max_turn_silence"] == 1600


async def test_stt_update_configuration_noop_without_socket():
    s = STTStream()
    s._ws = None
    assert await s.request_config_update(800, 1600) is False


async def test_stt_update_configuration_never_raises_on_send_error():
    class _BoomWs:
        async def send(self, m):
            raise RuntimeError("socket exploded")
    s = STTStream()
    s._ws = _BoomWs()
    # Must swallow and return False — a config push can never take down the call.
    assert await s.request_config_update(800, 1600) is False
