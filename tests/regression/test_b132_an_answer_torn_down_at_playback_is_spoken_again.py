"""
Regression: the answer to "my ankle hurts" was cut off to ask when he could come in.

B-132 — CA91020004728883f51fa90e325acb7ebc, northgate, build 68ef771d,
2 September 2026, 16:43.

    16:43:17.370  synth chunk 1  "An ankle that's been giving you trouble —"
    16:43:17.563  synth chunk 2  "a rolled or sprained ankle can leave…"   198 chars
    16:43:17.961  synth chunk 3  "Do you have a preference for when…"
    16:43:23.135  barge-in: partial='okay' — playback-only window
    16:43:24.514  barge-in #1 carried no words (partial='okay') — speaking
                  "Do you have a preference for when you'd like to come in?"

Three chunks — ~20s of audio — were synthesised inside 0.6s. The teardown
landed 5.2s in, inside chunk 2. It discarded the rest of chunk 2 *and* chunk 3,
and then spoke chunk 3: a sentence the caller had never reached. Reported by
the owner as "the answer to my ankle hurting gets stopped mid sentence to ask
when you are coming in".

It is not STT. Every transcript in that call is correct.

── FILED AS B-127, NUMBERED B-132 ─────────────────────────────────────────────
`OPEN_DEFECTS_2026-09-02.md` calls this B-127. That number was already spent on
the spoken-ordinal/keypad defect of 1 Sep (`connection.py:9418`, and its own
regression test), so the code and this file say B-132 — a grep for either now
returns exactly one defect.

── WHY THE EXISTING ARM MADE IT WORSE ─────────────────────────────────────────
The `_outstanding_q` arm below is right in principle — it refuses an ack that
would claim the caller spoke — but its stated premise is that `last_question`
is "by construction equal to the chunk just spoken". That holds for a
SINGLE-chunk turn. On a multi-chunk turn `last_question` is the LAST chunk,
which on this call is precisely the sentence nobody heard. So the recovery
picked, out of everything it could have said, the one line that caused the
complaint.

── WHAT CHANGED, AND WHAT DELIBERATELY DID NOT ────────────────────────────────
The recovery, not the trigger — the fourth commit in a row to make that choice
(B-67, B-107, c65f2a1c, B-120). This is B-120 generalised from slot readouts to
ordinary content.

Option 5 (suppress teardown on a backchannel partial) is CLOSED on evidence:
on this very call 'yeah' was the leading edge of two GENUINE interruptions
against one wordless 'okay', and B-107 records that a caller whose words STT
dropped and a garbled echo leave identical evidence at the partial.

The lost audio is by construction a SUFFIX of what was queued — audio plays in
order — so the replay is a suffix, and `_CONTENT_REREAD_MAX_CHARS` only chooses
how much of it.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.media_streams.connection import (
    SilenceHandler,
    WebSocketCallHandler,
    _BARGE_IN_ACKS,
    _CONTENT_REREAD_MAX_CHARS,
    _SLOT_REREAD_MIN_LOST_S,
    _WATCHDOG_REASK_MARKER,
)


# The call's own three chunks.
OPENER = "An ankle that's been giving you trouble is worth a proper look."
ANSWER = (
    "A rolled or sprained ankle can leave things a bit unstable for a while, "
    "and the sooner someone has a look at how it's moving the easier it "
    "usually is to settle down, so getting you booked in makes sense."
)
CLOSING_Q = "Do you have a preference for when you'd like to come in?"
CHUNKS = [OPENER, ANSWER, CLOSING_Q]

# ~20s was scheduled; the teardown landed 5.2s in, so ~14.8s never played.
LOST_S = 14.8


def _handler(session: dict) -> WebSocketCallHandler:
    """The B-107/B-120 harness, unchanged — this defect lands on the same arm."""
    h = object.__new__(WebSocketCallHandler)
    h.session = session
    h.call_sid = "CA91020004"
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
    # The live value: `last_question` is synthesis-anchored, so on a
    # multi-chunk turn it holds the LAST chunk — the sentence nobody heard.
    h._silence_handler.last_question = CLOSING_Q
    return h


def _interrupted_call(chunks=None, lost_s: float = LOST_S, **over):
    """The live call, up to the instant the recovery arm has to decide."""
    chunks = CHUNKS if chunks is None else chunks
    session = {
        "clinic_id": "northgate",
        "flow_step": 0,
        "_content_turn_chunks": list(chunks),
        "tts_inhibit": True,
        "interrupted_tts_text": chunks[-1],
        "barge_in_trigger_partial": "okay",
        "barge_in_playout_lost_s": lost_s,
        "barge_in_count": 1,
        "last_question": CLOSING_Q,
    }
    session.update(over)
    return _handler(session)


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
async def test_the_answer_goes_again_not_the_question_the_caller_never_reached():
    h = _interrupted_call()

    await h._on_final_transcript_clear("")

    assert _queued(h) == CHUNKS, (
        "the caller lost the answer to his own question and was asked, "
        "instead, when he could come in"
    )


@pytest.mark.asyncio
async def test_the_body_of_the_answer_actually_reaches_the_caller():
    """The complaint in one assertion: he asked about his ankle and never
    heard the reply."""
    h = _interrupted_call()

    await h._on_final_transcript_clear("")

    assert ANSWER in " ".join(_queued(h)), "the answer was lost twice"


@pytest.mark.asyncio
async def test_the_closing_question_is_never_spoken_on_its_own():
    """What the live call did. The question is fine — alone, after a destroyed
    answer, it is the defect."""
    h = _interrupted_call()

    await h._on_final_transcript_clear("")

    assert _queued(h) != [CLOSING_Q]


@pytest.mark.asyncio
async def test_the_first_chunk_carries_the_replay_marker():
    """Without it the consecutive-duplicate guard in `_tts_loop` drops the
    replay silently — the entire subject of c65f2a1c, and it went unnoticed
    for weeks because the caller was still hearing the original audio."""
    h = _interrupted_call()

    await h._on_final_transcript_clear("")

    first = h.tts_text_queue.get_nowait()
    assert first.startswith(_WATCHDOG_REASK_MARKER)


@pytest.mark.asyncio
async def test_only_the_first_chunk_carries_the_marker():
    """Marking the rest would drop every one of them out of the latency
    content-mark for nothing."""
    h = _interrupted_call()

    await h._on_final_transcript_clear("")

    rest = []
    while not h.tts_text_queue.empty():
        rest.append(h.tts_text_queue.get_nowait())
    assert not [c for c in rest[1:] if c.startswith(_WATCHDOG_REASK_MARKER)]


# ---------------------------------------------------------------------------
# The guards. Every one of these is a way to turn the fix into a new defect.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_single_chunk_turn_is_left_to_the_arm_below():
    """THE scope guard. On a one-chunk turn `last_question` really IS the chunk
    just spoken, so the existing arm is correct and must keep the case. Taking
    it here would replace a working recovery with an identical one and spend a
    budgeted resume doing it."""
    h = _interrupted_call(chunks=[CLOSING_Q])

    await h._on_final_transcript_clear("")

    assert _queued(h) == [CLOSING_Q]


@pytest.mark.asyncio
async def test_a_barge_in_after_playback_ended_does_not_re_speak():
    """The reason the trigger is scheduled-audio-lost and not anything
    time-based. A caller who interrupts once Susie has stopped talking heard
    the whole answer; re-speaking it would be the worse outcome."""
    h = _interrupted_call(lost_s=0.0)

    await h._on_final_transcript_clear("")

    assert _queued(h) == [CLOSING_Q]


@pytest.mark.asyncio
async def test_a_teardown_in_the_last_breath_does_not_re_speak():
    """Just under the floor: a syllable lost is not an answer lost."""
    h = _interrupted_call(lost_s=_SLOT_REREAD_MIN_LOST_S - 0.1)

    await h._on_final_transcript_clear("")

    assert _queued(h) == [CLOSING_Q]


@pytest.mark.asyncio
async def test_a_slot_readout_still_takes_the_b120_path():
    """Precedence. A readout has its own recovery and its own reason to go
    again whole; this arm sits after it and must not intercept."""
    readout = ["Number 1, eight in the morning.", "Number 2, ten to nine."]
    h = _interrupted_call(
        _slot_readout_chunks=list(readout),
        v3_dtmf_slot_map={1: "08:00", 2: "08:50"},
    )

    await h._on_final_transcript_clear("")

    assert _queued(h) == readout


@pytest.mark.asyncio
async def test_the_replay_is_capped_and_is_a_suffix():
    """A runaway reply must not re-read twenty seconds the caller half-heard —
    and whatever is dropped comes off the FRONT, because the lost audio is
    always a suffix of what was queued."""
    long_chunks = ["%d %s" % (i, "word " * 40) for i in range(4)]
    h = _interrupted_call(chunks=long_chunks)

    await h._on_final_transcript_clear("")

    out = _queued(h)
    assert out, "the answer was dropped entirely"
    assert out == long_chunks[-len(out):], "the replay is not a suffix"
    assert len(out) < len(long_chunks), "the cap did not bite"
    assert sum(len(c) for c in out) <= _CONTENT_REREAD_MAX_CHARS


@pytest.mark.asyncio
async def test_one_over_long_chunk_is_still_spoken():
    """The cap chooses how much of the suffix, never whether there is one. A
    single chunk longer than the budget is still the thing the caller lost."""
    huge = "word " * 200
    h = _interrupted_call(chunks=["short opener.", huge])

    await h._on_final_transcript_clear("")

    assert _queued(h) == [huge]


@pytest.mark.asyncio
async def test_no_ack_claims_the_caller_spoke():
    """The B-107 property this whole arm exists to protect, still holding."""
    h = _interrupted_call()

    await h._on_final_transcript_clear("")

    assert not [t for t in _queued(h) if t in _BARGE_IN_ACKS]


@pytest.mark.asyncio
async def test_the_deadlock_is_still_cleared():
    """B-67. Queueing behind an uncleared `tts_inhibit` is silence, not a
    recovery — and this arm returns, so there is no second exit."""
    h = _interrupted_call()

    await h._on_final_transcript_clear("")

    assert h.session["tts_inhibit"] is False


@pytest.mark.asyncio
async def test_the_resume_budget_is_spent_and_respected():
    """Two replays per call. Without the budget a caller who backchannels
    through a long answer never gets past it."""
    h = _interrupted_call(echo_resume_count=2)

    await h._on_final_transcript_clear("")

    assert _queued(h) != CHUNKS

    h2 = _interrupted_call()
    await h2._on_final_transcript_clear("")
    assert h2.session["echo_resume_count"] == 1
