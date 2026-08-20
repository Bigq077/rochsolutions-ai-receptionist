"""
Vital Edge live call (2026-08-20, CAa0f76e2c2851f9eb3f28eddc38b75e3b) — the
caller asked a question, Susie answered it into a muted queue, and the call sat
in silence until the caller spoke again to check the line was still alive.

Live-call trace:
    15:31:39,268  transcript: 'hi there uh can i speak to jonathan please'
    15:31:41,074  filler phrase triggered: 'Right with you…'
    15:31:41,466  barge-in start: synthesis_active=False playback_active=True
                  interrupted_text='Right with you…' tts_gen=1
    15:31:41,466  barge-in: partial='yeah yep'
    15:31:41,999  tts_inhibit: discarding stale chunk "Jonathan isn't
                  available to take calls directly, but I can a"
    15:31:42,002  WATCHDOG_DEFERRED reason=llm_in_flight q_gen=2
    15:31:42,002  WATCHDOG_DEFERRED_CLEAR reason=tts_still_playing q_gen=2
    15:31:42,845  garbage_transcript='' — watchdog preserved
                  ... 5.6 s of nothing ...
    15:31:47,068  barge-in: partial='hello'
    15:31:47,967  barge-in #1 confirmed (6500ms) — real transcript 'hello'

Root cause: `_resolve_barge_in()` is only ever called from the `_llm_loop`
dequeue, so it can only run for a final that STTStream actually put on the
transcript queue.  STTStream drops empty and garbage finals at the socket
boundary (`if not text: continue` / `if _is_garbage_transcript(text): continue`),
so a barge-in that resolves to noise is never resolved at all.

`_barge_in_pending` stays True, and — the part that ends the call —
`session["tts_inhibit"]`, set at barge-in start, is never cleared.  Every chunk
of the reply the caller was waiting for is discarded by the `_tts_loop` inhibit
check, and nothing queues anything in its place.

Both recovery paths are down at the same instant, which is why this is silence
and not a slow recovery:

  * the watchdog carries its own repair for exactly this flag
    ("cleared tts_inhibit before re-ask"), but that only helps if the watchdog
    is ARMED; and
  * arming had just been handed to `on_tts_finished()` by
    WATCHDOG_DEFERRED_CLEAR — which never fires, because every chunk of that
    turn was inhibited.

So the trace above is not "the caller was slow to be helped".  Had Ray waited
instead of saying "hello", that silence had no exit: no timer, no speech, until
he hung up.  He rescued the call himself.

The fix resolves the barge-in from `_on_final_transcript_clear`, which is the
one callback STTStream makes for EVERY final BEFORE it decides whether to
enqueue — and which already computes `_barge_in_duration` there for the same
reason.  It is gated on the same predicate STTStream drops on, so a final that
WILL be enqueued is untouched and still resolves in `_resolve_barge_in()`:
there is exactly one resolver per barge-in, which
test_a_substantive_final_is_left_for_the_llm_loop pins down.
"""

import asyncio
import time

from app.media_streams.connection import (
    SilenceHandler,
    WebSocketCallHandler,
    _BARGE_IN_ACKS,
)


def _live_call_session() -> dict:
    """The session as it stood at 15:31:41.999 on the live call.

    tts_inhibit=True is the whole defect: it was set 0.5 s earlier at barge-in
    start, and the chunk carrying the answer to "can I speak to Jonathan" had
    just been discarded by it.

    interrupted_tts_text is the FILLER rather than that answer, because the
    answer was still streaming when the barge-in tore the turn down — which is
    why the confirmed arm has to ack rather than replay it.
    """
    return {
        "clinic_id": "vital_edge",
        "state": "GREETING",
        "flow_step": 0,
        "tts_inhibit": True,
        "interrupted_tts_text": "Right with you…",
        "barge_in_count": 0,
    }


def _handler(session: dict) -> WebSocketCallHandler:
    """A skeletal handler — only what _on_final_transcript_clear touches.

    WebSocketCallHandler's real constructor wants a live WebSocket; this path
    needs none of it.
    """
    h = object.__new__(WebSocketCallHandler)
    h.session = session
    h.call_sid = "CAa0f76e2c2851f9eb3f28eddc38b75e3b"
    h.tts_text_queue = asyncio.Queue()
    h.transcript_queue = asyncio.Queue()
    h._clearing = True
    h._tts_task = None                  # synthesis had already completed
    h._barge_in_pending = True
    h._barge_in_ts = time.monotonic()   # overwritten per-test
    h._barge_in_duration = 0.0
    h._barge_in_flush_before = 0.0
    h._in_barge_in_recovery = False
    h._last_turn_done_at = 0.0          # disables the tail-fragment gate
    h._tts_last_start_ts = time.time()
    h._tts_audio_done_at = 0.0          # disables the theorem_v3 echo gate
    h._silence_handler = SilenceHandler(
        tts_text_queue=h.tts_text_queue,
        trigger_transfer_fn=lambda *a, **k: None,
        get_session=lambda: session,
    )
    h._silence_handler._tts_playing = False
    return h


def _queued(h: WebSocketCallHandler) -> list:
    out = []
    while not h.tts_text_queue.empty():
        out.append(h.tts_text_queue.get_nowait())
    return out


# -- the regression --------------------------------------------------------

async def test_an_empty_final_clears_the_tts_inhibit():
    """
    The whole defect in one assertion.  Before the fix tts_inhibit is still set
    when this returns, so every chunk the engine produces from here on is
    discarded before it reaches ElevenLabs and the call is silent for good.
    """
    h = _handler(_live_call_session())
    h._barge_in_ts = time.monotonic() - 1.379   # the live 15:31:41.47 → 42.85

    await h._on_final_transcript_clear("")

    assert h.session["tts_inhibit"] is False, (
        "the barge-in's final was dropped by STTStream, so _resolve_barge_in "
        "never ran and tts_inhibit stayed set — every later chunk is discarded "
        "by the _tts_loop inhibit check and the caller hears nothing at all"
    )


async def test_an_empty_final_resolves_the_barge_in():
    """A barge-in left pending also strands _in_barge_in_recovery and the
    stale-transcript flush, so the NEXT real utterance is handled as though it
    were still mid-interruption."""
    h = _handler(_live_call_session())
    h._barge_in_ts = time.monotonic() - 1.379

    await h._on_final_transcript_clear("")

    assert h._barge_in_pending is False, (
        "nothing downstream will ever resolve this barge-in — the only "
        "resolver runs off the transcript queue, which this final never reached"
    )


async def test_the_caller_hears_something_back():
    """Clearing the flag is necessary but not sufficient: with the turn torn
    down there is no producer left, so the resolution has to put speech on the
    queue itself.  Ray got 5.6 s of dead air here and spoke first."""
    h = _handler(_live_call_session())
    h._barge_in_ts = time.monotonic() - 1.379

    await h._on_final_transcript_clear("")

    spoken = _queued(h)
    assert spoken, (
        "the answer chunk was discarded and the turn was torn down, so if this "
        "path queues nothing the call is silent until the caller speaks again"
    )
    assert spoken[0] in _BARGE_IN_ACKS, (
        f"a confirmed barge-in carrying no words gets the same ack-and-wait as "
        f"_resolve_barge_in's noise arm, not {spoken[0]!r}"
    )
    assert h._in_barge_in_recovery is True, (
        "the ack was played, so the next utterance must be processed directly "
        "rather than earning a second ack"
    )


async def test_the_ack_is_queued_after_the_flag_is_cleared():
    """Ordering is load-bearing: the recovery speech goes through the very
    _tts_loop check that tts_inhibit controls.  Queue it first and it is
    discarded exactly like the chunk this defect already ate."""
    h = _handler(_live_call_session())
    h._barge_in_ts = time.monotonic() - 1.379

    seen = []
    real_put = h.tts_text_queue.put

    async def _watching_put(item):
        seen.append(h.session.get("tts_inhibit"))
        await real_put(item)

    h.tts_text_queue.put = _watching_put

    await h._on_final_transcript_clear("")

    assert seen, "nothing was queued at all"
    assert all(flag is False for flag in seen), (
        "recovery speech was queued while tts_inhibit was still True — the "
        "_tts_loop would discard it and the fix would be a no-op on a live call"
    )


async def test_a_short_barge_in_resumes_what_it_interrupted():
    """Below BARGE_IN_THRESHOLD_MS this is a false trigger, and the existing
    contract is to put back what the caller talked over rather than ack."""
    h = _handler(_live_call_session())
    h._barge_in_ts = time.monotonic() - 0.05    # 50 ms — well under 300 ms

    await h._on_final_transcript_clear("")

    assert h.session["tts_inhibit"] is False
    assert _queued(h) == ["Right with you…"], (
        "a sub-threshold barge-in must resume interrupted_tts_text, matching "
        "_resolve_barge_in's short-duration arm"
    )
    assert h._in_barge_in_recovery is False, (
        "a false trigger played no ack, so there is no recovery to be in"
    )


async def test_a_noise_word_final_is_treated_the_same_as_an_empty_one():
    """STTStream drops 'mm' by the same `continue` as ''.  Both leave the
    barge-in unresolved, so both have to be resolved here."""
    h = _handler(_live_call_session())
    h._barge_in_ts = time.monotonic() - 1.379

    await h._on_final_transcript_clear("mm")

    assert h.session["tts_inhibit"] is False
    assert h._barge_in_pending is False
    assert _queued(h), "a garbage final deadlocks exactly like an empty one"


async def test_a_substantive_final_is_left_for_the_llm_loop():
    """The other half of the contract — exactly ONE resolver per barge-in.

    'hello' IS enqueued by STTStream, so _resolve_barge_in will run for it and
    decide (correctly, at 6500 ms with real words) to process it directly.
    Resolving it here too would consume the barge-in, play a spurious ack over
    the caller's actual answer, and leave _resolve_barge_in with nothing to do.
    """
    h = _handler(_live_call_session())
    h._barge_in_ts = time.monotonic() - 6.5

    await h._on_final_transcript_clear("hello")

    assert h._barge_in_pending is True, (
        "'hello' reaches the transcript queue, so _resolve_barge_in owns it — "
        "this hook must not consume the barge-in first"
    )
    assert _queued(h) == [], (
        "queueing an ack here would talk over the caller's real answer"
    )


async def test_the_stale_reask_drain_does_not_abort_the_handler():
    """Found while fixing the above, one line further down the same method.

    The drain reached for `self._tts_text_queue`, which belongs to
    SilenceHandler — WebSocketCallHandler has only `tts_text_queue`.  Every
    final arriving while a watchdog re-ask was in flight raised AttributeError,
    which STTStream swallows (`await on_final_clear(text)` sits inside
    `except Exception: pass`).  So the drain never ran, and neither did the
    rest of the method — including the `on_transcript_received` that cancels
    the silence timer.
    """
    h = _handler(_live_call_session())
    h._barge_in_pending = False                 # isolate from the fix above
    h._silence_handler.currently_reasking = True
    h._silence_handler.last_question = "Sorry, I can't quite hear you…"
    await h.tts_text_queue.put("Sorry, I can't quite hear you…")

    await h._on_final_transcript_clear("yes that's right")

    assert h.tts_text_queue.empty(), (
        "the stale re-ask was left queued and would play over the caller's "
        "answer — the drain raised AttributeError and was swallowed"
    )
    assert h._silence_handler.last_question == "", (
        "on_transcript_received never ran: the AttributeError aborted the "
        "method before it, so the silence timer was never cancelled"
    )
