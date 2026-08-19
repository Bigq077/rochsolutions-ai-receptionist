"""`speed` is sent only where it is meant to be, and can never cause dead air.

The pacing rule in test_tts_phone_pacing.py decides what the words are; this
file covers the other half — the ElevenLabs voice_settings field that decides
how fast they are spoken.

Two properties matter more than the feature:

  * A deployment that has tuned nothing sends exactly the request it sent
    before this change.  `speed` is omitted at 1.0, not sent as 1.0.
  * A rejected `speed` must never cost the caller the utterance.  ElevenLabs
    returns 422 for a parameter the model or account does not accept, and the
    non-200 branch in synthesise_chunk returns without enqueuing a single audio
    frame — so an unsupported parameter on the phone-readback turn would be
    silence in the middle of a booking.  It retries without the field instead.
"""
import asyncio

import pytest

from app.media_streams import tts_stream
from app.media_streams.audio_out import AudioOutputProcessor


PHONE_LINE  = "Thanks — I've got 07502211207. Is that correct?"
NORMAL_LINE = "That appointment is 45 minutes, on Tuesday the fourth."

# 320 bytes of silent PCM16 — enough for convert_chunk to emit something.
_PCM = b"\x00\x00" * 160


class _FakeResponse:
    def __init__(self, status_code, bodies):
        self.status_code = status_code
        self.headers     = {"content-type": "audio/pcm"}
        self._bodies     = bodies

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        return b'{"detail": "speed is not supported"}'

    async def aiter_bytes(self, chunk_size=None):
        for b in self._bodies:
            yield b


class _FakeClient:
    """Records every request body synthesise_chunk sends."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.bodies   = []

    def stream(self, method, url, json=None, headers=None):
        import copy
        self.bodies.append(copy.deepcopy(json))
        status = self.statuses.pop(0) if self.statuses else 200
        return _FakeResponse(status, [_PCM] if status == 200 else [])


async def _synthesise(line):
    queue = asyncio.Queue()
    await tts_stream.TTSStream(clinic_id="test").synthesise_chunk(
        text=line, audio_out_queue=queue, audio_out_processor=AudioOutputProcessor(),
    )
    return queue


@pytest.fixture
def client(monkeypatch):
    """Isolate the module globals this feature latches onto."""
    monkeypatch.setattr(tts_stream, "_ELEVENLABS_EXHAUSTED", False)
    monkeypatch.setattr(tts_stream, "_ELEVENLABS_SPEED_UNSUPPORTED", False)
    monkeypatch.setattr(tts_stream, "ELEVENLABS_SPEED", 1.0)
    monkeypatch.setattr(tts_stream, "ELEVENLABS_PHONE_SPEED", 0.8)
    monkeypatch.delenv("TTS_BYPASS_CLINIC", raising=False)

    def _install(statuses=(200,)):
        fake = _FakeClient(statuses)
        monkeypatch.setattr(tts_stream, "_get_http_client", lambda: fake)
        return fake

    return _install


# ── Where the field is and is not sent ──────────────────────────────────────

async def test_a_phone_readback_is_sent_slower(client):
    fake = client()
    await _synthesise(PHONE_LINE)
    assert fake.bodies[0]["voice_settings"]["speed"] == 0.8


async def test_an_ordinary_turn_carries_no_speed_field_at_all(client):
    """Not "speed: 1.0" — absent.  At default config the request on every
    non-readback turn is byte-identical to the one sent before this change,
    which is what makes the blast radius one utterance wide."""
    fake = client()
    await _synthesise(NORMAL_LINE)
    assert "speed" not in fake.bodies[0]["voice_settings"]


async def test_stability_and_similarity_are_untouched(client):
    fake = client()
    await _synthesise(PHONE_LINE)
    vs = fake.bodies[0]["voice_settings"]
    assert vs["stability"] == tts_stream.ELEVENLABS_STABILITY
    assert vs["similarity_boost"] == tts_stream.ELEVENLABS_SIMILARITY_BOOST


async def test_the_words_sent_are_the_paced_ones(client):
    fake = client()
    await _synthesise(PHONE_LINE)
    assert "oh seven five oh two, two one one, two oh seven" in fake.bodies[0]["text"]


# ── The failure that must never reach the caller ────────────────────────────

async def test_a_rejected_speed_retries_without_it_rather_than_going_silent(client):
    fake  = client(statuses=(422, 200))
    queue = await _synthesise(PHONE_LINE)

    assert len(fake.bodies) == 2, "the chunk was not retried"
    assert fake.bodies[0]["voice_settings"]["speed"] == 0.8
    assert "speed" not in fake.bodies[1]["voice_settings"]
    assert fake.bodies[1]["text"] == fake.bodies[0]["text"]
    assert not queue.empty(), "caller heard nothing — this is dead air mid-booking"


async def test_a_rejection_latches_so_later_chunks_do_not_pay_it_again(client):
    client(statuses=(422, 200))
    await _synthesise(PHONE_LINE)
    assert tts_stream._ELEVENLABS_SPEED_UNSUPPORTED is True

    fake = client(statuses=(200,))
    await _synthesise(PHONE_LINE)
    assert "speed" not in fake.bodies[0]["voice_settings"]


async def test_a_422_that_is_not_about_speed_is_still_terminal(client):
    """The retry exists for one parameter.  A 422 on a request that never
    carried `speed` must not become an infinite retry loop."""
    fake = client(statuses=(422, 422, 422))
    await _synthesise(NORMAL_LINE)
    assert len(fake.bodies) == 1


# ── The clamp ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("0.8",  0.8),    # in band
    ("0.4",  0.7),    # below ElevenLabs' floor — clamped, not sent as-is
    ("2.0",  1.2),    # above its ceiling
    ("fast", 0.85),   # junk falls back to the default it was given
    ("",     0.85),
])
def test_a_typo_in_the_dashboard_cannot_422_every_request(raw, expected):
    from app.media_streams.config import _clamped_speed
    assert _clamped_speed(raw, 0.85) == expected
