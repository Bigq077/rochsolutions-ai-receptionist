"""The pre-tool hold latch must not be set by a sentence Gate 5 deletes.

FINDING 4 of the demo call of 2026-08-30, and the fourth instance in this
codebase of the same family: *code that matches one literal of model speech*.

`keep_pre_slot_speech` clinics preserve the model's own sentence in front of a
`check_availability` tool call. When that sentence IS a hold phrase, the engine
latches `_hold_head_spoken` so the tool-time producer does not say a second one
0.8s later — that is B-121, and the latch closed it.

The latch reads `full_text`: the raw generated tokens, **before Gate 5 has run
on any of them**. Gate 5 deletes banned sentences outright. On the 2026-08-30
call at 23:59:19 it latched on

    "Just a moment while I check what's available."

a sentence `_BANNED_SENTENCE_RE` had removed one line earlier. It cost nothing
there, because a head had already spoken on that turn. The general case does
cost something: the model's line is deleted, the latch says a hold phrase was
spoken, the tool-time producer stands down, and the caller gets the whole tool
round trip in silence.

THE FIX IS A REVOCATION, NOT A PREDICTION. Deciding it at latch time means
knowing what Gate 5 will do to text still sitting in the chunker's buffer.
Deciding it after the stream ends needs no guess: `_spoken_this_turn` is the
post-Gate-5 record of what the caller actually heard, so it is asked the same
question the latch asked `full_text` — does this still claim a lookup or a
write?

Note the test that matters most here is the LAST one. `_any_tts_emitted` was
the obvious revocation test and it is the wrong one: Gate 5 can delete the hold
sentence while another sentence of the same reply survives, and then something
was spoken but no hold phrase was.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.hold_speech import _NAMES_THE_WORK
from app.media_streams.llm_stream import LLMStream
from app.media_streams.turn_handler import sanitise_response


# The exact sentence from the call. Both halves of the defect in one string:
# it claims the work, and Gate 5 deletes it.
DELETED_HOLD = "Just a moment while I check what's available."

# A hold phrase Gate 5 leaves alone — the control.
SURVIVING_HOLD = "Let me check what's available for you as soon as possible."


def test_the_premise_holds_on_this_build():
    """If either half of this stops being true the rest of the file is vacuous.

    A test whose setup has silently stopped reproducing the defect passes for
    the wrong reason, which is how three of this file's ancestors went green
    while the defect was live.
    """
    assert _NAMES_THE_WORK.search(DELETED_HOLD), (
        "the sentence no longer reads as a hold phrase — the latch would not "
        "fire on it and this file is testing nothing"
    )
    assert sanitise_response(DELETED_HOLD, {"clinic_id": "northgate"}) == "", (
        "Gate 5 no longer deletes this sentence — pick another banned phrase "
        "or this file is testing nothing"
    )
    assert _NAMES_THE_WORK.search(SURVIVING_HOLD)
    assert sanitise_response(SURVIVING_HOLD, {"clinic_id": "northgate"}), (
        "the control sentence is now deleted too — it can no longer show that "
        "the revocation is conditional rather than unconditional"
    )


# ── A fake Anthropic stream, shaped like the events the real one emits ──────

def _text_delta(text: str):
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="text_delta", text=text),
    )


def _tool_start(name: str):
    return SimpleNamespace(
        type="content_block_start",
        content_block=SimpleNamespace(type="tool_use", name=name),
    )


class _FakeStream:
    def __init__(self, events, final):
        self._events = events
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):  # pragma: no cover - see __aiter__ below
        for e in self._events:
            yield e

    def __aiter__(self):  # noqa: F811 - async generator, not a coroutine
        async def _gen():
            for e in self._events:
                yield e
        return _gen()

    async def get_final_message(self):
        return self._final


class _FakeMessages:
    def __init__(self, stream):
        self._stream = stream

    def stream(self, **kwargs):
        return self._stream


class _FakeClient:
    def __init__(self, stream):
        self.messages = _FakeMessages(stream)


def _final_tool_message(text: str):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="text", text=text),
            SimpleNamespace(type="tool_use", id="tu_1", name="check_availability",
                            input={}),
        ],
    )


async def _drive(pre_tool_text: str) -> dict:
    """Stream `pre_tool_text`, then open a check_availability tool block.

    Returns the session, so the caller can read the latch.
    """
    session = {
        "clinic_id": "northgate",
        "booking_flow_active": True,
    }
    events = [_text_delta(tok) for tok in _tokenise(pre_tool_text)]
    events.append(_tool_start("check_availability"))
    stream = _FakeStream(events, _final_tool_message(pre_tool_text))

    q: asyncio.Queue = asyncio.Queue()
    await LLMStream()._one_streaming_call(
        client=_FakeClient(stream),
        model="claude-sonnet-5",
        system_prompt="",
        messages=[{"role": "user", "content": "what have you got saturday"}],
        tools=[],
        session=session,
        tts_text_queue=q,
        filler_sent=True,          # no ack-filler task, so no sleeping
        interim_played=False,
    )
    return session


def _tokenise(text: str):
    """One word per delta, like the real stream — the chunker needs several."""
    words = text.split(" ")
    return [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]


def test_a_hold_phrase_gate_5_deletes_does_not_latch():
    """The defect. Before the revocation the latch stayed set on nothing."""
    session = asyncio.run(_drive(DELETED_HOLD))
    assert not session.get("_hold_head_spoken"), (
        "the latch survived on a sentence Gate 5 deleted — the tool-time "
        "producer will stand down and the caller hears the whole lookup in "
        "silence"
    )


def test_a_hold_phrase_that_survives_gate_5_still_latches():
    """B-121 must stay closed. The revocation is conditional, not a rollback."""
    session = asyncio.run(_drive(SURVIVING_HOLD))
    assert session.get("_hold_head_spoken"), (
        "the latch was revoked on a phrase the caller actually heard — this "
        "re-opens B-121, two hold phrases 0.8s apart"
    )


def test_a_non_hold_line_never_latched_and_still_does_not():
    """The empathy case. `keep_pre_slot_speech` preserves these too, and
    latching on one would suppress a hold phrase the caller genuinely needs."""
    session = asyncio.run(
        _drive("I'm sorry to hear that, shoulder pain can be really limiting.")
    )
    assert not session.get("_hold_head_spoken")


def test_the_revocation_reads_what_was_SPOKEN_not_merely_whether_anything_was():
    """The test that `_any_tts_emitted` would have got wrong.

    Gate 5 deletes the hold sentence and leaves the rest of the reply standing.
    Something reached the caller, so "was anything emitted?" says yes — but no
    HOLD PHRASE reached them, so the tool-time producer must still speak.

    This is the case that decides which predicate the revocation uses, which is
    why it is here rather than implied by the two above.
    """
    session = asyncio.run(
        _drive(DELETED_HOLD + " I can see a few options for you.")
    )
    assert not session.get("_hold_head_spoken"), (
        "the latch survived because SOMETHING was spoken — but what survived "
        "Gate 5 was not a hold phrase, so the caller was never told to wait"
    )
