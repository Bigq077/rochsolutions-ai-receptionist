"""The observability transcript must hear BOTH sides — without disturbing session["turns"].

session["turns"] feeds owner-facing output on this branch (app/tools/actionable_summary
_format_turns, and the SMS router's last-8 window). The obs capture needs the caller's
words too, so it reads a separate session["obs_turns"]. These tests pin both halves of
that contract: obs_turns is complete, and turns is byte-for-byte what it always was.
"""
from __future__ import annotations

from app.media_streams.llm_stream import _append_history


def test_obs_turns_records_both_sides():
    session = {}
    _append_history(session, "how much is an assessment?", "It's forty five pounds.")
    assert session["obs_turns"] == [
        {"role": "user", "text": "how much is an assessment?"},
        {"role": "assistant", "text": "It's forty five pounds."},
    ]


def test_turns_stays_assistant_only():
    """Regression guard: changing this silently alters live owner summaries."""
    session = {}
    _append_history(session, "how much is an assessment?", "It's forty five pounds.")
    assert session["turns"] == [{"role": "assistant", "text": "It's forty five pounds."}]


def test_obs_turns_preserves_order_across_exchanges():
    session = {}
    _append_history(session, "hi", "Hello, this is Susie.")
    _append_history(session, "I'd like to book", "Sure — what day suits?")
    assert [t["role"] for t in session["obs_turns"]] == [
        "user", "assistant", "user", "assistant",
    ]


def test_obs_turns_skips_empty_caller_text():
    """Silence turns must not inject blank caller lines into the transcript."""
    session = {}
    _append_history(session, "   ", "Are you still there?")
    assert session["obs_turns"] == [{"role": "assistant", "text": "Are you still there?"}]


def test_conversation_history_still_records_both_sides():
    """The LLM's own history is untouched by the obs split."""
    session = {}
    _append_history(session, "hi", "Hello.")
    assert session["conversation_history"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hello."},
    ]
