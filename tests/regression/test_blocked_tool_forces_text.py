"""
Regression (CAd34a122247, Vital Edge, 2026-08-08): a blocked check_availability
was retried, costing a full model round trip each time.

Two turns in that call took 7.05s and 8.68s against ~2.3s for every
single-iteration turn. The decomposition:

    08:37:15.392  iteration=1
    08:37:18.292  tool: check_availability -> BLOCKED (booking_details_already_complete)
    08:37:18.294  iteration=2
    08:37:20.381  tool: check_availability -> BLOCKED  (the same call, again)
    08:37:20.384  iteration=3
    08:37:22.437  speech

The block's own message opens "Do NOT call check_availability. Produce the
booking summary now." The model called it again anyway, on two separate turns.
That is not an instruction-following failure: the result carries `"error"`, an
errored tool call reads as a FAILED call, and retrying a failed call is correct
default behaviour. The message and the frame it arrives in say opposite things,
and the frame wins.

So the fix is a request parameter, not stronger wording. `tool_choice:
{"type": "none"}` on the next iteration makes a tool call structurally
impossible, and the model has nothing to do but speak.

What is pinned here is the wiring, because every part of it is silent when
wrong: a flag that is read instead of popped disarms booking for the rest of
the turn; a 529 retry that drops the parameter re-opens the loop; and passing
`tool_choice=None` explicitly rather than omitting it changes the request shape
on every ordinary iteration.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.media_streams.config import FORCE_TEXT_NEXT_ITERATION
from app.media_streams.llm_stream import LLMStream


_SRC = Path(__file__).resolve().parents[2] / "app" / "media_streams" / "llm_stream.py"


# ── 1. The parameter reaches the API ────────────────────────────────────────

def test_the_streaming_call_accepts_a_tool_choice():
    sig = inspect.signature(LLMStream._one_streaming_call)
    assert "tool_choice" in sig.parameters, (
        "the suppression has no way to reach the Messages API"
    )
    assert sig.parameters["tool_choice"].default is None, (
        "tool_choice must default to None so ordinary iterations are unchanged"
    )


def test_tool_choice_is_omitted_rather_than_sent_as_null():
    """
    `tool_choice=None` is not the same as omitting the field. Building the
    kwargs conditionally keeps the request byte-identical on every normal
    iteration — which matters here because the static system block is ~19K
    tokens and sits behind a cache_control breakpoint.
    """
    src = _SRC.read_text(encoding="utf-8")
    assert '{"tool_choice": tool_choice} if tool_choice else {}' in src, (
        "tool_choice should be spread in only when set"
    )


# ── 2. The flag is set where the block happens ──────────────────────────────

def test_the_block_arms_the_suppression():
    """
    The blocked branch and the flag must stay adjacent. If a later edit moves
    one without the other the retry loop comes back silently — the call still
    completes, just two round trips slower.
    """
    src = _SRC.read_text(encoding="utf-8")
    idx = src.index('"error": "booking_details_already_complete"')
    window = src[idx: idx + 900]
    assert f"session[{FORCE_TEXT_NEXT_ITERATION!s}]" in window or (
        "FORCE_TEXT_NEXT_ITERATION" in window
    ), "the block does not arm the suppression"


# ── 3. The flag lasts exactly one iteration ─────────────────────────────────

def test_the_flag_is_popped_not_read():
    """
    The most dangerous way to get this wrong.

    Left set, tools stay disarmed for the rest of the turn — so when the caller
    then says "yes, go ahead", book_appointment cannot be called and the
    booking silently never happens while the call still sounds correct. That is
    the worst failure mode this system has (see the definition of production
    readiness in CLAUDE.md), and it would be reached by changing one word.
    """
    src = _SRC.read_text(encoding="utf-8")
    assert "session.pop(FORCE_TEXT_NEXT_ITERATION, False)" in src, (
        "the flag must be consumed at the first read, not left set"
    )
    assert "session.get(FORCE_TEXT_NEXT_ITERATION" not in src, (
        "reading the flag without consuming it disarms tools for the whole turn"
    )


def test_a_session_dict_round_trips_the_flag_once():
    """The pop semantics, at the level the loop actually uses them."""
    session: dict = {}
    session[FORCE_TEXT_NEXT_ITERATION] = True

    first = bool(session.pop(FORCE_TEXT_NEXT_ITERATION, False))
    second = bool(session.pop(FORCE_TEXT_NEXT_ITERATION, False))

    assert first is True, "the iteration after the block must suppress tools"
    assert second is False, "the iteration after that must have tools back"


# ── 4. The 529 retry carries it too ─────────────────────────────────────────

def test_the_overload_retry_reuses_the_same_tool_choice():
    """
    The overload path re-runs the SAME iteration after a 529. It is a second
    call site for `_one_streaming_call`, and the flag has already been popped
    by then — so if this call site doesn't pass `_tool_choice` through, a
    retried iteration silently regains tools and can re-issue the blocked call.
    """
    src = _SRC.read_text(encoding="utf-8")
    assert src.count("tool_choice=_tool_choice") == 2, (
        "both _one_streaming_call sites (normal + 529 retry) must pass the "
        f"suppression; found {src.count('tool_choice=_tool_choice')}"
    )


# ── 5. Scope ────────────────────────────────────────────────────────────────

def test_only_the_readback_block_arms_it():
    """
    Deliberately narrow. Other guards in the same elif chain block a tool
    because a DIFFERENT tool should run next (the slot-locked guard, the cancel
    retention question) — suppressing tools there would strand the turn with no
    way to act. Only the readback block, whose every branch ends in "say
    exactly this, then stop", wants speech.
    """
    src = _SRC.read_text(encoding="utf-8")
    assert src.count("session[FORCE_TEXT_NEXT_ITERATION] = True") == 1, (
        "the suppression is armed in more than one place — verify each one "
        "genuinely wants SPEECH next and not another tool"
    )
