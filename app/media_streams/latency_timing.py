"""Per-turn voice-to-voice latency instrumentation (latency-eval branch only).

Companion to LATENCY_MEASUREMENT_SPEC.md (what) + LATENCY_INSTRUMENTATION_WIRING.md
(how). Self-contained, stdlib-only, no imports from the hot path.

Design guarantees (see spec §1 / wiring §6):
  * Gated behind LATENCY_TIMING (default OFF). When OFF, ``new_turn`` returns
    ``None`` and every stamp site short-circuits on one falsy check — no
    allocation, no logging, no attribute walk. The branch stays
    byte-behaviour-identical to live until this flag (or a lever flag) is set.
  * time.monotonic() only; deltas reported in whole milliseconds.
  * Zero PII: enum tags + timings only, never transcript / name / digits / audio.

Emit one ``[LAT]`` line per turn to the dedicated ``susie.latency`` logger so it
can be routed to its own sink and grepped offline.
"""

import os
import time
import itertools
import logging
from dataclasses import dataclass
from typing import Optional

# ── Flag (read once at import; the eval service sets LATENCY_TIMING=true) ──────
LATENCY_TIMING = os.getenv("LATENCY_TIMING", "false").strip().lower() in (
    "true", "1", "yes", "on",
)

# Dedicated logger — route to its own file/sink, grep with "[LAT]".
_lat_log = logging.getLogger("susie.latency")

# Monotonic turn counter (offline correlation only; not a concurrency mechanism).
_turn_counter = itertools.count(1)

# STT model is fixed for the eval (AssemblyAI v3 universal-streaming-english).
_STT_MODEL = os.getenv("STT_MODEL_TAG", "universal-streaming-english")

# Lever flags recorded per turn so one log file can hold an A/B run (spec §6).
# Kept here (not imported from the levers) to avoid any hot-path coupling.
_LEVER_ENV = {
    "A": "WS_A_FAST_FIRST_CHUNK",
    "B": "WS_B_STREAM_TTS",
    "C": "WS_C_SEMANTIC_ENDPOINT",
}


def _active_flags() -> str:
    """CSV of active lever letters, e.g. "A" or "A|C" (empty for baseline)."""
    on = [
        letter
        for letter, env in _LEVER_ENV.items()
        if os.getenv(env, "false").strip().lower() in ("true", "1", "yes", "on")
    ]
    return "|".join(on)


def capture_phase(session: dict) -> str:
    """Which flow phase the turn ended in — enum only, never the digits/name.

    Used by WS-C's safety gate: any clipped/abandoned turn in phone/name capture
    is a hard fail. Derived from existing session flags; returns one of
    ``conversation | phone | name``.
    """
    if not session:
        return "conversation"
    # Phone capture: DTMF entry active, or awaiting a verbal "use this number".
    if (
        session.get("v3_phone_dtmf_active")
        or session.get("v3_awaiting_phone_confirm")
    ):
        return "phone"
    # Name capture: first-name locked and awaiting surname, or the current
    # prompt is a name question.
    if session.get("v3_awaiting_surname"):
        return "name"
    _prompt = (
        (session.get("last_bot_prompt") or "")
        + " "
        + (session.get("last_question") or "")
    ).lower()
    if any(k in _prompt for k in ("your name", "first name", "surname", "full name")):
        return "name"
    return "conversation"


def new_turn(t0: float) -> Optional["TurnTiming"]:
    """Return a fresh timing record, or ``None`` when instrumentation is OFF.

    ``None`` short-circuits every downstream stamp site, so the OFF hot-path
    cost is a single falsy check per capture point.
    """
    if not LATENCY_TIMING:
        return None
    return TurnTiming(
        turn_seq=next(_turn_counter),
        t0=t0,
        t_dispatch=time.monotonic(),
    )


@dataclass
class TurnTiming:
    """One caller turn. Fields stamped first-write-wins across the concurrent loops.

    asyncio is single-threaded and turns are strictly sequential (barge-in
    cancels the current turn before the next dispatches), so a plain ``is None``
    guard is atomic enough — no lock needed.
    """

    turn_seq: int
    t0: float                            # caller turn finalized (endpoint fired)
    t_dispatch: float                    # transcript dequeued, turn begins
    t1: Optional[float] = None           # first LLM token
    t2: Optional[float] = None           # first content chunk -> tts queue
    t3: Optional[float] = None           # first audio frame enqueued (any, incl. filler)
    t4: Optional[float] = None           # first audio frame sent to Twilio
    content_t3: Optional[float] = None   # first NON-filler audio enqueued
    content_t4: Optional[float] = None   # first NON-filler audio sent
    path: str = "llm"                    # llm | fast_path | filler | fallback | scripted (deterministic clinical layer — no LLM call)
    outcome: str = "completed"           # completed | barged_in | abandoned | superseded (replaced by a newer dispatch — not caller behaviour) | error
    model: str = ""                      # claude model id (set by the llm path)
    eot_confident: Optional[bool] = None  # WS-C: confidence-driven vs silence fallback
    capture_phase: str = "conversation"  # conversation | phone | name
    endpoint_wait_ms: int = -1           # WS-C: t_end_of_turn - t_last_partial (pre-t0 dead-time)
    _content_marked: bool = False        # content-boundary marker already enqueued
    _emitted: bool = False

    def stamp(self, field_name: str, now: Optional[float] = None) -> None:
        """First-write-wins stamp. Safe to call repeatedly; only the first sticks."""
        if getattr(self, field_name) is None:
            setattr(self, field_name, now if now is not None else time.monotonic())

    def emit(self) -> None:
        """Log the one structured ``[LAT]`` line for this turn. Idempotent.

        Two TTFAs are reported:
          * ``ttfa_ms``         = t4 − t0        — PERCEIVED (first sound the caller
                                                   hears, filler or content).
          * ``content_ttfa_ms`` = content_t4 − t0 — REAL content arrival (what the
                                                   levers actually reduce).
        The sub-splits use CONTENT timing (content_t3), because on a filler turn
        the perceived t3 is the filler frame and would make tts_first_byte
        meaningless. On a no-filler turn content_t3/t4 == t3/t4, so both TTFAs and
        the splits coincide (and llm_ttft+chunk_gate+tts_first_byte+audio_wire sum
        to content_ttfa — the built-in correctness check).

        Any stage not reached is reported as -1 (never 0) so "missing" can never
        contaminate an offline sum.
        """
        if self._emitted:
            return
        self._emitted = True

        def d(a: Optional[float], b: Optional[float]) -> int:
            return int((a - b) * 1000) if (a is not None and b is not None) else -1

        _lat_log.info(
            "[LAT] turn_seq=%d path=%s outcome=%s "
            "ttfa_ms=%d content_ttfa_ms=%d ep_dispatch_ms=%d llm_ttft_ms=%d "
            "chunk_gate_ms=%d tts_first_byte_ms=%d audio_wire_ms=%d "
            "flags=%s model=%s stt_model=%s "
            "eot_confident=%s capture_phase=%s endpoint_wait_ms=%d",
            self.turn_seq, self.path, self.outcome,
            d(self.t4, self.t0),                    # ttfa_ms — perceived headline
            d(self.content_t4, self.t0),            # content_ttfa_ms — real content
            d(self.t_dispatch, self.t0),            # ep_dispatch_ms — queue/scheduling
            d(self.t1, self.t_dispatch),            # llm_ttft_ms — Claude first token
            d(self.t2, self.t1),                    # chunk_gate_ms — WS-A lever
            d(self.content_t3, self.t2),            # tts_first_byte_ms — WS-B lever
            d(self.content_t4, self.content_t3),    # audio_wire_ms — encode/queue/send
            _active_flags() or "-",
            self.model or "-",
            _STT_MODEL,
            self.eot_confident,
            self.capture_phase,
            self.endpoint_wait_ms,                  # WS-C — endpoint silence before t0
        )


# ── WS-C cutoff detector (Phase 1, advisory) ─────────────────────────────────
# A caller turn that opens with a correction lead ("I said…", "I told you…")
# implies the PRIOR turn's capture was clipped by the endpointer. Emitted as a
# sibling ``[LAT-EP]`` line keyed by the prior turn_seq; the offline parser joins
# it to compute a per-phase cutoff rate. Heuristic + advisory — confirm flagged
# turns by listen-back; it is a relative baseline-vs-WS-C signal, not truth.
_EP_CORRECTION_LEADS = (
    "i said", "i told you", "i already said", "like i said",
    "no i ", "that's not", "that's wrong", "i meant",
)


def is_correction_lead(utterance: str) -> bool:
    """True if the utterance opens with a correction phrase. No-op/False when OFF."""
    if not LATENCY_TIMING or not utterance:
        return False
    u = utterance.strip().lower()
    return any(u.startswith(p) for p in _EP_CORRECTION_LEADS)


def emit_cutoff(prev_turn_seq: int, reason: str, capture_phase: str) -> None:
    """Log one advisory ``[LAT-EP]`` cutoff line for the prior turn. No-op when OFF."""
    if not LATENCY_TIMING:
        return
    _lat_log.info(
        "[LAT-EP] ep_cutoff turn_seq=%d reason=%s capture_phase=%s",
        prev_turn_seq, reason, capture_phase,
    )
