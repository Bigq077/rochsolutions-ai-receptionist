"""B-131 - the 50-word cutoff severed a sentence one word before it ended.

CA87204a699b, demo line, 2026-09-01. The model said:

    "...and it tends to respond really well once Priya's had a proper look at
     what's driving it. Do you have a preference for when you'd like to come in?"

`add_token`'s hard cutoff fired at word 50 with no notion of sentence position,
so the turn left the system as two chunks split mid-clause. `sanitise_response`
then capitalises every chunk head, which promoted the stranded tail into a
sentence of its own, and the caller heard:

    "...had a proper look at what's driving"   [pause]   "It. Do you have a..."

Five of these reached callers, measured over 8,555 stored assistant chunks:

    "...sorted with Marcus right"          -> "Away."
    "...so he can take a proper"           -> "Look?"
    "...for when you'd like to come"       -> "In?"    (twice)
    "...a proper look at what's driving"   -> "It."

Every one ended its sentence within two words of the cut. The chunker cannot
see ahead, but it can wait a little before giving up on the sentence, which is
what MAX_WORDS_SENTENCE_GRACE buys.

These tests drive the real ResponseChunker with production parameters
(`fast_first=False`, confirmed from media_streams/config.py), and fail on the
parent commit.
"""
import re

import pytest

from app.media_streams.chunker import (
    ResponseChunker,
    MAX_WORDS,
    MAX_WORDS_SENTENCE_GRACE,
)

_TERMINAL = re.compile(r"[.!?…—:,]$")


def _chunks(text: str) -> list:
    """Feed *text* through a live-configured chunker, word by word."""
    chunker = ResponseChunker()
    out = []
    for i, word in enumerate(text.split()):
        emitted = chunker.add_token((" " + word) if i else word)
        if emitted:
            out.append(emitted)
    final = chunker.flush()
    if final:
        out.append(final)
    return out


def _severed(chunks: list) -> list:
    """Chunks that end mid-sentence, i.e. on no punctuation at all."""
    return [c for c in chunks[:-1] if not _TERMINAL.search(c.strip())]


# The real turn, reconstructed from the call that prompted this.
CA87204A6 = (
    "A shoulder that's been sore for a couple of weeks can really get in the "
    "way — pain on the outside or catching when you lift your arm is one of "
    "the most common patterns we see, and it tends to respond really well "
    "once Priya's had a proper look at what's driving it. Do you have a "
    "preference for when you'd like to come in?"
)


class TestTheSentenceSurvivesTheCutoff:
    def test_the_live_call_that_prompted_this(self):
        chunks = _chunks(CA87204A6)
        assert not _severed(chunks), (
            "a chunk ends mid-sentence: {0!r}".format(_severed(chunks))
        )

    def test_no_chunk_head_is_a_stranded_fragment(self):
        """The audible symptom: a chunk opening on a one-word sentence, which is
        what the stranded tail becomes once its first letter is capitalised."""
        for chunk in _chunks(CA87204A6)[1:]:
            first = chunk.strip().split()[0] if chunk.strip() else ""
            assert not re.fullmatch(r"[A-Za-z]{1,8}[.!?]", first), (
                "chunk opens on a stranded fragment: {0!r}".format(chunk[:60])
            )

    @pytest.mark.parametrize("tail", [
        "we'll get you sorted with Marcus right away.",
        "so he can take a proper look?",
        "for when you'd like to come in?",
    ])
    def test_the_other_shapes_that_reached_callers(self, tail):
        """Each of these was severed one or two words from its end."""
        filler = "Right, so " + ("just a little more context here " * 6)
        assert not _severed(_chunks(filler + tail))


class TestTheGraceIsBounded:
    def test_a_run_on_is_still_cut_at_the_ceiling(self):
        """The grace must not become an unbounded wait: MAX_WORDS exists to
        guarantee forward progress on a sentence that never ends.

        Asserted on what the chunker EMITS, not on flush() output - flush
        combines the held chunk with the remainder, so a flush-only assertion
        measures hold-and-merge rather than the cutoff."""
        chunker = ResponseChunker()
        emitted = []
        for i in range(MAX_WORDS + MAX_WORDS_SENTENCE_GRACE + 80):
            out = chunker.add_token("word" if i == 0 else " word")
            if out:
                emitted.append(len(out.split()))
        assert emitted, "an unpunctuated run was never cut"
        assert max(emitted) <= MAX_WORDS + MAX_WORDS_SENTENCE_GRACE, (
            "a chunk grew past the grace ceiling: {0}".format(emitted)
        )

    def test_a_comma_list_is_cut_without_using_the_grace(self):
        """The shape that really does run long is a slot readout: a
        comma-separated list with no full stop in it. The longest
        punctuation-free run in 8,555 stored chunks is 60 words, two short of
        the ceiling - so if only a full stop could stop the wait, a slightly
        longer readout would push its emission past the point hold-and-merge
        can release, making the whole turn one very long TTS call."""
        # Long enough to produce at least TWO candidates: the chunker holds
        # one behind and releases it only when the next arrives, so a text
        # yielding a single candidate emits nothing until flush().
        item = "half past ten in the morning, "
        chunker = ResponseChunker()
        emitted = []
        for i, word in enumerate((item * 30).split()):
            out = chunker.add_token((" " + word) if i else word)
            if out:
                emitted.append(out)
        assert emitted, "a long comma list was never cut"
        assert max(len(c.split()) for c in emitted) < (
            MAX_WORDS + MAX_WORDS_SENTENCE_GRACE
        ), "a comma list ran to the grace ceiling instead of cutting at a comma"
        for c in emitted:
            assert c.strip().endswith(","), (
                "a comma list was cut somewhere other than a comma: {0!r}".format(c[-40:])
            )

    def test_the_grace_is_small_enough_to_stay_inaudible(self):
        """A 50-word chunk is ~15s of speech and is already playing while the
        next buffers, so a few more tokens delay no audio. A large grace would
        stop being free."""
        assert 0 < MAX_WORDS_SENTENCE_GRACE <= 20


class TestShorterTurnsAreUnchanged:
    @pytest.mark.parametrize("text", [
        "Do you have a preference for when you'd like to come in?",
        "So that's Wednesday the 19th of August at ten in the morning — "
        "shall I go ahead and book that in?",
        "Sorry to hear that —",
    ])
    def test_a_turn_that_never_reaches_the_cutoff_is_untouched(self, text):
        """The change only reaches text that hits MAX_WORDS. Everything shorter
        must chunk exactly as before, which is most of what Susie says."""
        assert len(text.split()) < MAX_WORDS
        assert " ".join(_chunks(text)).split() == text.split()
