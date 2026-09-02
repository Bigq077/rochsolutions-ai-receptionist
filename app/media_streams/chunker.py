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

# How far past MAX_WORDS the buffer may run to reach the end of the sentence
# it is in the middle of.
#
# The hard cutoff had no notion of sentence position, so it severed a clause
# wherever the 50th word happened to fall - and `sanitise_response` then
# capitalises every chunk head, promoting the stranded tail into a sentence
# of its own. Five of these reached callers (measured over 8,555 stored
# assistant chunks, 2026-09-01):
#
#   "...sorted with Marcus right"          -> "Away."
#   "...so he can take a proper"           -> "Look?"
#   "...for when you'd like to come"       -> "In?"   (twice)
#   "...a proper look at what's driving"   -> "It."
#
# Every one ended its sentence within two words of the cut, which is what
# makes a bounded grace the right shape: the chunker cannot see ahead, but it
# can afford to wait a little before giving up on the sentence.
#
# Costs nothing audible. A 50-word chunk is ~15s of speech and is already
# playing while the next one buffers, so waiting a few more tokens delays no
# audio; MAX_WORDS is a forward-progress guard, not a latency budget.
MAX_WORDS_SENTENCE_GRACE = 12

HARD_SPLIT_CHARS = frozenset({".", "!", "?"})

# WS-A first chunk ONLY. `chunk_gate_ms` — first LLM token to first chunk
# released — is floored by the time the model takes to generate a whole first
# SENTENCE, because Condition 2 below only stops at a hard `.!?`. Measured over
# 2,411 stored turns: p50 672ms, p90 1,975ms, and it is 17% of the caller's
# voice-to-voice budget. A clause boundary is a natural place for the voice to
# pause, so the first chunk can go a clause early and the caller hears the
# answer start sooner. Later chunks are untouched, so mid-response prosody is
# unchanged.
#
# Comma and semicolon only. NOT the em-dash: " — " is the head/filler
# separator, and NOT the ellipsis, because both are already load-bearing
# pause punctuation read by `split_tts_text` — splitting a synthesis call on
# them is the defect that once read a phone number across two TTS calls.
SOFT_FIRST_SPLIT_CHARS = frozenset({",", ";"})

# Spoken digits. A phone number reaches TTS as WORDS, not numerals — "oh seven
# five oh two, two one one, two oh seven" — so an `isdigit()` guard sees nothing
# and happily splits the number in half. That was caught on the first test of
# this change, and it is the same defect that once read a number across two
# synthesis calls with an audible seam in the middle.
_NUMBER_WORDS = frozenset({
    "oh", "o", "zero", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "double", "triple",
})


def _carries_a_number_sequence(text: str) -> bool:
    """True when *text* looks like it is reading digits aloud.

    A RUN of two or more adjacent number-words, not a single one: prose says
    "one of the things" and "a couple of weeks" all the time, and refusing to
    split on those would make the guard useless. Nothing says "seven five" in a
    row except a number being read out.
    """
    run = 0
    for w in text.lower().replace(",", " ").replace("-", " ").split():
        if w.strip(".!?;:") in _NUMBER_WORDS:
            run += 1
            if run >= 2:
                return True
        else:
            run = 0
    return False
SOFT_SPLIT_CHARS = frozenset({",", ";", ":"})   # only used at MAX_WORDS

# ---------------------------------------------------------------------------
# Forbidden chunk openers
# ---------------------------------------------------------------------------
# Time-of-day phrases and partial time lists that must never be the first
# words of a standalone TTS chunk.  A split producing one of these as the
# right-hand fragment is rejected; the boundary is extended rightward until
# a safe opening word is found.
#
# Used by both ResponseChunker (streaming path) and split_tts_text (barge-in
# sub-chunk path).
# ---------------------------------------------------------------------------

FORBIDDEN_CHUNK_STARTERS: list = [
    "in the morning",
    "in the afternoon",
    "in the evening",
    "ten or",
    "nine or",
    "eleven or",
    "or eleven",
    "or ten",
    "or nine",
]

# Hard upper bound on a merged hold chunk.  If the accumulated held text
# exceeds this, it is force-emitted at the nearest word boundary before
# the limit rather than growing indefinitely.
_MAX_HOLD_CHARS: int = 150


def _starts_with_forbidden(text: str) -> bool:
    """Return True if text (after leading whitespace) starts with any FORBIDDEN_CHUNK_STARTER."""
    lower = text.lstrip().lower()
    return any(lower.startswith(phrase) for phrase in FORBIDDEN_CHUNK_STARTERS)


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

    def __init__(
        self,
        min_words_first: int = MIN_WORDS,
        fast_first: bool = False,
    ) -> None:
        self._buffer: str = ""
        self._word_count: int = 0
        # Hold-and-merge: stores the most recently emitted candidate, releasing
        # it only once the NEXT candidate arrives with a valid (non-forbidden)
        # opening.  If the next candidate starts with a FORBIDDEN_CHUNK_STARTER
        # it is merged into _held_chunk instead of being emitted separately.
        self._held_chunk: str = ""
        # WS-A (latency-eval, flag-gated): lower the gate for the FIRST chunk of
        # a turn and release it immediately (skip hold-one-behind), so time-to-
        # first-audio isn't gated on a second sentence boundary. When
        # fast_first is False both effects are inert -> byte-identical to live.
        self._min_words_first: int = min_words_first
        self._fast_first: bool = fast_first
        self._emitted_count: int = 0  # chunks released this turn (per-instance)

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

        After producing a candidate chunk, boundary validation is applied via
        _handle_candidate():
          - If the candidate starts with a FORBIDDEN_CHUNK_STARTER it was
            orphaned from its day-label context; it is merged into _held_chunk
            rather than emitted.
          - If the candidate has a valid opening, the previously held chunk
            (if any) is released and the new candidate is held in its place.
        """
        if not token:
            return None

        self._buffer += token
        self._word_count += _count_words(token)

        # Condition 1: hard cutoff - but finish the sentence if it is close.
        # See MAX_WORDS_SENTENCE_GRACE. Past MAX_WORDS we stop adding words
        # speculatively and start looking for the sentence end; the first one
        # inside the grace wins, and if none arrives we cut as before.
        if self._word_count >= MAX_WORDS:
            _s = self._buffer.rstrip()
            # A COMMA counts as somewhere to stop, not just a full stop.
            # Without it the grace is unsafe on the one shape that really
            # does run long: a slot readout is a comma-separated list with no
            # full stop in it ("half past ten in the morning, twenty past
            # eleven in the morning, ..."). The longest punctuation-free run
            # in 8,555 stored chunks is 60 words - two short of the grace
            # ceiling - and a readout that ran three words longer would push
            # its emission past the point where hold-and-merge can release
            # it, turning the whole turn into one very long TTS call.
            #
            # With a comma accepted, a readout cuts at the first comma after
            # MAX_WORDS - a natural pause - and never approaches the ceiling,
            # while an ordinary sentence still gets its word or two of grace.
            _at_end = (
                bool(_s)
                and (_s[-1] in HARD_SPLIT_CHARS or _s[-1] == ",")
                and not _ends_with_abbreviation(_s)
            )
            if _at_end or self._word_count >= MAX_WORDS + MAX_WORDS_SENTENCE_GRACE:
                return self._handle_candidate(self._emit())
            return None

        # Condition 2: sentence boundary.  The threshold is lowered for the
        # first chunk of the turn only (WS-A); every later chunk keeps MIN_WORDS,
        # so mid-response prosody is unchanged.  fast_first=False => gate is
        # always MIN_WORDS (identical to live).
        gate = (
            self._min_words_first
            if (self._fast_first and self._emitted_count == 0)
            else MIN_WORDS
        )
        if self._word_count >= gate:
            stripped = self._buffer.rstrip()
            if stripped and not _ends_with_abbreviation(stripped):
                if stripped[-1] in HARD_SPLIT_CHARS:
                    return self._handle_candidate(self._emit())
                # WS-A first chunk: a clause is somewhere to stop too.
                #
                # Gated on the text carrying NO DIGITS, which is the whole
                # safety argument. A slot readout and a phone read-back are
                # comma-separated by construction ("oh seven five oh two, two
                # one one, two oh seven"), and splitting one across two
                # synthesis calls is a known live defect — the caller hears the
                # number in two pieces with a seam in the middle. Prose openers
                # ("Hamstring tightness after training is really common in
                # active people,") carry no digits and are exactly the case
                # this is for.
                if (
                    self._fast_first
                    and self._emitted_count == 0
                    and stripped[-1] in SOFT_FIRST_SPLIT_CHARS
                    and not any(ch.isdigit() for ch in stripped)
                    and not _carries_a_number_sequence(stripped)
                ):
                    return self._handle_candidate(self._emit())

        return None

    def flush(self) -> Optional[str]:
        """
        Return any remaining buffered text when the LLM stream ends.

        Combines any held chunk (awaiting boundary validation) with whatever
        remains in the buffer so nothing is silently dropped.
        Always emits (even < MIN_WORDS) so the tail of a response is returned.
        """
        remaining = self._buffer.strip()
        self._buffer = ""
        self._word_count = 0

        if remaining and self._held_chunk:
            combined = (self._held_chunk + " " + remaining).strip()
            self._held_chunk = ""
            return combined
        if remaining:
            return remaining
        if self._held_chunk:
            held = self._held_chunk
            self._held_chunk = ""
            return held
        return None

    def reset(self) -> None:
        """Clear state between turns (e.g. after barge-in cancels mid-stream)."""
        self._buffer = ""
        self._word_count = 0
        self._held_chunk = ""
        self._emitted_count = 0  # re-arm WS-A fast opener for the next turn

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

    def _handle_candidate(self, candidate: str) -> Optional[str]:
        """
        Apply hold-and-merge boundary validation before releasing a chunk.

        If candidate starts with a FORBIDDEN_CHUNK_STARTER:
          Fold it into _held_chunk.  If the combined text exceeds
          _MAX_HOLD_CHARS, force-emit up to the nearest word boundary before
          the limit and keep the remainder in _held_chunk.

        If candidate has a valid opening:
          Release the previously held chunk (if any) as the return value and
          store candidate as the new held chunk for the next round.

        The "hold one behind" pattern means the most recently validated chunk
        is always in _held_chunk and is only released when the next chunk
        confirms the boundary is safe.  flush() drains whatever remains.
        """
        if not candidate or not candidate.strip():
            return self._held_chunk or None

        if _starts_with_forbidden(candidate):
            # Orphaned fragment — merge with held context
            self._held_chunk = (
                (self._held_chunk + " " + candidate).strip()
                if self._held_chunk
                else candidate.strip()
            )
            if len(self._held_chunk) > _MAX_HOLD_CHARS:
                # Force-emit at nearest word boundary before hard limit
                split_pos = self._held_chunk.rfind(" ", 0, _MAX_HOLD_CHARS)
                if split_pos > 0:
                    to_emit = self._held_chunk[:split_pos].strip()
                    self._held_chunk = self._held_chunk[split_pos:].strip()
                else:
                    to_emit = self._held_chunk[:_MAX_HOLD_CHARS].strip()
                    self._held_chunk = self._held_chunk[_MAX_HOLD_CHARS:].strip()
                return to_emit
            return None
        else:
            # WS-A first-chunk fast release: emit chunk 0 immediately instead of
            # holding it one behind, so first audio isn't gated on a SECOND
            # boundary.  Safe here because this branch already means the
            # candidate's own opening is non-forbidden; the guard on an empty
            # _held_chunk prevents reordering if anything is already held.
            if (
                self._fast_first
                and self._emitted_count == 0
                and not self._held_chunk
            ):
                self._emitted_count += 1
                return candidate.strip()

            # Valid start — release held chunk, hold this candidate
            to_emit = self._held_chunk or None
            self._held_chunk = candidate
            if to_emit:
                self._emitted_count += 1
            return to_emit


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


_DECIMAL_RE = re.compile(r"\d\.\d")  # e.g. 10.30, £7.50 — don't split on these periods


_SPLIT_MIN_LEFT  = 40  # left fragment must be ≥ this to be a valid split point
_SPLIT_MIN_RIGHT = 20  # trailing fragment kept separate only if ≥ this length

_CLAUSE_BOUNDARIES = frozenset(".?!,;—–")


def _find_split_point(text: str, max_chars: int) -> int:
    """
    Find the best split position at or before max_chars.

    Never splits mid-word. Search order:
      1. Clause/sentence boundary (.?!,;—) followed by a space — best prosody
      2. Any space — word boundary fallback
      3. max_chars — last resort (only if no space exists at all)
    """
    if len(text) <= max_chars:
        return len(text)

    # 1. Clause boundary: scan backwards from max_chars for punct+space
    for i in range(max_chars, 0, -1):
        if text[i - 1] in _CLAUSE_BOUNDARIES and i < len(text) and text[i] == " ":
            return i

    # 2. Word boundary: scan backwards for any space
    for i in range(max_chars, 0, -1):
        if text[i - 1] == " ":
            return i

    # 3. Last resort — character limit (no spaces found)
    return max_chars


def split_tts_text(text: str, max_chars: int = 90) -> List[str]:
    """
    Split a TTS phrase into sub-chunks at natural spoken boundaries.

    Used by _tts_loop to shorten the audio in-flight when barge-in fires.
    Shorter sub-chunks mean Twilio's buffer holds at most ~2-3s of audio
    instead of ~6-7s for a long deterministic phrase.

    max_chars=90 ≈ 15 words ≈ ~3 seconds of ElevenLabs audio.
    Short texts (≤ max_chars) are returned unchanged as a single-item list.

    Split priority (only applied when len(text) > max_chars):
      1. " — " (em-dash + space) — clinic phrases like "I can do X — which works?"
         Only when left fragment ≥ _SPLIT_MIN_LEFT (avoids splitting "Just to confirm —")
      2. "? " / "! " / ". " — sentence boundaries (abbreviation + decimal guards)
      3. ", " — only when left fragment already ≥ max_chars // 2 chars
      4. _find_split_point() — word-boundary fallback; never splits mid-word

    Trailing fragments under _SPLIT_MIN_RIGHT chars are merged into the
    preceding sub-chunk (avoids unnatural TTS prosody on very short inputs).
    """
    text = text.strip()
    if not text or len(text) <= max_chars:
        return [text] if text else []

    parts: List[str] = []
    remaining = text

    while len(remaining) > max_chars:
        split_at: int = -1

        # Priority 1: em-dash " — "
        # Scan ALL em-dashes (not just the first) to find one whose right
        # fragment does not start with a FORBIDDEN_CHUNK_STARTER.
        _em = remaining.find(" — ")
        while _em != -1:
            candidate_left = remaining[: _em + len(" — ")].strip()
            if len(candidate_left) >= _SPLIT_MIN_LEFT:
                right_frag = remaining[_em + len(" — "):]
                if not _starts_with_forbidden(right_frag):
                    split_at = _em + len(" — ")
                    break
            _em = remaining.find(" — ", _em + 1)

        # Priority 2: sentence boundaries (? ! .)
        # Reject any boundary whose right fragment starts with a forbidden phrase.
        if split_at == -1:
            for marker in ("? ", "! ", ". "):
                idx = remaining.find(marker)
                while idx != -1:
                    # Skip decimals like 10.30 or 7.50
                    if marker == ". " and _DECIMAL_RE.search(remaining[max(0, idx - 2): idx + 2]):
                        idx = remaining.find(marker, idx + 1)
                        continue
                    candidate = remaining[: idx + 1]
                    right_frag = remaining[idx + len(marker):]
                    if (
                        not _ends_with_abbreviation(candidate)
                        and idx >= _SPLIT_MIN_LEFT
                        and not _starts_with_forbidden(right_frag)
                    ):
                        split_at = idx + len(marker)
                        break
                    idx = remaining.find(marker, idx + 1)
                if split_at != -1:
                    break

        # Priority 3: comma — only if left side is already long enough
        if split_at == -1:
            idx = remaining.find(", ")
            if idx != -1 and idx >= max_chars // 2:
                right_frag = remaining[idx + len(", "):]
                if not _starts_with_forbidden(right_frag):
                    split_at = idx + len(", ")

        # Priority 4: word-boundary fallback — never split mid-word.
        # If the fallback position produces a forbidden right fragment, scan
        # rightward for the nearest word boundary with a safe opening, up to
        # _MAX_HOLD_CHARS.
        if split_at == -1:
            split_at = _find_split_point(remaining, max_chars)
            if _starts_with_forbidden(remaining[split_at:]):
                _safe = split_at
                for _ext in range(split_at + 1, min(len(remaining), _MAX_HOLD_CHARS) + 1):
                    if remaining[_ext - 1] == " " and not _starts_with_forbidden(remaining[_ext:]):
                        _safe = _ext
                        break
                split_at = _safe

        if split_at >= len(remaining):
            break

        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        parts.append(remaining)

    if not parts:
        return [text]

    # Merge-forward: trailing fragments under _SPLIT_MIN_RIGHT attach to predecessor
    merged: List[str] = [parts[0]]
    for frag in parts[1:]:
        if len(frag) < _SPLIT_MIN_RIGHT and merged:
            merged[-1] = merged[-1] + " " + frag
        else:
            merged.append(frag)

    return merged


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
