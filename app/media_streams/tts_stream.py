# app/media_streams/tts_stream.py
"""
ElevenLabs streaming TTS pipeline.

Two integration modes are provided:

MODE A -- HTTP streaming (REST, per-chunk):
  synthesise_chunk(text, audio_out_queue, audio_out_processor)
  Used by the tts_loop in connection.py.
  One POST request per text chunk from the LLM chunker.
  Shares a persistent httpx.AsyncClient (connection pooling, avoids TLS overhead).

MODE B -- WebSocket streaming (long-lived connection):
  start_ws(tts_text_queue, audio_out_queue)
  Keeps one ElevenLabs WebSocket open for the entire call.
  Sends text chunks incrementally; receives audio as it arrives.
  Better for very low-latency delivery of long responses.

Both modes convert ElevenLabs PCM16 16kHz output through AudioOutputProcessor
(tomono 2:1 decimation + lin2ulaw) and put base64 mulaw strings onto
audio_out_queue for the send_loop to forward to Twilio.

CRITICAL: output_format MUST be a URL query param, NOT a body field.
  Confirmed in realtime.py: body field is silently ignored and returns MP3.

ElevenLabs API details:
  HTTP endpoint: POST /v1/text-to-speech/{voice_id}/stream?output_format=pcm_16000
  WS endpoint:   wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input
                 ?model_id=eleven_flash_v2_5&output_format=pcm_16000
  Auth:          xi-api-key header (HTTP) or xi_api_key in init message (WS)
  Model:         eleven_flash_v2_5
  Chunk size:    640 bytes = 20ms of 16kHz PCM16
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re as _re
import time
from typing import Any, Optional

import httpx

from .config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_VOICE_ID,
    ELEVENLABS_MODEL_ID,
    ELEVENLABS_STABILITY,
    ELEVENLABS_SIMILARITY_BOOST,
    ELEVENLABS_SPEED,
    ELEVENLABS_HEAD_SPEED,
    ELEVENLABS_PHONE_SPEED,
    TTS_STREAM_CHUNK_SIZE,
)
from .audio_out import AudioOutputProcessor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TTS phonetic substitution table
# ---------------------------------------------------------------------------
# The LLM and routing logic always use the canonical spelling; only the audio
# synthesis layer receives the phonetic form where needed.
#
# ElevenLabs path: belt-and-suspenders phonetic substitution.
# Even though ElevenLabs may handle British place names, call logs confirmed
# it was mispronouncing "Alcester" without this rule active.  All hardcoded
# TTS strings also use "Awlstuh" directly, so this catches any LLM-generated
# "Alcester" that slips through.
_TTS_SUBSTITUTIONS_ELEVENLABS: list[tuple] = [
    (_re.compile(r"\bAlcester\b", _re.IGNORECASE), "Awlstuh"),
]

# P22b: spell out currency amounts as words before synthesis.
# eleven_flash_v2_5 ships with text normalization OFF (latency trade-off), so a
# bare "175 pounds" is read digit-wise / garbled — call logs showed exactly this
# on the £175 90-min readback. We normalise currency ourselves, deterministically,
# with no added latency. Scoped to money only (£-prefixed OR "<n> pounds") so dates
# ("the 15th") and already-spoken times are never touched.
#   £125          -> "one hundred and twenty-five pounds"
#   175 pounds    -> "one hundred and seventy-five pounds"
#   £12.50        -> "twelve pounds fifty"
_CURRENCY_RE = _re.compile(r"£\s*(\d+)(?:\.(\d{2}))?|\b(\d{1,4})\s+pounds\b")


def _int_to_words(n: int) -> str:
    ones = [
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen",
    ]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
            "eighty", "ninety"]
    if n < 20:
        return ones[n]
    if n < 100:
        t, o = divmod(n, 10)
        return tens[t] + (f"-{ones[o]}" if o else "")
    if n < 1000:
        h, rem = divmod(n, 100)
        return ones[h] + " hundred" + (f" and {_int_to_words(rem)}" if rem else "")
    th, rem = divmod(n, 1000)
    return _int_to_words(th) + " thousand" + (f" {_int_to_words(rem)}" if rem else "")


def _spell_currency(m: "_re.Match") -> str:
    if m.group(1) is not None:            # £-prefixed form
        pounds, pence = int(m.group(1)), m.group(2)
    else:                                  # "<n> pounds" form
        pounds, pence = int(m.group(3)), None
    words = f"{_int_to_words(pounds)} pounds"
    if pence:
        words += f" {_int_to_words(int(pence))}"
    return words


# P22c: spell phone numbers as words before synthesis.
# Same root cause as the currency rule above — eleven_flash_v2_5 runs with text
# normalization OFF, so a bare "07502 211 207" is read as one rushed digit run
# and callers cannot check it against their own number.  Step 8 of the template
# prompt asks the model to say the digits in three word-groups, but that is a
# prompt-side hope: when the model emits numerals instead, the readback muddles.
# This makes the pacing deterministic regardless of what the model produced.
#
# Scoped to UK phone shapes ONLY.  Everything else that carries digits on a call
# — "the 4th of August", "6:30", "45 minutes", a B49 postcode — must survive
# untouched, so the span has to start 0 or +44 and carry 10-13 digits.  A price
# list ("125, 175") is excluded by the prefix rule; £-prices are already words
# by the time this runs.
_PHONE_SPAN_RE = _re.compile(r"(?<![\d:])(\+?\d[\d\s,‑-]{7,24}\d)(?![\d:])")

# "oh", not "zero" — UK convention, and it matches the wording step 8 already
# asks the model for, so the two paths sound the same.
_DIGIT_WORDS = {
    "0": "oh",   "1": "one", "2": "two",   "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


def _spell_phone(m: "_re.Match") -> str:
    """Render a UK phone-shaped digit span as spoken words, grouped for pacing.

    Returns the span UNCHANGED whenever it is not confidently a phone number —
    a false negative just leaves today's behaviour, a false positive reads a
    date or a duration out digit-by-digit.

        "07502 211 207" -> "oh seven five oh two, two one one, two oh seven"
        "+447502211207" -> the same (spoken in the familiar 0-form)
    """
    span   = m.group(1)
    digits = _re.sub(r"\D", "", span)

    # UK only: 0-prefixed, or +44 which we speak back in the 0-form a caller
    # recognises rather than as "plus four four".
    _was_intl = False
    if span.lstrip().startswith("+") or digits.startswith("44"):
        if not digits.startswith("44"):
            return span
        digits   = "0" + digits[2:]
        _was_intl = True
    elif not digits.startswith("0"):
        return span

    if not (10 <= len(digits) <= 11):
        return span

    # Honour the grouping already in the text.  A landline is written 0121 496
    # 0000 and read that way; imposing the mobile 5/3/3 on it produces "oh one
    # two one four, nine six oh, oh oh oh", which is harder to check than the
    # rushed run this rule exists to fix.  Only a bare unseparated run gets the
    # default grouping — which mirrors flow._format_phone_readback, so the
    # caller-ID readback and the dictation readback stay paced alike.
    #
    # `max(len) >= 2` is the difference between GROUPING and digit-SPACING.
    # The Theorem prompt (config.py, step "Caller gives phone number") asks the
    # model for "0 7 8 7 0 1 6 6 8 6 1" — eleven one-digit tokens.  Read as
    # authored that becomes a comma after every single digit, which is neither
    # the three groups every other readback on the call uses nor something a
    # caller can hold in their head.  Spaced digits carry no grouping intent, so
    # they fall through to the 5/3/3 default like any other bare run.
    _authored = [g for g in _re.split(r"[\s,‑-]+", span.strip()) if g.isdigit()]
    if len(_authored) > 1 and not _was_intl and max(len(g) for g in _authored) >= 2:
        groups = _authored
    else:
        groups = [digits[:5], digits[5:8], digits[8:]]

    return _pace_digit_groups(groups)


def _pace_digit_groups(groups: "list[str]") -> str:
    """Render already-grouped digits as the spoken, paced form.

    The single owner of what a spoken phone number sounds like.  Both callers
    reach it — `_spell_phone` for numerals the model emitted, and
    `_respell_spoken_digits` for the word form the template prompt asks for —
    so a number is paced identically no matter which one produced it.

        ["07502", "211", "207"] -> "oh seven five oh two, two one one, two oh seven"

    The separator is a comma, and it must stay a comma.  " — " reads as a longer
    pause and was the obvious improvement, but chunker._SPLIT_MIN_LEFT is 40:
    given "I've got you on <number> — is that the best number for the booking?",
    split_tts_text finds the em-dash after the SECOND group at 52 chars, accepts
    it, and sends "two oh seven" to ElevenLabs as a separate request from the
    first eight digits.  The number would then straddle two synthesis calls,
    with an uncontrolled gap in the middle and a barge-in able to land between
    them.  "..." splits the same way (priority 2 matches the ". " inside it).
    Pacing that needs to be slower than a comma belongs in `speed`, not in
    punctuation the chunker also reads.
    """
    # No group should run longer than five digits without a pause, however it
    # was written — "01527 123456" needs a breath in the second half too.
    _paced: list[str] = []
    for g in groups:
        if len(g) > 5:
            _paced.extend(g[i:i + 3] for i in range(0, len(g), 3))
        elif g:
            _paced.append(g)

    # ", " between groups is the pause; ElevenLabs honours comma prosody even
    # with normalization off.
    return ", ".join(" ".join(_DIGIT_WORDS[d] for d in g) for g in _paced)


# The other half of the same defect.
#
# _spell_phone only ever fires on NUMERALS.  But the template prompt used by
# every template_v1 clinic (clinic_template_prompt.py, booking step 8 and the
# reschedule/cancel lookup step) asks the model for the already-spoken form —
# "I've got you on oh seven five oh two, two one one, two oh seven" — and the
# model generally complies.  There are no digits in that, so _spell_phone
# declines it, correctly, and the readback reaches ElevenLabs paced however the
# model happened to write it.  The booking, reschedule and cancel readbacks —
# the three the caller most needs to be able to check — were therefore the ones
# with NO deterministic pacing at all.
#
# So the word form is parsed back to digits and re-emitted through the same
# _pace_digit_groups as the numeral form.  Output is a fixed point: re-running
# the rule over its own output yields the same string, which matters because
# substitutions are applied twice on the live path (connection.py _tts_loop and
# again inside synthesise_chunk).
#
# Conservatism is the whole game here, exactly as with _PHONE_SPAN_RE.  The run
# must START on "oh"/"zero" and be at least ten digit-words long before it is
# touched.  That prefix rule is what keeps ordinary speech out: "one or two",
# "four or five minutes", "the first, the second" are nowhere near ten
# consecutive digit-words, and none of them open on a zero.
_WORD_DIGITS = {
    "oh": "0", "zero": "0",
    "one": "1", "two": "2",   "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}

_DIGIT_WORD_ALT = "|".join(_WORD_DIGITS)          # oh|zero|one|two|...
_RUN_SEP = r"[\s,\u2014\u2013-]+"                 # space, comma, em/en dash, hyphen

_SPOKEN_DIGIT_RUN_RE = _re.compile(
    rf"(?<![A-Za-z])((?:oh|zero)(?:{_RUN_SEP}(?:{_DIGIT_WORD_ALT})){{9,}})(?![A-Za-z])",
    _re.IGNORECASE,
)


# In the spoken form the comma is the GROUP separator and the space is the
# digit separator, which is the same distinction _spell_phone draws between
# authored grouping and bare digit-spacing — just written the other way round.
_RUN_GROUP_SEP = _re.compile(r"[,—–]+")


def _spoken_run_groups(span: str) -> "Optional[list[str]]":
    """Digit groups behind a spoken run, or None when it is not a UK number.

    Same acceptance rule as _spell_phone: 0-prefixed, 10 or 11 digits.  A run
    longer than a phone number (a model reading out a reference code, say) fails
    the length check and is left alone rather than guessed at.
    """
    groups: list[str] = []
    for chunk in _RUN_GROUP_SEP.split(span):
        words = [w for w in _re.split(r"[\s-]+", chunk.strip()) if w]
        if not words:
            continue
        digits = "".join(_WORD_DIGITS.get(w.lower(), "") for w in words)
        if len(digits) != len(words):
            return None
        groups.append(digits)

    joined = "".join(groups)
    if not joined.startswith("0") or not (10 <= len(joined) <= 11):
        return None
    return groups


def _respell_spoken_digits(m: "_re.Match") -> str:
    """Re-pace a spoken digit run into the canonical grouped form.

    Honours grouping the model already chose, and ignores mere digit-spacing —
    the same rule, and for the same reasons, as _spell_phone.  Without this the
    rule would re-impose 5/3/3 on the landline _spell_phone had just correctly
    left as 0121 / 496 / 0000, since the numeral rule runs first and this one
    reads its output.
    """
    span   = m.group(1)
    groups = _spoken_run_groups(span)
    if groups is None:
        return span
    if len(groups) > 1 and max(len(g) for g in groups) >= 2:
        return _pace_digit_groups(groups)
    digits = "".join(groups)
    return _pace_digit_groups([digits[:5], digits[5:8], digits[8:]])


def _is_spoken_phone_number(text: str) -> bool:
    """True when `text` reads a phone number back to the caller.

    Run on the FINAL, post-substitution text, where every producer of a readback
    has been normalised to the same spoken form — so this one predicate covers
    the numerals the model emitted, the words it emitted, and the deterministic
    keypad readback in connection.py alike.  Used only to choose a speaking
    rate: a false positive slows one utterance slightly, a false negative leaves
    today's pace, and neither can change what is said.
    """
    for m in _SPOKEN_DIGIT_RUN_RE.finditer(text):
        if _spoken_run_groups(m.group(1)) is not None:
            return True
    return False


# OpenAI fallback path.
# Alcester: British English /ˈɔːlstə/ — "AWL-stuh".
#   OpenAI TTS has no pronunciation dictionary so needs the phonetic form.
#   "Awlstuh": "Awl" → /ɔːl/ (rhymes with "ball"), "stuh" → /stə/ (schwa).
_TTS_SUBSTITUTIONS_OPENAI: list[tuple] = [
    (_re.compile(r"\bAlcester\b", _re.IGNORECASE), "Awlstuh"),
]


def _apply_tts_substitutions_elevenlabs(text: str) -> str:
    """
    Apply ElevenLabs-specific substitutions before synthesis.
    """
    for pattern, replacement in _TTS_SUBSTITUTIONS_ELEVENLABS:
        text = pattern.sub(replacement, text)
    # Currency first: it turns "£175" into words, so no price can still look
    # like a digit span to the phone rule below.
    text = _CURRENCY_RE.sub(_spell_currency, text)
    text = _PHONE_SPAN_RE.sub(_spell_phone, text)
    # Last: the numeral rule above emits the spoken form, so running the spoken
    # rule after it re-reads that output and confirms it is already canonical.
    # Ordering it the other way round would leave numerals unpaced.
    text = _SPOKEN_DIGIT_RUN_RE.sub(_respell_spoken_digits, text)
    return text


def _apply_tts_substitutions_openai(text: str) -> str:
    """
    Apply OpenAI-fallback substitutions before synthesis.

    Alcester IS substituted to "Awlstuh" here because the OpenAI TTS
    engine has no pronunciation dictionary and needs the phonetic form.
    """
    for pattern, replacement in _TTS_SUBSTITUTIONS_OPENAI:
        text = pattern.sub(replacement, text)
    text = _CURRENCY_RE.sub(_spell_currency, text)
    # Applied here too so the dev-bypass path does not sound different from the
    # ElevenLabs one; words are read as words by either engine.
    text = _PHONE_SPAN_RE.sub(_spell_phone, text)
    text = _SPOKEN_DIGIT_RUN_RE.sub(_respell_spoken_digits, text)
    return text


# ---------------------------------------------------------------------------
# Shared httpx client singleton
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ElevenLabs exhaustion flag (per process lifetime)
# ---------------------------------------------------------------------------

# Set True the first time ElevenLabs returns 401 (credits exhausted).
# All subsequent synthesise_chunk() calls skip ElevenLabs and go straight
# to the OpenAI TTS fallback.
_ELEVENLABS_EXHAUSTED: bool = False

# Set True if ElevenLabs ever rejects `speed` in voice_settings (422).  The
# parameter is model- and account-dependent, and the failure mode if it is not
# accepted is the worst one this system has: a 422 on the phone-readback turn
# returns from synthesise_chunk without enqueuing a single audio frame, so the
# caller hears silence in the middle of a booking.  Rather than risk that on a
# live call, the first rejection retries the same chunk without `speed` and
# latches the flag so no later chunk on this process pays the round trip.
# Same shape as _ELEVENLABS_EXHAUSTED above, deliberately.
_ELEVENLABS_SPEED_UNSUPPORTED: bool = False

# ---------------------------------------------------------------------------
# ElevenLabs pronunciation dictionary — REMOVED 2026-08-02 (B-14)
# ---------------------------------------------------------------------------
# There was a loader here that read config/pronunciation_dict.json for a
# {pronunciation_dictionary_id, version_id} pair and injected it into every
# synthesis request. It never once ran: the file on disk was
# {"Alcester": "Awlstuh"} — a word->alias map, not the locator pair the loader
# wanted — so the lookup always failed, the locator stayed None, and the request
# body was never touched. Removed rather than repaired, for three reasons:
#
#   1. The one word it demonstrably mattered for is already handled, locally and
#      deterministically, by _TTS_SUBSTITUTIONS_ELEVENLABS above. That rule
#      exists because call logs caught "Alcester" being mispronounced, and it
#      costs a regex rather than a round trip.
#   2. Turning it on would put pronunciation in TWO places that can disagree.
#      One vocabulary maintained in several copies is the standing failure
#      pattern of this codebase — see DEFECT_REGISTER.md §A4, where an
#      affirmative list lived in four places and each fix patched one of them.
#   3. It has never executed in production, so removing it cannot change a
#      call. ENABLING it would have been the risky move: an untested field on
#      every TTS request, three days before a demo.
#
# ⚠️ One thing the dictionary would have covered that nothing covers now:
# "Redditch" (the doubled-d artefact, per scripts/setup_pronunciation_dictionary.py).
# There is no local substitution for it. That is recorded as an open row in
# REGISTER_B_U.md rather than fixed here — adding a substitution would change
# spoken output on no current evidence, and Redditch may not even be live
# vocabulary on this branch.
#
# To re-enable: scripts/setup_pronunciation_dictionary.py still creates the
# dictionary and writes real locator IDs; restore the loader from this commit's
# parent and decide, first, which of the two mechanisms owns pronunciation.


_elevenlabs_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """
    Return the shared ElevenLabs HTTP client.

    Created once and reused for all TTS requests in the process.
    Persistent TLS connections save ~50-100ms per TTS call by avoiding
    re-handshake (same pattern as realtime.py).
    """
    global _elevenlabs_client
    if _elevenlabs_client is None or _elevenlabs_client.is_closed:
        _elevenlabs_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
                # 300s (was 30s): on a low-traffic line every call fell outside
                # a 30s window, so the pool was cold at each greeting and the
                # first synthesise_chunk paid full DNS+TLS setup — 1,989ms on
                # call CA717c7cc1 vs ~120ms for every later chunk on that call.
                # A 5-minute expiry keeps back-to-back calls on a warm socket.
                keepalive_expiry=300.0,
            ),
        )
    return _elevenlabs_client


async def prewarm() -> float:
    """Open a TLS connection to ElevenLabs so the first TTS of a call is fast.

    Fire-and-forget from the Twilio webhook: the greeting is synthesised ~40ms
    after the WebSocket start event, which is far too late to warm anything, but
    the webhook lands ~450ms earlier — enough for DNS + TLS to complete off the
    critical path and land a live socket in the pool.

    Returns elapsed seconds (0.0 if skipped/failed). Never raises — a cold pool
    is a latency problem, never a call-failure one.
    """
    if not ELEVENLABS_API_KEY:
        return 0.0
    started = time.monotonic()
    try:
        resp = await _get_http_client().get(
            "https://api.elevenlabs.io/v1/models",
            headers={"xi-api-key": ELEVENLABS_API_KEY},
            timeout=httpx.Timeout(4.0),
        )
        elapsed = time.monotonic() - started
        # B-13 (2 Aug 2026): the status was interpolated but never branched on,
        # so a 401 logged at INFO as "connection ready in 214ms (status=401)".
        # The socket genuinely IS warm on a 401 — that is why this read as a
        # success — but the credential behind it is dead, and this is the
        # earliest moment anything in the process can know. synthesise_chunk()
        # already treats the same 401 as logger.error + a switch to the OpenAI
        # fallback for the rest of the process lifetime; it just does not run
        # until a caller is on the line. Reporting it here costs nothing and
        # moves the discovery off the first live call.
        # B-26 (3 Aug 2026): the wording above was wrong on both counts and it
        # fired at ERROR on every single call. `/v1/models` is NOT the endpoint
        # synthesis uses — that is POST /v1/text-to-speech/{voice}/stream, which
        # returned 200 roughly thirty-five times across the three sweep calls of
        # 2 Aug and five more calls on 3 Aug, including seconds after a 401 here.
        # Exhausted credits would fail the stream endpoint too, so this reads as
        # a key scoped for TTS but not `models_read`. Naming a consequence that
        # does not occur, at ERROR, on every call, is worse than saying nothing:
        # it trains an operator to ignore the one channel that would carry a real
        # outage. Demoted to WARNING and reworded to state only what is known.
        if resp.status_code == 401:
            logger.warning(
                "[ms_tts] prewarm: ElevenLabs returned 401 on /v1/models after "
                "%.0fms. This does NOT predict a synthesis failure — synthesis "
                "uses POST /v1/text-to-speech/{voice}/stream and is unaffected "
                "by a key that lacks `models_read`. If TTS is genuinely failing "
                "you will see a 401 from synthesise_chunk; check for that before "
                "touching credits or rotating the key.",
                elapsed * 1000,
            )
        elif resp.status_code >= 400:
            logger.warning(
                "[ms_tts] prewarm: ElevenLabs returned %d after %.0fms — socket "
                "is warm but the API is not answering normally",
                resp.status_code, elapsed * 1000,
            )
        else:
            logger.info(
                "[ms_tts] prewarm: connection ready in %.0fms (status=%d)",
                elapsed * 1000, resp.status_code,
            )
        # Unchanged on every branch: a warm socket is a warm socket, and this
        # return feeds latency accounting, not health. Deliberately NOT arming
        # _ELEVENLABS_EXHAUSTED here — that is a behaviour change (it would move
        # the fallback decision off the synth path) and belongs in its own row.
        return elapsed
    except Exception as exc:
        logger.warning(
            "[ms_tts] prewarm failed after %.0fms: %r — greeting will pay "
            "cold-start latency", (time.monotonic() - started) * 1000, exc,
        )
        return 0.0


# ---------------------------------------------------------------------------
# TTSStream class
# ---------------------------------------------------------------------------

class TTSStream:
    """
    Handles ElevenLabs TTS for one phone call.

    Primary interface: synthesise_chunk() (MODE A, per-chunk HTTP streaming).
    Secondary interface: start_ws() (MODE B, persistent WebSocket).
    """

    def __init__(self, clinic_id: str = "") -> None:
        self._clinic_id = clinic_id       # Used for TTS_BYPASS_CLINIC gate
        self._ws: Optional[Any] = None   # WebSocket connection (MODE B only)

    # =========================================================================
    # MODE A -- HTTP streaming (per-chunk REST)
    # =========================================================================

    async def synthesise_chunk(
        self,
        text: str,
        audio_out_queue: asyncio.Queue,
        audio_out_processor: AudioOutputProcessor,
    ) -> None:
        """
        Send a single text chunk to ElevenLabs, stream PCM16 audio back,
        convert to mulaw 8kHz, and enqueue for Twilio delivery.

        This is a cancellable coroutine -- connection.py wraps it in an
        asyncio.Task so barge-in can cancel it cleanly.

        Transcode pipeline per raw chunk:
          ElevenLabs PCM16 16kHz -> AudioOutputProcessor.convert_chunk()
          -> base64 mulaw 8kHz -> audio_out_queue -> send_loop -> Twilio

        Parameters
        ----------
        text                : Text to synthesise (15-50 words from ResponseChunker)
        audio_out_queue     : Queue where base64 mulaw strings are placed
        audio_out_processor : Stateful AudioOutputProcessor (maintains 4-byte alignment)
        """
        if not text or not text.strip():
            return

        text = _apply_tts_substitutions_elevenlabs(text)

        # Dev bypass: TTS_BYPASS_CLINIC env var routes a specific clinic to
        # the OpenAI TTS fallback instead of ElevenLabs (cheaper for testing).
        _bypass_clinic = os.getenv("TTS_BYPASS_CLINIC", "")
        if _bypass_clinic and _bypass_clinic == self._clinic_id:
            logger.info(
                "[ms_tts] TTS_BYPASS_CLINIC=%r matches clinic_id — using OpenAI fallback",
                _bypass_clinic,
            )
            await self._synthesise_openai_fallback(text, audio_out_queue)
            return

        # Fast-path: ElevenLabs known-exhausted → use OpenAI TTS directly
        global _ELEVENLABS_EXHAUSTED, _ELEVENLABS_SPEED_UNSUPPORTED
        if _ELEVENLABS_EXHAUSTED:
            await self._synthesise_openai_fallback(text, audio_out_queue)
            return

        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
            f"?output_format=pcm_16000"
        )
        headers = {
            "xi-api-key":    ELEVENLABS_API_KEY,
            "Content-Type":  "application/json",
        }
        body: dict = {
            "text":       text,
            "model_id":   ELEVENLABS_MODEL_ID,
            "voice_settings": {
                "stability":        ELEVENLABS_STABILITY,
                "similarity_boost": ELEVENLABS_SIMILARITY_BOOST,
            },
        }

        # A phone number being read back is synthesised slower than the rest of
        # the call.  The decision is made HERE, on the post-substitution text,
        # rather than being passed in by connection.py: this is the only place
        # that has seen the final words, and keeping it here means the change
        # costs nothing in the 12k-line _tts_loop.
        #
        # `speed` is omitted entirely at 1.0 so a deployment that has not tuned
        # anything sends a request byte-identical to today's.
        # A hold head is the other exception, and for the opposite reason: not
        # careful articulation but ordinary pace. It is a short fragment
        # synthesised alone, so flash has no sentence to pace it against and
        # rushes it -- see ELEVENLABS_HEAD_SPEED. Decided here, on the final
        # text, like the phone case; `is_hold_head` matches the head pools
        # themselves rather than guessing from shape, because the chunker
        # legitimately emits short dash-terminated fragments of model speech and
        # slowing those would change the cadence of the whole call.
        _is_phone = _is_spoken_phone_number(text)
        _is_head = False
        if not _is_phone:
            try:
                from app.hold_speech import is_hold_head

                _is_head = is_hold_head(text)
            except Exception:  # pragma: no cover - never break synthesis on this
                _is_head = False
        if _is_phone:
            _speed = ELEVENLABS_PHONE_SPEED
        elif _is_head:
            _speed = ELEVENLABS_HEAD_SPEED
        else:
            _speed = ELEVENLABS_SPEED
        if abs(_speed - 1.0) > 1e-9 and not _ELEVENLABS_SPEED_UNSUPPORTED:
            body["voice_settings"]["speed"] = _speed

        logger.info(
            "[ms_tts] synthesise_chunk: model=%s len=%d speed=%s phone=%s "
            "head=%s text=%r",
            ELEVENLABS_MODEL_ID, len(text),
            body["voice_settings"].get("speed", "default"), _is_phone, _is_head,
            text[:60],
        )

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                async with _get_http_client().stream(
                    "POST", url, json=body, headers=headers,
                ) as resp:
                    if resp.status_code == 401:
                        # Credits exhausted — mark globally and fall back to OpenAI TTS
                        _ELEVENLABS_EXHAUSTED = True
                        logger.error(
                            "[ms_tts] ElevenLabs 401 — credits exhausted; "
                            "switching to OpenAI TTS fallback for this process"
                        )
                        await self._synthesise_openai_fallback(text, audio_out_queue)
                        return

                    if resp.status_code == 429:
                        err = await resp.aread()
                        retry_after = int(resp.headers.get("Retry-After", 5))
                        logger.warning(
                            "[ms_tts] ElevenLabs rate-limited (429) — waiting %ds "
                            "(attempt %d/%d): %s",
                            retry_after, attempt + 1, max_attempts, err[:200],
                        )
                        if attempt < max_attempts - 1:
                            await asyncio.sleep(retry_after)
                            continue  # retry the for loop
                        return  # all retries exhausted
                    if (
                        resp.status_code == 422
                        and "speed" in body["voice_settings"]
                    ):
                        # Validation error while we are sending a parameter the
                        # account or model may not accept. Drop it and retry the
                        # SAME chunk — the alternative is returning with no
                        # audio, which is dead air mid-booking.
                        err = await resp.aread()
                        _ELEVENLABS_SPEED_UNSUPPORTED = True
                        body["voice_settings"].pop("speed", None)
                        logger.error(
                            "[ms_tts] ElevenLabs rejected voice_settings.speed "
                            "(422) — retrying without it and disabling it for "
                            "this process: %s",
                            err[:300],
                        )
                        continue  # retry the for loop

                    if resp.status_code != 200:
                        err = await resp.aread()
                        logger.error(
                            "[ms_tts] ElevenLabs error %d: %s",
                            resp.status_code, err[:300],
                        )
                        return  # non-retryable error

                    logger.debug(
                        "[ms_tts] ElevenLabs response status=%d content-type=%r",
                        resp.status_code,
                        resp.headers.get("content-type", "MISSING"),
                    )

                    chunk_count = 0
                    async for raw_chunk in resp.aiter_bytes(chunk_size=TTS_STREAM_CHUNK_SIZE):
                        if not raw_chunk:
                            continue

                        b64 = audio_out_processor.convert_chunk(raw_chunk)
                        if b64:
                            await audio_out_queue.put(b64)
                            chunk_count += 1

                    # Flush any remaining bytes from the alignment buffer
                    b64 = audio_out_processor.flush()
                    if b64:
                        await audio_out_queue.put(b64)

                    logger.debug("[ms_tts] synthesise_chunk done: %d chunks", chunk_count)
                    return  # success — exit the retry loop

            except asyncio.CancelledError:
                # Barge-in: reset alignment state so the next chunk starts clean
                audio_out_processor.reset()
                logger.info("[ms_tts] synthesise_chunk cancelled (barge-in)")
                raise

            except RuntimeError as exc:
                if "close message" in str(exc):
                    logger.warning("[ms_tts] WebSocket closed during TTS")
                else:
                    logger.error("[ms_tts] runtime error: %r", exc)
                audio_out_processor.reset()
                return

            except Exception as exc:
                logger.error("[ms_tts] synthesise_chunk error: %r", exc)
                audio_out_processor.reset()
                return

    # =========================================================================
    # OpenAI TTS fallback (used when ElevenLabs is exhausted / unavailable)
    # =========================================================================

    async def _synthesise_openai_fallback(
        self,
        text: str,
        audio_out_queue: asyncio.Queue,
    ) -> None:
        """
        Synthesise text using OpenAI TTS when ElevenLabs is unavailable.

        OpenAI TTS returns PCM16 at 24kHz.  We convert to 8kHz mulaw for Twilio:
          audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, state) → 8kHz PCM16
          audioop.lin2ulaw(pcm_8k, 2)                        → 8kHz mulaw
          base64.b64encode(mulaw)                            → Twilio payload

        NOTE: audio_out_processor is NOT used here because we produce mulaw
        directly.  The sentinel placed by _tts_loop in connection.py will
        still measure bytes-sent correctly via _send_loop._tts_bytes_sent.
        """
        from .config import OPENAI_API_KEY

        if not OPENAI_API_KEY:
            logger.error("[ms_tts_openai] OPENAI_API_KEY not set — cannot use fallback")
            return

        try:
            import audioop
        except ImportError:
            logger.error("[ms_tts_openai] audioop not available — cannot convert OpenAI PCM")
            return

        # Phonetic substitutions for OpenAI TTS — no pronunciation dictionary
        # available, so "Awlstuh" substitution is applied here only.
        text = _apply_tts_substitutions_openai(text)

        url = "https://api.openai.com/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type":  "application/json",
        }
        body = {
            "model":           "tts-1",
            "voice":           "nova",      # natural, clear female voice
            "input":           text,
            "response_format": "pcm",       # 24kHz PCM16 signed little-endian
        }

        # Match the ElevenLabs path's phone-readback pacing so the dev bypass and
        # the credit-exhausted fallback do not sound different from production.
        # OpenAI's `speed` is a top-level field, not a voice setting, and its
        # accepted range (0.25-4.0) is wider than the one config.py clamps to,
        # so any value that reaches here is already valid.
        _speed = (
            ELEVENLABS_PHONE_SPEED if _is_spoken_phone_number(text)
            else ELEVENLABS_SPEED
        )
        if abs(_speed - 1.0) > 1e-9:
            body["speed"] = _speed

        logger.info("[ms_tts_openai] synthesising (fallback): %r", text[:60])

        try:
            _ratecv_state = None
            chunk_count   = 0

            async with _get_http_client().stream(
                "POST", url, json=body, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    err = await resp.aread()
                    logger.error(
                        "[ms_tts_openai] error %d: %s",
                        resp.status_code, err[:300],
                    )
                    return

                # Stream PCM24k → convert → enqueue as mulaw
                # 1920 bytes = 40ms of 24kHz PCM16 stereo-equivalent chunk size
                async for raw_chunk in resp.aiter_bytes(chunk_size=1920):
                    if not raw_chunk:
                        continue
                    try:
                        # 3:1 rate conversion: 24000 Hz → 8000 Hz
                        pcm_8k, _ratecv_state = audioop.ratecv(
                            raw_chunk, 2, 1, 24000, 8000, _ratecv_state,
                        )
                        # PCM16 → G.711 mu-law
                        ulaw = audioop.lin2ulaw(pcm_8k, 2)
                        b64  = base64.b64encode(ulaw).decode("ascii")
                        await audio_out_queue.put(b64)
                        chunk_count += 1
                    except Exception as exc:
                        logger.warning("[ms_tts_openai] conversion error: %r", exc)

            logger.info("[ms_tts_openai] done: %d chunks", chunk_count)

        except asyncio.CancelledError:
            logger.info("[ms_tts_openai] cancelled (barge-in)")
            raise
        except Exception as exc:
            logger.error("[ms_tts_openai] error: %r", exc)

    # =========================================================================
    # MODE B -- WebSocket streaming (persistent connection)
    # =========================================================================

    async def start_ws(
        self,
        tts_text_queue: asyncio.Queue,
        audio_out_queue: asyncio.Queue,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Open one persistent ElevenLabs WebSocket for the entire call.

        Sends text chunks as they arrive from tts_text_queue.
        Receives audio concurrently and puts it onto audio_out_queue.

        WebSocket protocol:
          SEND init:  {"text": " ", "voice_settings": {...}, "xi_api_key": "...",
                       "generation_config": {"chunk_length_schedule": [50, 100, 150]}}
          SEND text:  {"text": "<chunk>", "flush": false}
          SEND flush: {"text": "", "flush": true}   <- end of utterance
          RECV audio: {"audio": "<base64 pcm16k>"}  or {"isFinal": true}
        """
        import websockets
        import websockets.exceptions

        ws_url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
            f"/stream-input?model_id={ELEVENLABS_MODEL_ID}&output_format=pcm_16000"
        )

        audio_proc = AudioOutputProcessor()
        reconnect_attempted = False

        while not stop_event.is_set():
            try:
                async with websockets.connect(ws_url, ping_interval=5, ping_timeout=10) as ws:
                    self._ws = ws
                    logger.info("[ms_tts_ws] connected to ElevenLabs WebSocket")

                    # Send initialisation message
                    await ws.send(json.dumps({
                        "text": " ",
                        "voice_settings": {
                            "stability":        ELEVENLABS_STABILITY,
                            "similarity_boost": ELEVENLABS_SIMILARITY_BOOST,
                        },
                        "xi_api_key":       ELEVENLABS_API_KEY,
                        "generation_config": {
                            "chunk_length_schedule": [50, 100, 150],
                        },
                    }))

                    send_task = asyncio.create_task(
                        self._ws_send_text_loop(ws, tts_text_queue, stop_event),
                        name="tts_ws_send",
                    )
                    recv_task = asyncio.create_task(
                        self._ws_receive_audio_loop(ws, audio_out_queue, audio_proc, stop_event),
                        name="tts_ws_recv",
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

                    logger.warning("[ms_tts_ws] WebSocket closed unexpectedly")

            except Exception as exc:
                logger.error("[ms_tts_ws] WebSocket error: %r", exc)

            finally:
                self._ws = None

            if stop_event.is_set():
                return

            if not reconnect_attempted:
                reconnect_attempted = True
                logger.info("[ms_tts_ws] attempting reconnect in 0.5s...")
                await asyncio.sleep(0.5)
            else:
                logger.error("[ms_tts_ws] reconnect failed -- giving up")
                return

    async def _ws_send_text_loop(
        self,
        ws: Any,
        tts_text_queue: asyncio.Queue,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Continuously read text chunks from tts_text_queue and send to ElevenLabs.
        A None sentinel signals end of utterance -- send flush message.
        """
        try:
            while not stop_event.is_set():
                try:
                    chunk_text = await asyncio.wait_for(tts_text_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                if chunk_text is None:
                    # End of utterance sentinel -- flush ElevenLabs buffer
                    await ws.send(json.dumps({"text": "", "flush": True}))
                    logger.debug("[ms_tts_ws] sent flush")
                    continue

                if not chunk_text or not chunk_text.strip():
                    continue

                # Apply ElevenLabs-specific substitutions (list is intentionally empty).
                chunk_text = _apply_tts_substitutions_elevenlabs(chunk_text)
                await ws.send(json.dumps({"text": chunk_text, "flush": False}))
                logger.debug("[ms_tts_ws] sent text: %r", chunk_text[:40])

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_tts_ws] send error: %r", exc)

    async def _ws_receive_audio_loop(
        self,
        ws: Any,
        audio_out_queue: asyncio.Queue,
        audio_proc: AudioOutputProcessor,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Receive audio from ElevenLabs WebSocket.

        {"audio": "<base64 pcm16k>"}  -> decode -> convert -> audio_out_queue
        {"isFinal": true}             -> put None sentinel onto audio_out_queue
        """
        import websockets.exceptions

        try:
            async for raw_msg in ws:
                if stop_event.is_set():
                    break

                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                if "audio" in msg and msg["audio"]:
                    try:
                        pcm_bytes = base64.b64decode(msg["audio"])
                    except Exception:
                        continue
                    b64 = audio_proc.convert_chunk(pcm_bytes)
                    if b64:
                        await audio_out_queue.put(b64)

                elif msg.get("isFinal"):
                    # Flush alignment buffer
                    b64 = audio_proc.flush()
                    if b64:
                        await audio_out_queue.put(b64)
                    await audio_out_queue.put(None)   # sentinel for downstream
                    logger.debug("[ms_tts_ws] isFinal received")

                elif "error" in msg:
                    logger.error("[ms_tts_ws] ElevenLabs error: %s", msg["error"])
                    return

        except asyncio.CancelledError:
            audio_proc.reset()
            raise
        except Exception as exc:
            logger.error("[ms_tts_ws] receive error: %r", exc)
            audio_proc.reset()
