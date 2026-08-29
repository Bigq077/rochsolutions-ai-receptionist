"""
Regression: the caller heard four ways of "let me look" in 4.6 seconds.

Two independent systems cover the same latency gap and neither knew about the
other:

  FillerGuard   — pre-recorded .ulaw clips, armed at TURN START, fires at 350ms.
                  It cannot know why it is speaking; the model has not decided
                  whether it is calling check_availability yet.
  with_filler   — TTS phrase, fired at TOOL INVOCATION, knows exactly which tool.

On CAa11b26a1 (2026-08-07 22:23:15–20) that produced, in order:

    15.496  clip 1   "Let me have a look at what we've got…"
    17.997  clip 2   "Just one moment…"
    18.080  TTS      "Let me see what we have available…"
    20.066  slots    "Here's what we've got coming up — Number 1, …"

The first and third are the same sentence in different words, and clip 1's text
was *verbatim* THINKING_FILLERS_PRIMARY[0] — so with pick_filler() choosing at
random the caller could hear the identical sentence twice.

This was invisible until O-4 shipped: before the clips existed FillerGuard held
b"" and the TTS phrase was the only voice in the gap. Fixing the dead air is
what exposed the redundancy.

Two guards here:
  1. with_filler suppresses its opening phrase when the clip already spoke, but
     KEEPS the 4-second secondary — the clips cover ~5s and suppressing both
     would reopen the dead air O-4 closed.
  2. The clip text must be intent-neutral and must not duplicate the TTS pool,
     because on a turn with no tool call the clip is the only thing that speaks.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from app.filler_phrases import THINKING_FILLERS_PRIMARY, with_filler
from app.media_streams.filler_guard import FillerGuard
from app.media_streams.connection import _AUDIO_CLIPS_DIR
from app.media_streams.turn_handler import _BANNED_SENTENCE_RE


def _load_synthesise_script():
    """scripts/ is not a package — load the module by path."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "synthesise_filler.py"
    spec = importlib.util.spec_from_file_location("_synthesise_filler", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 1. with_filler suppression ──────────────────────────────────────────────

async def test_opening_phrase_is_suppressed_when_the_clip_already_spoke():
    spoken: list[str] = []

    async def _tts(text: str) -> None:
        spoken.append(text)

    async def _api():
        return "slots"

    result = await with_filler(
        api_coro=_api(),
        filler_list=THINKING_FILLERS_PRIMARY,
        session={},
        tts_fn=_tts,
        skip_primary=True,
    )

    assert result == "slots"
    assert spoken == [], (
        f"FillerGuard's clip already told the caller to hold on; queuing "
        f"{spoken!r} on top is the second of four ways of saying it"
    )


async def test_opening_phrase_still_plays_when_the_clip_did_not_speak():
    """The suppression must be conditional — a turn with no clip still needs a
    voice in the gap, or this 'fix' reintroduces the dead air of O-4."""
    spoken: list[str] = []

    async def _tts(text: str) -> None:
        spoken.append(text)

    async def _api():
        return "slots"

    await with_filler(
        api_coro=_api(),
        filler_list=THINKING_FILLERS_PRIMARY,
        session={},
        tts_fn=_tts,
        skip_primary=False,
    )

    assert len(spoken) == 1 and spoken[0] in THINKING_FILLERS_PRIMARY


async def test_a_slow_api_still_gets_the_secondary_even_with_primary_suppressed(
    monkeypatch,
):
    """
    The clips cover roughly the first five seconds. Beyond that the caller is
    back in silence unless the >4s secondary still fires, so suppressing the
    primary must not suppress both.
    """
    import app.filler_phrases as fp

    spoken: list[str] = []

    async def _tts(text: str) -> None:
        spoken.append(text)

    async def _api():
        return "slots"

    async def _fake_wait(tasks, timeout=None):
        # Simulate the API blowing through the 4s watch without waiting 4s.
        return set(), set(tasks)

    monkeypatch.setattr(fp.asyncio, "wait", _fake_wait)

    result = await with_filler(
        api_coro=_api(),
        filler_list=THINKING_FILLERS_PRIMARY,
        session={},
        tts_fn=_tts,
        skip_primary=True,
    )

    assert result == "slots"
    assert len(spoken) == 1, "the >4s secondary must survive primary suppression"
    assert spoken[0] in fp.THINKING_FILLERS_SECONDARY


# ── 2. FillerGuard must report whether it actually spoke ────────────────────

async def test_the_guard_flags_the_session_only_once_audio_has_gone_out():
    sent: list[bytes] = []

    async def _send(b: bytes) -> None:
        sent.append(b)

    guard = FillerGuard(
        clip_path=_AUDIO_CLIPS_DIR / "filler_checking.ulaw",
        send_audio=_send,
    )
    session = {"booking_flow_active": True}

    await guard.arm(session, delay_ms=10)
    assert session["_filler_clip_spoke_this_turn"] is False, (
        "arming is not speaking — a turn whose LLM answers inside the delay "
        "cancels the timer having said nothing, and must still get a TTS filler"
    )

    await asyncio.sleep(0.08)
    assert session["_filler_clip_spoke_this_turn"] is True
    assert sent, "clip should have been sent"


async def test_arming_clears_the_previous_turns_flag():
    """Without the reset, one clip early in a call suppresses the TTS filler
    for every remaining turn."""
    async def _send(_b: bytes) -> None:
        return None

    guard = FillerGuard(
        clip_path=_AUDIO_CLIPS_DIR / "filler_checking.ulaw",
        send_audio=_send,
    )
    session = {"booking_flow_active": True, "_filler_clip_spoke_this_turn": True}

    await guard.arm(session, delay_ms=10_000)
    assert session["_filler_clip_spoke_this_turn"] is False

    guard.cancel()


async def test_the_flag_is_cleared_even_when_the_guard_is_gated_off():
    """arm() returns early when booking_flow_active is False. The reset must
    happen before that gate, or a stale True leaks across turns."""
    async def _send(_b: bytes) -> None:
        return None

    guard = FillerGuard(
        clip_path=_AUDIO_CLIPS_DIR / "filler_checking.ulaw",
        send_audio=_send,
    )
    session = {"booking_flow_active": False, "_filler_clip_spoke_this_turn": True}

    await guard.arm(session)
    assert session["_filler_clip_spoke_this_turn"] is False


# ── 3. The clip wording itself ──────────────────────────────────────────────

def test_clip_text_is_not_also_in_the_tts_filler_pool():
    """
    The root cause. filler_checking.ulaw's text was THINKING_FILLERS_PRIMARY[0]
    verbatim, so the recorded clip and the random TTS pick could be the same
    sentence.
    """
    mod = _load_synthesise_script()
    for text, path in mod.CLIPS:
        assert text not in THINKING_FILLERS_PRIMARY, (
            f"{path.name} says {text!r}, which is also in "
            f"THINKING_FILLERS_PRIMARY — the caller can hear it twice"
        )


def test_clip_text_avoids_phrases_the_engine_strips_from_model_speech():
    """
    `turn_handler` strips "bear with me" / "just a moment" from model speech.
    A deterministic clip is the one path by which a caller can still hear a
    phrase the engine forbids everywhere else — that has already happened twice,
    in THINKING_FILLERS_SECONDARY and LOOKUP_FILLERS.
    """
    mod = _load_synthesise_script()
    _checked = {"bear_with_me", "bare_with_me", "just_a_moment", "one_moment"}
    for text, path in mod.CLIPS:
        for desc, pattern in _BANNED_SENTENCE_RE:
            if desc in _checked:
                assert not pattern.search(text), (
                    f"{path.name} says {text!r}, which contains the banned "
                    f"phrase '{desc}' that turn_handler strips from model speech"
                )


def test_the_script_refuses_to_guess_the_voice():
    """
    The wrong voice shipped because this script fell back to a default when
    ELEVENLABS_VOICE_ID was unset locally, while Render sets it explicitly.
    A default cannot be right: the value that matters is in Render's
    environment, and a mismatch is only ever caught by ear on a live call.
    """
    mod = _load_synthesise_script()
    src = (Path(mod.__file__)).read_text(encoding="utf-8")
    assert 'os.environ.get("ELEVENLABS_VOICE_ID", "")' in src, (
        "synthesise_filler.py must not carry a fallback voice ID"
    )
