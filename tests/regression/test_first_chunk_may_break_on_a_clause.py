"""WS-A first chunk: a clause is somewhere to stop, unless it is a number.

`chunk_gate_ms` — first LLM token to first chunk released — is floored by the
time the model takes to generate a whole first SENTENCE, because the boundary
test only accepted a hard `.!?`. Measured over 2,411 stored turns: p50 672ms,
p90 1,975ms, ~17% of the caller's voice-to-voice budget.

The first chunk may now also break on a comma or semicolon. Later chunks are
untouched, so mid-response prosody is unchanged.

The guard is the point of this file. A phone number reaches TTS as WORDS —
"oh seven five oh two, two one one" — so an `isdigit()` test sees nothing and
splits the number in half. That is a known live defect (a number read across
two synthesis calls with an audible seam), and the first cut of this change
reintroduced it.
"""
import pytest

from app.media_streams.chunker import (
    ResponseChunker,
    _carries_a_number_sequence,
)

PROSE = (
    "Hamstring tightness after training is really common in active people, "
    "that kind of tension in the back of the thigh often builds up over weeks "
    "of loading. Do you have a preference for when you'd like to come in?"
)
PHONE = (
    "Thanks Quentin, I've got you on oh seven five oh two, two one one, "
    "two oh seven, is that the best number for the booking?"
)


def _first_chunk(text, **kw):
    c = ResponseChunker(**kw)
    for w in text.split(" "):
        out = c.add_token(w + " ")
        if out:
            return out
    return c.flush()


def _off(text):
    return _first_chunk(text, fast_first=False)


def _on(text):
    return _first_chunk(text, fast_first=True, min_words_first=6)


# ── the win ──────────────────────────────────────────────────────────────────

def test_prose_first_chunk_is_released_a_clause_early():
    on, off = _on(PROSE), _off(PROSE)
    assert on != off
    assert len(on.split()) < len(off.split())
    assert on.rstrip().endswith(",")


def test_later_chunks_are_untouched():
    """Only the FIRST chunk may break early; prosody after it is unchanged."""
    c = ResponseChunker(fast_first=True, min_words_first=6)
    chunks = [o for w in PROSE.split(" ") if (o := c.add_token(w + " "))]
    # hold-and-merge keeps the final candidate back until the stream ends
    tail = c.flush()
    if tail:
        chunks.append(tail)
    assert len(chunks) >= 2
    for later in chunks[1:]:
        assert not later.rstrip().endswith(","), later


# ── the guard ────────────────────────────────────────────────────────────────

def test_a_spoken_phone_number_is_never_split_early():
    """The defect the isdigit() guard missed: digits arrive as words."""
    assert _on(PHONE) == _off(PHONE)


@pytest.mark.parametrize("text,expected", [
    ("oh seven five oh two", True),
    ("two one one, two oh seven", True),
    ("double four seven", True),
    # prose that merely contains a number word is not a number sequence
    ("that's one of the things Jonathan looks at", False),
    ("Hamstring tightness after training is really common", False),
    ("come in for a session", False),
])
def test_number_sequence_detector(text, expected):
    assert _carries_a_number_sequence(text) is expected


def test_a_lone_number_word_does_not_block_the_split():
    """"one of the things" must still get the early release."""
    text = ("That's one of the things Jonathan looks at during the session, "
            "so it's worth mentioning when you come in for your appointment.")
    assert _on(text) != _off(text)


def test_numerals_are_still_blocked():
    """The original isdigit() guard must survive alongside the word guard."""
    text = "Your reference is 4471, 2208, please quote it when you arrive today."
    assert _on(text) == _off(text)


def test_flag_off_is_byte_identical():
    """fast_first=False must behave exactly as before this change."""
    for text in (PROSE, PHONE):
        c = ResponseChunker(fast_first=False)
        got = [o for w in text.split(" ") if (o := c.add_token(w + " "))]
        assert all(not g.rstrip().endswith(",") for g in got)
