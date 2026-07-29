# tests/regression/test_reasoning_never_reaches_tts.py
"""
A1 — the model's internal reasoning is spoken to the caller.

Five calls between 2026-07-27 and 2026-07-28 played chain-of-thought down the
phone line. Verbatim, from obs:

  CA76bc921f  "I'm missing the service, slot_iso, and reason from the
               conversation context shown."       <- a caller heard `slot_iso`
  CAfe6a4162  "Wait, that's the wrong screen - that's for back pain.
               Let me get back on track."
  CAbad8422e  "**Patient name:** Jewel"           <- markdown, read aloud
  CA198906b4  "That's a soft affirmative to the booking offer - good."
  CA2f0b0707  "I need to book this in now - I have everything I need."

WHY THIS WAS NOT CAUGHT EARLIER
-------------------------------
obs stores `full_reply`, assembled from RAW tokens (llm_stream.py:644) - not
what reached TTS. Reading a transcript therefore cannot tell you whether the
caller heard a sentence or whether Gate 5 stripped it. Every previous A1 count
was measured against model output and was an inference either way.

So this test asserts on the AUDIBLE result: it replays raw text through the
real ResponseChunker and the real sanitise_response, exactly as the streaming
loop does, and asserts on what lands on tts_text_queue. Replaying the five
calls that way showed Gate 5 fired ONCE across all five - the other four were
spoken verbatim.

WHY THE GATE MISSED THEM
------------------------
Gate 5b is ~40 patterns, each added after one observed call. It enumerates
past leaks; it does not detect the class. Every new phrasing walks through.
The patterns asserted here are class rules (internal identifiers, markdown
artefacts), not another entry in that list.

THE OVER-STRIP HALF IS NOT OPTIONAL
-----------------------------------
This file's history has two incidents where a broader gate caused the worse
failure: Gate 5c ate a legitimate confirmation and abandoned a completed
booking (turn_handler.py, 2026-06-12), and a chunk-level "I should" drop
swallowed real slot text (2026-06-18). So every case below asserts BOTH that
the reasoning is gone AND that the substantive question survives. A fix that
passes the first half and fails the second is a worse bug than A1.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from app.media_streams import llm_stream as ls
from app.media_streams.chunker import ResponseChunker
from app.media_streams.turn_handler import sanitise_response


def speak(raw: str, session: dict | None = None) -> str:
    """Return what the caller actually HEARS for a given raw model reply.

    Mirrors the live streaming path: tokens -> ResponseChunker -> Gate 5 ->
    tts_text_queue (llm_stream.py:1515-1556). Anything this returns was spoken.
    """
    session = {} if session is None else session
    chunker = ResponseChunker()
    spoken: list[str] = []
    for token in re.findall(r"\S+\s*", raw):
        chunk = chunker.add_token(token)
        if chunk:
            out = sanitise_response(chunk, session)
            if out:
                spoken.append(out)
    tail = chunker.flush()
    if tail:
        out = sanitise_response(tail, session)
        if out:
            spoken.append(out)
    return " ".join(spoken)


# ── The five calls, verbatim from obs ──────────────────────────────────────
# (sid, raw model reply, must-not-be-heard, must-still-be-heard)
CASES = [
    (
        "CA76bc921f",
        "The caller has confirmed the number. Let me now book the appointment.\n\n"
        "Looking at the call state, I need to check what service and slot were "
        "agreed. The call state shows the phone number confirmation step, but I "
        "don't have full context on the service or slot. Let me review what I know:\n\n"
        "- Name: Tom Green\n- Phone: 07368306992\n"
        "- The warm readback mentioned Saturday 1st August at 11am\n\n"
        "But I don't have the service or reason clearly established in the call "
        "state. Given the call state shows a lower back/leg screen was done, the "
        "service is likely msk_initial_assessment. But I should not guess the reason.\n\n"
        "I'm missing the service, slot_iso, and reason from the conversation "
        "context shown.\n\n"
        "I need to ask for the reason before booking since book_appointment requires it.\n\n"
        "What's the appointment for, Tom?",
        ["slot_iso", "book_appointment", "msk_initial_assessment", "call state"],
        "What's the appointment for, Tom?",
    ),
    (
        "CAfe6a4162",
        "Before we go any further, can I just check — do you have any numbness "
        "around the saddle area between your legs, or any changes in your bladder "
        "or bowel control?\n\n"
        "Wait, that's the wrong screen — that's for back pain. Let me get back on track.\n\n"
        "Just to confirm the number — I've got zero seven seven zero zero, nine "
        "zero zero, one two three. Is that right?",
        ["wrong screen", "back on track"],
        "Is that right?",
    ),
    (
        "CAbad8422e",
        "Perfect — so Friday the 31st of July at six in the evening. Just to "
        "confirm your details before I book that in:\n\n"
        "**Patient name:** Jewel\n**Phone:** 07368306992\n"
        "**Appointment:** Initial assessment for knee pain\n"
        "**Location:** Bolton clinic\n"
        "**Date and time:** Friday 31st July at six in the evening\n\n"
        "Is all of that correct?",
        ["**"],
        "Jewel",           # the readback CONTENT must survive the markdown strip
    ),
    (
        "CA198906b4",
        "That's a soft affirmative to the booking offer — good. Now I need a "
        "timing preference before checking availability.\n\n"
        "Noted — do you have a preference for when you'd like to come in?",
        ["soft affirmative", "checking availability"],
        "do you have a preference for when you'd like to come in?",
    ),
    (
        "CA2f0b0707",
        "Let me get that booked in for you now.I need to book this in now — I "
        "have everything I need.So that's Tom, Tuesday the 4th of August at "
        "quarter to six in the evening — shall I go ahead and book that in?",
        ["I need to book this in now", "I have everything I need"],
        "shall I go ahead and book that in?",
    ),
]


@pytest.mark.parametrize(
    "sid,raw,forbidden,_required", CASES, ids=[c[0] for c in CASES]
)
def test_reasoning_is_not_spoken(sid, raw, forbidden, _required):
    """No internal reasoning reaches the caller."""
    heard = speak(raw)
    for phrase in forbidden:
        assert phrase.lower() not in heard.lower(), (
            f"{sid}: caller heard internal reasoning {phrase!r}\nheard: {heard!r}"
        )


@pytest.mark.parametrize(
    "sid,raw,_forbidden,required", CASES, ids=[c[0] for c in CASES]
)
def test_substantive_content_survives(sid, raw, _forbidden, required):
    """The half that matters more: the caller is not left with silence.

    A gate that strips the reasoning AND the question hands the caller a dead
    end and fires the "Sorry, I didn't quite catch that" fallback. That has
    already cost this system a completed booking once.
    """
    heard = speak(raw)
    assert heard.strip(), f"{sid}: gate stripped the entire turn - caller heard nothing"
    assert required.lower() in heard.lower(), (
        f"{sid}: gate ate substantive content {required!r}\nheard: {heard!r}"
    )


# ── Legitimate speech must be untouched ────────────────────────────────────
# The over-fire guard for the new class rules. These are real receptionist
# lines containing tokens adjacent to the banned ones.
@pytest.mark.parametrize(
    "text",
    [
        "I need to book you in for Tuesday, does that work?",
        "Let me check what I have for Friday.",
        "I have everything I need to get that booked - shall I go ahead?",
        "That's confirmed for Wednesday the 5th at ten in the morning.",
        "Can I just check - do you get any pins and needles down the leg?",
        "The soonest I have is Thursday the 6th at half past nine.",
    ],
)
def test_legitimate_speech_untouched(text):
    heard = speak(text)
    assert heard.strip(), f"legitimate line stripped to nothing: {text!r}"


# ── The ungated path ───────────────────────────────────────────────────────
def test_gpt_fallback_sanitises_before_tts():
    """The GPT fallback path must apply Gate 5.

    _gpt_fallback fires when Claude is overloaded (llm_stream.py:1117) and puts
    its reply straight onto tts_text_queue (llm_stream.py:2334) with no
    sanitise_response call. Its only protection is a prompt prefix asking GPT
    not to misbehave.

    That means under load - the exact condition a busy Monday or a demo creates
    - every Gate 5 protection disappears, including Gate 5f, the guard that
    stops a phantom "all booked" reaching a caller when no booking exists.
    """
    class _Msg:
        content = "The caller said yes. I should book this now. You're all booked in."
        tool_calls = None

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        async def create(self, **_kw):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _FakeOpenAI:
        def __init__(self, **_kw):
            self.chat = _Chat()

    import openai
    orig_key, orig_client = ls.OPENAI_API_KEY, openai.AsyncOpenAI
    ls.OPENAI_API_KEY = "test-key"
    openai.AsyncOpenAI = _FakeOpenAI
    try:
        stream = object.__new__(ls.LLMStream)
        queue: asyncio.Queue = asyncio.Queue()
        session = {"booking_flow_active": True}   # and no booking_write_confirmed

        asyncio.run(
            stream._gpt_fallback(
                system_prompt="",
                messages=[{"role": "user", "content": "yes please"}],
                session=session,
                tts_text_queue=queue,
            )
        )
    finally:
        ls.OPENAI_API_KEY = orig_key
        openai.AsyncOpenAI = orig_client

    spoken = " ".join(queue.get_nowait() for _ in range(queue.qsize()))
    assert "I should book this now" not in spoken, (
        f"GPT fallback spoke reasoning unfiltered: {spoken!r}"
    )
    assert "all booked in" not in spoken.lower(), (
        "GPT fallback spoke a phantom booking confirmation with no successful "
        f"book_appointment - Gate 5f never ran: {spoken!r}"
    )
