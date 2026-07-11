"""Regression: the v3 media-streams flow records BOTH sides of the conversation
in session['turns'] — not just Susie's replies.

This is what the observability capture/judge and the SMS router read; a one-sided
transcript (assistant-only) meant the judge could not see what the caller said.
"""
from app.media_streams.llm_stream import _append_history


def test_append_history_records_caller_and_assistant_in_order():
    s = {}
    _append_history(s, "I'd like to book physio", "Sure — what day works?")
    _append_history(s, "Monday please", "Booked for Monday.")
    assert [(t["role"], t["text"]) for t in s["turns"]] == [
        ("user", "I'd like to book physio"),
        ("assistant", "Sure — what day works?"),
        ("user", "Monday please"),
        ("assistant", "Booked for Monday."),
    ]


def test_append_history_skips_empty_caller_turn():
    # Silence / re-ask turns have no caller text — don't insert a blank user turn.
    s = {}
    _append_history(s, "", "Are you still there?")
    _append_history(s, "   ", "Still with you?")
    assert s["turns"] == [
        {"role": "assistant", "text": "Are you still there?"},
        {"role": "assistant", "text": "Still with you?"},
    ]


def test_append_history_still_populates_conversation_history():
    s = {}
    _append_history(s, "hi", "hello")
    assert {"role": "user", "content": "hi"} in s["conversation_history"]
    assert {"role": "assistant", "content": "hello"} in s["conversation_history"]
