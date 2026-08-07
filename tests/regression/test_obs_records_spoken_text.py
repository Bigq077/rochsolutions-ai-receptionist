# tests/regression/test_obs_records_spoken_text.py
"""
The obs record must be what the CALLER HEARD, not the raw model tokens.

WHY
---
obs_turns used to be fed `full_reply`, assembled from raw LLM tokens. Gate 5
runs per-chunk on the way to TTS, AFTER that record is written, and strips a
great deal. So a transcript could not distinguish "the model generated this"
from "the caller heard this".

That is not a theoretical gap. On 2026-07-29 two conclusions were drawn from
these transcripts and both were wrong in consequence:

  * the A1 defect counts were an inference in BOTH directions — over-reporting
    reasoning the gate had caught, and under-reporting severity on the four
    calls where it caught nothing;
  * settling the question needed the raw text replayed through the real chunker
    and the real gate, because the record itself could not answer it.

The first fix passed the post-Gate-5 text into _append_history. That closed the
gate half but not the delivery half: three suppression checks in connection.py's
TTS loop run LATER still and drop chunks that Gate 5 passed. On CAe2120b the
judge quoted "Let me just check what we have for you." back to the operator as a
redundant step Susie had added — a chunk the pre-slot guard had suppressed, so
the caller never heard a word of it.

The record site is now inside that loop, past all three checks. "Spoken" is no
longer a value someone has to remember to pass; it is where the code lives.

SCOPE
-----
Only the obs record changes. conversation_history keeps the RAW reply, because
it is fed back to the model as its own prior turns and rewriting the model's
memory of what it said is a behavioural change, not an instrumentation one.
session["turns"] also keeps the raw form — it feeds the owner-facing summary and
the SMS router for live clinics. Both are asserted below so the scope stays
where it was put.
"""
from __future__ import annotations

import inspect

from app.media_streams import connection
from app.media_streams.llm_stream import _append_history
from app.obs import turns as obs_turns


RAW = (
    "The caller has confirmed the number. I'm missing the service, slot_iso, "
    "and reason. What's the appointment for, Tom?"
)
SPOKEN = "What's the appointment for, Tom?"


def test_obs_record_is_the_spoken_text():
    """Only what reached TTS is recorded; the reasoning preamble never does."""
    session: dict = {}
    obs_turns.mark_turn_start(session)
    obs_turns.record_assistant(session, SPOKEN)
    _append_history(session, "yes that's right", RAW, spoken_text=SPOKEN)

    assistant = [t for t in session["obs_turns"] if t["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["text"] == SPOKEN
    assert "slot_iso" not in assistant[0]["text"], (
        "obs recorded raw model output — a detector reading this cannot tell "
        "whether the caller heard it"
    )


def test_caller_turn_still_recorded_in_order():
    session: dict = {}
    obs_turns.mark_turn_start(session)
    obs_turns.record_assistant(session, SPOKEN)
    _append_history(session, "yes that's right", RAW, spoken_text=SPOKEN)
    assert [t["role"] for t in session["obs_turns"]] == ["user", "assistant"]
    assert session["obs_turns"][0]["text"] == "yes that's right"


def test_conversation_history_keeps_the_raw_reply():
    """The model's own prior turns are deliberately NOT rewritten."""
    session: dict = {}
    _append_history(session, "yes that's right", RAW, spoken_text=SPOKEN)

    assistant = [m for m in session["conversation_history"]
                 if m["role"] == "assistant"]
    assert assistant[0]["content"] == RAW


def test_session_turns_keeps_the_raw_reply():
    """session["turns"] feeds live-clinic summaries and SMS — out of scope here."""
    session: dict = {}
    _append_history(session, "yes that's right", RAW, spoken_text=SPOKEN)
    assert session["turns"][0]["text"] == RAW


def test_a_turn_the_gate_stripped_to_nothing_records_nothing():
    """The model generated something and the caller heard none of it.

    Previously this had to be expressed as an explicit spoken_text="" and an
    `is None` check, because _append_history had no other way to know. Now a
    turn that never reaches TTS simply never reaches the recorder.
    """
    session: dict = {}
    obs_turns.mark_turn_start(session)
    _append_history(session, "hello?", "Filtering the slots by lead time.",
                    spoken_text="")
    assert [t["role"] for t in session["obs_turns"]] == ["user"]


# ─────────────────────────────────────────────────────────────────────────
# The record site must stay downstream of every suppression check
# ─────────────────────────────────────────────────────────────────────────
def _tts_loop_source() -> str:
    src = inspect.getsource(connection)
    start = src.index("_pre_slot_chunk = chunk_text.startswith(PRE_SLOT_MARKER)")
    end = src.index("_obs_turns.record_assistant", start)
    return src[start:end]


def test_record_site_is_after_every_suppression_check():
    """Move the call earlier and the transcript starts holding unspoken text.

    Each of these three drops a chunk the caller never hears, and each sits
    between Gate 5 and the microphone. The pre-slot one is the one that actually
    reached an operator's phone as a quoted defect (CAe2120b).
    """
    between = _tts_loop_source()
    for guard in ("_pre_slot_cancelled", "tts_inhibit", "TTS dedup"):
        assert guard in between, (
            f"record_assistant now runs BEFORE the {guard} check — chunks that "
            f"are dropped after it will be recorded as spoken"
        )


def test_the_recorded_form_is_not_the_phonetic_one():
    """_apply_tts_subs turns Alcester into Awlstuh for ElevenLabs.

    That is genuinely what the caller heard, but on the page it reads as an
    invented place name — and the judge has a `hallucination` tag it is happy to
    reach for. obs keeps the canonical spelling.
    """
    src = inspect.getsource(connection)
    stash = src.index("_obs_chunk_text = chunk_text")
    subs = src.index("chunk_text = _apply_tts_subs(chunk_text)", stash)
    assert stash < subs, (
        "the obs form is taken after phonetic substitution — transcripts will "
        "read 'Awlstuh'"
    )
