# WS-A — Chunk-Gate Implementation Pseudocode

**Companion to** `LATENCY_WS-A_CHUNK_GATE_SPEC.md`. **Pseudocode, not final code.**
**Base:** `latency-eval` (derived from `jv-v1-onboarding` — eval runs on the JV clinic).
**Live untouched. Flag default OFF = byte-behaviour-identical to live.**
**Date:** 2026-07-13

---

## 1. Reading the real code first changed the design

`chunker.py` (on the JV base, identical to live) delays the first chunk **two** ways,
not one:

1. **The 15-word gate** — a candidate only forms at `word_count >= MIN_WORDS (15)`
   AND a hard `.!?` boundary (`add_token`, condition 2).
2. **Hold-one-behind** (`_handle_candidate`) — even once a valid candidate forms,
   it is **not emitted**. It is stored in `_held_chunk`, and only released when the
   **next** valid candidate arrives (which confirms the boundary wasn't going to be
   merged with a following `FORBIDDEN_CHUNK_STARTER` fragment). `flush()` drains the
   last held chunk at stream end.

Net: today the first audio waits until the stream reaches **~two** sentence
boundaries (~30 words), or a flush. The 15-word gate is only half the story — the
hold-one-behind is often the larger first-chunk delay. WS-A must relax **both**,
for the **first chunk only**.

---

## 2. Constructor + new constants

```python
# PSEUDOCODE — chunker.py

# existing:
MIN_WORDS = 15
MAX_WORDS = 50

# new (config-driven; see §6). Defaults chosen so OFF == today.
MIN_WORDS_FIRST = 15          # overridden to ~6 only when the flag is ON

class ResponseChunker:
    def __init__(self, min_words_first: int = MIN_WORDS_FIRST) -> None:
        self._buffer = ""
        self._word_count = 0
        self._held_chunk = ""
        self._min_words_first = min_words_first
        self._emitted_count = 0        # NEW: how many chunks released this turn
```

`min_words_first` is a constructor arg (not a global read inside the loop) so it's
testable and per-instance. The streaming path builds a fresh `ResponseChunker()`
per turn (`llm_stream.py:1163`), so `_emitted_count` is naturally per-turn.

---

## 3. `add_token` — lower the gate for the first candidate only

```python
# PSEUDOCODE — add_token, replacing the fixed MIN_WORDS check

self._buffer += token
self._word_count += _count_words(token)

# Condition 1: hard cutoff (unchanged)
if self._word_count >= MAX_WORDS:
    return self._handle_candidate(self._emit())

# Condition 2: sentence boundary — threshold depends on whether this is the
# first chunk of the turn.
gate = self._min_words_first if self._emitted_count == 0 else MIN_WORDS
if self._word_count >= gate:
    stripped = self._buffer.rstrip()
    if stripped and stripped[-1] in HARD_SPLIT_CHARS:
        if not _ends_with_abbreviation(stripped):
            return self._handle_candidate(self._emit())

return None
```

The abbreviation guard (`_ends_with_abbreviation`) and the decimal guard stay in
force — a low gate never splits "Dr." or "£7.50".

---

## 4. `_handle_candidate` — release the FIRST chunk immediately (skip hold)

This is the change that actually removes the big delay. For the first chunk, emit
straight away instead of holding it one behind — **but only if its own opening is
valid** (never emit a chunk that itself starts with a forbidden phrase).

```python
# PSEUDOCODE — _handle_candidate

if not candidate or not candidate.strip():
    return self._held_chunk or None

# FIRST-CHUNK FAST RELEASE: bypass hold-one-behind for chunk 0 so first audio
# isn't gated on a SECOND boundary. Only when the candidate's own opening is safe.
if self._emitted_count == 0 and not _starts_with_forbidden(candidate):
    self._emitted_count += 1
    return candidate.strip()            # emit now; nothing held

# ...existing behaviour unchanged for everything after the first chunk...
if _starts_with_forbidden(candidate):
    self._held_chunk = ((self._held_chunk + " " + candidate).strip()
                        if self._held_chunk else candidate.strip())
    if len(self._held_chunk) > _MAX_HOLD_CHARS:
        ...  # existing force-emit-at-word-boundary logic, unchanged
        return to_emit
    return None
else:
    to_emit = self._held_chunk or None
    self._held_chunk = candidate
    if to_emit:
        self._emitted_count += 1        # NEW: count released chunks
    return to_emit
```

Two edits to the existing `else` branch only: the first-chunk fast-release block
at the top, and incrementing `_emitted_count` when a held chunk is actually
released (so the "first chunk" state ends at the right moment on the normal path
too, e.g. when the flag is OFF and `min_words_first == 15`).

---

## 5. `flush()` and `reset()`

- **`flush()`** — unchanged. If the whole reply is shorter than the gate, flush
  still returns the tail (so a 4-word reply is never lost). Optionally increment
  `_emitted_count` there for symmetry; not required.
- **`reset()`** — already exists (clears buffer/held). **Add** `self._emitted_count = 0`
  so a chunker reused after a mid-stream barge-in re-arms the fast opener for the
  next turn.

```python
def reset(self) -> None:
    self._buffer = ""
    self._word_count = 0
    self._held_chunk = ""
    self._emitted_count = 0      # NEW
```

---

## 6. Config + flag wiring (retire the dead constant)

```python
# PSEUDOCODE — config.py
# The old dead line `MIN_CHUNK_WORDS = 8` is REMOVED (it was imported by nobody).
WS_A_FAST_FIRST_CHUNK = os.getenv("WS_A_FAST_FIRST_CHUNK", "false").lower() == "true"
WS_A_MIN_WORDS_FIRST  = int(os.getenv("WS_A_MIN_WORDS_FIRST", "6"))

# effective value passed to the chunker:
#   flag OFF -> 15  (identical to live)
#   flag ON  -> WS_A_MIN_WORDS_FIRST (default 6, env-sweepable)
EFFECTIVE_MIN_WORDS_FIRST = WS_A_MIN_WORDS_FIRST if WS_A_FAST_FIRST_CHUNK else MIN_WORDS
```

```python
# PSEUDOCODE — llm_stream.py (_stream_claude, ~:1163)
from .config import EFFECTIVE_MIN_WORDS_FIRST
chunker = ResponseChunker(min_words_first=EFFECTIVE_MIN_WORDS_FIRST)
```

When OFF: `min_words_first == 15` AND the first-chunk fast-release block in §4
still triggers — but at 15 words, which is the same word count the old hold path
would have used, one candidate earlier. **Note:** even OFF, §4 changes *hold*
timing (first chunk released immediately rather than held one behind). If strict
byte-identical-to-live is required for the OFF baseline, gate the §4 fast-release
block on `WS_A_FAST_FIRST_CHUNK` too:

```python
if self._fast_first and self._emitted_count == 0 and not _starts_with_forbidden(candidate):
    ...
```

with `self._fast_first = WS_A_FAST_FIRST_CHUNK` passed in. **Recommended** — keeps
the OFF baseline provably identical, so the A/B compares one variable.

---

## 7. The one real risk (what the listen-back gate is for)

Releasing chunk 1 immediately removes the hold-one-behind protection **at the
boundary between chunk 1 and chunk 2**. Residual risk: chunk 2 begins with a
`FORBIDDEN_CHUNK_STARTER` (a bare time-phrase like "in the morning…") that, under
today's behaviour, would have been merged *backward* into chunk 1.

Why it's low for the first chunk specifically: the opener of a reply is almost
always an acknowledgement ("Right, let me check that.") or a direct answer, not a
sentence that a time-phrase fragment trails. The forbidden-starter machinery
mostly fires deeper in slot-listing replies, which are chunks 2..n and keep full
protection.

**Guardrail (from the WS-A spec §5):** listen-back on ≥30 openers, specifically
including a slot-offer reply whose second sentence leads with a time phrase. If any
opener lands an orphaned time-fragment, either raise `WS_A_MIN_WORDS_FIRST` or drop
the §4 fast-release and accept the smaller (gate-only) win.

---

## 8. Test cases (unit-level, before the live call test)

| Input token stream (→ = boundary) | Expected first emit | Checks |
|---|---|---|
| "Right, let me check that for you. →" then more | "Right, let me check that for you." at ~6 words, **immediately** | gate + fast-release |
| "See Dr. Marcus first. →" | not split at "Dr." | abbreviation guard holds at low gate |
| "The first slot is Monday. In the morning at ten. →" | chunk 1 = "The first slot is Monday."; verify chunk 2 not orphaned | forbidden-starter residual risk |
| 4-word whole reply, no boundary | returned by `flush()` | short reply not lost |
| barge-in mid-stream → `reset()` → next turn | fast opener re-arms | `_emitted_count` reset |
| flag OFF | first emit identical to current live timing | baseline integrity |

Unit tests are appropriate here (deterministic string in → string out) even though
Susie is normally validated by live call — the chunker is pure and the boundary
logic is exactly where a low gate could regress. Live-call + listen-back is still
required on top (§7).

---

## 9. Measurement hook

Proof metric `chunk_gate_ms = t2 − t1` (from the wiring spec) will capture this
directly: t2 is the first `tts_text_queue.put`, which under WS-A fires a full
candidate earlier (no hold) and at a lower word count. Expect the largest single
leftward shift of any lever here.

---

## 10. Summary of the diff surface

- `chunker.py`: 1 constructor arg + `_emitted_count`; `gate` selection in `add_token`; first-chunk fast-release + release-count in `_handle_candidate`; one line in `reset()`.
- `config.py`: remove dead `MIN_CHUNK_WORDS`; add flag + `WS_A_MIN_WORDS_FIRST` + `EFFECTIVE_MIN_WORDS_FIRST`.
- `llm_stream.py`: pass `min_words_first=` (and `fast_first=`) into the per-turn `ResponseChunker(...)`.

No change to tool handling, barge-in, dedup, TTS transport, or endpointing. Nothing
promotes to `main`/live.
