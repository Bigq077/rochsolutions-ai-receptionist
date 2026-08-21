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
import math
import itertools
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# ── Flag (read once at import; the eval service sets LATENCY_TIMING=true) ──────
LATENCY_TIMING = os.getenv("LATENCY_TIMING", "false").strip().lower() in (
    "true", "1", "yes", "on",
)

# Dedicated logger — route to its own file/sink, grep with "[LAT]".
_lat_log = logging.getLogger("susie.latency")

# Monotonic turn counter (offline correlation only; not a concurrency mechanism).
_turn_counter = itertools.count(1)

def _flag(env: str, default: str = "false") -> bool:
    """Truthy env read, matching config.py's parsing exactly."""
    return os.getenv(env, default).strip().lower() in ("true", "1", "yes", "on")


# STT model actually in use this run. Previously hardcoded to
# universal-streaming-english via an env var nobody sets, which meant every
# [LAT] line reported the same model regardless of which one served the call —
# the one field the A/B depends on could not distinguish the two arms of the
# A/B. Tags and precedence (V2 > U3.5 > default) mirror stt_stream.py's
# stt_variant and config.py's _ws_url(); STT_MODEL_TAG still overrides, for
# labelling offline replays. Derived here rather than imported to preserve this
# module's stdlib-only / no-hot-path-import guarantee (see docstring).
_STT_MODEL = os.getenv("STT_MODEL_TAG") or (
    "v2" if _flag("ASSEMBLYAI_USE_V2")
    else "u3.5-pro" if _flag("ASSEMBLYAI_USE_U35")
    else "universal-streaming-english"
)

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


# Markers that identify the phone question as the one on the table (B-15).
#
# Deliberately a SUBSET of _PHONE_STEP_MARKERS in app/prompts/clinic_template_prompt.py
# — the vetted list for "has the phone question been put to the caller?". Copied
# rather than imported to keep this module stdlib-only: it is a leaf on the
# per-turn hot path, and importing a 2,500-line prompt module for nine strings
# would buy a cold-start cost for nothing. test_capture_phase_follows_the_question.py
# asserts the subset relation, so the two cannot drift apart silently.
#
# ONE member of that list is excluded on purpose: "on your keypad". It also
# appears in the LOCATION rung-3 prompt (connection.py:688, flow.py:9901 —
# "on your keypad, just press 1 for Awlstuh"), so keying on it would classify a
# location question as phone capture. Nothing is lost: every phone-keypad prompt
# says "type the number" in the same breath, and once digits are actually being
# typed v3_phone_dtmf_active is set and the flag branch below catches it first.
_PHONE_QUESTION_MARKERS: tuple = (
    "use this number",
    "best one for your",
    "best number",
    # Step 8's read-back opener, for a turn clipped before "best number" reaches
    # last_bot_prompt. Generic enough that the model could in principle say
    # "I've got you on Thursday", but it does not: the template mandates this
    # opener for the phone step only. The dead-air consumer also tests
    # v3_location_q_active and v3_awaiting_slot_selection BEFORE the phase, so a
    # stray match cannot reach the phone re-ask from a location or slot turn.
    "i've got you on",
    "ive got you on",
    "number you're calling on",
    "number you're calling from",
    "number you're ringing",
    "type the number",
    # "type YOUR number" — the model uses both. Susie said "could you type
    # your number on your keypad?" on CA9758ceab and this list matched nothing,
    # so the phone step was never recorded as asked. Safe to add: it does not
    # appear in the LOCATION rung ("on your keypad, just press 1 for Awlstuh"),
    # which is the collision that removed "on your keypad" from this list.
    "type your number",
)

_NAME_QUESTION_MARKERS: tuple = ("your name", "first name", "surname", "full name")


def capture_phase(session: dict) -> str:
    """Which phase the turn belongs to — enum only, never the digits/name.

    Returns one of ``conversation | phone | name``. Three consumers, and the
    ordering below is what keeps them honest:

      * the dead-air re-ask (connection.py, live regardless of LATENCY_TIMING) —
        picks the wording, so this answers "what did Susie just ask?";
      * the ``[LAT]`` / ``[LAT-EP]`` lines — per-phase latency and cutoff buckets;
      * ``_ws_c_apply_endpoint_profile`` — raises the endpointer's silence floor
        during capture so a spelled name or read-out number cannot be clipped.

    B-15 (2 Aug 2026) — the question on the table now outranks a stale flag.
    ``v3_awaiting_surname`` is sticky by design: it has three assignment sites,
    all in ``_v3_try_capture_name``, and both False-sites require a surname to
    have actually been found (connection.py:1790 — "nothing clears it when the
    conversation moves on"). It stays True so a later bare straggler word can be
    back-filled as the surname, which is load-bearing and must NOT be changed.

    But it was tested BEFORE the prompt, so a caller who gave a first name only
    was in phase "name" for the rest of the call — the slot choice, the phone
    step, the booking confirmation, the closing. The cost was audible: a caller
    who went quiet at the booking-confirm step was answered with "could I take
    your first name and surname again?".

    So the flag is now a FALLBACK, consulted only when no prompt was recorded at
    all — the genuine "we cannot tell" case — rather than an override of a
    question we can read. A live prompt about anything else ends name capture,
    which is the whole fix.

    Second defect closed in the same pass: the phone branch had only hard flags
    where the name branch had a prompt fallback, and ``v3_awaiting_phone_confirm``
    is set in exactly ONE place (connection.py:5292, the reschedule/cancel DTMF
    path). On an ordinary booking the phone step therefore never resolved to
    "phone" — it read "name" when the surname flag was stuck and "conversation"
    otherwise, which left the phone re-ask wording unreachable on the booking
    path. Phone now has the symmetric prompt test.
    """
    if not session:
        return "conversation"

    # 1 · Hard flags first. Both are set and cleared tightly around the moment
    #     they describe, so when either is on it beats any reading of the text.
    if (
        session.get("v3_phone_dtmf_active")
        or session.get("v3_awaiting_phone_confirm")
    ):
        return "phone"

    # 2 · Otherwise: the question actually on the table. Phone is tested before
    #     name to match the precedence the flags above already established; a
    #     turn carrying both markers is a phone turn.
    _prompt = (
        (session.get("last_bot_prompt") or "")
        + " "
        + (session.get("last_question") or "")
    ).lower()
    if _prompt.strip():
        if any(k in _prompt for k in _PHONE_QUESTION_MARKERS):
            return "phone"
        if any(k in _prompt for k in _NAME_QUESTION_MARKERS):
            return "name"
        # A prompt we can read, about neither — the call has moved on, whatever
        # any sticky flag still says. This line IS the B-15 fix.
        return "conversation"

    # 3 · No prompt recorded. Now, and only now, the sticky flag is the best
    #     evidence available.
    if session.get("v3_awaiting_surname"):
        return "name"
    return "conversation"


def new_turn(t0: float, call_sid: Optional[str] = None) -> Optional["TurnTiming"]:
    """Return a fresh timing record, or ``None`` when instrumentation is OFF.

    ``None`` short-circuits every downstream stamp site, so the OFF hot-path
    cost is a single falsy check per capture point.

    ``call_sid`` is what makes the timings durable. Without it a turn can be
    logged but not filed against a call, so it can never join the obs row —
    which is why every [LAT] number so far has only ever existed in a Render
    log window measured in hours. Optional so the OFF path and the unit tests
    stay callable without one; a turn with no call_sid is still logged, just
    not buffered.
    """
    if not LATENCY_TIMING:
        return None
    return TurnTiming(
        turn_seq=next(_turn_counter),
        t0=t0,
        t_dispatch=time.monotonic(),
        call_sid=call_sid,
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
    call_sid: Optional[str] = None       # owning call, for the durable per-call buffer
    t1: Optional[float] = None           # first LLM token
    t2: Optional[float] = None           # first content chunk -> tts queue
    t3: Optional[float] = None           # first audio frame enqueued (any, incl. filler)
    t4: Optional[float] = None           # first audio frame sent to Twilio
    content_t3: Optional[float] = None   # first NON-filler audio enqueued
    content_t4: Optional[float] = None   # first NON-filler audio sent
    path: str = "llm"                    # llm | fast_path | filler | fallback | scripted (deterministic clinical layer — no LLM call)
    outcome: str = "completed"           # completed | barged_in | abandoned | superseded (replaced by a newer dispatch — not caller behaviour) | no_content (audio played, but only a filler — content never arrived) | error
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

        record = self.as_record()
        _lat_log.info(
            "[LAT] turn_seq=%(turn_seq)d path=%(path)s outcome=%(outcome)s "
            "ttfa_ms=%(ttfa_ms)d content_ttfa_ms=%(content_ttfa_ms)d "
            "ep_dispatch_ms=%(ep_dispatch_ms)d llm_ttft_ms=%(llm_ttft_ms)d "
            "chunk_gate_ms=%(chunk_gate_ms)d tts_first_byte_ms=%(tts_first_byte_ms)d "
            "audio_wire_ms=%(audio_wire_ms)d "
            "flags=%(flags)s model=%(model)s stt_model=%(stt_model)s "
            "eot_confident=%(eot_confident)s capture_phase=%(capture_phase)s "
            "endpoint_wait_ms=%(endpoint_wait_ms)d",
            record,
        )
        _buffer(self.call_sid, record)

    def as_record(self) -> Dict[str, Any]:
        """The turn as a plain JSON-safe dict — the [LAT] line's exact fields.

        Single source for both consumers: ``emit`` formats the log line FROM this
        dict, and the durable buffer stores this dict. They cannot drift, which
        matters because lat_parse.py parses the log line while the obs query will
        read the stored dict — two readers of what has to be one measurement.

        PII-free by construction: enum tags, model ids and integers only. Any
        stage not reached is -1 (never 0, never null) so a missing measurement
        cannot contaminate an offline sum — the log line's convention, kept.

        ``call_sid`` is deliberately NOT a field here: it is the key the turn is
        filed under, and repeating it on every turn of a call would bloat the
        stored JSON for nothing.
        """

        def d(a: Optional[float], b: Optional[float]) -> int:
            return int((a - b) * 1000) if (a is not None and b is not None) else -1

        return {
            "turn_seq":          self.turn_seq,
            "path":              self.path,
            "outcome":           self.outcome,
            "ttfa_ms":           d(self.t4, self.t0),                 # perceived headline
            "content_ttfa_ms":   d(self.content_t4, self.t0),         # real content
            "ep_dispatch_ms":    d(self.t_dispatch, self.t0),         # queue/scheduling
            "llm_ttft_ms":       d(self.t1, self.t_dispatch),         # Claude first token
            "chunk_gate_ms":     d(self.t2, self.t1),                 # WS-A lever
            "tts_first_byte_ms": d(self.content_t3, self.t2),         # WS-B lever
            "audio_wire_ms":     d(self.content_t4, self.content_t3), # encode/queue/send
            "flags":             _active_flags() or "-",
            "model":             self.model or "-",
            "stt_model":         _STT_MODEL,
            "eot_confident":     self.eot_confident,
            "capture_phase":     self.capture_phase,
            "endpoint_wait_ms":  self.endpoint_wait_ms,               # WS-C
        }


# ── Durable per-call buffer ─────────────────────────────────────────────
# Why this exists: the [LAT] lines have never been persisted anywhere. The obs
# store holds hundreds of calls and not one latency figure, so a baseline could
# only ever be assembled by exporting a Render log window — and at roughly a
# dozen calls a day against a retention measured in hours, that export can never
# accumulate more than the handful of calls inside the window. Two sessions were
# spent establishing that; the largest sample either produced was 29 turns across
# 3 calls, which is directional and nothing more.
#
# So emit() now also files each turn under its call_sid, and CallLogger drains
# the call at teardown into the record that already flows to the JSONL log and to
# the obs `calls` row. No DB write on the hot path, no new table, no per-turn
# I/O: one dict append per turn, one pop per call.
#
# Bounded on both axes, because this runs in a live-call process and the drain is
# not guaranteed — a call that dies before teardown never drains, and an
# unbounded dict would leak for as long as the worker lives:
#   * _MAX_TURNS_PER_CALL — a call longer than this keeps its FIRST turns and
#     drops the rest. First rather than last because the opening turns carry the
#     greeting and the capture steps, which is where the latency questions are.
#   * _MAX_PENDING_CALLS — the oldest undrained call is evicted when a new one
#     starts. At this call volume that is days of head-room for a leak that
#     should never happen; it exists so that it cannot become an outage.
#
# No lock: asyncio is single-threaded and these are plain dict/list operations
# between awaits — the same reasoning that lets TurnTiming.stamp go unlocked.
_MAX_TURNS_PER_CALL = 200
_MAX_PENDING_CALLS = 32

_pending: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()


def _buffer(call_sid: Optional[str], record: Dict[str, Any]) -> None:
    """File one emitted turn under its call. No-op for a turn with no call_sid."""
    if not call_sid:
        return
    turns = _pending.get(call_sid)
    if turns is None:
        turns = []
        _pending[call_sid] = turns
        while len(_pending) > _MAX_PENDING_CALLS:
            evicted, _ = _pending.popitem(last=False)
            _lat_log.warning(
                "[LAT] evicted undrained latency buffer call_sid=%s", evicted
            )
    else:
        _pending.move_to_end(call_sid)
    if len(turns) < _MAX_TURNS_PER_CALL:
        turns.append(record)


def drain_call(call_sid: Optional[str]) -> List[Dict[str, Any]]:
    """Pop and return every buffered turn for a call, oldest first.

    Called once, by CallLogger at teardown. Returns [] for an unknown call —
    which is the normal answer whenever LATENCY_TIMING is OFF.
    """
    if not call_sid:
        return []
    return _pending.pop(call_sid, [])


def _percentile(values: List[int], p: float) -> Optional[float]:
    """Linear-interpolation percentile (numpy type-7), matching lat_parse.py.

    Deliberately the same method as the offline parser, so a figure read out of
    the database and a figure printed from a log export are the same number.
    """
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    h = (len(xs) - 1) * (p / 100.0)
    lo = math.floor(h)
    frac = h - lo
    if lo + 1 < len(xs):
        return xs[lo] + frac * (xs[lo + 1] - xs[lo])
    return float(xs[lo])


# The CLAUDE.md §6 bar: p95 caller-perceived turn latency under 1.5 s.
TTFA_BAR_MS = 1500


def close_outcome(t4: Optional[float]) -> str:
    """Outcome for a turn being closed because a NEWER dispatch replaced it.

    Pure so it can be tested without a socket — the discipline
    ``expect_slot_presentation`` follows, and for the same reason: this decides
    a metric, and a metric nobody can unit test is a metric nobody can trust.

    Reaching this point always means ``content_t4`` is None: ``emit()`` fires at
    content_t4 and sets ``_emitted``, so a turn that delivered content is never
    closed here. ``t4`` is therefore the whole discriminator:

      * t4 stamped  -> the caller HEARD something this turn, and it can only
                       have been a filler, because content never arrived.
                       "no_content" — a hold phrase that promised work and
                       delivered nothing.
      * t4 unstamped -> nothing was ever spoken; a split utterance or
                       deterministic branch replaced the record. "superseded",
                       which is not caller behaviour and must not pollute the
                       abandoned-rate line.
    """
    return "no_content" if t4 is not None else "superseded"


def summarise(turns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-call roll-up, so "which calls were slow?" is one SQL read.

    IMPORTANT — the fleet p50/p95 must be computed across the UNION of every
    call's stored `turns`, never by averaging these per-call summaries. One call
    carries a handful of turns, so its own p95 is close to meaningless; this is
    here to make bad calls findable, not to be aggregated.

    Only turns that reached audio are counted: a superseded or abandoned turn
    reports ttfa_ms = -1, and letting -1 into a percentile is exactly how a
    latency number silently becomes a lie.
    """
    ttfa = [t["ttfa_ms"] for t in turns if t.get("ttfa_ms", -1) >= 0]
    content = [t["content_ttfa_ms"] for t in turns if t.get("content_ttfa_ms", -1) >= 0]
    llm = [t["llm_ttft_ms"] for t in turns if t.get("llm_ttft_ms", -1) >= 0]
    return {
        "turns_logged":        len(turns),
        "turns_measured":      len(ttfa),
        # Turns where the caller heard a filler and no content ever followed.
        # Counted here rather than derived offline so "which calls dead-ended?"
        # stays the same one SQL read as "which calls were slow?".
        "no_content_turns":    sum(
            1 for t in turns if t.get("outcome") == "no_content"
        ),
        "ttfa_p50_ms":         _percentile(ttfa, 50),
        "ttfa_p95_ms":         _percentile(ttfa, 95),
        "content_ttfa_p50_ms": _percentile(content, 50),
        "llm_ttft_p50_ms":     _percentile(llm, 50),
        "over_bar":            sum(1 for v in ttfa if v > TTFA_BAR_MS),
        "bar_ms":              TTFA_BAR_MS,
        "flags":               _active_flags() or "-",
        "stt_model":           _STT_MODEL,
    }


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
