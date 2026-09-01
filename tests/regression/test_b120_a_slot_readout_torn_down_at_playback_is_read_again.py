"""
Regression: twelve seconds of appointment times, killed by a word nobody said.

B-120 — CAa2bdff2b702cea8869d29a0dca981e26, demo line, build 0bc6ca45,
1 September 2026, 15:14.

    15:14:45.985  tts_finished in 12.2s: "Number 3, … And I've a few o"
    15:14:47.213  barge-in: partial='hi'
    15:14:47.213  barge-in start: synthesis_active=False playback_active=True
    15:14:50.165  barge-in #0 carried no words (partial='hi' own_audio=False)
                  — speaking 'Any of those work?' instead of an ack (1/2)
    15:14:57.114  FINAL → queue: 'uh you got cut off say that again'

Nothing failed. Every ElevenLabs call returned 200 inside 800ms, lost_total=0,
media_frames=5204. The readout was built correctly and synthesised in full. The
caller heard "The available slots for Friday eleventh September are — Number
one, eight in the…" and then silence: options two and three, the more-times
line and the closing question were all sitting in Twilio's buffer when the
teardown flushed it, 1.9 seconds into 12.2.

── WHY THREE EXISTING GUARDS ALL MISSED ───────────────────────────────────────
  * `_BARGE_NOISE` runs on the FINAL. The teardown is on the PARTIAL, and no
    final ever arrived, so the filter never ran at all. It would not have
    helped either way: 'hi' is a real word. This is the shape recorded in
    barge-in-tears-down-before-the-noise-filter.
  * `synthesis_active=False` was read as "she has finished speaking". It means
    "we have finished SENDING". On a slot readout those differ by twelve
    seconds — the engine even logs the gap and then does not use it.
  * the heard-nothing slot recovery (Bug A, in `_tts_loop`) is built for this
    exact complaint, but triggers on chunks discarded by `tts_inhibit` BEFORE
    synthesis. These were synthesised fine and killed at PLAYBACK.

── WHAT CHANGED, AND WHAT DELIBERATELY DID NOT ────────────────────────────────
The recovery, not the trigger. Making the teardown reluctant would make Susie
un-interruptible, and the obvious version of that fix has already been wrong in
this repo more than once.

The arm that fired is right to refuse an ack claiming the caller spoke — but
re-asking `last_question` after a truncated readout is useless, because "Any of
those work?" refers to options the caller never heard. So when a barge-in
destroys a readout mid-playback, the readout goes again, whole.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.media_streams.connection import (
    SilenceHandler,
    WebSocketCallHandler,
    _BARGE_IN_ACKS,
    _MAX_ECHO_RESUMES,
    _SLOT_REREAD_MIN_LOST_S,
    _WATCHDOG_REASK_MARKER,
)
from app.media_streams.llm_stream import LLMStream


# The call's own day and times.
FRIDAY = {
    "date": "2026-09-11",
    "day_label": "Friday 11th September",
    "slot_times": ["08:00", "08:50", "09:20"],
    "slot_times_spoken": [
        "eight in the morning",
        "ten to nine in the morning",
        "twenty past nine in the morning",
    ],
    "slots": [
        {"start": "2026-09-11T08:00:00+01:00", "end": ""},
        {"start": "2026-09-11T08:50:00+01:00", "end": ""},
        {"start": "2026-09-11T09:20:00+01:00", "end": ""},
    ],
}

# 12.2s of audio was scheduled at 15:14:45.985; the teardown landed at
# 15:14:47.213, so 11.0s of it never reached the caller.
LOST_S = 11.0

CLOSING_Q = "Any of those work?"


async def _present_friday(session: dict) -> list:
    """Speak the readout the way the live call did, through the real builder.

    Driving `_flush_slot_buf` rather than hand-writing the chunks is the point:
    what has to survive a re-read is whatever the payload actually produces,
    including the more-times tail, which rides on the LAST chunk and is
    therefore the first thing any interruption costs.
    """
    from app.tools.slot_offer import build_slot_offer

    offer = build_slot_offer([FRIDAY], lead_in="", more_times=True)
    session["_slot_offer_prebuilt"] = {
        "chunks": list(offer.chunks),
        "slots": [
            {"start": s["start"], "end": s.get("end") or "",
             "spoken": s.get("spoken"), "date": s.get("date")}
            for s in offer.slots
        ],
        "dtmf_map": dict(offer.dtmf_map),
        "more_times": bool(offer.more_times),
    }
    session["available_days"] = [FRIDAY]

    buf, tts = asyncio.Queue(), asyncio.Queue()
    await buf.put("Number 1, eight. Number 2, ten to nine.")
    await LLMStream._flush_slot_buf(buf, tts, session)

    spoken = []
    while not tts.empty():
        spoken.append(tts.get_nowait())
    return spoken


def _handler(session: dict) -> WebSocketCallHandler:
    """The B-107 harness, unchanged — this defect lands on the same arm."""
    h = object.__new__(WebSocketCallHandler)
    h.session = session
    h.call_sid = "CAa2bdff2b"
    h.tts_text_queue = asyncio.Queue()
    h.transcript_queue = asyncio.Queue()
    h._clearing = True
    h._tts_task = None
    h._barge_in_pending = True
    h._barge_in_ts = time.monotonic() - 1.5     # past the confirm threshold
    h._barge_in_duration = 0.0
    h._barge_in_flush_before = 0.0
    h._in_barge_in_recovery = False
    h._last_turn_done_at = 0.0
    h._tts_last_start_ts = time.time()
    h._tts_audio_done_at = 0.0
    h._silence_handler = SilenceHandler(
        tts_text_queue=h.tts_text_queue,
        trigger_transfer_fn=lambda *a, **k: None,
        get_session=lambda: session,
    )
    h._silence_handler._tts_playing = False
    h._silence_handler.last_question = CLOSING_Q
    return h


async def _interrupted_call(lost_s: float = LOST_S, **over):
    """The live call, up to the instant the recovery arm has to decide."""
    session = {
        "clinic_id": "northgate",
        "state": "PRESENT_TIMES",
        "flow_step": 0,
    }
    chunks = await _present_friday(session)

    # …and now the teardown: `tts_inhibit` set, the last chunk snapshotted as
    # the text in flight, the partial recorded, and the audio that will never
    # play measured before the playout clock is cleared.
    session.update({
        "tts_inhibit": True,
        "interrupted_tts_text": chunks[-1],
        "barge_in_trigger_partial": "hi",
        "barge_in_playout_lost_s": lost_s,
        "barge_in_count": 0,
    })
    session.update(over)
    return _handler(session), chunks


def _queued(h) -> list:
    out = []
    while not h.tts_text_queue.empty():
        text = h.tts_text_queue.get_nowait()
        if text.startswith(_WATCHDOG_REASK_MARKER):
            text = text[len(_WATCHDOG_REASK_MARKER):]
        out.append(text)
    return out


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_readout_is_spoken_again_not_the_closing_question():
    """The caller's own instruction: "uh you got cut off say that again"."""
    h, chunks = await _interrupted_call()

    await h._on_final_transcript_clear("")

    assert _queued(h) == chunks, (
        "the caller heard 1.9s of a 12.2s readout and was answered with a "
        "question about options they never heard"
    )


@pytest.mark.asyncio
async def test_every_option_reaches_the_caller_the_second_time():
    h, _ = await _interrupted_call()

    await h._on_final_transcript_clear("")

    text = " ".join(_queued(h))
    for spoken in FRIDAY["slot_times_spoken"]:
        assert spoken in text, f"{spoken!r} was lost twice"


@pytest.mark.asyncio
async def test_the_more_times_tail_survives_the_re_read():
    """It is appended to the LAST chunk, after every option and just before the
    closing question, so on a 12.2s readout it lands 10-12 seconds in — the
    first thing any interruption costs. On the live call it was generated
    correctly and never heard, and that is what made a working feature look
    absent and produced a whole false lead."""
    h, _ = await _interrupted_call()

    await h._on_final_transcript_clear("")

    assert "others that day" in " ".join(_queued(h))


@pytest.mark.asyncio
async def test_no_ack_claims_the_caller_spoke():
    """The B-107 property this arm exists for, still holding."""
    h, _ = await _interrupted_call()

    await h._on_final_transcript_clear("")

    assert not [t for t in _queued(h) if t in _BARGE_IN_ACKS]


@pytest.mark.asyncio
async def test_the_deadlock_is_still_cleared():
    """B-67. Queueing behind an uncleared `tts_inhibit` is silence, not a
    recovery — and this arm returns, so there is no second exit."""
    h, _ = await _interrupted_call()

    await h._on_final_transcript_clear("")

    assert h.session["tts_inhibit"] is False


@pytest.mark.asyncio
async def test_the_first_chunk_carries_the_replay_marker():
    """Without it the consecutive-duplicate guard in `_tts_loop` can drop the
    re-read on a single-chunk readout, where chunk one IS the chunk just
    spoken — B-107's failure mode, silently."""
    h, _ = await _interrupted_call()

    await h._on_final_transcript_clear("")

    first = h.tts_text_queue.get_nowait()
    assert first.startswith(_WATCHDOG_REASK_MARKER)


# ---------------------------------------------------------------------------
# The guards. Every one of these is a way to turn the fix into a new defect.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_barge_in_after_playback_ended_does_not_re_read():
    """THE guard, and the reason the trigger is scheduled-audio-lost rather
    than anything time-based. A caller who interrupts once Susie has actually
    stopped talking has heard the whole readout, and replaying twelve seconds
    over them would be a worse defect than the one being fixed. With playback
    finished there is nothing scheduled, so the measurement is 0.0."""
    h, _ = await _interrupted_call(lost_s=0.0)

    await h._on_final_transcript_clear("")

    assert _queued(h) == [CLOSING_Q], (
        "re-read a readout the caller had already heard in full"
    )


@pytest.mark.asyncio
async def test_a_teardown_in_the_last_breath_does_not_re_read():
    """Below the threshold the caller lost a syllable, not an option."""
    h, _ = await _interrupted_call(lost_s=_SLOT_REREAD_MIN_LOST_S - 0.1)

    await h._on_final_transcript_clear("")

    assert _queued(h) == [CLOSING_Q]


@pytest.mark.asyncio
async def test_a_caller_who_actually_spoke_owns_the_turn():
    """Only a WORDLESS barge-in reaches this arm. Talking over a caller who
    said "number two" with a twelve-second re-read is the one outcome worse
    than the defect."""
    h, _ = await _interrupted_call()

    await h._on_final_transcript_clear("number two please")

    assert _queued(h) == []


@pytest.mark.asyncio
async def test_no_re_read_once_the_options_are_no_longer_live():
    """The slot map outlives the readout by design — it stays armed for DTMF
    until a slot is chosen — so it cannot be the discriminator on its own.
    `_slot_readout_chunks` is dropped by `_tts_loop` the moment a chunk from
    any later turn plays, and without it this arm stands down."""
    h, _ = await _interrupted_call()
    h.session.pop("_slot_readout_chunks", None)

    await h._on_final_transcript_clear("")

    assert _queued(h) == [CLOSING_Q]


@pytest.mark.asyncio
async def test_no_re_read_without_an_armed_map():
    h, _ = await _interrupted_call()
    h.session.pop("v3_dtmf_slot_map")

    await h._on_final_transcript_clear("")

    assert _queued(h) == [CLOSING_Q]


@pytest.mark.asyncio
async def test_the_re_read_is_capped():
    """A re-read is twelve seconds of new audio that can echo and re-trigger.
    Past the cap the turn falls through to the ack, so there is always an
    exit."""
    h, _ = await _interrupted_call()
    h.session["echo_resume_count"] = _MAX_ECHO_RESUMES

    await h._on_final_transcript_clear("")

    spoken = _queued(h)
    assert spoken and spoken[0] in _BARGE_IN_ACKS, (
        f"past the cap the ack is the exit, got {spoken!r}"
    )


@pytest.mark.asyncio
async def test_the_cap_counter_advances():
    h, _ = await _interrupted_call()

    await h._on_final_transcript_clear("")

    assert h.session["echo_resume_count"] == 1


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_readout_text_is_saved_when_the_map_is_armed():
    """Bug A saves only a chunk COUNT, which is all it needs — it fires when
    the caller heard nothing. A playback teardown has to speak the words
    again, so the words have to be kept."""
    session = {"clinic_id": "northgate"}
    chunks = await _present_friday(session)

    assert session["_slot_readout_chunks"] == [c.strip() for c in chunks]
    assert session["_slot_chunks_sent"] == len(chunks)


def test_the_lost_audio_is_measured_before_the_clock_is_reset():
    """`_tts_playout_end_mono` is zeroed by the teardown itself, so this is the
    only instant the number exists. Measure after the reset and the trigger
    reads 0.0 on every call and the fix is silently inert."""
    import inspect

    src = inspect.getsource(WebSocketCallHandler._on_partial_transcript)
    measure = src.index('self.session["barge_in_playout_lost_s"]')
    reset = src.index("self._tts_playout_end_mono = 0.0")
    assert measure < reset, (
        "the playout clock is cleared before the loss is measured — "
        "barge_in_playout_lost_s would be 0.0 on every barge-in"
    )
