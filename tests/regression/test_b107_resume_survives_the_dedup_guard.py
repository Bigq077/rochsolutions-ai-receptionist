"""
B-107's re-ask was queued and then silently dropped before it could be spoken.

CAfead969a2cb7de142248491368d8a4aa — JV demo line, 27 August 2026, build
ab0b3638. The first live exercise of the B-107 fix. It made the right decision
and the caller never heard the result of it:

    22:17:12,051  barge-in: partial='um'
    22:17:12,052  barge-in start: synthesis_active=False playback_active=True
                  interrupted_text="That Wednesday I've got half past five in
                  the evening, or se"
    22:17:13,628  WARNING barge-in #1 carried no words (partial='um'
                  own_audio=False) — speaking "That Wednesday I've got half
                  past five in the evening, or se" instead of an ack that
                  would claim the caller spoke (1/2)
    22:17:13,628  TTS dedup: skipping duplicate chunk "That Wednesday I've got
                  half past five in the evening, or seven in the evening —"

One millisecond apart. The guard chose the re-ask; the consecutive-duplicate
dedup guard in `_tts_loop` threw it away.

WHY THIS IS THE COMMON CASE, NOT AN EDGE ONE
--------------------------------------------
`_resume` is `last_question` — the sentence the barge-in tore down. The
barge-in fires *while that sentence is being spoken*, so the resume is by
construction equal to `_last_tts_chunk`. The dedup guard is therefore hit on
essentially every trip through this arm. It is not a rare collision; it is the
shape of the arm.

WHY NOBODY HEARD IT FAIL
------------------------
The live barge-in landed in the playback-only window — synthesis was done but
the audio was still in the caller's ear. They kept hearing the original
sentence, answered it ("yeah go for it", 22:17:20), and the call completed. The
fix was masked by a coincidence of timing.

WHAT IT COSTS WHEN THE TIMING DIFFERS
-------------------------------------
Move the same wordless barge-in past the end of playback and there is no
original audio to cover it. The arm `return`s immediately after queueing, so
there is no fall-through to `_BARGE_IN_ACKS` — the caller gets SILENCE until
the 10s watchdog. And `echo_resume_count` has already been incremented, so one
of the two budgeted resumes is spent on speech nobody heard.

THE FIX
-------
`_WATCHDOG_REASK_MARKER` already means exactly this: "a deliberate replay,
bypass dedup". It is what the silence-recovery re-ask uses, for the same
reason. Its only other effect is excluding the chunk from the latency
content-mark, which is also correct — a recovery replay is not the turn's first
content. `_tts_loop` strips it before the obs transcript seam, so stored
transcripts are unchanged.
"""

import asyncio
import inspect
import time

from app.media_streams import connection as c
from app.media_streams.connection import (
    SilenceHandler,
    WebSocketCallHandler,
    _BARGE_IN_ACKS,
    _MAX_ECHO_RESUMES,
    _WATCHDOG_REASK_MARKER,
)

# The live call, verbatim. The chunk in flight and the question outstanding are
# the SAME sentence here — which is the whole point.
WEDNESDAY = (
    "That Wednesday I've got half past five in the evening, or seven in the "
    "evening — either of those any good?"
)


def _handler(session: dict) -> WebSocketCallHandler:
    h = object.__new__(WebSocketCallHandler)
    h.session = session
    h.call_sid = "CAfead969a2cb7de142248491368d8a4aa"
    h.tts_text_queue = asyncio.Queue()
    h.transcript_queue = asyncio.Queue()
    h._clearing = True
    h._tts_task = None
    h._barge_in_pending = True
    h._barge_in_ts = time.monotonic() - 1.576   # the live 22:17:12.05 -> 13.63
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
    h._silence_handler.last_question = WEDNESDAY
    return h


def _live_call() -> WebSocketCallHandler:
    return _handler({
        "clinic_id": "jv_v1",
        "state": "GREETING",
        "flow_step": 0,
        "tts_inhibit": True,
        "interrupted_tts_text": WEDNESDAY[:60],
        "barge_in_trigger_partial": "um",
        "barge_in_count": 1,
    })


def _queued_raw(h) -> list:
    out = []
    while not h.tts_text_queue.empty():
        out.append(h.tts_text_queue.get_nowait())
    return out


def _strip(text: str) -> str:
    """What `_tts_loop` does to the marker before anything else sees the text."""
    if text.startswith(_WATCHDOG_REASK_MARKER):
        return text[len(_WATCHDOG_REASK_MARKER):]
    return text


# -- the defect ------------------------------------------------------------

async def test_the_resume_is_marked_as_a_deliberate_replay():
    """The one assertion. Without the marker the dedup guard eats this chunk
    and the caller hears nothing at all."""
    h = _live_call()

    await h._on_final_transcript_clear("")

    spoken = _queued_raw(h)
    assert spoken, "a wordless barge-in must never leave the turn silent"
    assert spoken[0].startswith(_WATCHDOG_REASK_MARKER), (
        "the re-ask was queued unmarked. It repeats the chunk just spoken, so "
        "the consecutive-duplicate dedup guard in _tts_loop drops it — and "
        "this arm returns without falling through to an ack, leaving the "
        "caller in silence until the watchdog"
    )


async def test_the_caller_still_hears_the_question_itself():
    """The marker is transport, not speech. Strip it and the words are
    unchanged — no marker text ever reaches ElevenLabs or the obs record."""
    h = _live_call()

    await h._on_final_transcript_clear("")

    assert [_strip(t) for t in _queued_raw(h)] == [WEDNESDAY]


async def test_the_resume_repeats_the_chunk_just_spoken():
    """States the mechanism rather than asserting the fix, so the reasoning in
    the commit message can be checked instead of taken on trust.

    If this ever stops being true the marker becomes unnecessary — but it is
    true by construction: `_resume` is `last_question`, and the barge-in fires
    while that question is being spoken."""
    h = _live_call()

    await h._on_final_transcript_clear("")

    resume = _strip(_queued_raw(h)[0])
    assert resume == h._silence_handler.last_question
    assert resume.strip().lower() == WEDNESDAY.strip().lower(), (
        "the resume and the chunk in flight are the same sentence — which is "
        "precisely what the dedup guard compares"
    )


# -- the coupling, pinned --------------------------------------------------

def _tts_loop_source() -> str:
    return inspect.getsource(c.WebSocketCallHandler._tts_loop)


def test_the_marker_is_the_only_way_past_the_dedup_guard():
    """If someone re-points the dedup bypass at a different signal, the fix
    above becomes a no-op silently. Fail here instead."""
    src = _tts_loop_source()
    assert "not _watchdog_reask" in src, (
        "the dedup guard no longer bypasses on _watchdog_reask — the B-107 "
        "resume's marker buys it nothing and the chunk is dropped again"
    )


def test_the_marker_is_stripped_before_the_dedup_check_and_the_obs_record():
    """Ordering contract. Strip too late and the comparison is against marked
    text; record too early and the transcript holds a control character."""
    src = _tts_loop_source()
    strip_at = src.index("_watchdog_reask = chunk_text.startswith(")
    dedup_at = src.index("TTS dedup: skipping duplicate chunk")
    record_at = src.index("_obs_turns.record_assistant")
    assert strip_at < dedup_at < record_at, (
        "the marker strip, the dedup guard and the obs record site have been "
        "reordered — see the ordering this fix depends on"
    )


# -- the guards. Loosen these and an earlier fix comes back. ---------------

async def test_the_ack_is_never_marked():
    """B-67's shape: nothing outstanding, so the ack is the turn's only exit.
    It is fresh speech, not a replay, and must stay subject to dedup."""
    h = _live_call()
    h._silence_handler.last_question = ""

    await h._on_final_transcript_clear("")

    spoken = _queued_raw(h)
    assert spoken and spoken[0] in _BARGE_IN_ACKS, (
        f"with no question outstanding the ack is the exit, got {spoken!r}"
    )
    assert not spoken[0].startswith(_WATCHDOG_REASK_MARKER)


async def test_past_the_cap_the_ack_is_still_the_exit_and_still_unmarked():
    h = _live_call()
    h.session["echo_resume_count"] = _MAX_ECHO_RESUMES

    await h._on_final_transcript_clear("")

    spoken = _queued_raw(h)
    assert spoken and spoken[0] in _BARGE_IN_ACKS
    assert not spoken[0].startswith(_WATCHDOG_REASK_MARKER)


async def test_a_final_carrying_words_is_still_not_this_arm():
    """A caller who actually spoke owns the turn — no marked replay over them."""
    h = _live_call()

    await h._on_final_transcript_clear("yeah go for it")

    assert _queued_raw(h) == []


async def test_the_budget_is_still_spent_only_once_per_resume():
    h = _live_call()

    await h._on_final_transcript_clear("")

    assert h.session["echo_resume_count"] == 1
