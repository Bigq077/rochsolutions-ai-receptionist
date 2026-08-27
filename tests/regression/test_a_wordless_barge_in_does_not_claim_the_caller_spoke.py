"""
Regression: "Yes, go on." to nobody — again, on a partial the echo test is
blind to.

B-107 — CAf6a63145d7f33cb91d5eacc6dc9d2465, jv_v1, build e449791c, JV go-live
rehearsal, 27 August 2026. The booking survived, so this cost a confused caller
rather than a lost one:

    Susie:  "Thanks Lucy — I've got you on oh seven five oh two, two one one,
             two oh seven — is that the best number for the booking?"
            barge-in: partial='bye'     <- nowhere in that sentence
            final: ''
    Susie:  "Yes, go on."
    caller: "oh i didn't say anything"

Word for word the complaint from CAfcb3130c, which
`test_susie_barges_in_on_herself` closed. That fix compares the triggering
partial against the sentence in flight, and it is exactly right for the case it
was built on — but 'bye' is not a fragment of the number read-back, so nothing
there could have fired.

── WHY THE ECHO TEST CANNOT BE WIDENED TO COVER THIS ──────────────────────────
A caller whose words STT dropped and a garbled echo leave the SAME evidence: a
partial absent from the interrupted text, plus an empty final. B-67's own live
call is the first (partial 'yeah yep'); this one is the second. No test over
that data can separate them, so a looser match would not catch echoes — it
would start swallowing real callers, which is the direction this whole family
must never fail in.

── SO THE ACK IS WHAT CHANGES ─────────────────────────────────────────────────
Every member of `_BARGE_IN_ACKS` asserts the caller spoke:

    "Sorry — go ahead."  /  "Yes, go on."  /  "Sorry about that — you were
    saying?"

When nobody spoke, all three are false, and the outstanding question is left
unanswered on top. Re-asking that question is right whichever actually
happened — correct against an echo, and better than "Yes, go on." for a caller
STT lost, who hears the question again instead of being asked to repeat words
they cannot know went missing.

`last_question` rather than `interrupted_tts_text`, for two reasons. The latter
holds only the CHUNK in flight, and the chunker splits a sentence at its em
dash — on this very call the question may have been a different chunk from the
one the barge-in tore down. And it is what lets B-67 keep its ack: there the
interrupted chunk was the filler "Right with you…" with no question
outstanding, so this arm stands down. Replaying a filler would have left that
caller waiting for an answer that never came.
"""

import asyncio
import time

from app.media_streams.connection import (
    SilenceHandler,
    WebSocketCallHandler,
    _BARGE_IN_ACKS,
    _MAX_ECHO_RESUMES,
    _WATCHDOG_REASK_MARKER,
)

# The chunk that was in flight, and the question outstanding at that moment.
NUMBER_CHUNK = (
    "I've got you on oh seven five oh two, two one one, two oh seven —"
)
NUMBER_Q = "is that the best number for the booking?"


def _handler(session: dict) -> WebSocketCallHandler:
    h = object.__new__(WebSocketCallHandler)
    h.session = session
    h.call_sid = "CAf6a63145"
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
    return h


def _session(**over) -> dict:
    s = {
        "clinic_id": "jv_v1",
        "state": "GREETING",
        "flow_step": 0,
        "tts_inhibit": True,
        "interrupted_tts_text": NUMBER_CHUNK,
        "barge_in_trigger_partial": "bye",      # not in NUMBER_CHUNK
        "barge_in_count": 1,
    }
    s.update(over)
    return s


def _live(question: str = NUMBER_Q):
    h = _handler(_session())
    h._silence_handler.last_question = question
    return h


def _queued(h) -> list:
    """Queued speech, with the replay marker stripped.

    The re-ask is enqueued behind _WATCHDOG_REASK_MARKER, which _tts_loop
    strips before the text reaches TTS or the obs record. Tests here assert on
    the WORDS, so they strip it too — the same convention as
    test_watchdog_no_repeat.py. That the marker is present at all is the
    subject of test_b107_resume_survives_the_dedup_guard.py: without it the
    re-ask repeats the chunk just spoken and the dedup guard drops it.
    """
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
async def test_no_affirmative_ack_when_nobody_spoke():
    """The assertion the caller made out loud: "oh i didn't say anything"."""
    h = _live()

    await h._on_final_transcript_clear("")

    spoken = _queued(h)
    assert not [t for t in spoken if t in _BARGE_IN_ACKS], (
        f"claimed the caller spoke when the final was empty: {spoken!r}"
    )


async def test_the_outstanding_question_is_asked_again():
    """Leaving the turn silent would be the B-67 deadlock all over again."""
    h = _live()

    await h._on_final_transcript_clear("")

    assert _queued(h) == [NUMBER_Q]
    assert h.session["tts_inhibit"] is False, (
        "the deadlock B-67 exists to clear must still be cleared on this arm"
    )


async def test_the_partial_need_not_resemble_her_own_speech():
    """'bye' is the whole point — the echo test is blind to it, and any
    attempt to make it see 'bye' would also see real callers."""
    h = _handler(_session(barge_in_trigger_partial="totally unrelated"))
    h._silence_handler.last_question = NUMBER_Q

    await h._on_final_transcript_clear("")

    assert _queued(h) == [NUMBER_Q]


# ---------------------------------------------------------------------------
# The guards. Loosen any of these and an earlier fix comes back.
# ---------------------------------------------------------------------------
async def test_with_no_question_outstanding_the_ack_still_plays():
    """B-67's shape, and why this is gated on `last_question`.

    There the interrupted chunk was the filler "Right with you…" and nothing
    was outstanding. The ack is the turn's only exit, and replaying a filler
    would leave the caller waiting on an answer that never comes.
    """
    h = _live(question="")

    await h._on_final_transcript_clear("")

    spoken = _queued(h)
    assert spoken, "a wordless barge-in must never leave the turn silent"
    assert spoken[0] in _BARGE_IN_ACKS, (
        f"with no question outstanding the ack is the exit, not {spoken[0]!r}"
    )


async def test_a_final_carrying_words_is_not_this_arm():
    """Only a WORDLESS barge-in qualifies. A caller who actually said
    something owns the turn, and must not be talked over with a re-ask."""
    h = _live()

    await h._on_final_transcript_clear("yes that's the one")

    assert _queued(h) == [], (
        "a real answer was treated as a wordless barge-in"
    )


async def test_the_re_ask_is_capped():
    """A re-ask can echo and re-trigger. After the cap it falls through to the
    ack so the turn always has an exit."""
    h = _live()
    h.session["echo_resume_count"] = _MAX_ECHO_RESUMES

    await h._on_final_transcript_clear("")

    spoken = _queued(h)
    assert spoken and spoken[0] in _BARGE_IN_ACKS, (
        f"past the cap the ack is the exit, got {spoken!r}"
    )


async def test_the_cap_counter_advances():
    h = _live()

    await h._on_final_transcript_clear("")

    assert h.session["echo_resume_count"] == 1
