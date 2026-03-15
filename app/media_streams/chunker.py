# app/media_streams/chunker.py
"""
Streaming LLM token -> speakable TTS chunk boundary detector.

Receives individual tokens from Claude's text_stream and decides when enough
text has accumulated to send to ElevenLabs TTS as a single request.

Chunking strategy:
  - Buffer tokens until MIN_WORDS (15) have accumulated
  - Emit at MIN_WORDS if the buffer ends with a HARD_SPLIT_CHAR (. ! ?)
    AND the word before the punctuation is NOT a NEVER_SPLIT_AFTER abbreviation
  - Force-emit at MAX_WORDS (50) regardless of punctuation
  - Do NOT emit on soft punctuation (, ; :) alone unless MAX_WORDS is reached
  - On LLM stream end: flush() returns whatever remains in the buffer

Design goals:
  - Minimum 15 words prevents choppy TTS (each chunk = one ElevenLabs request)
  - Sentence boundary detection gives natural prosody
  - MAX_WORDS ensures forward progress on run-on sentences
  - Abbreviation list prevents mid-sentence splits after "Dr.", "Mon.", etc.

Example for a 35-word response:
  "Right, so I've checked what we have available for you..." [11w, ends .]
    -> below MIN_WORDS, buffer continues
  "The first slot we have is Monday the 14th at 10am." [+11w = 22w total, ends .]
    -> >= MIN_WORDS AND ends period AND 'am' not in NEVER_SPLIT_AFTER
    -> EMIT chunk 1
  "Which would you prefer?" [4w remaining]
    -> flush() on stream end -> EMIT chunk 2
"""
from __future__ import annotations

import re
from typing import List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_WORDS = 15
MAX_WORDS = 50

HARD_SPLIT_CHARS = frozenset({".", "!", "?"})
SOFT_SPLIT_CHARS = frozenset({",", ";", ":"})   # only used at MAX_WORDS

NEVER_SPLIT_AFTER: frozenset = frozenset({
    # Titles
    "mr", "mrs", "ms", "dr", "prof", "rev", "st",
    # Days
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    # Months
    "jan", "feb", "mar", "apr", "jun", "jul", "aug",
    "sep", "oct", "nov", "dec",
    # Common abbreviations
    "approx", "eg", "ie", "etc", "vs", "no",
})

# Finds last alphabetic word or digit sequence immediately before a . ! ?
_LAST_WORD_RE = re.compile(r"(\b[a-zA-Z]{2,}|\b\d+)\s*[.!?]\s*$")


# ---------------------------------------------------------------------------
# ResponseChunker class
# ---------------------------------------------------------------------------

class ResponseChunker:
    """
    Stateful token accumulator that emits speakable chunks for ElevenLabs TTS.

    Usage inside LLMStream:
        chunker = ResponseChunker()
        async for token in stream.text_stream:
            chunk = chunker.add_token(token)
            if chunk:
                await tts_text_queue.put(chunk)
        final = chunker.flush()
        if final:
            await tts_text_queue.put(final)
    """

    def __init__(self) -> None:
        self._buffer: str = ""
        self._word_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_token(self, token: str) -> Optional[str]:
        """
        Append a token to the internal buffer.

        Returns a chunk string if it is time to emit, or None to buffer further.

        Emit conditions (checked in order):
          1. word_count >= MAX_WORDS  -> force emit (hard cutoff)
          2. word_count >= MIN_WORDS AND buffer ends with HARD_SPLIT_CHAR
             AND word before punctuation NOT in NEVER_SPLIT_AFTER
        """
        if not token:
            return None

        self._buffer += token
        self._word_count += _count_words(token)

        # Condition 1: hard cutoff
        if self._word_count >= MAX_WORDS:
            return self._emit()

        # Condition 2: sentence boundary
        if self._word_count >= MIN_WORDS:
            stripped = self._buffer.rstrip()
            if stripped and stripped[-1] in HARD_SPLIT_CHARS:
                if not _ends_with_abbreviation(stripped):
                    return self._emit()

        return None

    def flush(self) -> Optional[str]:
        """
        Return any remaining buffered text when the LLM stream ends.

        Always emits whatever is buffered (even < MIN_WORDS) so the tail of
        a response is never silently dropped.
        """
        if self._buffer.strip():
            return self._emit()
        return None

    def reset(self) -> None:
        """Clear state between turns (e.g. after barge-in cancels mid-stream)."""
        self._buffer = ""
        self._word_count = 0

    @property
    def buffer(self) -> str:
        return self._buffer

    @property
    def word_count(self) -> int:
        return self._word_count

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit(self) -> str:
        chunk = self._buffer.strip()
        self._buffer = ""
        self._word_count = 0
        return chunk


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _count_words(text: str) -> int:
    """Count whitespace-delimited tokens in text."""
    return len(text.split())


def _ends_with_abbreviation(text: str) -> bool:
    """
    Return True if the word immediately before a trailing . ! ? is in
    NEVER_SPLIT_AFTER — indicating a false sentence boundary.

    Examples:
      "See Dr."      -> True  (Dr. is an abbreviation, don't split)
      "at 10am."     -> False (ok to split)
      "on Mon."      -> True  (day abbreviation, don't split)
      "ready now."   -> False (ok to split)
      "e.g. this"    -> False (already has space after period, handled by regex)
    """
    match = _LAST_WORD_RE.search(text)
    if not match:
        return False
    word = match.group(1).lower().rstrip(".")
    return word in NEVER_SPLIT_AFTER


def chunk_text_static(
    text: str,
    min_words: int = MIN_WORDS,
    max_words: int = MAX_WORDS,
) -> List[str]:
    """
    Split a complete text string into chunks using the same boundary rules
    as ResponseChunker.

    Used for non-streaming paths (greeting injection, filler phrases,
    fast-path responses) where the full text is available upfront.

    Returns a list of chunk strings. Always returns at least one chunk if
    text is non-empty.
    """
    if not text.strip():
        return []

    chunker = ResponseChunker()
    chunks: List[str] = []

    # Feed word-by-word to simulate token streaming
    words = text.split()
    for i, word in enumerate(words):
        token = (" " + word) if i > 0 else word
        result = chunker.add_token(token)
        if result:
            chunks.append(result)

    final = chunker.flush()
    if final:
        chunks.append(final)

    return chunks
