# app/media_streams/connection.py
"""
Twilio Media Streams WebSocket connection handler.

Manages the full lifecycle of a single Twilio call:
  - Receives JSON events from Twilio (connected / start / media / stop)
  - Coordinates the pipeline: STT -> LLM -> TTS -> audio output
  - Runs all pipeline coroutines concurrently via asyncio.gather
  - Handles graceful shutdown and error recovery

WebSocket message protocol (Twilio -> server):
  {"event": "connected", "protocol": "Call", "version": "1.0.0"}
  {"event": "start",     "streamSid": "...", "start": {"callSid": "...", ...}}
  {"event": "media",     "streamSid": "...", "media": {"payload": "<base64 mulaw>"}}
  {"event": "stop",      "streamSid": "...", "stop":  {"callSid": "..."}}

WebSocket message protocol (server -> Twilio):
  {"event": "media",  "streamSid": "...", "media": {"payload": "<base64 mulaw>"}}
  {"event": "clear",  "streamSid": "..."}  <- drains Twilio's audio buffer on barge-in

Pipeline queues (all asyncio.Queue, unbounded):
  audio_in_queue    : raw mulaw bytes from Twilio          -> AudioInputProcessor
  stt_input_queue   : PCM16 16kHz bytes (converted)        -> STTStream
  transcript_queue  : completed utterance strings          -> LLM loop
  tts_text_queue    : text chunks to synthesise            -> TTS loop
  audio_out_queue   : base64-encoded mulaw strings         -> send_loop -> Twilio

Error handling contract:
  - Silence re-ask: SilenceHandler fires after 4s of caller silence, re-asks
    the last question up to 2 times; 3rd silence triggers transfer
  - Pipeline failures: each component has fallback phrases; complete failure plays
    pre-recorded message then closes cleanly
  - Unstable call tracking: if call never completes one STT->LLM->TTS cycle,
    logs "UNSTABLE CALL" for monitoring
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

from .config import (
    TWILIO_STARTED_TIMEOUT_SEC,
    PIPELINE_FAILURE_PHRASE,
    CLAUDE_ERROR_PHRASE,
    BOOKING_OPEN,
    BARGE_IN_THRESHOLD_MS,
)

# ---------------------------------------------------------------------------
# Barge-in constants
# ---------------------------------------------------------------------------

_BARGE_IN_THRESHOLD_S: float = BARGE_IN_THRESHOLD_MS / 1000.0

# Phrases spoken after a confirmed barge-in (selected at random).
_BARGE_IN_ACKS: List[str] = [
    "Sorry — go ahead.",
    "Yes, go on.",
    "Sorry about that — you were saying?",
]
from .session import (
    get_or_create_session,
    save_session,
)
from .audio_in import AudioInputProcessor
from .audio_out import AudioOutputProcessor
from .stt_stream import STTStream

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drain_queue(q: asyncio.Queue) -> int:
    """Remove all items from an asyncio.Queue without blocking. Returns item count."""
    count = 0
    while True:
        try:
            q.get_nowait()
            count += 1
        except asyncio.QueueEmpty:
            break
    return count


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Sentinel object placed on audio_out_queue AFTER the last audio chunk for a
# TTS utterance.  send_loop detects it and fires on_tts_finished() only once
# all audio for that utterance has actually been sent to Twilio.
_TTS_DONE_SENTINEL = object()


# ---------------------------------------------------------------------------
# Greeting (built at call start from clinic_config.json)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Question-worth-storing guard (mirrors the one in flow.py)
# ---------------------------------------------------------------------------

# Phrases whose presence anywhere in the text means we must NOT store it
# as last_question.  Checked with `phrase in text_lower` (substring match).
_NEVER_STORE_PHRASES = [
    # Re-ask / error phrases — must never overwrite the original question
    "sorry, i didn't quite catch",
    "sorry about that",
    "sorry, i'm having",
    "i'm having a little trouble",
    "didn't quite catch",
    "bear with me",
    "one moment",
    "let me check",
    "just bear",
    # Greeting / preamble phrases — not actionable questions
    "hi there",
    "hello",
    "this is susie",
    "roch solutions",
    "theorem health",
]

_KNOWN_QUESTION_PHRASES = [
    "what brings you in",
    "how long have you had",
    "does that sound ok",
    "been with us before",
    "work best for you",
    "first name please",
    "reach you on",
    "which would you prefer",
    "that right",
    "slot would you",
    "would you like",
    "no problem — which",
    "sound ok",
]


def _is_question_worth_storing(text: str) -> bool:
    """
    Return True only if text is a real question Susie asked.
    Rejects greetings, re-ask phrases, filler phrases, and error phrases.
    Uses substring match (not startswith) so "sorry about that — X?" is also rejected.
    """
    t = text.strip().lower()
    for phrase in _NEVER_STORE_PHRASES:
        if phrase in t:
            return False
    for q in _KNOWN_QUESTION_PHRASES:
        if q in t:
            return True
    if t.endswith("?"):
        return True
    return False


# ---------------------------------------------------------------------------
# SilenceHandler — re-ask after caller silence
# ---------------------------------------------------------------------------

class SilenceHandler:
    """
    Fires a re-ask phrase if the caller has been silent for an extended
    period after Susie asked a question.

    last_audio_received_at is updated ONLY by on_speech_started() and
    on_transcript_received() — NOT by on_audio_received() — so the
    since_audio guard reflects actual speech, not Twilio's continuous
    silence packets (which arrive every ~20ms regardless of speech).

    Silence windows:
        1st (26 s) → "Sorry, I didn't quite catch that — <question>"
        2nd (15 s) → "Sorry about that — <question>"
        3rd (15 s) → transfer phrase + trigger_transfer()

    Windows are sized so that:
      - Re-ask #1 fires AFTER the 25-second injection window used by the
        automated test runner (TURN_WAIT_SECONDS=25).  With typical LLM+TTS
        latency of 3-8 s, the timer arms at t=3-8s and would fire at t=23-28s
        — which overlaps with the t=25s injection window when questions are
        short.  Raising Window 1 to 26 s ensures the timer always fires at
        t=L+26 ≥ 26 s, well after the t=25s injection which cancels it.
      - For genuine silence scenarios (Phase 6 tests), no injection arrives
        during the 25s empty-turn window, so the timer still fires at ~36 s
        (greeting TTS ≈ 10 s + Window 1 = 26 s) and the re-ask plays correctly.
      - Transfer does NOT fire before a second silent turn's response
        arrives (~70 s from call start), allowing recovery scenarios to work.
      - The since_audio < 3.5 guard means the window fires only when
        genuinely no speech has been detected.
    """

    def __init__(
        self,
        tts_text_queue: asyncio.Queue,
        trigger_transfer_fn,
        on_reask=None,
        on_transfer=None,
        get_session=None,
    ) -> None:
        self.reask_count:             int   = 0
        self.last_audio_received_at:  float = time.time()
        # last_engagement_at — broadest "caller was doing something" clock.
        # Updated by: speech-start/VAD, partial transcripts, final transcripts,
        # DTMF presses, confirmed barge-in, fragment-suppressed transcripts.
        # Unlike last_audio_received_at it is NOT reset between questions and
        # serves as the primary guard in _speech_recovery.
        self.last_engagement_at:      float = time.time()
        self.last_question:           str   = ""
        self._replay_flow_step:       int   = -1
        self.current_state:           str   = "default"
        self._consecutive_silence_count: int = 0
        self.currently_reasking:      bool  = False
        self._last_question_set_at:   float = time.time()
        self._task: Optional[asyncio.Task]  = None
        self._tts_text_queue                = tts_text_queue
        self._trigger_transfer              = trigger_transfer_fn
        self._llm_busy:               bool  = False
        self._on_reask                      = on_reask      # optional async callback(text)
        self._on_transfer                   = on_transfer   # optional async callback(text)
        # Callable that returns the current session dict (passed as lambda: self.session
        # from WebSocketCallHandler so it always reflects the live session after
        # the "start" event reassigns self.session).
        self._get_session                   = get_session   # () -> dict | None
        self._recovery_task: Optional[asyncio.Task] = None  # re-arms timer if STT misses audio
        self._stt_miss_count: int = 0  # consecutive STT misses since last successful transcript
        self._cancelled: bool = False  # set by cancel() — hard synchronous guard for _run()/_transfer()
        # No-input watchdog: fires 3 s after TTS ended if there is zero caller
        # engagement (no VAD, no partial, no final transcript).  Unlike
        # _speech_recovery, it does not require a preceding VAD event — it is
        # the only recovery path when STT misses the utterance entirely.
        # _no_input_reask_count tracks how many watchdog prompts have fired for
        # the current question; reset on every real transcript or new question.
        self._no_input_reask_count: int = 0
        self._no_input_watchdog_task: Optional[asyncio.Task] = None
        # Timestamp when the last question's TTS audio finished playing (set in
        # on_tts_finished just before _restart_timer).  Used by _speech_recovery to
        # enforce a minimum response window so energy VAD noise before the caller
        # has realistically had time to answer cannot trigger a premature re-ask.
        self._tts_done_at: float = 0.0
        # Question generation counter — incremented by on_question_asked() for every
        # distinct question.  All timers (_run) and recovery tasks (_speech_recovery)
        # capture the generation at creation time and are no-ops if _q_gen has since
        # advanced.  This eliminates stale recovery from a previous question firing
        # during a new question and prevents double-fire in re-ask cycles.
        self._q_gen: int = 0
        # Timestamp of the most recent on_tts_started() call.  Used by
        # on_tts_finished() to detect whether a newer TTS chunk has already started,
        # preventing _tts_playing from being cleared prematurely during multi-chunk /
        # multi-part responses (FAQ answer + re-anchor, long PRESENT_DAYS lists, etc.).
        self._tts_last_start_ts: float = 0.0
        # True while any TTS chunk is actively being sent to Twilio.
        # Set in on_tts_started(), cleared in on_tts_finished().
        # _speech_recovery checks this as Guard 0 — no recovery phrase can fire
        # while Susie is already speaking, which was the root cause of prompts
        # playing on top of long PRESENT_DAYS / FAQ TTS responses.
        self._tts_playing: bool = False

    # ── public API ─────────────────────────────────────────────────────────

    def on_audio_received(self) -> None:
        """Called for every Twilio audio packet (~every 20ms, even during silence).
        Does NOT update last_audio_received_at — use on_speech_started() for that."""
        pass

    def on_speech_started(self) -> None:
        """Call when STT detects actual speech (partial transcript or energy VAD).

        Cancels the W1/W2/W3 silence cascade timer so Susie doesn't re-ask while
        the caller is speaking.

        WATCHDOG BEHAVIOUR — re-arm, not cancel:
        Previously _cancel_timer() was called here, which permanently killed both
        the main timer AND the no-input watchdog on any VAD event.  That made the
        watchdog useless: near-instant echo when TTS finishes, speakerphone hiss,
        or a brief noise burst would all permanently cancel the 3-second guarantee,
        and the only surviving path was _speech_recovery — which has its own guards
        that can independently suppress.

        The correct semantic for VAD is "caller may be speaking — give them 3 more
        seconds", NOT "caller has responded — kill the deadline forever."  So the
        watchdog is now RESET to a fresh 3-second window from this moment.  It fires
        3s after the most recent VAD event.  Only a real final transcript from STT
        (on_transcript_received) should permanently cancel the deadline.

        If _no_input_watchdog_task is None, no question is live (or the answer has
        already arrived), so no re-arm is performed.
        """
        self.last_audio_received_at = time.time()
        self.last_engagement_at     = time.time()

        # ── Cancel W1/W2/W3 main timer (caller is speaking; W1 would fire too early) ──
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.currently_reasking = False

        # ── Cancel stale recovery task before starting a new one ──────────────
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()

        # ── Re-arm no-input watchdog with a fresh 3-second deadline ───────────
        # Only re-arm if the watchdog was already live (set by _restart_timer when
        # a question's TTS finished — meaning a question is actually outstanding).
        # This prevents spurious arming between questions while LLM is processing.
        import os as _os_sp
        if (
            self._no_input_watchdog_task is not None
            and not self._cancelled
        ):
            if not self._no_input_watchdog_task.done():
                self._no_input_watchdog_task.cancel()
            self._no_input_watchdog_task = None
            _wdg_wait = float(_os_sp.getenv("NO_INPUT_WATCHDOG_SEC", "3.0"))
            if _wdg_wait > 0:
                _armed_at = time.time()
                _my_q_gen_w = self._q_gen
                self._no_input_watchdog_task = asyncio.create_task(
                    self._no_input_watchdog(_armed_at, _my_q_gen_w),
                    name="ms_silence_no_input_watchdog",
                )
                logger.info(
                    "[ms_watchdog] WATCHDOG_ARMED q_gen=%d wait=%.1fs (re-arm on speech event)",
                    _my_q_gen_w, _wdg_wait,
                )
        elif self._no_input_watchdog_task is not None and self._no_input_watchdog_task.done():
            # Completed task — clear stale reference; no re-arm needed (question resolved)
            self._no_input_watchdog_task = None

        # ── Arm speech-recovery as secondary safety net ────────────────────────
        # Capture the current flow_step so _speech_recovery can detect if the
        # state has advanced during its sleep and suppress a stale prompt.
        _recovery_step = self._replay_flow_step
        _my_q_gen = self._q_gen  # bind recovery to this question generation
        self._recovery_task = asyncio.create_task(
            self._speech_recovery(_recovery_step, _my_q_gen), name="ms_silence_speech_recovery"
        )
        logger.debug(
            "[ms_silence] speech started — W1 timer cancelled, watchdog re-armed, "
            "recovery armed (step=%d q_gen=%d)",
            _recovery_step, _my_q_gen,
        )

    async def _speech_recovery(self, recovery_step: int = -1, q_gen: int = 0) -> None:
        """If STT doesn't transcribe within N seconds of speech detection, prompt the caller.

        The wait window is state-aware (not a hardcoded 5 s):
          - extra_slow states (PRESENT_DAYS, PRESENT_TIMES, COLLECT_REASON,
            CONFIRM_ASSESSMENT): 10 s — caller may still be choosing or thinking
            after a long option list or detailed explanation.
          - medium states (phone, name, day, time):  7 s
          - fast states (greeting, location, confirm yes/no): 5 s

        Guards (evaluated after the sleep, in order):
          0. TTS currently playing — never interrupt Susie mid-sentence.
             This was the primary root cause: energy VAD during PRESENT_DAYS
             playback fired a 5 s recovery that surfaced before the list ended.
          1. Stale flow_step — state has advanced since this recovery was armed.
          2. Minimum response window — TTS finished fewer than 8 s ago (belt-and-
             suspenders backup for Guard 0 in case _tts_playing is momentarily stale).
          3. Recent engagement — last_engagement_at < 3.5 s ago (extended from 2 s;
             consistent with the W1 since_audio guard).
          4. LLM busy or main timer running (transcript already being processed).
          5. STT miss cap — max 2 misses per question (stt_miss_count > 2).

        Sequencing fix (prevents double-fire loop):
          currently_reasking=True while phrase plays (blocks on_tts_started cancel
          and on_tts_finished re-arm), then _restart_timer() is called AFTER a 5 s
          TTS-play wait — exactly as _run() does for W1/W2.
        """
        # ── State-aware wait window ───────────────────────────────────────────
        import os as _os_r
        _env_w1 = _os_r.getenv("SILENCE_WINDOW_1_SEC")
        if _env_w1:
            # In test mode the env override shortens W1; keep recovery proportionally
            # shorter so tests are not blocked by a long recovery sleep.
            _recovery_wait = max(3.0, float(_env_w1) * 0.20)
        else:
            _sess_r = self._get_session() if self._get_session else {}
            _state_r = (_sess_r or {}).get("state", "")
            from app.silence_handler import get_silence_threshold as _gst
            _thresh_r = _gst(_state_r)
            # Scale recovery wait to match state cadence:
            #   extra_slow (≥30 s, e.g. PRESENT_DAYS/TIMES) → 8 s — caller may
            #     still be scanning a long option list or mid-thought.
            #   medium/default (≥10 s, e.g. default 26 s) → 5 s — down from 7 s;
            #     faster re-ask but still clears Guard-3's 3.5 s engagement window.
            #   fast (< 10 s, e.g. phone/name/confirm at 3 s) → 4 s — well above
            #     Guard-3's 3.5 s floor so a single VAD event always passes Guard 3.
            # The 0.5 s gap above Guard-3 (4.0 > 3.5) also protects against a
            # second VAD pulse at T+0.4 s pushing last_engagement_at forward and
            # causing Guard 3 to suppress, which would orphan the call.
            if _thresh_r >= 30.0:
                _recovery_wait = 8.0
            elif _thresh_r >= 10.0:
                _recovery_wait = 5.0
            else:
                _recovery_wait = 4.0

        try:
            await asyncio.sleep(_recovery_wait)
        except asyncio.CancelledError:
            return

        # Guard -1: stale question generation.  If on_question_asked() was called
        # after this recovery task was created, _q_gen has advanced and this task
        # belongs to the previous question — suppress unconditionally.
        # This eliminates stale recovery firing after a state transition and
        # prevents double-fire across re-ask cycles.
        if q_gen != 0 and q_gen != self._q_gen:
            logger.debug(
                "[ms_silence] recovery: stale q_gen %d vs current %d — suppressed",
                q_gen, self._q_gen,
            )
            return

        # Guard 0: TTS is currently playing — never fire while Susie is speaking.
        # on_tts_started() sets _tts_playing=True; on_tts_finished() clears it.
        # This is the primary fix for energy VAD triggering recovery during long
        # PRESENT_DAYS / FAQ TTS responses.
        if self._tts_playing:
            logger.debug("[ms_silence] recovery: TTS currently playing — suppressed")
            return

        # Guard 1: stale flow_step — the flow has advanced since we were armed.
        # recovery_step == -1 means no question was active (e.g. greeting);
        # in that case skip step validation.
        if recovery_step != -1:
            _sess_chk = self._get_session() if self._get_session else {}
            _current_step = (_sess_chk or {}).get("flow_step", -1)
            if _current_step != recovery_step:
                logger.debug(
                    "[ms_silence] recovery: stale step stored=%d current=%d — suppressed",
                    recovery_step, _current_step,
                )
                return

        # Guard 2: minimum response window — belt-and-suspenders backup for Guard 0.
        # _tts_done_at is 0.0 at call start (no question asked yet); skip guard then.
        # Threshold is dynamic: _recovery_wait + 0.5 s.  This scales the echo-
        # protection window to the state's recovery cadence so that a legitimate
        # VAD event (caller spoke ≥ 0.5 s after TTS ended) always passes Guard 2
        # after one _recovery_wait sleep, while a near-instant echo (< 0.5 s) is
        # still suppressed and handled by the watchdog re-arm below.
        # Previously hardcoded at 8.0 s, which meant fast-state (3 s threshold)
        # recovery always suppressed here even 5 s after TTS — causing 8–9 s
        # total dead air instead of ~4 s.
        _guard2_min = _recovery_wait + 0.5
        if self._tts_done_at > 0 and (time.time() - self._tts_done_at) < _guard2_min:
            logger.debug(
                "[ms_silence] recovery: TTS finished only %.1fs ago (guard2_min=%.1fs) — suppressing premature re-ask",
                time.time() - self._tts_done_at, _guard2_min,
            )
            # Re-arm only if the no-input watchdog is NOT already live.
            # If it is running it will fire 3 s after TTS ends — resetting it
            # here would cancel the countdown and create a restart loop where
            # recovery perpetually resets the watchdog without ever letting it fire.
            _wdg_live = (
                self._no_input_watchdog_task is not None
                and not self._no_input_watchdog_task.done()
            )
            if not _wdg_live and not self._cancelled and not self._tts_playing and not self._llm_busy:
                self._restart_timer()
            return

        # Guard 3: recent engagement — extended from 2.0 s to 3.5 s to match the
        # W1 since_audio guard.  Protects split answers and delayed STT finals.
        since_engagement = time.time() - self.last_engagement_at
        if since_engagement < 3.5:
            logger.debug(
                "[ms_silence] recovery: recent engagement (%.1fs ago) — suppressing prompt",
                since_engagement,
            )
            # Same watchdog-preservation logic as Guard 2: only restart timers
            # when the watchdog is not already counting down.  The leading cause
            # of the perpetual-reset loop is recovery waking at T+3 (recovery_wait)
            # when last_engagement_at is T+0 — since_engagement=3.0 < 3.5 fires
            # this guard, which calls _restart_timer(), which cancels + resets
            # the watchdog, which cancels the just-armed watchdog — repeat forever.
            _wdg_live = (
                self._no_input_watchdog_task is not None
                and not self._no_input_watchdog_task.done()
            )
            if not _wdg_live and not self._cancelled and not self._tts_playing and not self._llm_busy:
                self._restart_timer()
            return

        # Guard 4: LLM busy or main timer running (transcript already being processed)
        if self._llm_busy or not (self._task is None or self._task.done()):
            return

        # Guard 5: STT miss cap — max 2 recovery prompts per question.
        # _stt_miss_count is reset ONLY by on_transcript_received, never by
        # on_tts_started, so this cap is now effective across re-arm cycles.
        self._stt_miss_count += 1
        if self._stt_miss_count > 2:
            logger.info(
                "[ms_silence] recovery: STT miss #%d — cap reached, suppressing prompt",
                self._stt_miss_count,
            )
            # Re-arm only if watchdog is not already live (same restart-loop
            # prevention as Guard 2/3 above).
            _wdg_live = (
                self._no_input_watchdog_task is not None
                and not self._no_input_watchdog_task.done()
            )
            if not _wdg_live and not self._cancelled and not self._tts_playing and not self._llm_busy:
                self._restart_timer()
            return

        _sess  = self._get_session() if self._get_session else {}
        _state = (_sess or {}).get("state", "")

        logger.info(
            "[ms_silence] recovery: STT miss #%d — prompting (step=%d state=%s tts_age=%.1fs)",
            self._stt_miss_count, recovery_step, _state,
            (time.time() - self._tts_done_at) if self._tts_done_at > 0 else -1.0,
        )

        # DTMF digits already in buffer — caller is actively typing.
        # Reset the silence timer silently; do not interrupt with a spoken prompt.
        if (_sess or {}).get("phone_dtmf_buffer") and _state in (
            "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE"
        ):
            self._restart_timer()
            return

        # State-specific repair prompts
        if _state in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL"):
            if (_sess or {}).get("lookup_correction_mode"):
                phrase = "Sorry — what first name and surname was the booking under?"
            elif (_sess or {}).get("rc_stage") == "lookup_done":
                phrase = "Sorry — was that the right appointment? Yes or no?"
            else:
                phrase = "Sorry — just bear with me while I look up your appointment."
        elif _state in ("PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE"):
            _lq = (_sess or {}).get("last_question", "")
            phrase = _lq if _lq else "Sorry — which day works best for you?"
        elif _state in ("CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING"):
            phrase = (
                "Sorry, I didn't quite catch that — "
                "please say yes if I can use this number, "
                "or no if you'd like to use a different one."
            )
        elif (_sess or {}).get("phone_awaiting_dtmf"):
            phrase = (
                "Sorry, I didn't quite catch that — "
                "please enter the phone number using your keypad."
            )
        elif _state in (
            "COLLECT_NAME", "COLLECT_NAME_RETURNING",
            "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
        ):
            # Use NC substate to pick the right scaffold prompt.
            # name_fragment == NC's first_name (set when first name is stored).
            # When present we are in the surname step; otherwise first-name step.
            _nf = (_sess or {}).get("name_fragment")
            if _nf:
                phrase = (
                    "Sorry, I didn't quite catch that \u2014 "
                    "please say: my surname is..."
                )
            else:
                phrase = (
                    "Sorry, I didn't quite catch that \u2014 "
                    "please say: my first name is..."
                )
        elif _state in ("COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE"):
            phrase = (
                "Sorry, I didn't quite catch that — "
                "please say the phone number slowly."
            )
        elif _state in ("GREETING", "DETECT_INTENT", ""):
            phrase = "Sorry, I didn't quite catch that. Are you calling to book, reschedule, or cancel an appointment?"
        elif _state == "ASK_LOCATION":
            phrase = "Sorry, I didn't catch that. Which of our locations were you looking for — Alcester or Redditch?"
        else:
            phrase = "Sorry — I'm having a little trouble hearing you. Could you say that again?"

        # Set currently_reasking BEFORE enqueuing the phrase.
        # This prevents the double-fire loop:
        #   on_tts_started() checks `if not self.currently_reasking` before cancelling
        #   the silence timer; with currently_reasking=True it does nothing.
        #   on_tts_finished() returns early when currently_reasking=True, so it does
        #   not re-arm the timer — preventing W1 from firing a second phrase 26 s later.
        self.currently_reasking = True
        await self._tts_text_queue.put(phrase)
        if self._on_reask:
            asyncio.create_task(self._on_reask(phrase))

        # Wait ~5 s for TTS to finish, then hand off to the main cascade (_run).
        # _restart_timer() is called HERE (after the phrase plays), not before.
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            self.currently_reasking = False
            return
        self.currently_reasking = False
        self._restart_timer()

    def set_state(self, state: str) -> None:
        """Update current_state so re-ask phrases are context-aware."""
        if state:
            self.current_state = state

    def on_question_asked(self, question: str) -> None:
        """Update last_question so re-asks have the right text."""
        if not question or not question.strip():
            return
        if _is_question_worth_storing(question):
            self.last_question         = question.strip()
            self.reask_count           = 0
            self._no_input_reask_count = 0  # new question — reset dead-air watchdog counter
            self._last_question_set_at = time.time()
            self._q_gen               += 1   # new question = new silence generation
            _session = self._get_session() if self._get_session else None
            self._replay_flow_step = (_session or {}).get("flow_step", -1) if _session else -1

    def on_tts_started(self) -> None:
        """Track TTS activity and cancel silence/recovery timers before Susie speaks.

        _tts_playing is set unconditionally (even when currently_reasking=True) so
        _speech_recovery Guard 0 reliably suppresses recovery while the recovery
        phrase itself is playing — preventing a second recovery firing on top of
        the first.

        _recovery_task is cancelled when NOT currently_reasking: if TTS is starting
        for a flow response (not a re-ask), any pending recovery is stale because the
        flow has already decided to speak again.  Cancelling it here prevents the
        race where energy VAD fires during Susie's response, a 7-10 s recovery task
        starts, and the task later fires its prompt after the real response has ended.

        WATCHDOG: the no-input watchdog is intentionally NOT cancelled here.
        Previously _cancel_timer() was called, which killed the watchdog for ALL TTS
        events including non-question bridge/filler phrases.  When a non-question
        phrase's on_tts_finished() ran, is_question=False so _restart_timer() was
        never called, orphaning the call permanently.  The watchdog now survives TTS:
        Guard 2 (_tts_playing) suppresses it while audio is playing and re-arms it
        so it fires 3 s after TTS ends if no caller response arrives.

        NOTE: _stt_miss_count is intentionally NOT reset here.  It must only
        reset when a real caller transcript arrives (on_transcript_received).
        Resetting here allowed recovery to loop: miss→TTS starts→reset→miss→repeat.
        """
        self._tts_playing = True  # always track, even during re-ask playback
        self._tts_last_start_ts = time.time()  # record when this chunk started
        if not self.currently_reasking:
            # Cancel main silence timer (_task / W1-W2-W3) — Susie is speaking.
            # Do NOT cancel the no-input watchdog: it must survive non-question TTS
            # so it can fire once the audio ends if caller still hasn't responded.
            if self._task and not self._task.done():
                self._task.cancel()
            self._task = None
            # Cancel stale recovery task — TTS starting without a fresh transcript
            # means either (a) the flow responded to a previous utterance (recovery
            # is moot) or (b) energy VAD fired and a recovery task is pending; in
            # both cases the task would be stale by the time it wakes up.
            if self._recovery_task and not self._recovery_task.done():
                self._recovery_task.cancel()
                self._recovery_task = None
            logger.debug("[ms_silence] TTS started — W1 timer cancelled (watchdog preserved)")

    def on_llm_started(self) -> None:
        """Called when the LLM begins processing — suppress silence timer."""
        self._llm_busy = True
        self._cancel_timer()
        logger.debug("[ms_silence] LLM started — timer cancelled")

    def on_llm_finished(self) -> None:
        """Called when the LLM finishes processing — allow silence timer again."""
        self._llm_busy = False

    def restart_for_question(self, question: str) -> None:
        """Re-arm the silence timer after fragment suppression (Bug 9 / Bug 6).
        Ensures the silence handler keeps waiting for a real utterance instead
        of going permanently silent when a fragment was discarded."""
        if question and question.strip():
            self.last_question = question.strip()
        self._restart_timer()
        logger.info("[ms_silence] restart_for_question: %r", (self.last_question or "")[:60])

    def on_tts_finished(self, text: str, chunk_started_at: float = 0.0) -> None:
        """After a flow question finishes playing, arm the silence timer.
        Never restarts timer while currently_reasking — _run() owns its timing.
        Never arms timer while LLM is still processing — the delayed TTS-done
        callback for the *previous* question can fire after on_transcript_received()
        cancels the timer but before _llm_busy is set; without this guard the
        timer re-arms and can fire during the check_availability tool call,
        causing a spurious re-ask concatenated with the slot list.
        Never arms if more TTS chunks are queued — prevents stacking re-asks
        after multi-part responses (FAQ answer + re-anchor question).

        chunk_started_at — the _tts_last_start_ts value captured when this chunk
        began synthesis (set in _tts_loop before the sub-chunk loop).  If a newer
        chunk has since started (_tts_last_start_ts > chunk_started_at), we must
        NOT clear _tts_playing — doing so would open a window where _speech_recovery
        Guard 0 passes while the new chunk is still playing.  This was the root
        cause of false recovery firing during long multi-chunk FAQ / PRESENT_DAYS
        responses."""
        # Conditionally clear _tts_playing — only if no newer TTS chunk has started.
        # When chunk N's _delayed_tts_finished fires while chunk N+1 is already
        # playing, _tts_last_start_ts will be > chunk_started_at (chunk N's timestamp),
        # so we leave _tts_playing=True and Guard 0 stays effective.
        if chunk_started_at == 0.0 or chunk_started_at >= self._tts_last_start_ts:
            self._tts_playing = False
        # else: a newer chunk is actively playing — preserve _tts_playing=True
        if self._cancelled:   # Bug 3: stale TTS callbacks must not restart after teardown
            return
        if self.currently_reasking:
            return
        if self._llm_busy:
            return
        # Suppress if more TTS chunks are still pending (multi-part response)
        if not self._tts_text_queue.empty():
            logger.debug("[ms_silence] on_tts_finished: more TTS pending — suppressing timer")
            return
        if self.reask_count >= 1:
            # W1 (or both re-asks) has already fired; _run() owns its timing
            # through W2 and W3.  Do NOT restart the timer here — that would
            # cancel the in-progress _run() coroutine (e.g. the W2 sleep) and
            # prevent W2 / W3 from ever triggering.
            # reask_count is reset to 0 by on_transcript_received, so this
            # guard is lifted as soon as the caller speaks again.
            return
        t = text.strip()
        if t.startswith("Sorry,") or t.startswith("Sorry about") or "didn't quite catch" in t:
            return  # Never restart timer for re-ask phrases
        # Guard: if a transcript arrived AFTER the last question was set,
        # on_transcript_received() already cancelled the timer for this turn.
        # A late TTS-done callback (audio still playing when caller spoke) must
        # not re-arm the timer — doing so would cause a spurious re-ask ~26s later.
        if self.last_audio_received_at > self._last_question_set_at:
            logger.debug(
                "[ms_silence] on_tts_finished: late TTS callback "
                "(transcript received after question set) — suppressing timer restart"
            )
            return
        is_question = (
            t.endswith("?") or
            any(p in t.lower() for p in [
                "what brings", "how long", "does that", "been with us",
                "work best", "full name", "reach you", "which would",
                "sound ok", "that right", "help you", "how can i",
                "your name", "your number", "shall i", "slot would",
            ])
        )
        if is_question:
            if _is_question_worth_storing(t):
                if not self.last_question:
                    # last_question was cleared by on_transcript_received and
                    # on_question_asked hasn't set it yet — let TTS completion fill it.
                    # Extract only the final question sentence so re-asks don't replay
                    # a full multi-sentence FAQ response (e.g. "The clinic is open Mon–Fri
                    # 8:30am–9pm. Would you like to book?" → re-ask = "Would you like to
                    # book?" not the whole opening-hours paragraph).
                    import re as _re
                    _parts = _re.split(r'(?<=[.!?])\s+|\n+', t)
                    _q = next(
                        (p.strip() for p in reversed(_parts) if p.strip().endswith('?')),
                        t,
                    )
                    self.last_question = _q
                    logger.debug("[ms_silence] on_tts_finished: last_question set → %r", _q[:60])
                else:
                    # on_question_asked already set last_question — do NOT overwrite.
                    # A stale TTS chunk completing after a step transition must never
                    # replace the live question (e.g. full-day phrase finishing after
                    # constrained offer was already committed).
                    logger.debug(
                        "[ms_silence] on_tts_finished: last_question already live %r — not overwriting stale %r",
                        self.last_question[:40], t[:40],
                    )
            # Record when the question's audio finished so _speech_recovery can
            # enforce a minimum response window before firing a premature re-ask.
            self._tts_done_at = time.time()
            self._restart_timer()
            logger.info("[ms_silence] timer restarted: %r", t[:50])
        elif self._task is None:
            # FIX C: Non-question TTS must NEVER arm or restart the silence
            # timer.  Only the `if is_question:` branch above can arm it.
            # This is a hard guarantee — bridge phrases ("Got that.",
            # "Of course — good to have you back.", "No problem — let's get
            # you sorted."), barge-in acks ("Sorry — go ahead."), and any
            # other non-question speech cannot own silence timing.  The timer
            # is armed exclusively when a real question finishes playing.
            logger.debug(
                "[ms_silence] non-question TTS — NOT arming timer: %r", t[:50]
            )

    def on_transcript_received(self) -> None:
        """Call whenever a FinalTranscript arrives from STT."""
        self._cancel_timer()
        # Cancel recovery task — transcript arrived, no re-arm needed
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
        self._recovery_task              = None
        self.reask_count                 = 0
        self._stt_miss_count             = 0  # real transcript — reset STT miss counter
        self._no_input_reask_count       = 0  # real transcript — reset dead-air watchdog counter
        self._consecutive_silence_count  = 0
        self.currently_reasking          = False
        self.last_audio_received_at      = time.time()
        self.last_engagement_at          = time.time()
        self.last_question               = ""
        self._replay_flow_step           = -1
        logger.info("[ms_silence] transcript — timer cancelled")

    def cancel(self) -> None:
        """Cancel the timer. Call when the call ends."""
        self._cancelled = True  # synchronous flag — prevents stale re-asks/transfers racing asyncio
        self._cancel_timer()
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
        self._recovery_task = None

    # ── internal ───────────────────────────────────────────────────────────

    async def _no_input_watchdog(self, armed_at: float, q_gen: int) -> None:
        """Dead-air watchdog: fires 3 s after TTS ended with ZERO caller activity.

        Unlike _speech_recovery — which requires a preceding VAD/partial event —
        this watchdog covers the case where the caller spoke but STT emitted nothing
        at all (no partials, no finals, no energy detection).  The two paths are
        mutually exclusive: on_speech_started() calls _cancel_timer(), which cancels
        this task, before scheduling _speech_recovery, so they cannot double-fire.

        Escalation ladder:
          Attempt 1 — "Sorry, I didn't catch that — <state-specific re-ask>"
          Attempt 2 — "I'm sorry, I'm still not hearing you clearly. Let's try again — <re-ask>"
          Attempt 3+ — graceful exit phrase → _transfer()

        Guards (evaluated after the sleep, in order):
          0. _cancelled          — call torn down
          1. Stale q_gen         — new question owns silence; this one is stale
          2. _tts_playing        — Susie is mid-speech; never interrupt
          3. last_engagement_at  — any caller activity (VAD/partial/DTMF) after arming
          4. caller_pause_active — pause mode; caller explicitly asked to wait
          5. currently_reasking  — another recovery path is already mid-phrase
          6. _llm_busy           — LLM processing a transcript; prompt not needed
        """
        import os as _os_w
        _wait = float(_os_w.getenv("NO_INPUT_WATCHDOG_SEC", "3.0"))
        logger.info("[ms_watchdog] WATCHDOG_ARMED q_gen=%d wait=%.1fs", q_gen, _wait)
        try:
            await asyncio.sleep(_wait)
            await asyncio.sleep(0)  # deliver any pending cancels
        except asyncio.CancelledError:
            logger.info("[ms_watchdog] WATCHDOG_CANCELLED q_gen=%d (CancelledError in sleep)", q_gen)
            return

        logger.info("[ms_watchdog] WATCHDOG_WAKE q_gen=%d armed_at=%.3f", q_gen, armed_at)

        # Guard 0: call teardown
        if self._cancelled:
            logger.info("[ms_watchdog] WATCHDOG_SUPPRESSED reason=call_cancelled q_gen=%d", q_gen)
            return

        # Guard 1: stale question generation — new question owns silence
        if q_gen != 0 and q_gen != self._q_gen:
            logger.info(
                "[ms_watchdog] WATCHDOG_SUPPRESSED reason=stale_q_gen stale=%d current=%d",
                q_gen, self._q_gen,
            )
            return

        # Guard 2: TTS currently playing — re-arm for post-TTS so the watchdog fires
        # 3 s after the audio ends, rather than silently dying here.
        if self._tts_playing:
            logger.info(
                "[ms_watchdog] WATCHDOG_SUPPRESSED reason=tts_playing q_gen=%d — re-arming for post-TTS",
                q_gen,
            )
            if not self._cancelled:
                _rearm_at = time.time()
                _rearm_gen = self._q_gen
                self._no_input_watchdog_task = asyncio.create_task(
                    self._no_input_watchdog(_rearm_at, _rearm_gen),
                    name="ms_silence_no_input_watchdog",
                )
            return

        # Guard 3: any caller engagement since watchdog was armed
        if self.last_engagement_at > armed_at:
            logger.info(
                "[ms_watchdog] WATCHDOG_SUPPRESSED reason=caller_engaged last=%.3f armed=%.3f",
                self.last_engagement_at, armed_at,
            )
            return

        # Guard 4: caller-requested pause mode
        _sess = self._get_session() if self._get_session else {}
        if (_sess or {}).get("caller_pause_active"):
            logger.info("[ms_watchdog] WATCHDOG_SUPPRESSED reason=pause_mode q_gen=%d", q_gen)
            return

        # Guard 5: another recovery phrase already mid-play
        if self.currently_reasking:
            logger.info("[ms_watchdog] WATCHDOG_SUPPRESSED reason=currently_reasking q_gen=%d", q_gen)
            return

        # Guard 6: LLM is processing a transcript
        if self._llm_busy:
            logger.info("[ms_watchdog] WATCHDOG_SUPPRESSED reason=llm_busy q_gen=%d", q_gen)
            return

        self._no_input_reask_count += 1
        _attempt = self._no_input_reask_count
        _state = (_sess or {}).get("state", "")

        logger.info(
            "[ms_watchdog] WATCHDOG_FIRE attempt=#%d state=%s q_gen=%d",
            _attempt, _state, q_gen,
        )

        # ── Graceful exit on 3rd+ attempt ──────────────────────────────────
        if _attempt >= 3:
            phrase = (
                "I'm sorry, I'm having trouble hearing you right now. "
                "Please call again in a moment."
            )
            self.currently_reasking = True
            await self._tts_text_queue.put(phrase)
            if self._on_reask:
                asyncio.create_task(self._on_reask(phrase))
            logger.info("[ms_watchdog] graceful exit — max attempts reached")
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                self.currently_reasking = False
                return
            self.currently_reasking = False
            await self._transfer()
            return

        # ── Build contextual re-ask phrase ─────────────────────────────────
        if _attempt == 1:
            _prefix = "Sorry, I didn't catch that"
        else:  # attempt 2
            _prefix = "I'm sorry, I'm still not hearing you clearly. Let's try again"

        if _state in ("GREETING", "DETECT_INTENT", ""):
            phrase = _prefix + " — how can I help today?"
        elif _state == "ASK_LOCATION":
            phrase = _prefix + " — please say Alcester or Redditch."
        elif _state in (
            "COLLECT_NAME", "COLLECT_NAME_RETURNING",
            "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
        ):
            _nf = (_sess or {}).get("name_fragment")
            if _nf:
                phrase = _prefix + " \u2014 please say: my surname is\u2026"
            else:
                phrase = _prefix + " \u2014 please say: my first name is\u2026"
        elif _state in ("CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING"):
            phrase = (
                _prefix + " — please say yes if I can use this number, "
                "or no if you'd like to use a different one."
            )
        elif _state in (
            "PRESENT_DAYS", "PRESENT_DAYS_RESCHEDULE",
            "PRESENT_TIMES", "PRESENT_TIMES_RESCHEDULE",
        ):
            _lq = (_sess or {}).get("last_question", "")
            phrase = _lq if _lq else _prefix + " — which option works best?"
        elif _state == "CONFIRM_BOOKING":
            phrase = _prefix + " — please say yes to confirm, or no to change it."
        elif _state in (
            "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE"
        ):
            if (_sess or {}).get("phone_awaiting_dtmf"):
                phrase = _prefix + " — please enter the phone number using your keypad."
            else:
                phrase = _prefix + " — please say the phone number slowly."
        elif _state in ("LOOKUP_RESCHEDULE", "LOOKUP_CANCEL"):
            if (_sess or {}).get("lookup_correction_mode"):
                phrase = _prefix + " — what first name and surname was the booking under?"
            else:
                phrase = _prefix + " — could you say that again?"
        else:
            _lq = (_sess or {}).get("last_question") or self.last_question
            if _lq and _lq.strip():
                phrase = _prefix + ". " + _lq.strip()
            else:
                phrase = _prefix + " — could you say that again?"

        logger.info("[ms_watchdog] WATCHDOG_FIRE prompt=%r attempt=#%d state=%s", phrase[:80], _attempt, _state)
        self.currently_reasking = True
        await self._tts_text_queue.put(phrase)
        if self._on_reask:
            asyncio.create_task(self._on_reask(phrase))

        # Wait ~5 s for TTS to play before re-arming.
        # CancelledError means caller spoke during the phrase — reset and exit.
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            self.currently_reasking = False
            return
        self.currently_reasking = False

        # Re-arm: replace self in the watchdog slot and restart the main silence
        # timer.  We do NOT call _restart_timer() here to avoid self-cancellation
        # (it would call _cancel_timer() which holds self._no_input_watchdog_task
        # and cancels it — i.e. cancels the currently-running coroutine, which
        # is harmless but unnecessary).  Instead we update the slots directly.
        if self._cancelled or q_gen != self._q_gen:
            return

        _new_armed_at = time.time()
        _my_q_gen = self._q_gen
        _session2 = self._get_session() if self._get_session else None
        self._replay_flow_step = (_session2 or {}).get("flow_step", -1) if _session2 else -1
        # Restart main silence timer (stale after re-ask)
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._run(_my_q_gen), name="ms_silence_timer")
        # Self-replace: current task is about to return; new task owns the slot.
        self._no_input_watchdog_task = asyncio.create_task(
            self._no_input_watchdog(_new_armed_at, _my_q_gen),
            name="ms_silence_no_input_watchdog",
        )
        logger.info(
            "[ms_watchdog] WATCHDOG_ARMED q_gen=%d wait=%.1fs (re-arm after attempt #%d)",
            _my_q_gen, _wait, _attempt,
        )

    def _restart_timer(self) -> None:
        if self._cancelled:   # guard: don't restart after teardown
            return
        self._cancel_timer()
        self.currently_reasking = False
        _session = self._get_session() if self._get_session else None
        self._replay_flow_step = (_session or {}).get("flow_step", -1) if _session else -1
        _my_q_gen = self._q_gen  # bind timer to current question generation
        self._task = asyncio.create_task(self._run(_my_q_gen), name="ms_silence_timer")
        # Arm the no-input watchdog.  Set NO_INPUT_WATCHDOG_SEC=0 to disable it
        # (e.g. in the automated test harness where silence scenarios are driven
        # by explicit transcript injection and a 3-second watchdog would race).
        import os as _os_w
        _wdg_wait = float(_os_w.getenv("NO_INPUT_WATCHDOG_SEC", "3.0"))
        if _wdg_wait > 0:
            _armed_at = time.time()
            self._no_input_watchdog_task = asyncio.create_task(
                self._no_input_watchdog(_armed_at, _my_q_gen),
                name="ms_silence_no_input_watchdog",
            )
            logger.info(
                "[ms_watchdog] WATCHDOG_ARMED q_gen=%d wait=%.1fs (via _restart_timer)",
                _my_q_gen, _wdg_wait,
            )
        else:
            logger.info("[ms_watchdog] WATCHDOG_NOT_ARMED reason=NO_INPUT_WATCHDOG_SEC=0 q_gen=%d", _my_q_gen)
        logger.debug("[ms_silence] timer started (q_gen=%d)", _my_q_gen)

    def _cancel_timer(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task              = None
        # Cancel the no-input watchdog with the same triggers as the main timer.
        # All callers of _cancel_timer (speech detected, transcript received, TTS
        # starting for a new response, LLM busy) should also abort the watchdog.
        if self._no_input_watchdog_task and not self._no_input_watchdog_task.done():
            logger.info(
                "[ms_watchdog] WATCHDOG_CANCELLED caller=%s",
                # cheaply identify the caller for log traceability
                __import__("traceback").extract_stack()[-2].name,
            )
            self._no_input_watchdog_task.cancel()
        self._no_input_watchdog_task = None
        self.currently_reasking = False

    async def _run(self, q_gen: int = 0) -> None:
        """
        Flat sequential re-ask coroutine.

        q_gen — the _q_gen value at timer creation.  If on_question_asked() fires
        after this task starts (new question), _q_gen advances and we return early
        at each window check.  This prevents stale timers from a previous question
        firing during a new question's silence window.

        Window 1: per-state sleep → since_audio guard → re-ask #1 → 5s TTS wait
        Window 2: 15s sleep → since_audio guard → re-ask #2 → 5s TTS wait
        Window 3: 15s sleep → since_audio guard → transfer

        Pause mode (caller said "hang on" etc.):
          While caller_pause_active: 45s extended windows, no re-ask.
          At 45s total silence: check-in phrase.
          At 90s total silence: termination phrase + transfer.

        Never recurses with create_task.  CancelledError exits cleanly at
        any sleep — caller spoke (on_speech_started) or Susie spoke
        (on_tts_started / on_transcript_received).
        """
        # ── Pause mode branch ─────────────────────────────────────────────
        _session = self._get_session() if self._get_session else None
        if _session and _session.get("caller_pause_active"):
            # Loop in 45-second increments while caller is paused.
            # CancelledError exits when speech is detected.
            while True:
                try:
                    await asyncio.sleep(45.0)
                    await asyncio.sleep(0)  # deliver pending cancels
                except asyncio.CancelledError:
                    return

                since_audio = time.time() - self.last_audio_received_at
                if since_audio < 3.5:
                    return  # speech detected — timer will be restarted by on_question_asked

                # Re-fetch session (may have been reassigned) to get latest state
                _session = self._get_session() if self._get_session else _session
                if not _session or not _session.get("caller_pause_active"):
                    return  # pause cleared by substantive utterance

                _session["pause_silence_total"] = (
                    _session.get("pause_silence_total", 0.0) + 45.0
                )
                _total = _session["pause_silence_total"]
                logger.info(
                    "[ms_silence] pause mode: %.0fs total silence", _total
                )

                if _total >= 90.0:
                    # Caller has been silent too long — terminate the call gracefully
                    phrase = (
                        "I'll let you go — give us a ring back when you're ready "
                        "and we'll get you sorted."
                    )
                    await self._tts_text_queue.put(phrase)
                    logger.info("[ms_silence] pause 90s limit reached — terminating")
                    await self._transfer()
                    return

                if _total >= 45.0:
                    # First check-in — let caller know we're still here
                    phrase = (
                        "Just checking you're still there — "
                        "no rush at all, take your time."
                    )
                    await self._tts_text_queue.put(phrase)
                    logger.info("[ms_silence] pause 45s check-in played")
                    # Continue loop for another 45s

            return  # unreachable but guards against fall-through

        from app.silence_handler import get_silence_response, get_silence_threshold, log_silence_event
        q = self.last_question.strip()

        # ── Window 1: per-state silence threshold ──────────────────────────
        # Falls back to SILENCE_THRESHOLDS["default"] for unmapped states.
        # SILENCE_WINDOW_1_SEC env var overrides all per-state values (used by
        # the automated test runner to avoid collision with TURN_WAIT_SECONDS).
        import os as _os
        _env_override = _os.getenv("SILENCE_WINDOW_1_SEC")
        _w1 = float(_env_override) if _env_override else get_silence_threshold(self.current_state)
        try:
            await asyncio.sleep(_w1)
            # Yield once more so any task.cancel() that arrived while we were
            # sleeping (but after sleep() returned normally) is delivered here
            # before we check the guards — fixes the race where _llm_busy is
            # not yet True at the moment we check it.
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            return

        since_audio = time.time() - self.last_audio_received_at
        if since_audio < 3.5:
            return
        if self.currently_reasking:
            return
        if self._llm_busy:
            return

        # Stale question generation guard — if on_question_asked() fired after
        # this timer was created, _q_gen has advanced and we belong to the old
        # question.  Return silently; the new question has its own timer.
        if q_gen != 0 and q_gen != self._q_gen:
            logger.info(
                "[ms_silence] W1: stale q_gen %d vs current %d — suppressed",
                q_gen, self._q_gen,
            )
            return

        _session_now = self._get_session() if self._get_session else None
        _current_step = (_session_now or {}).get("flow_step", -1) if _session_now else -1
        if _current_step != self._replay_flow_step:
            logger.info(
                "[ms_silence] W1 stale replay suppressed stored_step=%d current_step=%d",
                self._replay_flow_step, _current_step,
            )
            return
        if self._cancelled:
            return

        # Sync last_question from live session to prevent stale TTS content
        # from being replayed when on_tts_finished updated it after the step transition.
        _live_q_w1 = (_session_now or {}).get("last_question", "")
        if _live_q_w1 and _live_q_w1.strip() != self.last_question:
            logger.info(
                "[ms_silence] W1: syncing last_question from %r to live %r",
                self.last_question[:40], _live_q_w1[:40],
            )
            self.last_question = _live_q_w1.strip()
            q = self.last_question

        # Phone-capture DTMF guard: if digit collection is already underway,
        # do not interrupt with a spoken prompt — the keypress flow owns timing.
        if (
            self.current_state in (
                "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE"
            )
            and (_session_now or {}).get("phone_dtmf_buffer")
        ):
            logger.debug("[ms_silence] W1: DTMF digits in buffer — suppressing phone recovery")
            return

        self.currently_reasking = True
        self.reask_count += 1
        secs_since_q = time.time() - self._last_question_set_at
        phrase1 = get_silence_response(
            self.current_state, self._consecutive_silence_count
        )
        if self._consecutive_silence_count >= 1:
            self._consecutive_silence_count = 0
        else:
            self._consecutive_silence_count += 1
        log_silence_event(self.current_state, _w1, phrase1, self.reask_count - 1)
        logger.info(
            "[ms_reask] firing re-ask #%d of last_question: %r  time_since_question=%.1fs",
            self.reask_count, q[:80], secs_since_q,
        )
        _reask1 = phrase1 + (" " + q if q else "")

        # Name-capture structured recovery: replace generic phrase+last_question
        # with a substate-aware scaffold prompt. name_fragment is set (in session)
        # when the first name has been accepted, so its presence identifies the
        # surname step.  One recovery fires at 3 s; W2/W3 handle the fallback.
        if self.current_state in (
            "COLLECT_NAME", "COLLECT_NAME_RETURNING",
            "COLLECT_NAME_RESCHEDULE", "COLLECT_NAME_CANCEL",
        ):
            if (_session_now or {}).get("name_fragment"):
                _reask1 = (
                    "Sorry, I didn't quite catch that \u2014 "
                    "please say: my surname is..."
                )
            else:
                _reask1 = (
                    "Sorry, I didn't quite catch that \u2014 "
                    "please say: my first name is..."
                )

        # Phone-capture structured recovery: replace generic phrase+last_question
        # with a targeted prompt. For COLLECT_PHONE, distinguish keypad vs speech.
        # One structured recovery fires at 3 s; W2/W3 handle the longer fallback.
        elif self.current_state in ("CONFIRM_PHONE", "CONFIRM_PHONE_RETURNING"):
            _reask1 = (
                "Sorry, I didn't quite catch that — "
                "please say yes if I can use this number, "
                "or no if you'd like to use a different one."
            )
        elif self.current_state in (
            "COLLECT_PHONE", "COLLECT_PHONE_RETURNING", "COLLECT_PHONE_RESCHEDULE"
        ):
            if (_session_now or {}).get("phone_awaiting_dtmf"):
                _reask1 = (
                    "Sorry, I didn't quite catch that — "
                    "please enter the phone number using your keypad."
                )
            else:
                _reask1 = (
                    "Sorry, I didn't quite catch that — "
                    "please say the phone number slowly."
                )

        await self._tts_text_queue.put(_reask1)
        if self._on_reask:
            asyncio.create_task(self._on_reask(_reask1))

        # Wait for TTS to finish playing
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            self.currently_reasking = False
            return
        self.currently_reasking = False

        # ── Window 2: 15 s silence ─────────────────────────────────────────
        try:
            await asyncio.sleep(15.0)
            await asyncio.sleep(0)  # deliver any pending cancel before guard checks
        except asyncio.CancelledError:
            return

        since_audio = time.time() - self.last_audio_received_at
        if since_audio < 1.0:   # reduced from 3.5 — AssemblyAI empty transcripts can reset the clock
            return
        if self.currently_reasking:
            return
        if self._llm_busy:
            return

        # Stale question generation guard (same as W1)
        if q_gen != 0 and q_gen != self._q_gen:
            logger.info(
                "[ms_silence] W2: stale q_gen %d vs current %d — suppressed",
                q_gen, self._q_gen,
            )
            return

        _session_now = self._get_session() if self._get_session else None
        _current_step = (_session_now or {}).get("flow_step", -1) if _session_now else -1
        if _current_step != self._replay_flow_step:
            logger.info(
                "[ms_silence] W2 stale replay suppressed stored_step=%d current_step=%d",
                self._replay_flow_step, _current_step,
            )
            return
        if self._cancelled:
            return

        # Sync last_question from live session (same guard as W1)
        _live_q_w2 = (_session_now or {}).get("last_question", "")
        if _live_q_w2 and _live_q_w2.strip() != self.last_question:
            logger.info(
                "[ms_silence] W2: syncing last_question from %r to live %r",
                self.last_question[:40], _live_q_w2[:40],
            )
            self.last_question = _live_q_w2.strip()
            q = self.last_question

        self.currently_reasking = True
        self.reask_count += 1
        secs_since_q = time.time() - self._last_question_set_at
        phrase2 = get_silence_response(
            self.current_state, self._consecutive_silence_count
        )
        if self._consecutive_silence_count >= 1:
            self._consecutive_silence_count = 0
        else:
            self._consecutive_silence_count += 1
        log_silence_event(self.current_state, 15.0, phrase2, self.reask_count - 1)
        logger.info(
            "[ms_reask] firing re-ask #%d of last_question: %r  time_since_question=%.1fs",
            self.reask_count, q[:80], secs_since_q,
        )
        _reask2 = phrase2 + (" " + q if q else "")
        await self._tts_text_queue.put(_reask2)
        if self._on_reask:
            asyncio.create_task(self._on_reask(_reask2))

        # Wait for TTS to finish playing
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            self.currently_reasking = False
            return
        self.currently_reasking = False

        # ── Window 3: 15 s silence → transfer ─────────────────────────────
        try:
            await asyncio.sleep(15.0)
        except asyncio.CancelledError:
            return

        since_audio = time.time() - self.last_audio_received_at
        if since_audio < 3.5:
            return

        await self._transfer()

    async def _transfer(self) -> None:
        if self._cancelled:
            logger.info("[ms_silence] _transfer suppressed — handler already cancelled (stale call)")
            return
        logger.info("[ms_silence] max reasks reached — transferring")
        phrase = (
            "I'm having a little trouble hearing you — "
            "let me transfer you to someone who can help."
        )
        # Save to conversation_history so the test evaluator can see it.
        # _on_transfer is the _silence_history_fn closure in WebSocketCallHandler.
        if self._on_transfer:
            asyncio.create_task(self._on_transfer(phrase))
        await self._tts_text_queue.put(phrase)
        # Set the silence_transfer flag so _should_allow_transfer() passes.
        # _trigger_transfer is a closure that sets session["silence_transfer"]
        # before calling _on_transfer_request — see SilenceHandler instantiation.
        try:
            await self._trigger_transfer()
        except Exception as exc:
            logger.error("[ms_silence] transfer error: %r", exc)


# ---------------------------------------------------------------------------
# Active handler registry (call_sid → WebSocketCallHandler)
# ---------------------------------------------------------------------------

# Maps inbound call_sid → active handler for that call.
# Used by the /ms/test/inject-transcript endpoint so the test runner can
# drive Susie's conversation pipeline directly without going through STT.
# Handlers register on "start" event and deregister in _cleanup().
_active_handlers: Dict[str, "WebSocketCallHandler"] = {}


# ---------------------------------------------------------------------------
# Main handler class
# ---------------------------------------------------------------------------

class WebSocketCallHandler:
    """
    Manages a single Twilio Media Streams WebSocket call.

    Instantiated once per incoming WebSocket connection by router.py.
    All pipeline state lives on this instance; nothing is shared between calls.

    Usage:
        handler = WebSocketCallHandler(websocket)
        await handler.handle()

    Stability contract:
        _call_stable is set True after the first complete STT -> LLM -> TTS cycle.
        If the call ends before this, router.py logs "UNSTABLE CALL" and may
        redirect to the legacy system.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket   = websocket

        # Call identity — populated from the "start" event
        self.call_sid:   Optional[str] = None
        self.stream_sid: Optional[str] = None

        # Session dict — loaded / created on "start" event
        self.session: Dict[str, Any] = {}

        # ── Pipeline queues ────────────────────────────────────────────────
        self.audio_in_queue:   asyncio.Queue = asyncio.Queue()  # raw mulaw bytes
        self.stt_input_queue:  asyncio.Queue = asyncio.Queue()  # PCM16 16kHz bytes
        self.transcript_queue: asyncio.Queue = asyncio.Queue()  # str utterances
        self.tts_text_queue:   asyncio.Queue = asyncio.Queue()  # str text chunks
        self.audio_out_queue:  asyncio.Queue = asyncio.Queue()  # base64 str payloads

        # ── Pipeline components ────────────────────────────────────────────
        self._audio_in_proc  = AudioInputProcessor()
        self._audio_out_proc = AudioOutputProcessor()
        self._stt_stream     = STTStream()

        # ── Control events ─────────────────────────────────────────────────
        self._stop_event    = asyncio.Event()  # set when "stop" received or WS closes
        self._started_event = asyncio.Event()  # set when "start" event is processed

        # ── Barge-in / TTS state ───────────────────────────────────────────
        self._tts_task:  Optional[asyncio.Task] = None  # current TTS chunk task
        self._clearing   = False   # True while Twilio buffer is draining after barge-in
        self._llm_busy   = False   # True while Claude is generating

        # Barge-in timing/state — used for false-trigger gate and ack injection
        self._current_tts_text:    str   = ""    # text being synthesised right now
        self._barge_in_pending:    bool  = False  # True between partial and final transcript
        self._barge_in_ts:         float = 0.0   # monotonic time when barge-in first fired
        self._barge_in_duration:   float = 0.0   # elapsed seconds (set by _on_final_transcript_clear)
        # Recovery flag: True after we've already played a barge-in ack and are
        # waiting for the caller's actual utterance.  Prevents ack-loop when the
        # caller's continued speech triggers a second barge-in before the first
        # utterance is processed.
        self._in_barge_in_recovery: bool = False

        # Prompt generation counter — monotonically increasing.
        # Incremented whenever a confirmed barge-in clears the active TTS.
        # Each _delayed_tts_finished task captures the generation at creation
        # time and is silently ignored if the generation has advanced, preventing
        # stale "does that sound OK?" callbacks from overwriting last_question
        # and re-arming the silence timer after the flow has moved on.
        self._tts_gen: int = 0

        # ── Latency / timing ──────────────────────────────────────────────
        self._last_audio_at:          float = 0.0   # monotonic time of last audio sent to Twilio
        self._last_filler_at:         float = 0.0   # monotonic time of last filler phrase played
        self._bad_line_played         = False        # once-per-call bad-line phrase guard
        self._last_audio_received_at: float = 0.0   # monotonic time of last inbound Twilio audio
        # Text of the TTS utterance currently in-flight through audio_out_queue.
        # Set in _tts_loop when synthesis completes; cleared in send_loop when
        # the _TTS_DONE_SENTINEL is drained — at that point on_tts_finished fires.
        self._tts_text_pending: str = ""
        # _tts_last_start_ts captured when the current chunk's on_tts_started() fired.
        # Forwarded to _delayed_tts_finished so on_tts_finished() can detect whether a
        # newer chunk has started before clearing _tts_playing (fixes multi-chunk gap).
        self._tts_pending_chunk_start_ts: float = 0.0

        # ── Silence handler (4-second re-ask) ─────────────────────────────
        # Created eagerly so _handle_media can call on_audio_received() before
        # the LLM loop starts.  tts_text_queue exists from __init__ so it's
        # safe to pass here.
        # Wrap _on_transfer_request so silence-triggered transfers set the
        # silence_transfer flag before the guard runs.  We use a closure
        # (not a bound-method reference) because self.session is reassigned
        # on the "start" event — the closure captures `self`, not the dict.
        async def _silence_transfer_fn() -> None:
            self.session["silence_transfer"] = True
            await self._on_transfer_request()

        async def _silence_reask_fn(text: str) -> None:
            """Save re-ask to conversation_history so the test evaluator can see it."""
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": text}
            )
            await save_session(self.call_sid, self.session)

        async def _silence_history_fn(text: str) -> None:
            """Save transfer phrase to conversation_history so evaluator can check it.
            The transfer phrase is played directly from SilenceHandler (bypassing
            FlowEngine) so without this callback it would be invisible to the test
            evaluator's transfer_played / transfer_has_trouble_hearing checks."""
            self.session.setdefault("conversation_history", []).append(
                {"role": "assistant", "content": text}
            )
            await save_session(self.call_sid, self.session)

        self._silence_handler = SilenceHandler(
            tts_text_queue=self.tts_text_queue,
            trigger_transfer_fn=_silence_transfer_fn,
            on_reask=_silence_reask_fn,
            on_transfer=_silence_history_fn,
            # Lambda captures self (not the dict) so it always returns the
            # current session even after self.session is reassigned on "start".
            get_session=lambda: self.session,
        )

        # ── Call stability ─────────────────────────────────────────────────
        # Set True after the first complete STT -> LLM -> TTS cycle.
        # Router uses this to distinguish "pipeline failed at startup" from
        # "call ended normally or after a stable conversation started".
        self._call_stable: bool = False

    # ========================================================================
    # Public entry point
    # ========================================================================

    async def handle(self) -> None:
        """
        Main entry point called once per WebSocket connection.

        Starts all pipeline coroutines concurrently, waits for the stop event,
        then cancels and cleans up.

        Sets self._call_stable = True after the first complete STT->LLM->TTS cycle.
        Raises nothing — all exceptions are caught internally.
        """
        logger.info("[ms_conn] new WebSocket connection")
        await self.websocket.accept()

        tasks = [
            asyncio.create_task(self._receive_loop(),  name="ms_receive"),
            asyncio.create_task(self._audio_in_loop(), name="ms_audio_in"),
            asyncio.create_task(self._stt_loop(),      name="ms_stt"),
            asyncio.create_task(self._llm_loop(),      name="ms_llm"),
            asyncio.create_task(self._tts_loop(),      name="ms_tts"),
            asyncio.create_task(self._send_loop(),     name="ms_send"),
            # _silence_reask_loop replaced by SilenceHandler (event-driven)
        ]

        try:
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("[ms_conn] handle(): unexpected error: %r", exc)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._cleanup()

    # ========================================================================
    # Receive loop
    # ========================================================================

    async def _receive_loop(self) -> None:
        """
        Read JSON messages from the Twilio WebSocket continuously.

          connected  -> log
          start      -> _handle_start() (creates session, sets _started_event)
          media      -> decode base64, enqueue raw mulaw bytes
          stop       -> set _stop_event, exit
        """
        try:
            while not self._stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(
                        self.websocket.receive_text(),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    continue
                except WebSocketDisconnect:
                    logger.info("[ms_conn] Twilio WebSocket disconnected")
                    self._stop_event.set()
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("[ms_conn] non-JSON frame: %r", raw[:80])
                    continue

                event = msg.get("event")

                if event == "connected":
                    logger.info(
                        "[ms_conn] connected protocol=%s version=%s",
                        msg.get("protocol"), msg.get("version"),
                    )

                elif event == "start":
                    await self._handle_start(msg)

                elif event == "media":
                    await self._handle_media(msg)

                elif event == "dtmf":
                    await self._handle_dtmf(msg)

                elif event == "stop":
                    logger.info("[ms_conn] stop event stream_sid=%s", msg.get("streamSid"))
                    self._stop_event.set()
                    break

                else:
                    logger.debug("[ms_conn] unknown event=%r", event)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _receive_loop error: %r", exc)
            self._stop_event.set()

    async def _handle_dtmf(self, msg: Dict[str, Any]) -> None:
        """
        Process a Twilio "dtmf" event (keypad digit press).

        Accumulates digits into session["phone_dtmf_buffer"].
        When 10–11 digits are collected, synthesises a transcript so the
        COLLECT_PHONE hard gate can process the phone number naturally.

        Only active while state == "COLLECT_PHONE" and phone_awaiting_dtmf=True.
        Resets the silence timer after each keypress so the caller can type
        without triggering a silence re-ask mid-entry.
        """
        if not self.session:
            return

        digit = (msg.get("dtmf") or {}).get("digit", "")
        if not digit or digit in ("#", "*"):
            return

        # ASK_LOCATION: digit 1 → alcester, digit 2 → redditch (immediate, no accumulation)
        if self.session.get("state") == "ASK_LOCATION":
            if digit == "1":
                logger.info("[ms_conn] DTMF digit=1 → synthetic transcript 'alcester'")
                await self.transcript_queue.put("alcester")
            elif digit == "2":
                logger.info("[ms_conn] DTMF digit=2 → synthetic transcript 'redditch'")
                await self.transcript_queue.put("redditch")
            return

        # Only accumulate DTMF while in phone-collection state
        if self.session.get("state") not in ("COLLECT_PHONE", "COLLECT_PHONE_RETURNING"):
            return

        buf = self.session.get("phone_dtmf_buffer", "") + digit
        self.session["phone_dtmf_buffer"] = buf

        # Each keypress resets the silence timer (caller is actively typing)
        self._silence_handler.last_audio_received_at = time.time()
        self._silence_handler.last_engagement_at     = time.time()

        logger.info("[ms_conn] DTMF digit=%r buf=%r", digit, buf)

        if len(buf) >= 11:
            # Full UK number collected via keypad (min 11 digits) — push as
            # synthetic transcript.  10-digit buffers are incomplete (BUG 5).
            complete = buf[:11]
            self.session["phone_dtmf_buffer"]   = ""
            self.session["phone_awaiting_dtmf"] = False
            logger.info("[ms_conn] DTMF buffer complete → synthetic transcript %r", complete)
            await self.transcript_queue.put(complete)

    async def _handle_start(self, msg: Dict[str, Any]) -> None:
        """
        Process the Twilio "start" event.

        Extracts call_sid / stream_sid, loads or creates the session,
        stamps stream_sid into the session, and fires _started_event so
        the other pipeline loops can begin.
        """
        start_data      = msg.get("start", {})
        self.stream_sid = msg.get("streamSid") or start_data.get("streamSid", "")
        self.call_sid   = start_data.get("callSid", "")

        custom_params = start_data.get("customParameters", {})
        twilio_from   = custom_params.get("twilio_from") or start_data.get("from", "")
        twilio_to     = custom_params.get("twilio_to")   or start_data.get("to",   "")

        logger.info(
            "[ms_conn] start call_sid=%s stream_sid=%s from=%s to=%s",
            self.call_sid, self.stream_sid, twilio_from, twilio_to,
        )

        # Check Redis for From/To numbers pre-cached by /ms/incoming POST handler.
        # Twilio does NOT reliably forward customParameters or caller numbers into
        # the WebSocket start event — Redis is the only reliable fallback.
        if (not twilio_from or not twilio_to) and self.call_sid:
            try:
                from .session import _get_redis
                _redis = _get_redis()
                if _redis:
                    if not twilio_from:
                        _cached_from = await _redis.get(f"ms_caller:{self.call_sid}")
                        if _cached_from:
                            twilio_from = _cached_from.decode() if isinstance(_cached_from, bytes) else _cached_from
                            logger.info("[ms_conn] twilio_from from Redis: %s", twilio_from)
                            await _redis.delete(f"ms_caller:{self.call_sid}")
                    if not twilio_to:
                        _cached_to = await _redis.get(f"ms_to:{self.call_sid}")
                        if _cached_to:
                            twilio_to = _cached_to.decode() if isinstance(_cached_to, bytes) else _cached_to
                            logger.info("[ms_conn] twilio_to from Redis: %s", twilio_to)
                            await _redis.delete(f"ms_to:{self.call_sid}")
            except Exception as _exc:
                logger.warning("[ms_conn] Redis caller lookup failed: %r", _exc)

        initial: Dict[str, Any] = {}

        # Direct-WS test mode: the call_runner sends a fake accountSid that
        # contains "direct_ws".  Flag it in the session so flow.py can
        # auto-complete steps that have no subsequent user turn in test scripts.
        _account_sid = start_data.get("accountSid", "")
        if "direct_ws" in _account_sid:
            initial["direct_ws_test"] = True
            logger.info("[ms_conn] direct_ws_test mode detected (accountSid=%s)", _account_sid)

        if twilio_from:
            initial["twilio_from"] = twilio_from
            if twilio_from.startswith("+44"):
                initial["twilio_from_local"] = "0" + twilio_from[3:]
        if twilio_to:
            initial["twilio_to"] = twilio_to
            # Resolve clinic_id from the dialled number so tools/SMS/config use the right clinic.
            from app.clinic_config import clinic_id_from_twilio_to
            initial["clinic_id"] = clinic_id_from_twilio_to(twilio_to)
            logger.info("[ms_conn] clinic_id resolved: %s (to=%s)", initial["clinic_id"], twilio_to)

        # ── Layer 2 fallback: env var override ───────────────────────────
        # If clinic_id is still not resolved (twilio_to was empty through all
        # three resolution paths: customParameters, Redis, start_data), use the
        # MEDIA_STREAMS_CLINIC_ID env var as an absolute last resort.
        # Set MEDIA_STREAMS_CLINIC_ID=theorem on Render for this service.
        if not initial.get("clinic_id"):
            import os as _os
            _env_cid = _os.getenv("MEDIA_STREAMS_CLINIC_ID", "").strip()
            if _env_cid:
                initial["clinic_id"] = _env_cid
                logger.warning(
                    "[ms_conn] clinic_id NOT resolved from twilio_to — "
                    "using env MEDIA_STREAMS_CLINIC_ID=%s (twilio_to=%r)",
                    _env_cid, twilio_to,
                )
            else:
                logger.error(
                    "[ms_conn] clinic_id unresolved AND MEDIA_STREAMS_CLINIC_ID not set — "
                    "calls will route to demo/Google Calendar. "
                    "Set MEDIA_STREAMS_CLINIC_ID on Render.",
                )

        self.session = await get_or_create_session(self.call_sid, initial=initial)
        self.session["stream_sid"]   = self.stream_sid
        self.session["ws_connected"] = True

        # Register in the active-handler map so /ms/test/inject-transcript
        # can drive the conversation without going through STT.
        if self.call_sid:
            _active_handlers[self.call_sid] = self
            logger.info(
                "[ms_conn] WS session registered sid=%s total_active=%d",
                self.call_sid, len(_active_handlers),
            )

        # Populate collected.phone from Twilio caller-ID so Susie never asks for it.
        if twilio_from:
            logger.info("[ms_conn] caller number from Twilio: %s", twilio_from)
            collected = self.session.setdefault("collected", {})
            if not collected.get("phone"):
                collected["phone"] = twilio_from
            self.session["phone_from_twilio"] = True
        else:
            logger.info("[ms_conn] no caller number from Twilio — will collect manually")

        await save_session(self.call_sid, self.session)

        # Instantiate per-call logger (stored on instance, not in session — not JSON-serialisable)
        from app.call_logger import CallLogger
        self._call_logger = CallLogger(self.call_sid, self.session)

        self._started_event.set()

        # Inject greeting asynchronously (no LLM round-trip)
        asyncio.create_task(self._inject_greeting())

    async def _handle_media(self, msg: Dict[str, Any]) -> None:
        """
        Process a Twilio "media" event.

        Decodes the base64 mulaw payload and puts raw bytes onto audio_in_queue.
        Audio ALWAYS flows to AssemblyAI regardless of _clearing state.
        Dropping audio while _clearing=True was the barge-in deadlock:
          _clearing=True → no audio → no STT final → _on_final_transcript_clear
          never fires → _clearing stays True forever.
        """
        payload_b64 = msg.get("media", {}).get("payload", "")
        if not payload_b64:
            return

        try:
            raw_mulaw = base64.b64decode(payload_b64)
        except Exception as exc:
            logger.warning("[ms_conn] base64 decode error: %r", exc)
            return

        self._last_audio_received_at = time.monotonic()
        self._silence_handler.on_audio_received()
        self.audio_in_queue.put_nowait(raw_mulaw)

        # ── Energy VAD: cancel silence timer the moment caller speaks ─────────
        # Twilio μ-law silence packets consist almost entirely of 0xFF bytes
        # (G.711 μ-law encoding of PCM zero).  When the caller speaks, non-0xFF
        # bytes appear immediately — cancelling the re-ask timer here closes the
        # 1-3 second gap between caller speaking and AssemblyAI delivering a
        # partial transcript, which was the root cause of questions being asked
        # twice during real calls.
        # Only checked when the silence timer is actually running (task exists
        # and is not done) so this adds near-zero overhead during normal flow.
        if (
            not self._clearing
            and self._silence_handler._task is not None
            and not self._silence_handler._task.done()
            and len(raw_mulaw) - raw_mulaw.count(0xFF) > 3
        ):
            self._silence_handler.on_speech_started()

    # ========================================================================
    # Audio input loop
    # ========================================================================

    async def _audio_in_loop(self) -> None:
        """
        Wait for the "start" event, then run AudioInputProcessor which
        converts mulaw 8kHz -> PCM16 16kHz and writes to stt_input_queue.
        """
        await self._wait_for_start("audio_in_loop")
        try:
            await self._audio_in_proc.process_stream(
                self.audio_in_queue,
                self.stt_input_queue,
                self._stop_event,
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _audio_in_loop error: %r", exc)

    # ========================================================================
    # STT loop
    # ========================================================================

    async def _stt_loop(self) -> None:
        """
        Wait for the "start" event, then run STTStream which connects to
        AssemblyAI and puts FinalTranscript utterances into transcript_queue.

        On AssemblyAI disconnect: STTStream handles reconnect internally.
        If all reconnects fail, the STT loop exits — but the call continues
        (caller can still hear Susie; just can't be heard).
        """
        await self._wait_for_start("stt_loop")
        try:
            await self._stt_stream.start(
                stt_input_queue=self.stt_input_queue,
                transcript_queue=self.transcript_queue,
                stop_event=self._stop_event,
                on_partial=self._on_partial_transcript,
                on_final_clear=self._on_final_transcript_clear,
                tts_text_queue=self.tts_text_queue,
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _stt_loop error: %r", exc)

    # ========================================================================
    # LLM loop  (FlowEngine-driven — single point of decision)
    # ========================================================================

    async def _llm_loop(self) -> None:
        """
        Wait for the "start" event, then drive the booking flow.

        First caller utterance → flow.ask_current_question()  (starts the flow)
        Every subsequent utterance → flow.handle_transcript()  (advances the flow)

        That is the entire conversation logic.  Nothing else here makes
        a decision about what Susie says.
        """
        await self._wait_for_start("llm_loop")

        from .llm_stream import LLMStream
        from .flow import FlowEngine

        llm = LLMStream()

        # Build the LLM callable the flow engine will use for LLM steps.
        # It streams output directly to tts_text_queue and returns full text.
        async def _llm_fn(instruction: str, allow_tools: bool = True, error_phrase: str = None) -> str:
            result = await llm.run_instruction(
                instruction=instruction,
                session=self.session,
                tts_text_queue=self.tts_text_queue,
                call_sid=self.call_sid,
                stream_sid=self.stream_sid,
                audio_out_queue=self.audio_out_queue,
                websocket=self.websocket,
                on_transfer=self._on_transfer_request,
                allow_tools=allow_tools,
                error_phrase=error_phrase,
            )
            # Mark that LLM produced audible speech this turn so the global
            # hard-fallback in the outer loop does not fire a duplicate response.
            if result and result.strip():
                self.session["_turn_speech_emitted"] = True
            return result

        flow = FlowEngine(
            session=self.session,
            tts_queue=self.tts_text_queue,
            llm_fn=_llm_fn,
        )
        self._flow = flow

        try:
            while not self._stop_event.is_set():
                try:
                    utterance = await asyncio.wait_for(
                        self.transcript_queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                if not utterance or not utterance.strip():
                    continue

                # Drop if the previous turn is still generating
                if self._llm_busy:
                    logger.info(
                        "[ms_conn] busy — dropping utterance: %r", utterance[:80],
                    )
                    continue

                # ── Barge-in resolution ───────────────────────────────────────
                # Must run before setting _llm_busy so:
                #   - false triggers resume TTS without entering the flow
                #   - confirmed barge-ins queue an ack and wait for next utterance
                if await self._resolve_barge_in(utterance):
                    continue

                # A real utterance is being processed — barge-in recovery complete.
                self._in_barge_in_recovery = False
                self._llm_busy          = True
                self._silence_handler.on_llm_started()
                self._last_audio_at     = time.monotonic()
                self.session["llm_generation_active"] = True
                # New turn begins — allow TTS output for this response (Bug 5).
                self.session["tts_inhibit"] = False
                await save_session(self.call_sid, self.session)

                logger.info("[ms_conn] transcript received: %r", utterance[:120])

                try:
                    # ── Pause detection (before state machine) ─────────────────────
                    # If the caller said "hang on", "one sec", etc., enter pause mode.
                    # Do NOT pass utterance to the flow — do NOT advance state.
                    from app.pause_detector import detect_caller_pause_request as _detect_pause
                    _words = utterance.strip().split()
                    _is_pause = _detect_pause(utterance)
                    _is_substantive = len(_words) > 2 and not _is_pause

                    if _is_pause:
                        self.session["caller_pause_active"] = True
                        self.session["pause_silence_total"] = 0.0
                        await self.tts_text_queue.put("Of course, take your time.")
                        await save_session(self.call_sid, self.session)
                        logger.info("[ms_conn] caller pause detected — entering pause mode")
                        # Don't pass to state machine; silence timer will use 45s window
                        # We still need to re-arm the silence handler after speaking
                        self._silence_handler.on_question_asked(self.session.get("last_question", ""))
                        # Fall through to finally: clears _llm_busy
                    else:
                        # Clear pause mode if caller resumes with a substantive utterance
                        if _is_substantive and self.session.get("caller_pause_active"):
                            self.session["caller_pause_active"] = False
                            self.session["pause_silence_total"] = 0.0
                            logger.info("[ms_conn] caller_pause_active cleared — resuming flow")

                    # Record utterance for tone detection (first two turns lock the tone)
                    if not _is_pause:
                        try:
                            from app.tone_detector import ToneDetector as _ToneDetector
                            _td = self.session.get("tone_detector")
                            if not isinstance(_td, _ToneDetector):
                                _td = _ToneDetector.from_dict(self.session.get("_tone_state") or {})
                            _td.record_utterance(utterance)
                            self.session["_tone_state"] = _td.to_dict()
                        except Exception as _td_err:
                            logger.warning("[ms_conn] ToneDetector record failed: %r", _td_err)

                    if not _is_pause:
                        # BUG 1 fix — clear stale LLM reply before each transcript so
                        # post-turn diagnostic log always reflects the NEW bot output
                        self.session["last_bot_prompt"] = ""
                        # Reset per-turn speech-emission flag.  _TrackedQueue and _llm_fn
                        # both set this True whenever audible text is enqueued.
                        self.session["_turn_speech_emitted"] = False
                        if not self.session.get("flow_started"):
                            # First caller utterance — detect intent then kick off the flow.
                            self.session["flow_started"] = True
                            logger.info("[ms_conn] flow start — first utterance: %r", utterance[:80])
                            await self.tts_text_queue.put("\x00DEDUP_RESET\x00")
                            await flow.handle_transcript(utterance)
                        else:
                            logger.info(
                                "[ms_conn] flow transcript: %r  step=%s",
                                utterance[:80], self.session.get("flow_step", 0),
                            )
                            await self.tts_text_queue.put("\x00DEDUP_RESET\x00")
                            await flow.handle_transcript(utterance)

                        # ── GLOBAL HARD FALLBACK ──────────────────────────────────────
                        # If handle_transcript completed without producing any audible
                        # speech, and the turn is not already handled by a deferred
                        # path (repair / repeat / fragment / transfer / graceful exit),
                        # emit a recovery phrase + the current live re-anchor question.
                        # This is the last-resort guarantee that no turn is ever silent.
                        _turn_silent = (
                            not self.session.get("_turn_speech_emitted")
                            and not self.session.get("repair_requested")
                            and not self.session.get("repeat_requested")
                            and not self.session.get("fragment_suppressed")
                            and not self.session.get("request_transfer")
                            and not self.session.get("graceful_exit")
                            and not flow.is_complete()
                        )
                        if _turn_silent:
                            _fallback_lq = self.session.get("last_question", "")
                            _fallback_text = (
                                "Sorry, I can\u2019t answer that properly right now, "
                                "but I can still help you continue."
                            )
                            if _fallback_lq:
                                _fallback_text += f" {_fallback_lq}"
                            await self.tts_text_queue.put(_fallback_text)
                            self.session["last_question"] = _fallback_text
                            logger.warning(
                                "[ms_conn] GLOBAL HARD FALLBACK: no speech this turn "
                                "(state=%s) — emitting: %r",
                                self.session.get("state", "?"), _fallback_text[:100],
                            )

                    # ── Transfer check (deterministic flow path) ─────────────
                    # The LLM stream handles transfers that fire via tool call.
                    # The deterministic transfer path (intent=transfer in flow.py)
                    # sets request_transfer=True but bypasses the LLM stream entirely,
                    # so we must check here and fire the Twilio transfer directly.
                    if self.session.get("request_transfer"):
                        logger.info("[ms_conn] deterministic transfer flag detected — firing")
                        self.session["request_transfer"] = False
                        await self._on_transfer_request()

                    if not self._call_stable:
                        self._call_stable = True
                        logger.info("[ms_conn] call reached stable state")

                    # Diagnostic: log what the LLM last said and what question was stored
                    _llm_reply = self.session.get("last_bot_prompt", "")
                    if _llm_reply:
                        logger.info("[ms_conn] LLM response: %r", _llm_reply[:200])
                    _last_q = self.session.get("last_question", "")
                    if _last_q:
                        logger.info("[ms_conn] last_question stored: %r", _last_q[:120])
                        if flow.is_complete():
                            # Flow is done — do NOT re-arm silence handler.
                            # Also zero the handler's stored question so the silence
                            # timer cannot fire a stale re-ask after the flow completes.
                            self._silence_handler.last_question = ""
                            logger.info(
                                "[ms_conn] flow complete — silence handler cleared "
                                "(stale question suppressed: %r)", _last_q[:80]
                            )
                        else:
                            self._silence_handler.set_state(
                                self.session.get("state", "default")
                            )
                            self._silence_handler.on_question_asked(_last_q)
                            # Scaffold continuation: fragment received but no TTS was
                            # spoken.  Backdate last_audio_received_at so W1's 3.5 s
                            # audio-recency guard doesn't suppress the recovery prompt,
                            # then arm the silence timer directly.
                            if self.session.pop("_nc_scaffold_hold", False):
                                self._silence_handler.last_audio_received_at = (
                                    time.time() - 4.0
                                )
                                self._silence_handler.restart_for_question(_last_q)
                                logger.info(
                                    "[ms_conn] scaffold_hold: silence timer armed for %r",
                                    _last_q[:60],
                                )
                    logger.info(
                        "[ms_conn] state after turn: %s  flow_step=%s",
                        self.session.get("state", "?"),
                        self.session.get("flow_step", 0),
                    )

                    await save_session(self.call_sid, self.session)

                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.error(
                        "[ms_conn] flow error: %r\n%s", exc, traceback.format_exc(),
                    )
                    await self.tts_text_queue.put(CLAUDE_ERROR_PHRASE)
                    # Re-ask whatever question was pending so the caller isn't left
                    # in silence after the technical blip — but only if flow is still
                    # active; replaying a stale question after completion is wrong.
                    _lq = self.session.get("last_question", "")
                    if _lq and not flow.is_complete():
                        await self.tts_text_queue.put(_lq)
                finally:
                    self._llm_busy                        = False
                    self._silence_handler.on_llm_finished()
                    self.session["llm_generation_active"] = False
                    # Bug 5: drain pending TTS if a repair was detected this turn
                    # so old LLM output doesn't play after the repair phrase.
                    if self.session.pop("repair_requested", False):
                        while not self.tts_text_queue.empty():
                            try:
                                self.tts_text_queue.get_nowait()
                            except Exception:
                                break
                        logger.info("[ms_conn] repair_requested: TTS queue drained")
                        # Use the state-aware repair phrase set by flow.py (stored in
                        # last_question before repair_requested=True was set).  Fall back
                        # to the generic phrase only when flow.py left it empty, which
                        # should not happen for any mapped state.
                        _repair_phrase = (
                            self.session.get("last_question")
                            or "Sorry about that \u2014 what was your inquiry?"
                        )
                        # Enqueue repair phrase AFTER drain so it isn't wiped.
                        await self.tts_text_queue.put(_repair_phrase)
                    # Repeat request — drain stale TTS and replay last relevant answer.
                    if self.session.pop("repeat_requested", False):
                        while not self.tts_text_queue.empty():
                            try:
                                self.tts_text_queue.get_nowait()
                            except Exception:
                                break
                        logger.info("[ms_conn] repeat_requested: TTS queue drained")
                        _cur_state = self.session.get("state", "")
                        _replay = (
                            self.session.get("last_faq_answer", "")
                            if _cur_state == "FAQ_BOOKING_OFFER"
                            else ""
                        ) or self.session.get("last_question", "")
                        # Guard: always emit something — never let repeat leave the
                        # caller in silence when last_question/last_faq_answer are empty.
                        if not _replay:
                            _replay = "Sorry, could you say that again?"
                        await self.tts_text_queue.put(_replay)
                        logger.info("[ms_conn] repeat_requested: replaying %r", _replay[:60])
                    # Bug 9: restart silence timer after fragment suppression
                    # so the call doesn't go permanently silent.
                    if self.session.pop("fragment_suppressed", False):
                        _frag_lq = self.session.get("last_question", "")
                        if _frag_lq:
                            self._silence_handler.restart_for_question(_frag_lq)
                    await save_session(self.call_sid, self.session)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _llm_loop fatal: %r", exc)

    # ========================================================================
    # TTS loop
    # ========================================================================

    async def _tts_loop(self) -> None:
        """
        Wait for the "start" event, then consume text chunks from tts_text_queue
        and synthesise each through TTSStream -> audio_out_queue.

        Each chunk is a separate cancellable ElevenLabs request.
        Chunks always play in order.

        On ElevenLabs error: logs and continues to next chunk (audio may skip
        but pipeline keeps running). If ElevenLabs is completely down, the
        CLAUDE_ERROR_PHRASE chunks get silently dropped — the caller will
        hear dead air, and the watchdog will eventually play a bridge phrase.
        """
        await self._wait_for_start("tts_loop")

        from .tts_stream import TTSStream
        tts = TTSStream()
        _last_tts_chunk: str = ""  # BUG 2: dedup — track last synthesised text chunk

        try:
            while not self._stop_event.is_set():
                try:
                    chunk_text = await asyncio.wait_for(
                        self.tts_text_queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                # Sentinel: enqueued before each handle_transcript call to reset dedup
                # state between caller turns so fresh identical phrases are not suppressed.
                if chunk_text == "\x00DEDUP_RESET\x00":
                    _last_tts_chunk = ""
                    continue

                if not chunk_text or not chunk_text.strip():
                    continue

                # Bug 5: discard stale LLM chunks that arrived after a confirmed barge-in.
                # The flag is cleared in _llm_loop finally when the new turn starts.
                if self.session.get("tts_inhibit"):
                    logger.info(
                        "[ms_conn] tts_inhibit: discarding stale chunk %r", chunk_text[:60]
                    )
                    continue

                # Skip consecutive identical chunks (dedup guard)
                if chunk_text.strip().lower() == _last_tts_chunk.lower():
                    logger.info(
                        "[ms_conn] TTS dedup: skipping duplicate chunk %r",
                        chunk_text[:80],
                    )
                    continue
                _last_tts_chunk = chunk_text.strip()

                # Split long phrases into shorter sub-chunks so barge-in fires
                # sooner — at most ~1-2s of audio in Twilio's buffer instead of
                # up to ~6-7s for a full deterministic day/time phrase.
                from .chunker import split_tts_text
                sub_chunks = split_tts_text(chunk_text)
                _any_cancelled = False

                # Notify silence handler ONCE per chunk (not per sub-chunk).
                # on_tts_started() is paired with exactly one on_tts_finished() call
                # (via _delayed_tts_finished after the sentinel).  Calling it per
                # sub-chunk created a counting imbalance that let chunk N's delayed
                # callback clear _tts_playing while chunk N+1 was already playing,
                # opening a Guard-0 gap in _speech_recovery.
                self._silence_handler.on_tts_started()
                # Capture the timestamp set by on_tts_started() so _delayed_tts_finished
                # can pass it to on_tts_finished() for the multi-chunk stale check.
                _chunk_tts_start_ts = self._silence_handler._tts_last_start_ts

                for sub_text in sub_chunks:
                    # Track current sub-chunk so barge-in resume is accurate.
                    self._current_tts_text = sub_text

                    self._tts_task = asyncio.create_task(
                        tts.synthesise_chunk(
                            text=sub_text,
                            audio_out_queue=self.audio_out_queue,
                            audio_out_processor=self._audio_out_proc,
                        )
                    )
                    try:
                        await self._tts_task
                    except asyncio.CancelledError:
                        logger.info("[ms_conn] TTS sub-chunk cancelled (barge-in)")
                        _any_cancelled = True
                        break
                    except Exception as exc:
                        logger.error("[ms_conn] TTS sub-chunk error: %r", exc)
                    finally:
                        self._tts_task = None

                    # Barge-in may have fired between sub-chunks (rare race).
                    if self._barge_in_pending:
                        _any_cancelled = True
                        break

                if not _any_cancelled:
                    # All sub-chunks completed — place sentinel so send_loop can
                    # fire on_tts_finished once every byte has been sent to Twilio.
                    self._tts_text_pending = chunk_text
                    self._tts_pending_chunk_start_ts = _chunk_tts_start_ts
                    await self.audio_out_queue.put(_TTS_DONE_SENTINEL)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _tts_loop fatal: %r", exc)

    # ========================================================================
    # Send loop
    # ========================================================================

    async def _send_loop(self) -> None:
        """
        Continuously read base64-encoded mulaw payloads from audio_out_queue
        and forward them to Twilio as JSON "media" events.

        Updates _last_audio_at on every successful send (used by watchdog).
        If the WebSocket closes mid-call, drain the queue and exit.
        """
        _tts_bytes_sent: int = 0  # mulaw bytes sent for the current TTS utterance

        try:
            while not self._stop_event.is_set():
                try:
                    b64_payload = await asyncio.wait_for(
                        self.audio_out_queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                if not b64_payload:
                    continue

                # TTS-done sentinel: all audio for this utterance has been sent
                # to Twilio.  Schedule on_tts_finished to fire after the audio
                # has actually played out (bytes_sent / 8000 Hz = play duration).
                if b64_payload is _TTS_DONE_SENTINEL:
                    text = self._tts_text_pending
                    chunk_start_ts = self._tts_pending_chunk_start_ts
                    self._tts_text_pending = ""
                    self._tts_pending_chunk_start_ts = 0.0
                    play_secs = _tts_bytes_sent / 8000.0
                    _tts_bytes_sent = 0
                    # Only arm the silence timer if audio was actually delivered.
                    # If ElevenLabs failed (0 bytes sent), play_secs == 0 and we
                    # must NOT arm the timer — doing so triggers a spurious 26-second
                    # silence-transfer cascade (12s + 10s + 4s windows) even though
                    # Susie never spoke.
                    if text and play_secs > 0.01:
                        logger.info(
                            "[ms_silence] tts_finished in %.1fs: %r",
                            play_secs, text[:60],
                        )
                        asyncio.create_task(
                            self._delayed_tts_finished(play_secs, text, self._tts_gen, chunk_start_ts),
                            name="ms_silence_tts_delay",
                        )
                    elif text:
                        logger.warning(
                            "[ms_silence] TTS sentinel with 0 bytes — ElevenLabs likely "
                            "rate-limited; silence timer NOT armed to prevent spurious transfer"
                        )
                    continue

                try:
                    await self.websocket.send_json({
                        "event":     "media",
                        "streamSid": self.stream_sid,
                        "media":     {"payload": b64_payload},
                    })
                    now = time.monotonic()
                    self._last_audio_at                = now
                    self.session["last_audio_sent_at"] = _iso_now()
                    # Count raw mulaw bytes for play-duration estimate.
                    # base64 encodes 3 bytes as 4 chars → multiply by 0.75.
                    _tts_bytes_sent += int(len(b64_payload) * 0.75)

                except WebSocketDisconnect:
                    logger.info("[ms_conn] send_loop: WS closed")
                    self._stop_event.set()
                    break
                except RuntimeError as exc:
                    if "close message" in str(exc):
                        self._stop_event.set()
                        break
                    logger.error("[ms_conn] send_loop runtime error: %r", exc)
                except Exception as exc:
                    logger.error("[ms_conn] send_loop error: %r", exc)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[ms_conn] _send_loop fatal: %r", exc)

    async def _delayed_tts_finished(
        self,
        delay: float,
        text: str,
        gen: int = 0,
        chunk_started_at: float = 0.0,
    ) -> None:
        """
        Fire on_tts_finished after `delay` seconds so the silence timer starts
        only once the caller has actually heard the last word, not when the
        audio was merely enqueued into Twilio's buffer.
        delay = mulaw_bytes_sent / 8000 Hz

        gen — the _tts_gen value captured at creation time.  If a barge-in has
        occurred since this task was created, _tts_gen will have advanced and
        this callback is stale: firing it would overwrite last_question with an
        old prompt (e.g. "does that sound OK?") after the flow has already moved
        on, and re-arm the silence timer for the wrong question.

        chunk_started_at — the _tts_last_start_ts value when this chunk's
        on_tts_started() fired.  Forwarded to on_tts_finished() so it can
        detect whether a newer chunk has started, preventing premature
        clearing of _tts_playing during multi-chunk / multi-part responses.
        """
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            # Stale-generation guard: barge-in increments _tts_gen.
            if gen != self._tts_gen:
                logger.debug(
                    "[ms_silence] tts_finished ignored — stale gen %d vs current %d: %r",
                    gen, self._tts_gen, text[:60],
                )
                return
            # Don't arm the silence timer once the booking flow is complete.
            # Without this guard, CONFIRM_BOOKING's LLM response (which often
            # ends with "?") re-arms the timer and causes a spurious CONFIRM_PHONE
            # re-ask after booking is confirmed, failing no_question_asked_twice /
            # no_state_corruption checks (seen in tests 2.7 and 6.4).
            if hasattr(self, "_flow") and self._flow.is_complete():
                logger.debug("[ms_silence] flow complete — skipping tts_finished")
                return
            self._silence_handler.on_tts_finished(text, chunk_started_at=chunk_started_at)
            logger.debug("[ms_silence] tts_finished fired after %.1fs delay gen=%d", delay, gen)
        except asyncio.CancelledError:
            pass

    # ========================================================================
    # Barge-in
    # ========================================================================

    async def _on_partial_transcript(self, text: str) -> None:
        """
        Called by STTStream when a non-empty PartialTranscript arrives.

        Implements barge-in (only when TTS is actually playing):
          1. Cancel the current TTS streaming task
          2. Drain tts_text_queue (discard pending text chunks)
          3. Drain audio_out_queue (discard buffered audio)
          4. Send Twilio "clear" to drain its playback buffer
          5. Set _clearing=True to suppress energy VAD until final transcript arrives

        If TTS is NOT active (caller speaks after Susie finished), only the
        silence timer is cancelled — no queue drain, no Twilio clear, no _clearing.
        This prevents suppressing the energy VAD unnecessarily and avoids draining
        flow responses that arrive between the partial and final transcript.
        """
        if not text.strip():
            return

        logger.info("[ms_conn] barge-in: partial=%r", text[:60])

        # Always cancel the silence timer — caller is speaking.
        # on_transcript_received() handles the full reset when the utterance ends.
        self._silence_handler.on_speech_started()

        # Only perform barge-in teardown if TTS is actually playing.
        # When the caller speaks after Susie has already finished (e.g. right after
        # the greeting), there is nothing to interrupt — skip drain/clear/_clearing
        # and do NOT set _barge_in_pending so _resolve_barge_in() won't fire an
        # ack phrase and discard the utterance.
        if not (self._tts_task and not self._tts_task.done()):
            return

        # Record barge-in start time (only once per barge-in event)
        if not self._barge_in_pending:
            self._barge_in_ts = time.monotonic()
            self._barge_in_pending = True
            # Snapshot the text currently being spoken for potential TTS resume
            self.session["interrupted_tts_text"] = self._current_tts_text
            # Advance the prompt generation so any in-flight _delayed_tts_finished
            # tasks for the interrupted TTS are treated as stale and ignored.
            self._tts_gen += 1
            # Inhibit _tts_loop from speaking any LLM chunks that arrive after
            # the barge-in until the new turn completes (Bug 5 — stale output).
            self.session["tts_inhibit"] = True
            logger.info(
                "[ms_conn] barge-in start: interrupted_text=%r tts_gen=%d",
                self._current_tts_text[:60], self._tts_gen,
            )

        self._tts_task.cancel()

        drained_text  = _drain_queue(self.tts_text_queue)
        drained_audio = _drain_queue(self.audio_out_queue)
        if drained_text or drained_audio:
            logger.debug(
                "[ms_conn] barge-in drained: text_chunks=%d audio_chunks=%d",
                drained_text, drained_audio,
            )

        if self.stream_sid:
            try:
                await self.websocket.send_json({
                    "event":     "clear",
                    "streamSid": self.stream_sid,
                })
            except Exception:
                pass

    async def _resolve_barge_in(self, utterance: str = "") -> bool:
        """
        Check and resolve a pending barge-in event.

        Called from _llm_loop before processing each utterance.

        Returns True if the utterance should be SKIPPED (barge-in handled):
          - False trigger (speech < BARGE_IN_THRESHOLD_MS): TTS resumed from
            session["interrupted_tts_text"], utterance discarded.
          - Confirmed barge-in with empty/noise utterance: ack queued, utterance
            discarded so the NEXT utterance drives the flow.

        Returns False if no barge-in was pending — normal processing continues.
        Also returns False when a confirmed barge-in carries a substantive
        transcript (≥2 words) — the caller's answer is processed immediately
        instead of being dropped and re-asked.
        """
        if not self._barge_in_pending:
            return False

        self._barge_in_pending = False
        dur = self._barge_in_duration

        if dur < _BARGE_IN_THRESHOLD_S:
            # ── False trigger: speech too short to be intentional ─────────
            interrupted = self.session.get("interrupted_tts_text", "")
            if interrupted:
                await self.tts_text_queue.put(interrupted)
            logger.info(
                "[ms_conn] barge-in false trigger (%.0fms < %dms threshold) — resuming TTS",
                dur * 1000, BARGE_IN_THRESHOLD_MS,
            )
            return True  # skip utterance

        # ── Confirmed barge-in ────────────────────────────────────────────
        # If we're already in recovery (ack was played, waiting for the real
        # utterance), skip the second ack and process this utterance directly.
        if self._in_barge_in_recovery:
            _state = self.session.get("state", "unknown")
            logger.info(
                "[ms_conn] barge-in during recovery — skipping ack, processing utterance directly (state=%s)",
                _state,
            )
            self._in_barge_in_recovery = False
            return False  # process utterance normally

        # ── FIX A: if the transcript carries a real answer, process it
        # immediately instead of dropping it and playing an ack.  The caller
        # already gave their answer — making them repeat it is the #1 observed
        # failure.  Only empty strings and pure filler noise ("uh", "um") get
        # the ack-and-wait treatment.  Single-word valid answers like "yes",
        # "new", "redditch", "recently" must be processed directly.
        _BARGE_NOISE = frozenset({
            "uh", "um", "hmm", "ah", "er", "oh", "erm", "ehm", "hm",
            "mm", "mhm", "ugh", "huh",
        })
        _barge_text = utterance.strip().lower() if utterance else ""
        _barge_words = _barge_text.split()
        _is_barge_noise = (
            not _barge_words
            or (len(_barge_words) == 1 and _barge_words[0] in _BARGE_NOISE)
        )
        if not _is_barge_noise:
            self.session["barge_in_count"] = self.session.get("barge_in_count", 0) + 1
            logger.info(
                "[ms_conn] barge-in #%d confirmed (%.0fms) — real transcript %r, "
                "processing directly instead of ack+drop (state=%s)",
                self.session["barge_in_count"], dur * 1000,
                utterance[:60], self.session.get("state", "unknown"),
            )
            self._in_barge_in_recovery = False
            return False  # process utterance normally — do NOT drop it

        ack = random.choice(_BARGE_IN_ACKS)
        await self.tts_text_queue.put(ack)
        self._in_barge_in_recovery = True
        self.session["barge_in_count"] = self.session.get("barge_in_count", 0) + 1
        _state = self.session.get("state", "unknown")
        logger.info(
            "[ms_conn] barge-in #%d confirmed (%.0fms) ack=%r state=%s",
            self.session["barge_in_count"], dur * 1000, ack, _state,
        )
        # slot question is NOT re-asked here — the NEXT utterance goes through
        # flow.handle_transcript() normally; re-ask only fires if that fails.
        return True  # skip current utterance (ack plays, next turn processes)

    async def _on_final_transcript_clear(self) -> None:
        """
        Called by STTStream on each FinalTranscript to reset _clearing.
        Ensures audio is no longer dropped once the caller finishes speaking.
        Also resets the SilenceHandler so the re-ask timer is cancelled.

        If a barge-in is pending, compute how long speech lasted so _llm_loop
        can decide: < threshold → false trigger (resume TTS), ≥ threshold → confirmed.
        """
        if self._barge_in_pending and self._barge_in_ts > 0:
            self._barge_in_duration = time.monotonic() - self._barge_in_ts
        self._clearing = False
        self._silence_handler.on_transcript_received()

    # ========================================================================
    # Greeting injection
    # ========================================================================

    async def _inject_greeting(self) -> None:
        """
        Speak Susie's opening greeting directly via ElevenLabs TTS without
        an LLM round-trip — saves ~500ms on the first word of the call.

        Guards against double-fire (Twilio reconnect / duplicate start events).
        Advances state from GREETING → CLINIC_SELECTION so the LLM never
        sees GREETING state and tries to re-introduce itself.
        """
        # Guard: only fire once per call
        if self.session.get("greeting_delivered"):
            logger.info("[ms_conn] greeting already delivered — skipping")
            return

        from app.greeting_builder import build_greeting
        greeting = build_greeting()
        logger.info("[ms_conn] greeting: %r", greeting[:80])

        self.session.setdefault("turns", []).append({"role": "assistant", "text": greeting})
        history = self.session.setdefault("conversation_history", [])
        history.append({"role": "user",      "content": "[call connected — patient is on the line]"})
        history.append({"role": "assistant", "content": greeting})
        self.session["last_bot_prompt"]    = greeting
        # Clear any stale last_question that may have been loaded from Redis
        # for this call_sid (e.g. previous call left "Just to confirm — shall I
        # use the number..." and the session was reloaded).  The silence handler
        # is also zeroed so no cross-call question can leak into the re-ask path.
        self.session["last_question"]       = ""
        self._silence_handler.last_question = ""
        self.session["greeting_delivered"]  = True

        # State stays at GREETING after the initial greeting plays.
        # The first caller utterance triggers DETECT_INTENT → booking flow.
        # (No state advance here — keep GREETING until caller speaks.)

        await save_session(self.call_sid, self.session)

        await self.tts_text_queue.put(greeting)
        # The silence timer is armed automatically by _tts_loop's on_tts_finished()
        # hook once the greeting audio finishes playing.  No explicit call needed here.

    # ========================================================================
    # Transfer callback
    # ========================================================================

    def _should_allow_transfer(self) -> bool:
        """
        Single choke-point for transfer authorisation.
        Transfer fires ONLY under these exact conditions — nothing else can trigger it.
        """
        return (
            self.session.get("transfer_requested_by_caller") is True
            or self.session.get("medical_emergency_detected") is True
            or self.session.get("failed_understanding_count", 0) >= 3
            or self.session.get("request_transfer") is True      # set by transfer_to_human tool
            or self.session.get("silence_transfer") is True      # set by SilenceHandler after 3 re-asks
        )

    async def _on_transfer_request(self) -> None:
        """Initiate live call transfer via Twilio REST API."""
        if not self._should_allow_transfer():
            logger.warning("[ms_conn] transfer blocked — guard conditions not met session=%s", {
                "transfer_requested_by_caller": self.session.get("transfer_requested_by_caller"),
                "medical_emergency_detected":   self.session.get("medical_emergency_detected"),
                "failed_understanding_count":   self.session.get("failed_understanding_count"),
                "request_transfer":             self.session.get("request_transfer"),
            })
            return
        logger.info("[ms_conn] transfer authorised — initiating")
        try:
            from app.routes.realtime import _handle_transfer
            await _handle_transfer(self.call_sid, self.session)
        except Exception as exc:
            logger.error("[ms_conn] transfer failed: %r", exc)

    # ========================================================================
    # Pipeline failure: complete collapse handler
    # ========================================================================

    async def play_pipeline_failure(self) -> None:
        """
        Play the pre-recorded total-failure message and close cleanly.

        Called by router.py if handle() raises an exception before the
        call reaches a stable state. The message gives the caller something
        to hear before the line drops.
        """
        logger.error("[ms_conn] playing pipeline failure message")
        try:
            await self.tts_text_queue.put(PIPELINE_FAILURE_PHRASE)
            # Give 4 seconds for TTS to play before closing
            await asyncio.sleep(4.0)
        except Exception:
            pass
        finally:
            self._stop_event.set()

    # ========================================================================
    # Cleanup
    # ========================================================================

    async def _cleanup(self) -> None:
        """
        Called once when the call ends.

        - Mark session flags as inactive
        - Save final session to ms_session: prefix
        - Mirror-save to call: prefix so /twilio/status webhook can read it
        """
        if not self.call_sid:
            return

        logger.info("[ms_conn] cleanup call_sid=%s stable=%s", self.call_sid, self._call_stable)

        # Deregister from the active-handler map
        _was_registered = self.call_sid in _active_handlers
        _active_handlers.pop(self.call_sid, None)
        logger.info(
            "[ms_conn] REMOVE sid=%s reason=cleanup was_registered=%s remaining=%d",
            self.call_sid, _was_registered, len(_active_handlers),
        )

        # Cancel the silence handler timer so it doesn't fire after the call ends
        self._silence_handler.cancel()

        self.session["ws_connected"]          = False
        self.session["stt_active"]            = False
        self.session["tts_active"]            = False
        self.session["llm_generation_active"] = False

        # Flush structured per-call log
        call_logger = getattr(self, "_call_logger", None)
        if call_logger is not None:
            try:
                success = bool(self.session.get("booking_confirmed") or self.session.get("confirmation_sms_sent"))
                if self.session.get("graceful_exit"):
                    reason = "graceful_exit"
                elif self.session.get("booking_confirmed"):
                    reason = "booked"
                elif self.session.get("transfer_attempted"):
                    reason = "transferred"
                else:
                    reason = "caller_hung_up"
                call_logger.complete(success=success, reason=reason)
                await call_logger.flush()
            except Exception as _cl_exc:
                logger.error("[ms_conn] call_logger flush error: %r", _cl_exc)

        try:
            await save_session(self.call_sid, self.session)
        except Exception as exc:
            logger.error("[ms_conn] cleanup save failed: %r", exc)

        # Mirror-save to call: prefix for /twilio/status webhook compatibility
        try:
            import copy
            import json as _json
            from app.storage.redis_store import redis_client
            if redis_client:
                await redis_client.set(
                    f"call:{self.call_sid}",
                    _json.dumps(copy.deepcopy(self.session)),
                    ex=7200,
                )
                logger.info("[ms_conn] mirrored to call: prefix call_sid=%s", self.call_sid)
        except Exception as exc:
            logger.warning("[ms_conn] mirror-save failed: %r", exc)

        # Notify staff if caller asked for a human but didn't get through
        if (
            self.session.get("human_requested")
            and not self.session.get("booking_confirmed")
            and not self.session.get("transfer_attempted")
        ):
            try:
                import os as _os
                from app.notifications.sms import send_sms as _send_sms
                _staff_phone = _os.getenv("THEOREM_NOTIFICATION_SMS")
                _caller      = (
                    self.session.get("twilio_from_local")
                    or self.session.get("twilio_from")
                    or "unknown number"
                )
                if _staff_phone:
                    await _send_sms(
                        to=_staff_phone,
                        message=(
                            f"Hi Mark, a caller just asked to speak to you "
                            f"but didn't get through. Their number is {_caller}. "
                            f"Give them a call back when you get a chance. — Susie"
                        ),
                    )
                    logger.info("[ms_conn] staff notify SMS sent → %s", _staff_phone)
            except Exception as _notify_exc:
                logger.warning("[ms_conn] staff notify SMS failed: %r", _notify_exc)

    # ========================================================================
    # Internal helper
    # ========================================================================

    async def _wait_for_start(self, loop_name: str) -> None:
        """Wait for the 'start' event to be processed before entering a loop."""
        try:
            await asyncio.wait_for(
                self._started_event.wait(),
                timeout=TWILIO_STARTED_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning("[ms_conn] %s: timed out waiting for start event", loop_name)
            raise asyncio.CancelledError
