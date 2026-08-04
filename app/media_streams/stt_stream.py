# app/media_streams/stt_stream.py
"""
AssemblyAI Universal Streaming STT integration.

v3 (primary): wss://streaming.assemblyai.com/v3/ws
  Auth:    Authorization: <api_key> header  (server-to-server, recommended by AssemblyAI)
           NOTE: ?token= is for TEMPORARY tokens from /v3/token, NOT the raw API key.
  Input:   PCM16 16kHz mono (upsampled from Twilio 8kHz in audio_in.py)
  Events (v3 message types):
    {"type": "Begin",       "id": "...", "expires_at": ...}           → session ready
    {"type": "Turn",        "transcript": "...", "end_of_turn": bool} → speech
    {"type": "Termination", "audio_duration_seconds": ...}            → session ended

v2 (fallback, ASSEMBLYAI_USE_V2=true): wss://api.assemblyai.com/v2/realtime/ws
  Auth:    Authorization: <api_key> header
  Input:   PCM16 8kHz mono
  Events:  PartialTranscript, FinalTranscript

Audio chunk sizing (AssemblyAI v3 requires 50–1000ms per send):
  Twilio mulaw is 20ms frames → audio_in.py converts to PCM16 and buffers to 60ms.
  The keepalive was 10ms (320 bytes), violating the 50ms minimum.
  AudioChunkBuffer accumulates all audio — real chunks and keepalives — to 100ms
  (3200 bytes) before sending. The buffer lives on the STTStream instance so it
  persists across reconnects (no audio lost during the reconnect window).

Audio gating:
  _send_audio_loop blocks until connection_ready is set.
  connection_ready is set when the "Begin" message arrives from AssemblyAI.
  Prevents sending audio before the session handshake completes.

Reconnect classification:
  Immediate disconnect (< 0.5s) → auth/config rejection.
    3 consecutive immediate disconnects → log FATAL, play failure phrase, give up.
  Late disconnect (>= 0.5s) → network drop.
    Retry up to ASSEMBLYAI_MAX_RECONNECTS times.

Diagnostics:
  First 10 messages received from AssemblyAI are logged verbatim at DEBUG level.
  Close frame codes and reasons are logged on all disconnects.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from itertools import zip_longest
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import quote as _url_quote

import websockets
import websockets.exceptions

from .config import (
    ASSEMBLYAI_API_KEY,
    ASSEMBLYAI_WS_URL,
    ASSEMBLYAI_WS_URL_V2,
    ASSEMBLYAI_USE_V2,
    ASSEMBLYAI_USE_U35,
    ASSEMBLYAI_MAX_RECONNECTS,
    NOISE_ONLY_WORDS,
    U35_DEFORMAT,
    assemblyai_ws_url,
)
# WS-C endpoint-latency measurement (latency-eval branch). Read-once flag; when
# OFF every stamp below is skipped on one falsy check — no hot-path cost.
from .latency_timing import LATENCY_TIMING as _LAT_ON

logger = logging.getLogger(__name__)

AsyncCallback = Callable[..., Coroutine[Any, Any, None]]

# ---------------------------------------------------------------------------
# Keyterm boosting — sent as ?keyterms_prompt= at connection time (v3)
#
# 2026-07-24: this list was previously named _WORD_BOOST_TERMS and was DEAD.
# Its only sender, _send_word_boost(), was disconnected when v3 started
# rejecting the post-Begin JSON message with close code 3006, and the terms
# were never migrated to the URL parameter that replaced it.  The live boost
# was a hardcoded ["Alcester", "Redditch"] — two Theorem clinic names — so on
# every other clinic nothing useful was boosted at all.
#
# Cost of that: "calf" came back from AssemblyAI as "car" and then "coffee" on
# two consecutive JV test calls (2026-07-24).  clinical_screening's DVT screen
# triggers on the literal token "calf", so the deterministic red-flag layer
# never armed and the life-safety path fell through to the model alone.
#
# This list is GENERIC clinical + British-idiom vocabulary — it is domain
# knowledge, not clinic knowledge, so it legitimately lives in engine code.
# Clinic proper nouns (names, practitioners, locations, STT variants) and the
# clinic's own red-flag trigger vocabulary come from clinic.json via
# build_keyterms() below.  Do NOT add a clinic name here.
# ---------------------------------------------------------------------------

# ORDER IS LOAD-BEARING.  The combined list is truncated at _KEYTERMS_MAX, and
# a clinic with a large screening vocabulary (jv_v1: 100 terms, at the cap)
# only ever receives this list's opening entries.  Clinical vocabulary is said
# on every call and is what the booking flow depends on; the dialect block is a
# comprehension nicety and is also regionally wrong for half the estate (Vital
# Edge is Kingston-upon-Thames — "nesh" and "gradely" buy it nothing).  So:
# anatomy and clinical terms first, idiom last.  Reordered 2026-07-24 after
# jv_v1 was measured spending all 23 of its surviving generic slots on dialect
# and losing "knee", "hip", "shoulder", "physio" and "assessment".
_GENERIC_KEYTERMS: list[str] = [
    # Body parts and physio conditions
    "knee", "hip", "shoulder", "ankle",
    "spine", "elbow", "wrist", "neck",
    "back", "hamstring", "calf", "achilles",
    "rotator", "cuff", "sciatic", "sciatica",
    "plantar", "fasciitis", "tendon", "tendonitis",
    "frozen shoulder", "tennis elbow", "golfer's elbow",
    "ligament", "meniscus", "cartilage",
    # Physio / clinical terms
    "physiotherapy", "physio", "assessment",
    "physiotherapist", "musculoskeletal",
    "rehabilitation", "osteopath", "acupuncture",
    "shockwave", "psychotherapy", "laser",
    "prescribing", "Pilates",
    # Northern English / informal affirmatives (Bug #8)
    "aye", "yeah", "yep", "nah", "nowt",
    "owt", "summat", "reight", "sorted",
    "champion", "mint", "sound",
    "me back", "me knee", "me shoulder",
    "gi'o'er", "nesh", "gradely", "mardy",
    "ta", "cheers", "right",
    # NOTE: clinic proper nouns (Theorem/Alcester/Redditch/Kinwarton/Greig/
    # Mark/Leanne/Dyer and their nearby towns) used to be hardcoded here.
    # They now come from each clinic's own clinic.json — see build_keyterms().
    # British phrases
    "fortnight", "whilst", "fortnightly",
    "go on then", "right then", "fair enough",
    "no bother", "no worries", "half past",
    "quarter past", "quarter to",
]


# AssemblyAI v3 keyterms_prompt limits.  Conservative: the documented ceiling
# is 100 terms of up to 6 words each.  Over-long terms are dropped rather than
# truncated — a half-term boosts nothing and costs a slot.
_KEYTERMS_MAX = 100
_KEYTERM_MAX_WORDS = 6
_KEYTERM_MAX_CHARS = 50


# Common English words carry no boosting value and would burn the 100-term
# budget.  JV alone has 167 screening keywords ("stiff in the morning", "both
# hands"); boosted whole, they crowd out two entire screens' vocabulary.  The
# recognition win is in the distinctive WORD — AssemblyAI boosting "calf" helps
# it hear "calf" inside any phrase — so screening phrases are reduced to their
# distinctive tokens and the phrases themselves are not sent.
# Boosting only helps words the model would otherwise get WRONG.  Ordinary
# English is never mis-heard, so every common word in this set is a wasted slot
# — and slots are the scarce resource that starved a whole screen.
_KEYTERM_STOPWORDS: frozenset = frozenset({
    # articles / prepositions / conjunctions
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "with",
    "if", "as", "by", "from", "into", "onto", "over", "under", "about",
    "after", "before", "since", "while", "than", "then",
    # pronouns / determiners
    "my", "me", "i", "it", "its", "he", "his", "she", "her", "him", "they",
    "them", "their", "you", "your", "we", "our", "this", "that", "these",
    "those", "there", "here", "any", "all", "both", "each", "some",
    "several", "multiple", "anything", "something", "everything", "myself",
    # auxiliaries / very common verbs
    "is", "are", "was", "were", "be", "been", "being", "am", "have", "has",
    "had", "do", "does", "did", "can", "cant", "cannot", "will", "wont",
    "would", "could", "should", "get", "got", "go", "goes", "went", "gone",
    "come", "came", "take", "took", "make", "made", "put", "keep", "kept",
    "hold", "held", "use", "used", "done", "give", "gave", "seem", "seems",
    # common adverbs / adjectives / quantities
    "no", "not", "so", "very", "just", "really", "quite", "much", "many",
    "more", "less", "most", "least", "good", "bad", "big", "small", "long",
    "short", "little", "bit", "up", "out", "down", "off", "when", "still",
    "also", "only", "even", "own", "same", "other", "another", "one", "two",
    "hour", "hours", "day", "days", "week", "weeks", "time", "times",

    # ── Ordinary English that happens to appear in screening phrases ────────
    # Measured 2026-07-24: the three shipped clinics' screening vocabulary
    # reduces to 121 distinct tokens against a 100-term API cap, so the tiers
    # below the triggers were being truncated away entirely — jv_v1 kept only
    # 5 of the generic clinical terms and lost "physio", "physiotherapy",
    # "assessment", "knee", "hip" and "shoulder", words said on every call.
    #
    # Selection rule, applied strictly: a term earns a slot only if STT is
    # plausibly going to get it WRONG.  Clinical significance is NOT the
    # criterion — "swollen" and "warm" are red-flag answer keywords and both
    # transcribed perfectly on the incident calls, while "calf" (short,
    # low-information, many near-neighbours) did not.  Everything below is
    # ordinary, high-frequency English with no confusable clinical neighbour.
    # Anatomical, pathological and mechanism-of-injury words are deliberately
    # NOT here — those keep their slots.
    "away", "back", "backs", "bed", "below", "badly", "drive", "drop",
    "dropping", "fall", "fallen", "fell", "feel", "feeling", "generally",
    "half", "hand", "hands", "heard", "history", "hot", "huge", "hurt",
    "landed", "leg", "legs", "lost", "loss", "losing", "low", "lower",
    "massive", "morning", "mornings", "move", "night", "pain", "red", "rest",
    "shape", "sides", "sleep", "sore", "straight", "things", "turn", "wake",
    "walk", "warm", "weight", "wet", "wrong", "black", "double", "angle",
    "ache", "aching",
})


def _distinctive_tokens(phrase: str) -> list[str]:
    """Reduce a keyword phrase to the words worth boosting.

    Drops stopwords and 1-2 character fragments. Order preserved so the
    caller's priority ordering survives.
    """
    out: list[str] = []
    for word in re.split(r"[^a-z0-9']+", (phrase or "").lower()):
        word = word.strip("'")
        if len(word) < 3 or word in _KEYTERM_STOPWORDS:
            continue
        out.append(word)
    return out


def _collect_strings(value: Any, out: list[str]) -> None:
    """Flatten str / list / dict-of-those into *out*. Ignores everything else."""
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, list):
        for v in value:
            _collect_strings(v, out)
    elif isinstance(value, dict):
        for k, v in value.items():
            _collect_strings(k, out)
            _collect_strings(v, out)


# Keys in ``stt_variants`` that name a CATEGORY rather than a spoken term.
# "clinic_name" and "services" are schema, not vocabulary — boosting the
# literal string "clinic_name" is nonsense and costs a capped slot.  This is
# knowledge of the config *schema*, not of any one clinic, so it belongs here.
_STT_VARIANT_STRUCTURAL_KEYS: frozenset = frozenset({
    "clinic_name", "services", "practitioners", "locations", "brand_names",
})


def _collect_canonical_keys(value: Any, out: list[str]) -> None:
    """Collect only the KEYS of a variants mapping.

    ``stt_variants`` maps a canonical term to the ways STT has been heard to
    mangle it ("joint venture physio" -> "joint vencher physio", "fizzy-oh").
    Boosting a mangling asks AssemblyAI to EMIT it, which is the opposite of
    the intent — only the canonical keys belong in keyterms.

    Two shapes appear in the wild, so both are handled:
      "bolton":   [...variants]              -> key IS the canonical term
      "services": {"deep_tissue_massage": [...]}  -> key is a category

    Category keys are skipped and recursed into.  Nested keys are slugs
    ("deep_tissue_massage"), which nobody says out loud, so underscores and
    hyphens become spaces to recover the spoken form.
    """
    if not isinstance(value, dict):
        return
    for k, v in value.items():
        if isinstance(k, str) and k.lower() not in _STT_VARIANT_STRUCTURAL_KEYS:
            term = re.sub(r"[_\-]+", " ", k).strip()
            if term:
                out.append(term)
        # Nested groupings ("services": {"acupuncture": [...]}) — recurse
        # into dicts for their keys, but never collect list values.
        if isinstance(v, dict):
            _collect_canonical_keys(v, out)


def build_keyterms(clinic: Optional[dict]) -> list[str]:
    """Build the ``keyterms_prompt`` list for one clinic.

    Priority order matters — the list is capped at _KEYTERMS_MAX and truncated
    from the end, so the most consequential vocabulary goes first:

      1. clinical_screening trigger + red-flag answer keywords.  These decide
         whether the deterministic red-flag layer arms at all; a miss here is
         a safety-path miss, which is exactly how "calf" -> "coffee" defeated
         the DVT screen on 2026-07-24.
      2. clinic proper nouns — name, brand names, practitioner, locations and
         the clinic's own hand-written stt_variants.  Unguessable by a general
         model, and wrong ones send the caller to the wrong site.
      3. generic physio / British-idiom vocabulary (_GENERIC_KEYTERMS).

    Pure and side-effect free so the composition is testable without a socket.
    Passing None (clinic unresolved) yields the generic list alone.
    """
    clinic = clinic or {}
    triggers: list[str] = []
    answers: list[str] = []
    proper_nouns: list[str] = []

    # Screens are interleaved round-robin rather than concatenated.  JV has six
    # screens and more vocabulary than the cap allows; concatenating gave the
    # first five everything and the sixth (inflammatory) nothing at all — the
    # same silent-starvation failure this fix exists to remove.  Round-robin
    # degrades every screen a little instead of deleting one entirely.
    #
    # Triggers rank above answers: a trigger miss means the screen never arms
    # and the question is never asked, which is strictly worse than an answer
    # miss (where the question WAS asked and the caller's reply is still read
    # by the model).
    screening = clinic.get("clinical_screening") or {}
    _trigger_rows: list[list[str]] = []
    _answer_rows: list[list[str]] = []
    for screen in screening.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        for key, rows in (
            ("trigger_keywords", _trigger_rows),
            ("red_flag_answer_keywords", _answer_rows),
        ):
            phrases: list[str] = []
            _collect_strings(screen.get(key), phrases)
            tokens: list[str] = []
            for phrase in phrases:
                tokens.extend(_distinctive_tokens(phrase))
            rows.append(tokens)
    for rows, sink in ((_trigger_rows, triggers), (_answer_rows, answers)):
        for row in zip_longest(*rows):
            sink.extend(t for t in row if t)

    for key in ("clinic_name", "brand_names", "practitioner"):
        _collect_strings(clinic.get(key), proper_nouns)
    _collect_canonical_keys(clinic.get("stt_variants"), proper_nouns)
    for loc in clinic.get("locations") or []:
        if isinstance(loc, dict):
            _collect_strings(loc.get("name"), proper_nouns)
            _collect_strings(loc.get("serves_areas"), proper_nouns)

    # Tier 4/5 — vocabulary that is not derived from the clinic's own structure.
    #
    # _GENERIC_KEYTERMS was written for JV: a Yorkshire PHYSIOTHERAPY clinic. It
    # boosts "physiotherapy", "osteopath", "acupuncture", "shockwave", "Pilates"
    # and Yorkshire dialect ("nowt", "owt", "reight", "mardy", "gradely").
    # Applied to Vital Edge — a massage-only clinic in Kingston upon Thames —
    # it spent ~30 of the 100 slots boosting words for treatments Jonathan
    # declines and a dialect 200 miles from his callers, crowding out the
    # massage vocabulary that decides whether a booking is heard correctly.
    # Measured on the first live U3.5 call, 2026-08-04.
    #
    # A clinic may now supply its own list via clinic.json:
    #     "stt_keyterms": {"use_generic": false, "terms": [...]}
    # Absent the key, behaviour is byte-identical to before — the generic list
    # alone — so no existing clinic moves.
    _kt_cfg = clinic.get("stt_keyterms") or {}
    _clinic_terms: list[str] = []
    _collect_strings(_kt_cfg.get("terms"), _clinic_terms)
    _generic = (
        list(_GENERIC_KEYTERMS) if _kt_cfg.get("use_generic", True) else []
    )

    tiers: list[list[str]] = [
        triggers, proper_nouns, answers, _clinic_terms, _generic,
    ]

    seen: set[str] = set()
    out: list[str] = []
    for tier in tiers:
        for term in tier:
            term = (term or "").strip()
            if not term or len(term) > _KEYTERM_MAX_CHARS:
                continue
            if len(term.split()) > _KEYTERM_MAX_WORDS:
                continue
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(term)
            if len(out) >= _KEYTERMS_MAX:
                return out
    return out


_STT_FAILURE_PHRASE = (
    "I'm really sorry, I'm having a small technical issue right now. "
    "Please call back in a moment and I'll be ready to help you."
)

# Connections that close faster than this are treated as protocol/auth rejections.
_IMMEDIATE_THRESHOLD_SEC = 0.5
_MAX_IMMEDIATE_STREAK    = 3


# ---------------------------------------------------------------------------
# Audio chunk buffer
# ---------------------------------------------------------------------------

class AudioChunkBuffer:
    """
    Accumulates PCM16 audio bytes until a full 100ms chunk is ready to send.

    AssemblyAI v3 requires chunks of 50–1000ms.
    Incoming chunks are smaller:
      - Real audio: ~60ms bursts from audio_in.py
      - Keepalive:   10ms silence (320 bytes) — the direct cause of the error

    Both real audio and keepalives flow through this buffer so AssemblyAI
    always receives correctly-sized frames.

    Math (PCM16 @ 16kHz):
      TARGET_BYTES = 16000 Hz * 2 bytes/sample * 100ms / 1000 = 3200 bytes
      MIN_BYTES    = 16000 Hz * 2 bytes/sample *  50ms / 1000 = 1600 bytes
    """
    SAMPLE_RATE        = 16000
    BYTES_PER_SAMPLE   = 2            # pcm_s16le = 16-bit = 2 bytes per sample
    TARGET_DURATION_MS = 100
    TARGET_BYTES       = (SAMPLE_RATE * BYTES_PER_SAMPLE * TARGET_DURATION_MS) // 1000
    # = 16000 * 2 * 100 / 1000 = 3200 bytes
    MIN_BYTES          = (SAMPLE_RATE * BYTES_PER_SAMPLE * 50) // 1000
    # = 16000 * 2 *  50 / 1000 = 1600 bytes

    def __init__(self) -> None:
        self.buffer = bytearray()

    def add(self, chunk: bytes) -> Optional[bytes]:
        """
        Append chunk to the internal buffer.
        Returns a 100ms chunk if enough data has accumulated; None otherwise.
        Excess bytes are kept for the next call.
        """
        self.buffer.extend(chunk)
        if len(self.buffer) >= self.TARGET_BYTES:
            output       = bytes(self.buffer[:self.TARGET_BYTES])
            self.buffer  = bytearray(self.buffer[self.TARGET_BYTES:])
            return output
        return None

    def flush(self) -> Optional[bytes]:
        """
        Return whatever remains in the buffer if it meets the 50ms minimum.
        Discards the buffer in either case (too-small remainder would be
        rejected by AssemblyAI with an Input Duration Violation error).
        """
        if len(self.buffer) >= self.MIN_BYTES:
            output      = bytes(self.buffer)
            self.buffer = bytearray()
            return output
        # Too small to send — discard to avoid the duration violation
        if self.buffer:
            logger.debug(
                "[ms_stt] flush: discarding %d bytes (< 50ms minimum)",
                len(self.buffer),
            )
        self.buffer = bytearray()
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_garbage_transcript(text: str) -> bool:
    """Return True if transcript contains no recognisable words.

    Phone numbers, postcodes and dates are digit-heavy — allow them through
    rather than silently discarding.  3+ consecutive digits is a reliable
    signal that the caller said something meaningful (not just noise).
    """
    if not text.strip():
        return True
    # Allow digit-heavy input (phone fragments, postcodes, dates, etc.)
    if re.search(r'\d{3,}', text):
        return False
    words = re.findall(r"[a-zA-Z]{2,}", text.lower())
    real_words = [w for w in words if w not in NOISE_ONLY_WORDS]
    return len(real_words) == 0


# U3.5 Pro has no format_turns switch — formatting is always on, so finals
# arrive as "My name is Sarah." where every matcher in this engine expects
# "my name is sarah".  Three consumers read the transcript raw and break on that
# (phone-number capture in connection.py, the name-wrapper patterns in flow.py,
# _NAME_AFTER_IS_RE in name_collector.py), so we undo the formatting here — one
# place at the socket boundary — instead of chasing every anchored regex.
#
# Apostrophes and hyphens are KEPT: "it's", "o'brien" and "smith-jones" are
# load-bearing in the name and yes/no matchers.  Digits are kept as-is so
# "07502211207." still satisfies ^\d{5,}$ after the trailing stop is removed —
# but see _U35_DIGIT_GROUPS_RE below: stripping punctuation is only half of it,
# because the formatter also splits a long number across words.
# Em-dash, en-dash and horizontal bar are included; the ASCII hyphen is NOT.
# U3.5 punctuates self-corrections with em-dashes — measured live 2026-08-04:
#     'um well just— i want— just want a deep tissue massage'
# and that breaks name extraction outright: name_collector._NAME_AFTER_IS_RE
# finds 'sarah' in "my name is sarah" and NOTHING in "my name is— sarah".
#
# ⚠️ This also corrects the note above ASSEMBLYAI_WS_URL_U35 in config.py.
# Sending format_turns=false did NOT stop U3.5 formatting — the finals above
# arrived from a socket that requested it. The original in-repo claim that
# formatting is unconditional was closer to the truth than the API reference
# implied, so U35_DEFORMAT is load-bearing, not belt-and-braces.
#
# The ASCII hyphen stays: 'smith-jones' and '90-minute' are both load-bearing,
# and neither has ever been a disfluency marker.
_U35_PUNCT_RE = re.compile(r"[.,!?;:\"“”„…—–―]+")

# Stripping punctuation is not enough on its own.  U3.5's formatter also GROUPS
# long digit runs ("07502 211207"), and connection.py's phone path is
# _PHONE_NUMBER_RE = ^\d{5,}$ guarded by len(words) == 1 — so a grouped number is
# two words, fails the match, and is discarded by _is_short_meaningless_fragment
# exactly as the punctuated one was.  Measured 2026-08-04: with the shim on but
# without this rejoin, "07502 211207." and "0750 221 1207." were both DISCARDED.
#
# Only a whole utterance of digit groups is rejoined, and only at >= 7 digits.
# Both limits are deliberate:
#   - whole-utterance keeps "i'm 34 years old" untouched;
#   - the 7-digit floor keeps a spoken time ("9 30" -> would become "930") and a
#     year ("20 25" -> "2025") from being welded into a fake phone number.
# A UK mobile is 11 digits and a landline 10-11, so nothing real is excluded.
_U35_DIGIT_GROUPS_RE = re.compile(r"^\+?\d+(?: \d+)+$")


def _deformat_transcript(text: str) -> str:
    """Strip U3.5's always-on formatting back to the unformatted contract.

    Lowercases and removes sentence punctuation, collapsing any whitespace the
    removal leaves behind, then rejoins a grouped all-digit utterance.  A no-op
    for text that was never formatted, so it is safe if the model or the flag
    changes underneath it.

        "My name is Sarah."   -> "my name is sarah"
        "Yes, that's right!"  -> "yes that's right"
        "07502 211207."       -> "07502211207"
        "0750 221 1207."      -> "07502211207"
        "I'm 34 years old."   -> "i'm 34 years old"    (not all digits)
        "9 30."               -> "9 30"                (under the 7-digit floor)
    """
    if not text:
        return text
    out = " ".join(_U35_PUNCT_RE.sub(" ", text).split()).lower()
    if _U35_DIGIT_GROUPS_RE.match(out):
        joined = out.replace(" ", "")
        if len(joined.lstrip("+")) >= 7:
            return joined
    return out


def _mask_key(url_or_str: str, key: str) -> str:
    """Mask API key to first-8-chars + '...' for safe logging."""
    if not key:
        return url_or_str
    masked = key[:8] + "..." if len(key) > 8 else "***"
    return url_or_str.replace(key, masked)


def _close_info(exc: websockets.exceptions.ConnectionClosed) -> str:
    """Extract close code + reason from a ConnectionClosed exception."""
    if exc.rcvd:
        return f"code={exc.rcvd.code} reason={exc.rcvd.reason!r}"
    return "no close frame"


# ---------------------------------------------------------------------------
# STTStream
# ---------------------------------------------------------------------------

class STTStream:
    """
    Manages one AssemblyAI WebSocket session per call.

    start() opens the WebSocket and runs send + receive loops concurrently.
    Audio is held until the "Begin" message is received (connection_ready gate).
    The AudioChunkBuffer lives on the instance so it survives reconnects.
    """

    def __init__(self, clinic_id: Optional[str] = None) -> None:
        # Clinic whose vocabulary is boosted (keyterms_prompt).  Usually unset
        # at construction — connection.py builds STTStream before the Twilio
        # "start" event resolves the clinic — so _stt_loop passes it to start().
        self._clinic_id:     Optional[str]      = clinic_id
        self._ws:            Optional[Any]      = None
        self._last_final_at: float              = 0.0
        # Instance-level buffer: persists across reconnects so no audio is
        # lost during the reconnect window.
        self._chunk_buffer:  AudioChunkBuffer   = AudioChunkBuffer()
        # WS-C endpoint-latency measurement (only touched when _LAT_ON).
        # _t_last_partial: monotonic time of the most recent non-empty partial;
        # _last_endpoint_wait_ms: t_end_of_turn - t_last_partial for the last
        # final, read by connection at turn dispatch. -1 = not yet measured.
        self._t_last_partial:        float      = 0.0
        self._last_endpoint_wait_ms: int        = -1

    def _resolve_clinic(self) -> Optional[dict]:
        """Load this call's clinic config, or None.

        Imported lazily (app.clinic_config pulls in the clinic loader) and
        never allowed to raise: a keyterm lookup failing must degrade to the
        generic vocabulary, never take down the STT connection.
        """
        if not self._clinic_id:
            return None
        try:
            from app.clinic_config import get_clinic
            return get_clinic(self._clinic_id)
        except Exception as exc:
            logger.warning(
                "[ms_stt] clinic %r not loaded for keyterms — using generic "
                "vocabulary only (%r)", self._clinic_id, exc,
            )
            return None

    async def request_config_update(
        self, min_turn_silence: int, max_turn_silence: int
    ) -> bool:
        """WS-C: change AssemblyAI's turn-detection silence thresholds mid-session
        via an ``UpdateConfiguration`` message (v3 applies it without a reconnect).

        Called from the transcript-processing task while ``_send_audio_loop`` is
        running; websockets>=13 serialises concurrent ``send()``, so this is safe.
        A safe no-op (returns False) if the socket isn't open yet or has closed —
        never raises, so a config push can never take down the call.
        """
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send(json.dumps({
                "type": "UpdateConfiguration",
                "min_turn_silence": int(min_turn_silence),
                "max_turn_silence": int(max_turn_silence),
            }))
            logger.info(
                "[ms_stt] WS-C UpdateConfiguration sent: "
                "min_turn_silence=%d max_turn_silence=%d",
                min_turn_silence, max_turn_silence,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[ms_stt] WS-C config update failed (non-fatal): %r", exc
            )
            return False

    async def start(
        self,
        stt_input_queue: asyncio.Queue,
        transcript_queue: asyncio.Queue,
        stop_event: asyncio.Event,
        on_partial: Optional[AsyncCallback] = None,
        on_final_clear: Optional[AsyncCallback] = None,
        tts_text_queue: Optional[asyncio.Queue] = None,
        clinic_id: Optional[str] = None,
    ) -> None:
        """
        Open AssemblyAI WebSocket and run send + receive concurrently.

        Parameters
        ----------
        stt_input_queue  : Queue of PCM16 bytes to forward to AssemblyAI
        transcript_queue : Queue where final transcript strings are placed
        stop_event       : Set when the call ends
        on_partial       : async(text: str) called on partial Turn (barge-in)
        on_final_clear   : async(text: str) called on each end-of-turn to reset _clearing
        tts_text_queue   : If set, failure phrase is played here on fatal STT error
        clinic_id        : Clinic whose vocabulary is boosted; resolved from the
                           Twilio "start" event, so it arrives here rather than
                           at construction. Falls back to the ctor value.
        """
        if clinic_id:
            self._clinic_id = clinic_id
        # ── Auth: raw API key in Authorization header (server-to-server) ──────
        # ?token= in the URL is for *temporary* browser tokens — NOT the raw key.
        # V2 > U3.5 > default; see config.assemblyai_ws_url() for why V2 wins.
        url        = assemblyai_ws_url()
        # min_turn_silence is set directly in the URL constant (config.py).
        # keyterms_prompt: JSON-encoded array boosted at the STT session level.
        # Built per-clinic from clinic.json (screening vocabulary first) plus
        # the generic clinical list — see build_keyterms().  This used to be a
        # hardcoded ["Alcester", "Redditch"], which boosted nothing on any
        # other clinic and let "calf" degrade to "coffee" past the DVT screen.
        # v3 only — v2 does not support this parameter.
        if not ASSEMBLYAI_USE_V2:
            _keyterms = build_keyterms(self._resolve_clinic())
            url += "&keyterms_prompt=" + _url_quote(json.dumps(_keyterms))
            logger.info(
                "[ms_stt] keyterms_prompt: %d terms for clinic=%r (first 5: %s)",
                len(_keyterms), self._clinic_id, _keyterms[:5],
            )
        ws_headers = {"Authorization": ASSEMBLYAI_API_KEY}

        masked_url = _mask_key(url, ASSEMBLYAI_API_KEY)
        audio_fmt  = "pcm_s16le@16kHz" if not ASSEMBLYAI_USE_V2 else "pcm_s16le@8kHz"
        # stt_variant is grep-able per call: the A/B is worthless if a transcript
        # cannot be attributed to the model that produced it.
        stt_variant = (
            "v2" if ASSEMBLYAI_USE_V2
            else "u3.5-pro" if ASSEMBLYAI_USE_U35
            else "universal-streaming-english"
        )
        logger.info(
            "[ms_stt] init — stt_variant=%s url=%s audio=%s chunk_target=%dms(%dB)",
            stt_variant, masked_url, audio_fmt,
            AudioChunkBuffer.TARGET_DURATION_MS, AudioChunkBuffer.TARGET_BYTES,
        )

        attempt          = 0
        immediate_streak = 0
        backoff_delays   = [0.5, 1.0, 2.0]

        while not stop_event.is_set():
            attempt          += 1
            connection_ready  = asyncio.Event()   # fresh per attempt
            connect_time      = 0.0

            logger.info("[ms_stt] connecting attempt=%d", attempt)

            try:
                async with websockets.connect(
                    url,
                    additional_headers=ws_headers,
                    ping_interval=5,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws     = ws
                    connect_time = time.monotonic()
                    logger.info("[ms_stt] TCP+TLS connected attempt=%d", attempt)

                    send_task = asyncio.create_task(
                        self._send_audio_loop(
                            ws, stt_input_queue, stop_event,
                            connection_ready, self._chunk_buffer,
                        ),
                        name="stt_send",
                    )
                    recv_task = asyncio.create_task(
                        self._receive_results_loop(
                            ws, transcript_queue, stop_event,
                            on_partial=on_partial,
                            on_final_clear=on_final_clear,
                            connection_ready=connection_ready,
                        ),
                        name="stt_recv",
                    )

                    done, pending = await asyncio.wait(
                        {send_task, recv_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

                    if stop_event.is_set():
                        return

                    for t in done:
                        try:
                            exc = t.exception()
                            if exc:
                                logger.warning("[ms_stt] task raised: %r", exc)
                        except (asyncio.CancelledError, asyncio.InvalidStateError):
                            pass

                    # ── Classify disconnect ────────────────────────────────────
                    duration = time.monotonic() - connect_time
                    if duration < _IMMEDIATE_THRESHOLD_SEC:
                        immediate_streak += 1
                        logger.warning(
                            "[ms_stt] immediate disconnect %.3fs — "
                            "likely protocol/config rejection "
                            "(streak=%d/%d) url=%s",
                            duration, immediate_streak, _MAX_IMMEDIATE_STREAK,
                            masked_url,
                        )
                        if immediate_streak >= _MAX_IMMEDIATE_STREAK:
                            logger.error(
                                "[ms_stt] FATAL: %d immediate disconnects — "
                                "AssemblyAI rejecting connection. "
                                "Check API key and URL params. URL: %s",
                                immediate_streak, masked_url,
                            )
                            _notify_stt_failure(tts_text_queue)
                            return
                    else:
                        immediate_streak = 0
                        logger.warning(
                            "[ms_stt] connection closed after %.1fs — will reconnect",
                            duration,
                        )

            except websockets.exceptions.ConnectionClosedError as exc:
                logger.warning("[ms_stt] ConnectionClosedError %s", _close_info(exc))
                if connect_time > 0 and (time.monotonic() - connect_time) < _IMMEDIATE_THRESHOLD_SEC:
                    immediate_streak += 1
                    if immediate_streak >= _MAX_IMMEDIATE_STREAK:
                        logger.error(
                            "[ms_stt] FATAL: %d consecutive immediate disconnects",
                            immediate_streak,
                        )
                        _notify_stt_failure(tts_text_queue)
                        return
            except websockets.exceptions.WebSocketException as exc:
                logger.error("[ms_stt] WebSocketException: %r", exc)
            except OSError as exc:
                logger.error("[ms_stt] OS/network error: %r", exc)
            except Exception as exc:
                logger.error("[ms_stt] unexpected error: %r", exc)
            finally:
                self._ws = None

            if stop_event.is_set():
                return
            if attempt > ASSEMBLYAI_MAX_RECONNECTS:
                logger.error(
                    "[ms_stt] max reconnects (%d) reached — giving up",
                    ASSEMBLYAI_MAX_RECONNECTS,
                )
                _notify_stt_failure(tts_text_queue)
                return

            delay = 2.0 if immediate_streak > 0 else (
                backoff_delays[min(attempt - 1, len(backoff_delays) - 1)]
            )
            logger.info(
                "[ms_stt] reconnecting in %.1fs (attempt %d)...",
                delay, attempt + 1,
            )
            await asyncio.sleep(delay)

    # -------------------------------------------------------------------------
    # Send loop
    # -------------------------------------------------------------------------

    async def _send_audio_loop(
        self,
        ws: Any,
        stt_input_queue: asyncio.Queue,
        stop_event: asyncio.Event,
        connection_ready: asyncio.Event,
        chunk_buffer: AudioChunkBuffer,
    ) -> None:
        """
        Wait for the "Begin" message, then stream buffered PCM16 to AssemblyAI.

        All audio — both real speech and keepalive silence — passes through
        chunk_buffer before being sent. This ensures every send is exactly
        100ms (3200 bytes), which is safely within AssemblyAI's 50–1000ms window.

        chunk_buffer is the instance-level AudioChunkBuffer from STTStream so it
        persists across reconnects — buffered audio is not lost on reconnect.
        """
        # Block until AssemblyAI confirms the session is ready
        try:
            await asyncio.wait_for(connection_ready.wait(), timeout=5.0)
            logger.info("[ms_stt] send: Begin received — audio stream open")
        except asyncio.TimeoutError:
            logger.warning(
                "[ms_stt] send: no Begin within 5s — "
                "sending audio anyway (check Begin message handling)"
            )

        # Flush any audio that accumulated in the buffer DURING the handshake.
        # On reconnects this prevents stale pre-Begin audio from corrupting the
        # new session (which would make the first real word appear cut-off because
        # AssemblyAI was confused by out-of-context audio at the session start).
        # flush() sends if >= 50ms, silently discards if too short.
        pre_begin = chunk_buffer.flush()
        if pre_begin:
            logger.info(
                "[ms_stt] post-reconnect flush: %d bytes (%.1fms) → new session",
                len(pre_begin),
                len(pre_begin) / (AudioChunkBuffer.SAMPLE_RATE
                                  * AudioChunkBuffer.BYTES_PER_SAMPLE) * 1000,
            )
            try:
                await ws.send(pre_begin)
            except Exception:
                return
        else:
            if chunk_buffer.buffer:
                logger.debug(
                    "[ms_stt] post-reconnect: discarded %d bytes (< 50ms minimum)",
                    len(chunk_buffer.buffer),
                )
                chunk_buffer.buffer = bytearray()

        # 10ms silence at 16kHz PCM16 = 320 bytes.
        # Goes through chunk_buffer so it never reaches AssemblyAI at 10ms size.
        KEEPALIVE_BYTES = bytes(320)

        first_send_done = False

        try:
            while not stop_event.is_set():
                try:
                    pcm_chunk = await asyncio.wait_for(
                        stt_input_queue.get(), timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    # No audio — feed silence keepalive through the buffer
                    buffered = chunk_buffer.add(KEEPALIVE_BYTES)
                    if buffered is not None:
                        try:
                            await ws.send(buffered)
                        except Exception:
                            return
                    continue

                if not pcm_chunk:
                    continue

                # Route real audio through buffer
                buffered = chunk_buffer.add(pcm_chunk)
                if buffered is not None:
                    if not first_send_done:
                        first_send_done = True
                        logger.info(
                            "[ms_stt] first chunk sent: %d bytes = %.1fms",
                            len(buffered),
                            len(buffered) / (AudioChunkBuffer.SAMPLE_RATE
                                             * AudioChunkBuffer.BYTES_PER_SAMPLE) * 1000,
                        )
                    try:
                        await ws.send(buffered)
                    except websockets.exceptions.ConnectionClosed:
                        logger.warning("[ms_stt] send: connection closed")
                        return
                    except Exception as exc:
                        logger.error("[ms_stt] send error: %r", exc)
                        return

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_stt] _send_audio_loop error: %r", exc)
        finally:
            # Flush any remaining buffered audio before closing (call ended).
            # Only attempt if stop_event is set (clean call end) so we don't
            # try to send on an already-dead reconnect socket.
            if stop_event.is_set():
                remainder = chunk_buffer.flush()
                if remainder:
                    try:
                        await ws.send(remainder)
                        logger.debug(
                            "[ms_stt] flushed %d bytes on call end", len(remainder),
                        )
                    except Exception:
                        pass  # WebSocket may already be closing — best effort only

    # -------------------------------------------------------------------------
    # Receive loop
    # -------------------------------------------------------------------------

    async def _receive_results_loop(
        self,
        ws: Any,
        transcript_queue: asyncio.Queue,
        stop_event: asyncio.Event,
        on_partial: Optional[AsyncCallback] = None,
        on_final_clear: Optional[AsyncCallback] = None,
        connection_ready: Optional[asyncio.Event] = None,
    ) -> None:
        """
        Route AssemblyAI v3 events.

        v3 message types (field: "type"):
          Begin       → set connection_ready, log session details
          Turn        → end_of_turn=false: partial (barge-in trigger)
                        end_of_turn=true:  final   (enqueue transcript)
                        NOTE: text is in "transcript" field (NOT "text")
          Termination → session ended normally
          error       → log and exit

        v2 message types (field: "message_type") handled for fallback:
          PartialTranscript, FinalTranscript
        """
        msg_count = 0  # log first N messages verbatim for diagnostics

        try:
            async for raw_msg in ws:
                if stop_event.is_set():
                    break

                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    logger.warning("[ms_stt] non-JSON: %r", str(raw_msg)[:80])
                    continue

                # Log first 10 messages in full for connection diagnostics
                msg_count += 1
                if msg_count <= 10:
                    logger.debug("[ms_stt] msg#%d raw=%r", msg_count, msg)

                # v3 uses "type"; v2 uses "message_type"
                msg_type = msg.get("type") or msg.get("message_type") or ""

                # v3 text is in "transcript"; v2 uses "text"
                text = (
                    msg.get("transcript") or msg.get("text") or ""
                ).strip()

                # U3.5 formats unconditionally — undo it here so every consumer
                # downstream sees the same unformatted text it always has.
                # Applied to partials too: the barge-in noise gate and the
                # first-turn extractor both read partials.
                if text and ASSEMBLYAI_USE_U35 and U35_DEFORMAT:
                    _raw_text = text
                    text = _deformat_transcript(text)
                    if text != _raw_text:
                        logger.debug(
                            "[ms_stt] deformat %r -> %r", _raw_text[:60], text[:60]
                        )

                # Per-message diagnostic log (debug level — high frequency)
                if msg_type not in ("Begin", "SessionBegins", "session_begins", "Termination"):
                    logger.debug("[ms_stt] msg type=%s text=%r", msg_type, text[:60] if text else "")

                # ── v3: Begin (session ready) ──────────────────────────────────
                if msg_type == "Begin":
                    logger.info(
                        "[ms_stt] Begin received — session_id=%s expires_at=%s — "
                        "unblocking audio stream",
                        msg.get("id"), msg.get("expires_at"),
                    )
                    if connection_ready is not None:
                        connection_ready.set()
                    # NOTE: _send_word_boost(ws) was called here but AssemblyAI
                    # v3 rejects the post-Begin JSON message with close code 3006
                    # (invalid message type), killing the STT connection.  Word
                    # boost for v3 must be configured via URL query parameters
                    # at connection time — not as a WebSocket message.
                    # That migration is now done: see build_keyterms() and the
                    # &keyterms_prompt= assembly in start().

                # ── v2 compat: SessionBegins ───────────────────────────────────
                elif msg_type in ("SessionBegins", "session_begins"):
                    logger.info(
                        "[ms_stt] SessionBegins session_id=%s — unblocking audio stream",
                        msg.get("session_id"),
                    )
                    if connection_ready is not None:
                        connection_ready.set()

                # ── v3: Turn (partial or final) ────────────────────────────────
                elif msg_type == "Turn":
                    end_of_turn = msg.get("end_of_turn", False)

                    if not end_of_turn:
                        # Partial — trigger barge-in if caller is speaking
                        if text and on_partial:
                            try:
                                await on_partial(text)
                            except Exception as exc:
                                logger.warning("[ms_stt] on_partial error: %r", exc)
                        # WS-C: mark the last time the caller was still speaking,
                        # so the endpoint silence (this → final) can be measured.
                        if _LAT_ON and text:
                            self._t_last_partial = time.monotonic()
                    else:
                        # Final — enqueue for LLM
                        if on_final_clear:
                            try:
                                await on_final_clear(text)
                            except Exception:
                                pass
                        self._last_final_at = time.monotonic()
                        # WS-C: endpoint_wait = silence the endpointer imposed
                        # after the caller's last word. Reset for the next turn.
                        if _LAT_ON and self._t_last_partial:
                            self._last_endpoint_wait_ms = int(
                                (self._last_final_at - self._t_last_partial) * 1000
                            )
                            self._t_last_partial = 0.0
                        if not text:
                            logger.debug("[ms_stt] empty Turn final — ignoring")
                            continue
                        if _is_garbage_transcript(text):
                            logger.info("[ms_stt] garbage transcript: %r", text)
                            continue
                        logger.info("[ms_stt] FINAL → queue: %r", text)
                        self._put_transcript(transcript_queue, text)

                # ── v2 compat: PartialTranscript ───────────────────────────────
                elif msg_type == "PartialTranscript":
                    if text and on_partial:
                        try:
                            await on_partial(text)
                        except Exception as exc:
                            logger.warning("[ms_stt] on_partial error: %r", exc)

                # ── v2 compat: FinalTranscript ─────────────────────────────────
                elif msg_type == "FinalTranscript":
                    if on_final_clear:
                        try:
                            await on_final_clear(text)
                        except Exception:
                            pass
                    self._last_final_at = time.monotonic()
                    if not text:
                        logger.debug("[ms_stt] empty FinalTranscript — ignoring")
                        continue
                    if _is_garbage_transcript(text):
                        logger.info("[ms_stt] garbage transcript: %r", text)
                        continue
                    logger.info("[ms_stt] FINAL → queue: %r", text)
                    self._put_transcript(transcript_queue, text)

                # ── v3: Termination (normal session end) ───────────────────────
                elif msg_type == "Termination":
                    logger.info(
                        "[ms_stt] Termination audio_duration=%.1fs session_duration=%.1fs",
                        msg.get("audio_duration_seconds", 0),
                        msg.get("session_duration_seconds", 0),
                    )
                    return

                # ── Error (any version) ────────────────────────────────────────
                elif msg_type == "error":
                    logger.error("[ms_stt] AssemblyAI error: %s", msg.get("error"))
                    return

                else:
                    logger.debug("[ms_stt] unhandled type=%r msg=%r", msg_type, msg)

        except websockets.exceptions.ConnectionClosedError as exc:
            logger.info("[ms_stt] receive: connection closed %s", _close_info(exc))
        except websockets.exceptions.ConnectionClosedOK as exc:
            logger.info("[ms_stt] receive: connection closed OK %s", _close_info(exc))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_stt] _receive_results_loop error: %r", exc)

    @staticmethod
    def _put_transcript(q: asyncio.Queue, text: str) -> None:
        """Put (enqueue_timestamp, text) onto transcript_queue; discard oldest if full."""
        item = (time.monotonic(), text)
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            q.put_nowait(item)
            logger.warning("[ms_stt] transcript_queue full -- discarded oldest")


# ---------------------------------------------------------------------------
# STT failure helper
# ---------------------------------------------------------------------------

def _notify_stt_failure(tts_text_queue: Optional[asyncio.Queue]) -> None:
    """
    Put the failure phrase on tts_text_queue so the caller hears something.
    Does not set stop_event — TTS plays the phrase and the caller hangs up naturally.
    """
    if tts_text_queue is not None:
        try:
            tts_text_queue.put_nowait(_STT_FAILURE_PHRASE)
            logger.info("[ms_stt] STT failure phrase queued")
        except Exception as exc:
            logger.warning("[ms_stt] could not queue failure phrase: %r", exc)
