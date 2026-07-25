# tests/regression/test_audio_capture.py
"""Diagnostic capture of the caller's inbound audio — DEFAULT OFF.

Why it exists
-------------
Across six jv_v1 calls on 2026-07-24/25 the caller said "I said…" — believing
they had already answered — while the logs showed no partial, no final and no
energy-VAD event for those windows. Three attempts to infer the cause from
server logs alone were wrong, because the server can only observe what Twilio
hands it: a caller whose voice never left the handset and a caller sitting in
silence produce byte-identical logs.

Two artefacts bracket it — Twilio's dual-channel recording (what Twilio got)
and this module's WAV (what our server got). Whichever is silent is the side
that lost the audio.

What these tests actually protect
---------------------------------
1. OFF by default, and off means allocating nothing. This touches the 50 Hz
   inbound audio path on a live phone call.
2. It can never raise into that path. A diagnostic that degrades a call is
   worse than the fault it measures — the same lesson as the dead-air backstop,
   where a probe raising inside the safety net would have stranded a caller.
3. The WAV is real, decodable 8 kHz PCM16 — an unplayable file would waste the
   one test call it exists to inform.
"""

import os
import struct
import wave

import pytest

from app.media_streams import audio_capture as ac


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "AUDIO_CAPTURE_ENABLED", "TWILIO_CALL_RECORDING_ENABLED",
        "AUDIO_CAPTURE_DIR", "AUDIO_CAPTURE_MAX_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Off by default — this is a healthcare line.
# ---------------------------------------------------------------------------
def test_both_switches_default_off():
    assert ac.capture_enabled() is False
    assert ac.twilio_recording_enabled() is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", "On"])
def test_switches_accept_the_usual_truthy_spellings(monkeypatch, value):
    monkeypatch.setenv("AUDIO_CAPTURE_ENABLED", value)
    monkeypatch.setenv("TWILIO_CALL_RECORDING_ENABLED", value)
    assert ac.capture_enabled() is True
    assert ac.twilio_recording_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "maybe"])
def test_anything_else_is_off(monkeypatch, value):
    monkeypatch.setenv("AUDIO_CAPTURE_ENABLED", value)
    assert ac.capture_enabled() is False


def test_connection_allocates_nothing_when_disabled():
    """The hot path must cost one is-None test, not an object per call."""
    import inspect

    from app.media_streams import connection as conn

    src = inspect.getsource(conn.WebSocketCallHandler.__init__)
    assert "capture_enabled()" in src, (
        "capture is constructed unconditionally — a disabled call would still "
        "allocate a buffer on every connection"
    )
    assert "self._audio_capture = None" in src


# ---------------------------------------------------------------------------
# The WAV must be real.
# ---------------------------------------------------------------------------
def _silence(n_bytes: int) -> bytes:
    """G.711 mu-law digital silence is 0xFF."""
    return b"\xff" * n_bytes


def test_wav_is_decodable_8khz_pcm16():
    cap = ac.CallAudioCapture("CAtest")
    cap.append(_silence(8000))          # 1 second
    data = cap.to_wav_bytes()

    import io
    with wave.open(io.BytesIO(data), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 8000
        assert wav.getnframes() == 8000  # 1s at 8 kHz


def test_mulaw_is_expanded_not_copied_raw():
    """ulaw2lin must run — raw mu-law bytes in a PCM16 container is noise."""
    cap = ac.CallAudioCapture("CAtest")
    cap.append(b"\x00" * 100)           # mu-law 0x00 is a large NEGATIVE sample
    import io
    with wave.open(io.BytesIO(cap.to_wav_bytes()), "rb") as wav:
        frames = wav.readframes(1)
    (sample,) = struct.unpack("<h", frames)
    assert sample != 0, "mu-law 0x00 decoded as PCM zero — expansion did not run"


def test_seconds_reports_wall_clock():
    cap = ac.CallAudioCapture("CAtest")
    cap.append(_silence(8000 * 3))
    assert cap.seconds == pytest.approx(3.0)


def test_write_lands_on_disk_and_is_playable(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIO_CAPTURE_DIR", str(tmp_path))
    cap = ac.CallAudioCapture("CAwrite")
    cap.append(_silence(8000))
    path = cap.write()

    assert path and os.path.exists(path)
    assert path.endswith("CAwrite.wav")
    with wave.open(path, "rb") as wav:
        assert wav.getnframes() == 8000


def test_write_names_the_file_from_the_sid_supplied_at_flush(tmp_path, monkeypatch):
    """The handler is built BEFORE Twilio's "start" event, so call_sid is None
    at construction. Without the flush-time override every call on a test round
    would write unknown.wav over the previous one."""
    monkeypatch.setenv("AUDIO_CAPTURE_DIR", str(tmp_path))
    cap = ac.CallAudioCapture("unknown")        # what __init__ actually sees
    cap.append(_silence(800))
    path = cap.write("CAreal123")               # what cleanup passes
    assert path.endswith("CAreal123.wav")
    assert (tmp_path / "CAreal123.wav").exists()


def test_two_calls_do_not_overwrite_each_other(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIO_CAPTURE_DIR", str(tmp_path))
    for sid in ("CAone", "CAtwo"):
        cap = ac.CallAudioCapture("unknown")
        cap.append(_silence(800))
        cap.write(sid)
    assert {p.name for p in tmp_path.iterdir()} == {"CAone.wav", "CAtwo.wav"}


def test_connection_passes_the_real_sid_at_flush():
    import inspect

    from app.media_streams import connection as conn

    src = inspect.getsource(conn.WebSocketCallHandler._cleanup)
    assert "_audio_capture.write(self.call_sid)" in src, (
        "cleanup calls write() without the sid — every WAV would be unknown.wav"
    )


def test_no_audio_writes_no_file_and_says_so(tmp_path, monkeypatch, caplog):
    """The interesting case: nothing reached this server at all."""
    monkeypatch.setenv("AUDIO_CAPTURE_DIR", str(tmp_path))
    cap = ac.CallAudioCapture("CAsilent")
    with caplog.at_level("INFO"):
        assert cap.write() is None
    assert "no inbound audio reached this server" in caplog.text
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# It must never disturb a live call.
# ---------------------------------------------------------------------------
def test_append_is_bounded(monkeypatch, caplog):
    """A stuck stream must not grow memory without limit.

    Uses 10s because _max_seconds() floors at 10 — a smaller value here would
    be silently clamped and the test would prove nothing.
    """
    monkeypatch.setenv("AUDIO_CAPTURE_MAX_SECONDS", "10")   # 80_000 bytes
    cap = ac.CallAudioCapture("CAbig")
    with caplog.at_level("WARNING"):
        for _ in range(200):                                # 200_000 bytes offered
            cap.append(_silence(1000))
    assert cap.seconds <= 10.0
    assert "ceiling" in caplog.text


def test_max_seconds_is_floored_so_a_typo_cannot_disable_capture(monkeypatch):
    monkeypatch.setenv("AUDIO_CAPTURE_MAX_SECONDS", "1")
    assert ac._max_seconds() == 10


def test_append_never_raises_on_junk():
    cap = ac.CallAudioCapture("CAjunk")
    for junk in (None, b"", "not-bytes", 12345, []):
        cap.append(junk)   # must not raise
    assert cap.seconds == 0.0


def test_write_never_raises_on_an_unwritable_dir(tmp_path, monkeypatch):
    """Point the capture dir at an existing FILE — makedirs cannot succeed.

    Portable: a null byte in the path is rejected by os.environ on Windows
    before it ever reaches the code under test.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("AUDIO_CAPTURE_DIR", str(blocker / "sub"))
    cap = ac.CallAudioCapture("CAbad")
    cap.append(_silence(800))
    assert cap.write() is None   # degraded, not raised


def test_bad_max_seconds_falls_back(monkeypatch):
    monkeypatch.setenv("AUDIO_CAPTURE_MAX_SECONDS", "not-a-number")
    cap = ac.CallAudioCapture("CAenv")
    cap.append(_silence(8000))
    assert cap.seconds == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Twilio side.
# ---------------------------------------------------------------------------
async def test_twilio_recording_is_a_noop_when_disabled(monkeypatch):
    called = False

    def _boom(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("must not touch the network when disabled")

    monkeypatch.setattr("httpx.AsyncClient", _boom)
    await ac.start_twilio_recording("CA123")
    assert called is False


async def test_twilio_recording_needs_credentials(monkeypatch, caplog):
    monkeypatch.setenv("TWILIO_CALL_RECORDING_ENABLED", "true")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    with caplog.at_level("WARNING"):
        await ac.start_twilio_recording("CA123")
    assert "are not set" in caplog.text


async def test_twilio_recording_failure_never_propagates(monkeypatch, caplog):
    """A diagnostic must not stop a call being answered."""
    monkeypatch.setenv("TWILIO_CALL_RECORDING_ENABLED", "true")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr("httpx.AsyncClient", _Boom)
    with caplog.at_level("WARNING"):
        await ac.start_twilio_recording("CA123")   # must not raise
    assert "recording request failed" in caplog.text


async def test_twilio_recording_asks_for_dual_channel(monkeypatch):
    """Mono would not show whether the caller was talking over Susie."""
    monkeypatch.setenv("TWILIO_CALL_RECORDING_ENABLED", "true")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    seen = {}

    class _Resp:
        status_code = 201
        text = "{}"

    class _Client:
        def __init__(self, *a, **k):
            seen["timeout"] = k.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, auth=None):
            seen["url"] = url
            seen["data"] = data
            seen["auth"] = auth
            return _Resp()

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    await ac.start_twilio_recording("CA123")

    assert seen["data"]["RecordingChannels"] == "dual"
    assert seen["data"]["RecordingTrack"] == "both"
    assert "/Calls/CA123/Recordings.json" in seen["url"]
    assert seen["auth"] == ("AC_test", "tok")
    assert seen["timeout"] is not None, (
        "no timeout — CLAUDE.md flags un-timed outbound calls as a live hazard"
    )
