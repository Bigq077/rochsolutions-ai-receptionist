"""Gate 5cb — callback promise / retract (CA9d48f8f7ce, Raymond, 2026-08-14).

Susie said she'd passed a refund callback to Jonathan, then contradicted
herself with "I need to log that properly before I can promise", then promised
again. The SMS did deliver; the spoken contradiction is the defect.

Pins:
  1. Without callback_write_confirmed, a completion claim is re-steered.
  2. With callback_write_confirmed, a retract sentence is stripped.
  3. A legitimate offer / question is left alone.
  4. A legitimate post-tool promise is left alone once confirmed.
"""
from __future__ import annotations

from app.media_streams.turn_handler import (
    _FALSE_CALLBACK_RESTEER,
    _apply_callback_promise_gate,
    _false_callback_promise,
    sanitise_response,
)


def test_phantom_callback_promise_is_detected():
    assert _false_callback_promise(
        "I've passed that on to Jonathan — he'll be in touch with you directly."
    )
    assert _false_callback_promise(
        "That's all sent over to Jonathan — he'll be in touch with you directly."
    )


def test_callback_offer_question_is_not_a_promise():
    assert not _false_callback_promise(
        "Could I take your name and number and arrange for Jonathan to call you back?"
    )
    assert not _false_callback_promise(
        "Let me get a callback request sent over to Jonathan for you."
    )


def test_unconfirmed_promise_is_resteered():
    session = {}
    out = _apply_callback_promise_gate(
        "I've passed that on to Jonathan — he'll be in touch with you directly.",
        session,
    )
    assert out == _FALSE_CALLBACK_RESTEER
    assert session.get("_callback_promise_resteered") is True


def test_second_unconfirmed_promise_is_dropped():
    session = {"_callback_promise_resteered": True}
    out = _apply_callback_promise_gate(
        "That's all sent over to Jonathan.",
        session,
    )
    assert out == ""


def test_confirmed_promise_survives():
    session = {"callback_write_confirmed": True}
    text = "That's all sent over to Jonathan — he'll be in touch with you directly. Take care, Raymond."
    assert _apply_callback_promise_gate(text, session) == text


def test_retract_after_confirmed_is_stripped():
    """The Raymond contradiction — undo after a real notify."""
    session = {"callback_write_confirmed": True}
    text = (
        "I need to actually log that request properly before I can promise "
        "he'll call — let me do that now."
    )
    out = _apply_callback_promise_gate(text, session)
    assert "log that request" not in out.lower()
    assert "before i can promise" not in out.lower()


def test_sanitise_wires_the_gate():
    session = {}
    out = sanitise_response(
        "I've passed that on to Jonathan — he'll be in touch with you directly.",
        session,
    )
    assert out == _FALSE_CALLBACK_RESTEER
