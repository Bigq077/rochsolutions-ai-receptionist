"""
Regression: a comma became a full stop, and the caller heard two questions.

D-A, CA90ccb117bccbe9d5f344d9b335a1e2a9, northgate, build a66a34371749,
3 September 2026, 15:40. Written up in OPEN_DEFECTS_2026-09-03_EVENING.md.

The model wrote ONE question:

    "...do you have a sense of whereabouts on the knee it's bothering you,
     or how it came on?"

Since `fff61547` ("let the first chunk break on a clause, not only a full
stop") the streaming chunker may split a first chunk at a COMMA. It did:

    chunk 1  "...whereabouts on the knee it's bothering you,"
    chunk 2  "or how it came on? Either way, ..."

`chunk_text_static` returns ONE chunk for the same text, so this is the
streaming path only — which is why no test saw it.

Then chunk 2 reached TTS as **"Or how it came on?"**. The capital was never in
the model's output. The caller heard a full stop mid-question, and the reason
captured for the whole call was "um just my knee" — no site, no mechanism.

── WHERE THE CAPITAL CAME FROM ────────────────────────────────────────────────
`sanitise_response`'s last statement (turn_handler.py, "Fix A"). It existed to
restore capitalisation after a banned OPENER was stripped — "Lovely — we've
got…" → "We've got…" — and its comment says exactly that. The code did not:
it ran on EVERY chunk unconditionally, so a continuation chunk got a capital
it had no claim to.

Same shape as B-120 and B-132: an operation whose safety rests on a premise —
"this chunk starts a sentence" — that is false for a continuation.

The fix is (b) of the two the defect doc proposed, and the smaller one: it
keeps `fff61547`'s latency win and changes only what the split is allowed to
imply.

── WHAT THIS DOES NOT PROVE ───────────────────────────────────────────────────
Removing the capital removes the false sentence from the TEXT. Whether the
caller still hears a hard stop depends on ElevenLabs prosody across two
separate synthesis requests, which no test here can settle. The defect doc's
own hypothesis is that "the split alone is a pause rather than a defect" —
that needs a call.
"""
from __future__ import annotations

import pytest

from app.media_streams.turn_handler import sanitise_response


def _s():
    return {"clinic_id": "northgate"}


# The live call's own chunk 2.
CONTINUATION = "or how it came on? Either way, do you have a preference?"


def test_the_live_continuation_keeps_its_lower_case():
    assert sanitise_response(CONTINUATION, _s()).startswith("or how it came on?")


@pytest.mark.parametrize(
    "chunk",
    [
        "or how it came on? Either way, do you have a preference?",
        "and the sooner someone looks at it the easier it settles.",
        "but Tuesday is free if that suits better.",
        "so I can get you booked in for that.",
    ],
)
def test_no_continuation_chunk_is_given_a_capital(chunk):
    """Every one of these is a clause the chunker can hand over mid-sentence
    now that a first chunk may break on a comma."""
    assert sanitise_response(chunk, _s())[:1].islower()


@pytest.mark.parametrize(
    "chunk,expected_start",
    [
        ("Lovely — we have got Tuesday at ten.", "We have got"),
        # The case a first-character comparison would miss: the stripped opener
        # and the surviving word start with the same letter.
        ("Wonderful — we have got Tuesday at ten.", "We have got"),
    ],
)
def test_an_opener_strip_still_re_capitalises(chunk, expected_start):
    """THE guard. Fix A was written for a real defect and must keep working —
    this change narrows when it fires, it does not remove it."""
    assert sanitise_response(chunk, _s()).startswith(expected_start)


def test_a_normal_sentence_is_untouched():
    text = "Do you have a preference for when you would like to come in?"
    assert sanitise_response(text, _s()) == text


def test_the_split_that_produced_this_still_happens():
    """Pins the CAUSE, so the two halves cannot drift apart. If fff61547 is
    ever reverted this test should be revisited rather than deleted: the
    capitalisation would then be harmless again, but only by accident.

    Asserts the streaming chunker splits the live sentence at the comma while
    `chunk_text_static` does not — the asymmetry that hid this from every
    existing test.
    """
    from app.media_streams.chunker import ResponseChunker

    sentence = (
        "Knee pain can have a few different causes — do you have a sense "
        "of whereabouts on the knee it's bothering you, or how it came on?"
    )
    ch = ResponseChunker(min_words_first=3, fast_first=True)
    out = []
    for tok in sentence.split(" "):
        c = ch.add_token(tok + " ")
        if c:
            out.append(c)
    tail = ch.flush()
    if tail:
        out.append(tail)

    assert len(out) >= 2, "the streaming chunker no longer splits this sentence"
    # Whatever the split point, no chunk after the first may arrive capitalised
    # once sanitise_response has run on it.
    for c in out[1:]:
        cleaned = sanitise_response(c, _s())
        if cleaned and cleaned[:1].isalpha():
            assert cleaned[:1].islower(), (
                "a continuation chunk was capitalised: %r" % cleaned[:40]
            )
