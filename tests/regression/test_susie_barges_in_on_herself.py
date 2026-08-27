"""
Regression: Susie heard herself, and answered.

Live on Marcus's line, 25 Aug 2026 (`CAfcb3130c`). The slot was already agreed
— the caller had said "yeah go on then that would work" — and the call ended
abandoned at 168 s with no booking:

    21:18:45  Susie: "So that's Tuesday the 1st of September at five in
                      the evening"
    21:18:47  barge-in: partial="that's"        <- HER OWN WORD, off the line
    21:18:48  barge-in #4 unqueued-final confirmed (1482ms) text=''
              ack='Yes, go on.'
    21:18:49  BACKSTOP armed — turn asked nothing ('Yes, go on.')
    21:18:52  caller: "i didn't say anything"
    21:19:03  hang up.  outcome=abandoned  name=None

The clearest bug report a caller can file. Susie's own audio came back through
the phone line, AssemblyAI transcribed one word of it as a partial, that tore
down her turn mid-readback, and the empty final that followed was answered with
a manufactured "Yes, go on." — an affirmative reply to something nobody said.

WHY THE EXISTING GUARDS DID NOT CATCH IT

  * `_barge_in_duration` is time spent in the barge-in STATE, not time the
    caller spoke. 1482 ms > threshold, so the B-67 unqueued-final resolver took
    its "confirmed barge-in" arm — but with `text=''` there is no evidence that
    anybody spoke at all. The duration proves nothing here.
  * The theorem_v3 TTS-echo suppressor is gated on `v3_location_asked`, serves
    watchdog preservation rather than barge-in, and keys off
    `_tts_audio_done_at`. This echo arrived DURING playback, not after it, so
    its timestamp window would not have fired even on the right clinic.

WHY "EMPTY FINAL" IS NOT THE DISCRIMINATOR

B-67's own live call (`CAa0f76e2c`, Vital Edge) also ended in an empty final —
and there the ack is CORRECT, because its triggering partial was 'yeah yep':
the caller really had spoken and STT lost the words. Refusing to ack on every
empty final would re-break that fix.

The thing that separates the two calls is the partial that STARTED the
barge-in: "that's" is a contiguous fragment of the sentence Susie was speaking
at that instant; "yeah yep" is not a fragment of "Right with you…". So the test
is against the DATA — what she was actually saying — not against any phrase
list, and not against a fixed literal of model speech.

WHAT HAPPENS INSTEAD: the same thing a short false trigger already does — put
back what the caller never actually interrupted. Bounded, because a resume can
echo again and re-trigger; after the cap it falls through to today's ack so the
turn always has an exit.
"""

import asyncio
import time

from app.media_streams.connection import (
    SilenceHandler,
    WebSocketCallHandler,
    _BARGE_IN_ACKS,
    _WATCHDOG_REASK_MARKER,
    _partial_is_own_speech,
)

READBACK = "So that's Tuesday the 1st of September at five in the evening"


def _handler(session: dict) -> WebSocketCallHandler:
    h = object.__new__(WebSocketCallHandler)
    h.session = session
    h.call_sid = "CAfcb3130c"
    h.tts_text_queue = asyncio.Queue()
    h.transcript_queue = asyncio.Queue()
    h._clearing = True
    h._tts_task = None
    h._barge_in_pending = True
    h._barge_in_ts = time.monotonic() - 1.482      # the live 1482 ms
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
    return h


def _echo_session(**over) -> dict:
    s = {
        "clinic_id": "jv_v1",
        "state": "GREETING",
        "flow_step": 0,
        "tts_inhibit": True,
        "interrupted_tts_text": READBACK,
        "barge_in_trigger_partial": "that's",
        "barge_in_count": 3,
    }
    s.update(over)
    return s


def _queued(h) -> list:
    """Queued speech, with the replay marker stripped.

    The put-back is enqueued behind _WATCHDOG_REASK_MARKER, which _tts_loop
    strips before the text reaches TTS or the obs record. It has to be: the
    text put back IS the chunk that was just spoken, so without the marker the
    consecutive-duplicate dedup guard drops it and the caller hears nothing.
    Tests here assert on the WORDS and strip it, the same convention as
    test_watchdog_no_repeat.py; the marker itself is the subject of
    test_b107_resume_survives_the_dedup_guard.py.
    """
    out = []
    while not h.tts_text_queue.empty():
        text = h.tts_text_queue.get_nowait()
        if text.startswith(_WATCHDOG_REASK_MARKER):
            text = text[len(_WATCHDOG_REASK_MARKER):]
        out.append(text)
    return out


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------
async def test_susies_own_word_does_not_earn_an_affirmative_ack():
    """The assertion the caller made out loud: "i didn't say anything"."""
    h = _handler(_echo_session())

    await h._on_final_transcript_clear("")

    spoken = _queued(h)
    assert not [t for t in spoken if t in _BARGE_IN_ACKS], (
        f"answered its own echo with an affirmative ack: {spoken!r}"
    )


async def test_the_interrupted_readback_is_put_back():
    """She was cut off mid-sentence by her own audio. Finish the sentence."""
    h = _handler(_echo_session())

    await h._on_final_transcript_clear("")

    assert READBACK in _queued(h), "the caller never heard the rest of the readback"


async def test_the_tts_inhibit_is_still_cleared():
    """B-67's deadlock must not come back through this branch: whatever we
    decide to say, the flag that discards every later chunk has to go."""
    h = _handler(_echo_session())

    await h._on_final_transcript_clear("")

    assert h.session["tts_inhibit"] is False


# ---------------------------------------------------------------------------
# B-67 must survive — an empty final is NOT the discriminator
# ---------------------------------------------------------------------------
async def test_a_real_caller_whose_words_were_lost_still_gets_the_ack():
    """`CAa0f76e2c` — partial 'yeah yep' against interrupted 'Right with you…'.

    The caller genuinely spoke and STT dropped it. Acking is right there, and
    refusing to ack on every empty final would re-break B-67.
    """
    h = _handler(_echo_session(
        interrupted_tts_text="Right with you…",
        barge_in_trigger_partial="yeah yep",
    ))

    await h._on_final_transcript_clear("")

    assert [t for t in _queued(h) if t in _BARGE_IN_ACKS], (
        "a real barge-in with lost words lost its ack too"
    )


async def test_an_unknown_partial_is_treated_as_a_real_barge_in():
    """Deny by default runs the OTHER way here: without evidence that it was
    her own audio, assume the caller spoke."""
    h = _handler(_echo_session(barge_in_trigger_partial=""))

    await h._on_final_transcript_clear("")

    assert [t for t in _queued(h) if t in _BARGE_IN_ACKS]


# ---------------------------------------------------------------------------
# The echo test itself — data, not a phrase list
# ---------------------------------------------------------------------------
def test_echo_detection_is_against_what_she_was_saying():
    assert _partial_is_own_speech("that's", READBACK)
    assert _partial_is_own_speech("the 1st of September", READBACK)
    assert _partial_is_own_speech("THAT'S", READBACK)
    # Same word, different sentence in flight — not an echo.
    assert not _partial_is_own_speech("that's", "Right with you…")
    assert not _partial_is_own_speech("yeah yep", READBACK)
    # Words present but not contiguous: the caller, not the line.
    assert not _partial_is_own_speech("Tuesday evening", READBACK)


def test_echo_detection_survives_nonsense():
    for partial, spoken in [
        ("", READBACK), (None, READBACK), ("that's", ""), ("that's", None),
        (None, None), (123, READBACK), ("that's", 456), ("   ", READBACK),
    ]:
        assert _partial_is_own_speech(partial, spoken) is False


# ---------------------------------------------------------------------------
# Bounded — a resume can echo again
# ---------------------------------------------------------------------------
async def test_repeated_echoes_stop_resuming_and_fall_back_to_the_ack():
    """`interrupted_tts_text` is snapshotted at barge-in start and never
    cleared, so resuming re-speaks the same sentence — which can echo again.
    The turn must always keep an exit."""
    session = _echo_session()
    acked = False
    for _ in range(4):
        h = _handler(session)
        await h._on_final_transcript_clear("")
        if [t for t in _queued(h) if t in _BARGE_IN_ACKS]:
            acked = True
            break
    assert acked, "echo resumes are unbounded — a live call could ping-pong"


async def test_real_speech_resets_the_echo_budget():
    """A caller who actually says something ends the echo episode; the next
    genuine echo must not inherit a spent budget."""
    session = _echo_session()
    for _ in range(3):
        h = _handler(session)
        await h._on_final_transcript_clear("")

    h = _handler(session)
    await h._on_final_transcript_clear("yes that works for me")

    h2 = _handler(session)
    h2._barge_in_pending = True
    await h2._on_final_transcript_clear("")
    assert READBACK in _queued(h2), "the budget did not reset after real speech"


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def test_the_partial_that_started_the_barge_in_is_recorded():
    """Without this the guard has nothing to compare against."""
    import inspect

    from app.media_streams.connection import WebSocketCallHandler as W

    src = inspect.getsource(W._on_partial_transcript)
    assert 'barge_in_trigger_partial' in src
