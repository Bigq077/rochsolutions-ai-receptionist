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

Every detector in scripts/detect_defects.py and the judge in app/obs/judge.py
read this record. If it is not the spoken form, they are all scoring the wrong
artefact.

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

from app.media_streams.llm_stream import _append_history


RAW = (
    "The caller has confirmed the number. I'm missing the service, slot_iso, "
    "and reason. What's the appointment for, Tom?"
)
SPOKEN = "What's the appointment for, Tom?"


def test_obs_record_is_the_spoken_text():
    session: dict = {}
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


def test_spoken_text_omitted_falls_back_to_raw():
    """Deterministic paths (fast path, slot follow-up) pass one string only.

    There the queued text IS the spoken text, so the fallback must be exact —
    a None default that recorded nothing would silently blank those turns.
    """
    session: dict = {}
    _append_history(session, "hi", "Hello, Susie speaking.")
    assistant = [t for t in session["obs_turns"] if t["role"] == "assistant"]
    assert assistant[0]["text"] == "Hello, Susie speaking."


def test_empty_spoken_text_is_recorded_as_empty_not_raw():
    """A turn the gate stripped to nothing must NOT resurrect as raw text.

    "" is a meaningful value here: the model generated something and the caller
    heard none of it. Recording the raw text instead would put words in the
    caller's ear that were never spoken — the exact confusion this fix removes.
    Requires an `is None` check, not a falsy check.
    """
    session: dict = {}
    _append_history(session, "hello?", "Filtering the slots by lead time.",
                    spoken_text="")
    assistant = [t for t in session["obs_turns"] if t["role"] == "assistant"]
    assert assistant[0]["text"] == ""
