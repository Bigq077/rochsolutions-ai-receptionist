"""Diagnostic capture of the caller's inbound audio. DEFAULT OFF.

WHY THIS EXISTS
---------------
Across six jv_v1 calls on 2026-07-24/25 the caller repeatedly said "I said…",
meaning they believed they had already answered — while the server logs showed
no partial transcript, no final, and no energy-VAD event for those windows.
Three separate attempts to infer what happened from server logs alone were
wrong, because the server can only see what Twilio hands it: a caller whose
voice never left the handset and a caller sitting in silence produce byte-
identical logs.

Two artefacts bracket the problem, and whichever side is silent is the side
that lost the audio:

    Twilio dual-channel recording   what TWILIO received (upstream of us)
    this module's WAV               what OUR SERVER received

If the caller is audible on the Twilio recording but not in the WAV, the loss
is between Twilio and Render. If audible in the WAV but no transcript exists
for that moment, AssemblyAI was fed speech and dropped it — actionable with
timestamps. If audible on neither, the audio never reached Twilio and nothing
in this repo is responsible.

PRIVACY — READ BEFORE ENABLING
------------------------------
This is a healthcare line. Recording patient calls engages UK GDPR: you need a
lawful basis, normally notification in the greeting, a retention policy and
secure storage. Both switches default to FALSE and must stay false on any
number a real patient can reach. They exist for test calls where the engineer
is the only speaker and therefore the data subject.

Nothing here is imported into the hot path unless enabled, and every entry
point swallows its own exceptions: a diagnostic must never be able to degrade
or drop a live call.
"""

from __future__ import annotations

import audioop
import io
import logging
import os
import wave
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)

# Twilio streams 8 kHz mono G.711 mu-law.
_SAMPLE_RATE = 8000
_SAMPLE_WIDTH = 2  # bytes per sample AFTER ulaw2lin expansion to PCM16


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in ("true", "1", "yes", "on")


def capture_enabled() -> bool:
    """Server-side WAV of the inbound leg."""
    return _flag("AUDIO_CAPTURE_ENABLED")


def twilio_recording_enabled() -> bool:
    """Twilio-side dual-channel recording of the whole call."""
    return _flag("TWILIO_CALL_RECORDING_ENABLED")


def capture_dir() -> str:
    return os.getenv("AUDIO_CAPTURE_DIR", "logs/audio")


def _max_seconds() -> int:
    """Memory ceiling. mu-law 8 kHz is 8 kB/s, so 600 s ~= 4.8 MB in RAM.

    Bounded because a stuck or abandoned stream would otherwise grow without
    limit on a long-lived process.
    """
    try:
        return max(10, int(os.getenv("AUDIO_CAPTURE_MAX_SECONDS", "600")))
    except ValueError:
        return 600


class CallAudioCapture:
    """Accumulates inbound mu-law and writes one WAV per call.

    Construct only when capture_enabled() — a disabled call allocates nothing.
    """

    def __init__(self, call_sid: str) -> None:
        self.call_sid = call_sid or "unknown"
        self._buf = bytearray()
        self._max_bytes = _max_seconds() * _SAMPLE_RATE  # 1 byte/sample in mu-law
        self._truncated = False

    def append(self, mulaw: bytes) -> None:
        """Hot path — called ~50x/second. Must never raise, never block."""
        if not mulaw or self._truncated:
            return
        try:
            if len(self._buf) + len(mulaw) > self._max_bytes:
                self._truncated = True
                logger.warning(
                    "[audio_capture] %s hit the %ds ceiling — capture stopped, "
                    "call unaffected", self.call_sid, _max_seconds(),
                )
                return
            self._buf += mulaw
        except Exception:
            self._truncated = True  # stop trying; never disturb the call

    @property
    def seconds(self) -> float:
        return len(self._buf) / float(_SAMPLE_RATE)

    def to_wav_bytes(self) -> bytes:
        """PCM16 8 kHz mono WAV. Pure — used by the tests without touching disk."""
        pcm = audioop.ulaw2lin(bytes(self._buf), _SAMPLE_WIDTH)
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(_SAMPLE_WIDTH)
            wav.setframerate(_SAMPLE_RATE)
            wav.writeframes(pcm)
        return out.getvalue()

    def write(self, call_sid: Optional[str] = None) -> Optional[str]:
        """Flush to disk at call end. Returns the path, or None.

        `call_sid` overrides the constructor value and should be passed. The
        handler is built before Twilio's "start" event arrives, so the sid is
        still None at construction — without this every call would write
        `unknown.wav` and overwrite the previous one, which on a test round is
        the difference between N artefacts and one.
        """
        if call_sid:
            self.call_sid = call_sid
        if not self._buf:
            logger.info(
                "[audio_capture] %s: nothing captured — no inbound audio reached "
                "this server at all", self.call_sid,
            )
            return None
        try:
            directory = capture_dir()
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, f"{self.call_sid}.wav")
            with open(path, "wb") as fh:
                fh.write(self.to_wav_bytes())
            logger.warning(
                "[audio_capture] %s: wrote %.1fs of inbound audio to %s%s",
                self.call_sid, self.seconds, path,
                " (TRUNCATED)" if self._truncated else "",
            )
            return path
        except Exception as exc:
            logger.warning("[audio_capture] %s: write failed: %r", self.call_sid, exc)
            return None


# ---------------------------------------------------------------------------
# ONE RECORDING REQUEST PER CALL
#
# Twilio POSTs /ms/incoming a SECOND time with the SAME CallSid once the
# <Connect><Stream> verb finishes — it is asking what to do next, not
# announcing a new call. The recording request fired on every inbound POST,
# so the post-stream one always hit a call that can no longer be recorded:
#
#     16:21:55.191  POST .../Calls/CAxxxx/Recordings.json  HTTP 400
#                   {"code":21220,"message":"Requested resource is not
#                    eligible for recording"}
#
# Harmless but not free: it is a spurious 400 in every call log, on a line
# that is being read to diagnose audio loss, and it costs an outbound HTTP
# round trip during call teardown.
#
# The claim is Redis-first because Render can run more than one worker and the
# re-POST is not guaranteed to land on the process that served the first one.
# SET NX is atomic, so two workers racing still yield exactly one request. The
# TTL only has to outlive a single call.
# ---------------------------------------------------------------------------
_RECORDING_CLAIM_TTL_S = 3600

# Fallback for when Redis is down. Per-process, so it neither survives a
# restart nor covers a second worker — which is why it is the fallback and not
# the mechanism. Bounded: a long-lived process must not accumulate CallSids
# without limit.
_recording_claimed_local: "OrderedDict[str, None]" = OrderedDict()
_RECORDING_CLAIM_LOCAL_MAX = 512


async def _claim_recording(call_sid: str) -> bool:
    """True if this is the first request to record `call_sid`.

    Fails OPEN on an unexpected error — a duplicate 400 is a cosmetic problem,
    and silently never recording would defeat the diagnostic this module
    exists for.
    """
    try:
        from .session import _get_redis
        redis = _get_redis()
        if redis is not None:
            claimed = await redis.set(
                f"ms_rec_claimed:{call_sid}", "1",
                ex=_RECORDING_CLAIM_TTL_S, nx=True,
            )
            return bool(claimed)
    except Exception as exc:
        logger.warning(
            "[audio_capture] recording claim via Redis failed (%r) — "
            "falling back to the per-process guard", exc,
        )
    if call_sid in _recording_claimed_local:
        return False
    _recording_claimed_local[call_sid] = None
    while len(_recording_claimed_local) > _RECORDING_CLAIM_LOCAL_MAX:
        _recording_claimed_local.popitem(last=False)
    return True


async def start_twilio_recording(call_sid: str) -> None:
    """Ask Twilio to record the in-progress call, dual-channel.

    Idempotent per CallSid — see the claim block above for why that matters.

    Dual channel puts the CALLER on one channel and Susie on the other, so the
    recording answers "was the caller talking over her, and did their voice
    survive?" — which a mixed-mono recording cannot.

    Fire-and-forget: awaited in a background task so the TwiML response is not
    delayed (a slow webhook delays the whole call). Explicit timeout — CLAUDE.md
    flags ~49 un-timed outbound call sites as a live hazard; this is not going
    to be the fiftieth.
    """
    if not twilio_recording_enabled() or not call_sid:
        return
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not (account_sid and auth_token):
        logger.warning(
            "[audio_capture] TWILIO_CALL_RECORDING_ENABLED is on but "
            "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are not set — skipping"
        )
        return
    # After the credential check, so a misconfigured deploy does not burn the
    # claim and then silently skip recording once the secrets are fixed.
    if not await _claim_recording(call_sid):
        logger.info(
            "[audio_capture] recording already requested for %s — skipping the "
            "post-stream duplicate", call_sid,
        )
        return
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
        f"/Calls/{call_sid}/Recordings.json"
    )
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                url,
                data={"RecordingChannels": "dual", "RecordingTrack": "both"},
                auth=(account_sid, auth_token),
            )
        if resp.status_code in (200, 201):
            logger.warning(
                "[audio_capture] Twilio dual-channel recording started for %s",
                call_sid,
            )
        else:
            logger.warning(
                "[audio_capture] Twilio recording refused for %s: HTTP %s %s",
                call_sid, resp.status_code, resp.text[:200],
            )
    except Exception as exc:
        # Never let a diagnostic stop a call being answered.
        logger.warning(
            "[audio_capture] Twilio recording request failed for %s: %r",
            call_sid, exc,
        )
