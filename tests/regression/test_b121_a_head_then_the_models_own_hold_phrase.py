"""Two hold phrases in a row — the head first, the model's own second.

B-121 in the direction nobody checked, reported by the owner from the demo
line on 2026-08-30: *"it goes with the filler, and then it says 'Let me look
that up', which sounds a bit robotic."*

`CAd1bc6681b69e48fc8527449d65a03a23`, build `61d651804c20`:

    10:26:01.403  situational head (named_day): 'Let me see what Tuesday looks like —'
    10:26:03.084  synthesise:  "Let me check what's available on Tuesday for you."
                               ^^ 1.68s later, a SECOND hold phrase

    10:26:21.016  situational head (earliest): 'Let me see what the earliest is —'
    10:26:22.397  synthesise:  'Let me check that for you.'

The finding-4 latch (`test_the_hold_latch_survives_gate_5.py`) closes the
MODEL-FIRST order: the model's preserved pre-tool line latches
`_hold_head_spoken` so the tool-time producer stands down. This file is the
other order — OUR head speaks first, and the model's own line follows.

WHY THE EXISTING MACHINERY DID NOT CATCH IT. The handover guessed that
`join_after_head` was "not reached on this path". It is reached, and
`_strip_interim_opener` correctly reduces both of the sentences above to "".
The sentence comes back out of `join_after_head`'s fail-safe:

    body = _strip_interim_opener(chunk).lstrip()
    if not body:
        # ...Saying the phrase twice is a much smaller fault than saying
        # nothing, so the original stands.
        return chunk

That trade was priced against silence. It is the wrong price HERE, because
`join_after_head` is only ever called when `interim_played or
_hold_head_spoken` — i.e. when the caller has ALREADY heard a hold phrase. The
turn cannot go silent from suppressing this chunk: the head is the audio, and
the empty-turn rescue in `_one_streaming_call` ("no TTS emitted this turn")
covers the case where the model promised a lookup and then made no tool call,
and covers it with a real sentence rather than a repetition.

NOT A PHRASE BLACKLIST. "Code must never match one literal of model speech" has
cost five fixes here. Nothing below adds a phrase. The discriminator is the one
that already exists — does the opener stripper reduce this chunk to nothing? —
and it is asked of the model's sentence, not matched against a list.

THE SECOND HALF IS THE OWNERSHIP BUG. Suppressing the duplicate alone is
self-defeating. The latch site says of itself:

    # Only OUR latch is revocable; another producer's records audio that has
    # already gone out.

but it does not enforce that: it sets `_latched_on_ungated_text = True` without
checking whether `_hold_head_spoken` was ALREADY True. On this call it was —
the head set it at 600ms. So suppressing the model's line removes the phrase
from `_spoken_this_turn`, the end-of-stream revocation concludes no hold phrase
was heard, clears a latch that belongs to the head producer, and the tool-time
producer speaks a second phrase after all. Different producer, same defect.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.hold_speech import _NAMES_THE_WORK
from app.media_streams.llm_stream import (
    PRE_SLOT_MARKER,
    LLMStream,
    _may_suppress_pure_dupe,
    _strip_interim_opener,
    join_after_head,
)
from app.media_streams.turn_handler import sanitise_response


# The two exact pairs from the call.
HEAD_NAMED_DAY = "Let me see what Tuesday looks like —"
DUPE_NAMED_DAY = "Let me check what's available on Tuesday for you."

HEAD_EARLIEST = "Let me see what the earliest is —"
DUPE_EARLIEST = "Let me check that for you."

# `keep_pre_slot_speech` preserves this too, and it carries real information.
# It is the control in every test below: the fix must not touch it.
EMPATHY = "I'm sorry to hear that, shoulder pain can be really limiting."


# ── Premise ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dupe", [DUPE_NAMED_DAY, DUPE_EARLIEST])
def test_the_premise_holds_on_this_build(dupe):
    """If any of these stops being true the rest of the file is vacuous.

    The third assertion is the one the handover flagged as easy to miss: when
    Gate 5 DELETES the model's line the caller hears one phrase and it sounds
    right, so the defect appears and disappears with the model's wording. These
    two survive Gate 5, which is why the owner could hear them.
    """
    assert _NAMES_THE_WORK.search(dupe), (
        "the model's line no longer reads as claiming a lookup"
    )
    assert _strip_interim_opener(dupe) == "", (
        "the opener stripper no longer sees this as nothing-but-a-hold-phrase, "
        "so the suppression below is not being exercised"
    )
    assert sanitise_response(dupe, {"clinic_id": "northgate"}), (
        "Gate 5 now deletes this sentence — the caller would hear only one "
        "phrase and this file is testing a defect that cannot occur"
    )


def test_the_empathy_control_is_not_a_hold_phrase():
    assert not _NAMES_THE_WORK.search(EMPATHY)
    assert _strip_interim_opener(EMPATHY) == EMPATHY


# ── The pure function ───────────────────────────────────────────────────────

@pytest.mark.parametrize("head,dupe", [
    (HEAD_NAMED_DAY, DUPE_NAMED_DAY),
    (HEAD_EARLIEST, DUPE_EARLIEST),
])
def test_a_pure_duplicate_is_suppressed_when_the_caller_can_afford_it(head, dupe):
    """The defect, at the smallest reproducible scale."""
    assert join_after_head(dupe, head, suppress_pure_duplicate=True) == "", (
        "the caller hears the head and then the model's own version of the "
        "same sentence ~1.5s later"
    )


@pytest.mark.parametrize("head,dupe", [
    (HEAD_NAMED_DAY, DUPE_NAMED_DAY),
    (HEAD_EARLIEST, DUPE_EARLIEST),
])
def test_the_default_contract_is_unchanged(head, dupe):
    """Suppression is opt-in. Any other caller of this pure function keeps the
    old fail-safe, because only `_one_streaming_call` has the empty-turn rescue
    underneath it that makes returning "" safe."""
    assert join_after_head(dupe, head) == dupe


def test_a_reply_with_content_behind_the_opener_keeps_the_content():
    """Suppression is for chunks that are NOTHING BUT the opener. A chunk that
    carries information must lose the opener and keep the information —
    the behaviour that already existed and must not regress."""
    out = join_after_head(
        "Let me check. Tuesday at ten is free.",
        HEAD_NAMED_DAY,
        suppress_pure_duplicate=True,
    )
    assert "Tuesday at ten is free" in out
    assert not out.lower().startswith("let me check")


def test_empathy_is_never_suppressed():
    out = join_after_head(EMPATHY, HEAD_NAMED_DAY, suppress_pure_duplicate=True)
    assert "shoulder pain" in out


# ── The streaming path ──────────────────────────────────────────────────────

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

    def __aiter__(self):
        async def _gen():
            for e in self._events:
                yield e
        return _gen()

    async def get_final_message(self):
        return self._final


class _FakeClient:
    def __init__(self, stream):
        self.messages = SimpleNamespace(stream=lambda **kw: stream)


def _final_tool_message(text: str):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="text", text=text),
            SimpleNamespace(type="tool_use", id="tu_1",
                            name="check_availability", input={}),
        ],
    )


def _tokenise(text: str):
    words = text.split(" ")
    return [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]


async def _drive_after_a_head(pre_tool_text: str, head: str):
    """A head has ALREADY been spoken; then the model produces `pre_tool_text`
    in front of a check_availability call. Returns (session, spoken chunks)."""
    session = {
        "clinic_id": "northgate",
        "booking_flow_active": True,
        # What the situational head producer leaves behind at 600ms.
        "_hold_head_spoken": True,
        "_hold_head_text": head,
    }
    events = [_text_delta(t) for t in _tokenise(pre_tool_text)]
    events.append(_tool_start("check_availability"))

    q: asyncio.Queue = asyncio.Queue()
    await LLMStream()._one_streaming_call(
        client=_FakeClient(_FakeStream(events, _final_tool_message(pre_tool_text))),
        model="claude-sonnet-5",
        system_prompt="",
        messages=[{"role": "user", "content": "what have you got tuesday"}],
        tools=[],
        session=session,
        tts_text_queue=q,
        filler_sent=True,
        interim_played=False,
    )

    spoken = []
    while not q.empty():
        c = q.get_nowait()
        spoken.append(c[len(PRE_SLOT_MARKER):] if c.startswith(PRE_SLOT_MARKER) else c)
    return session, spoken


@pytest.mark.parametrize("head,dupe", [
    (HEAD_NAMED_DAY, DUPE_NAMED_DAY),
    (HEAD_EARLIEST, DUPE_EARLIEST),
])
def test_the_caller_does_not_hear_a_second_hold_phrase(head, dupe):
    """The call, end to end on the streaming path."""
    _session, spoken = asyncio.run(_drive_after_a_head(dupe, head))
    joined = " ".join(spoken).strip()
    assert not _NAMES_THE_WORK.search(joined), (
        f"a second hold phrase reached the caller {joined!r} — the head "
        f"{head!r} already said this 1.5s earlier"
    )


@pytest.mark.parametrize("head,dupe", [
    (HEAD_NAMED_DAY, DUPE_NAMED_DAY),
    (HEAD_EARLIEST, DUPE_EARLIEST),
])
def test_suppressing_the_duplicate_does_not_disown_the_head(head, dupe):
    """The ownership half, and the reason the first fix alone is self-defeating.

    The head genuinely spoke. If the end-of-stream revocation clears
    `_hold_head_spoken` because the model's line no longer appears in
    `_spoken_this_turn`, the tool-time producer stops standing down and says a
    hold phrase of its own — the same defect from a different producer.
    """
    session, _spoken = asyncio.run(_drive_after_a_head(dupe, head))
    assert session.get("_hold_head_spoken") is True, (
        "the latch was revoked, but it was never ours to revoke — the head "
        "producer set it and the caller heard that audio. The tool-time "
        "producer will now speak a second hold phrase"
    )


# ── When suppression is NOT allowed ─────────────────────────────────────────
#
# The first version of this fix passed `suppress_pure_duplicate=True`
# unconditionally at both call sites, and it broke the OTHER direction of B-121
# — `test_a_hold_phrase_that_survives_gate_5_still_latches` went red. The flush
# path runs AFTER the `content_block_start` for the tool call, so by then the
# model's own preserved line may itself have latched `_hold_head_spoken`. The
# flush then joined that sentence against a latch the sentence had set ~0ms
# earlier, suppressed it against ITSELF, and the end-of-stream revocation
# cleared the latch because nothing hold-shaped was left in `_spoken_this_turn`.
# Fixing the head-first direction re-opened the model-first one.
#
# These four pin the discriminator so that cannot come back silently.

def test_a_placeholder_head_never_licenses_suppression():
    """The call sites pass `_head or "…"` so the seam logic always has an
    argument. A placeholder is not evidence a hold phrase was spoken — it is
    evidence we do not know what the caller heard. The fast-path interim lands
    here too: it records no wording, so it keeps the old fail-safe."""
    assert _may_suppress_pure_dupe({"_hold_head_spoken": True}, "", False) is False
    assert _may_suppress_pure_dupe({"_hold_head_spoken": True}, "   ", False) is False


def test_the_models_own_latch_never_licenses_suppressing_itself():
    """The regression above, as a unit. `latched_on_ungated_text` means the
    latch came from THIS turn's model text, so the "head" is the very sentence
    being judged."""
    assert _may_suppress_pure_dupe(
        {"_hold_head_spoken": True}, HEAD_NAMED_DAY, True
    ) is False


def test_a_real_head_from_another_producer_does_license_it():
    assert _may_suppress_pure_dupe(
        {"_hold_head_spoken": True}, HEAD_NAMED_DAY, False
    ) is True


def test_no_latch_means_no_head_was_spoken_so_nothing_is_suppressed():
    assert _may_suppress_pure_dupe({}, HEAD_NAMED_DAY, False) is False


def test_empathy_in_front_of_the_tool_still_reaches_the_caller():
    """The whole point of `keep_pre_slot_speech`. It must survive a head."""
    _session, spoken = asyncio.run(
        _drive_after_a_head(EMPATHY, HEAD_NAMED_DAY)
    )
    assert "shoulder pain" in " ".join(spoken), (
        "a head suppressed the empathy line — the fix is gated on the opener "
        "stripper, not on there having been a head"
    )
