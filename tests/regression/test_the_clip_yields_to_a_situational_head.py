"""The recorded clip stands down when a better head is already coming.

WHY THIS EXISTS
---------------
The clip is generic by construction: it is one recording, so it cannot name the
day the caller just asked for. A situational head can.

The clip's advantage was always speed. That argument was decisive while the head
was produced at tool detection, ~2.2s into the turn -- something had to cover the
1.85s before it. Reading the head from the caller's own words instead of from the
tool moved it to HOLD_HEAD_DELAY_MS (600ms), so the gap is ~370ms once
ElevenLabs' first byte is counted, against the clip's 350ms. A third of a second
does not buy a second utterance saying a vaguer version of the same thing.

The gate is OFF unless the clinic has opted into hold_speech, and that is the
part worth guarding: on a clinic with no arbiter no head is ever produced, so
suppressing the clip there is not a better phrase, it is silence.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from app.media_streams.filler_guard import FillerGuard

_AUDIO_CLIPS_DIR = Path(__file__).resolve().parents[2] / "audio_clips"


def _guard(sent):
    async def _send(b: bytes) -> None:
        sent.append(b)

    return FillerGuard(
        clip_path=_AUDIO_CLIPS_DIR / "filler_checking.ulaw", send_audio=_send
    )


async def _armed(guard, session, **kwargs):
    await guard.arm(session, delay_ms=10, **kwargs)
    await asyncio.sleep(0.06)
    guard.cancel()


async def test_the_clip_stands_down_when_a_head_is_coming():
    sent: list[bytes] = []
    await _armed(_guard(sent), {"booking_flow_active": True}, situational_head=True)
    assert sent == [], "the clip spoke over a head that names the caller's own day"


async def test_the_clip_still_fires_when_no_head_is_coming():
    """The turns with no intent match are exactly the ones the clip is for."""
    sent: list[bytes] = []
    await _armed(_guard(sent), {"booking_flow_active": True}, situational_head=False)
    assert len(sent) == 1


async def test_suppression_does_not_spend_the_once_per_call_latch():
    """A clip that never played must not count as the call's one clip.

    Otherwise a single early head silences the recording for the rest of the
    call, including the turns that have no head of their own.
    """
    session = {"booking_flow_active": True}
    sent: list[bytes] = []
    guard = _guard(sent)
    await _armed(guard, session, situational_head=True)
    assert not session.get("_filler_clip_spoke_this_call")
    await _armed(guard, session, situational_head=False)
    assert len(sent) == 1, "the clip was permanently disabled by a suppressed turn"


async def test_suppression_leaves_the_with_filler_flag_clear():
    """`with_filler` reads _filler_clip_spoke_this_turn to decide whether its own
    opening phrase would merely repeat the clip. A suppressed clip said nothing,
    so that fallback must stay available."""
    session = {"booking_flow_active": True}
    await _armed(_guard([]), session, situational_head=True)
    assert session["_filler_clip_spoke_this_turn"] is False


def test_the_gate_defaults_to_off():
    """Every other arm() call site in connection.py passes expect_lookup only.
    A default of True would silence the clip everywhere those sites fire."""
    default = inspect.signature(FillerGuard.arm).parameters["situational_head"].default
    assert default is False


def test_the_call_site_is_gated_on_hold_speech():
    """Without the gate this changes what every clinic hears, and on a clinic
    with no arbiter the change is silence rather than a better phrase."""
    src = (Path(__file__).resolve().parents[2]
           / "app" / "media_streams" / "connection.py").read_text(encoding="utf-8")
    idx = src.index("situational_head=_situational_head")
    window = src[max(0, idx - 2000):idx]
    assert "_hs_enabled(self.session)" in window, (
        "the head lookahead is not gated on hold_speech_enabled"
    )
