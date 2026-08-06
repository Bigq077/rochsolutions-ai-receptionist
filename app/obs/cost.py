"""
app/obs/cost.py
---------------
Per-call cost of goods, in GBP pence. Spec: docs/plan/COST_ROLLUP_SPEC.md.

Why this exists: cohort 1 sells a flat £199/mo product across four metered
vendors (Twilio, AssemblyAI, ElevenLabs, the LLM). Whether a high-volume clinic
is profitable at flat rate is currently unknown, and the busiest clinics are the
most likely to sign and the most visible in the HOM network. This module turns
that guess into a measurement.

Design:
- Pure functions. No I/O, no DB, no network, no clock. Fully unit-testable.
- Integer pence throughout. Money is never a float.
- Every result carries RATE_TABLE_VERSION, so a price change is a version bump
  and a recompute, not a silently-mixed average.
- Never raises. estimate_call_cost() returns a zeroed result with an "error"
  note rather than propagating — a pricing bug must never cost us a call
  record. store.capture_call() is the caller and it runs at teardown.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, Iterable, List, Optional

_log = logging.getLogger(__name__)

# Bump on ANY change to RATES below, then re-run scripts/backfill_call_costs.py.
# Rows keep the version they were computed under; cost_report refuses to average
# across versions.
RATE_TABLE_VERSION = "unset"

# ---------------------------------------------------------------------------
# Rates — UNFILLED. See COST_ROLLUP_SPEC.md §3.1.
# ---------------------------------------------------------------------------
# ⚠️  THESE ARE PLACEHOLDERS AND THE MODULE REFUSES TO PRODUCE COSTS UNTIL THEY
#     ARE FILLED. Do not populate them from vendors' published list prices —
#     list prices ignore committed-use discounts, minimums and rounding, and
#     will be wrong in BOTH directions. Pull one real month from each of the
#     four vendor dashboards and derive the effective unit rate.
#
#     After filling: set RATE_TABLE_VERSION (e.g. "2026-08"), run the backfill,
#     then reconcile against the invoices per spec §5. An unreconciled cost
#     model is worse than none, because it gets believed.
RATES: Dict[str, Optional[float]] = {
    # Twilio bills per STARTED minute, so these are per whole minute.
    "twilio_inbound_per_min": None,      # PSTN inbound leg
    "twilio_stream_per_min": None,       # Media Streams leg
    # AssemblyAI streaming ASR is priced on audio duration.
    "assemblyai_per_hour": None,
    # ElevenLabs streaming TTS is priced per character synthesised.
    "elevenlabs_per_1k_chars": None,
    # LLM, per million tokens.
    "llm_input_per_1m_tokens": None,
    "llm_output_per_1m_tokens": None,
    # USD→GBP. Versioned with the table so a recompute is reproducible.
    "usd_gbp": None,
}

# Transcript turns are [{"role", "text"}] (app/flows/brain.py:_append_turn).
# The assistant literal is consistently "assistant". The CALLER side is not:
# brain.py writes "user", routes/twilio.py writes "caller". We only need the
# assistant side for TTS, but do not assume caller-side symmetry if this module
# is ever extended.
_ASSISTANT_ROLES = frozenset({"assistant"})

# Fallback only, when the LLM layer reports no real token usage. Deliberately
# crude — a flagged estimate is honest, a precise-looking guess is not.
_CHARS_PER_TOKEN = 4.0


class RatesNotConfigured(RuntimeError):
    """Raised by require_rates(); never escapes estimate_call_cost()."""


def rates_configured() -> bool:
    """True only when every rate is populated and the version is set."""
    return RATE_TABLE_VERSION != "unset" and all(v is not None for v in RATES.values())


def require_rates() -> None:
    if not rates_configured():
        missing = sorted(k for k, v in RATES.items() if v is None)
        raise RatesNotConfigured(
            "app/obs/cost.py RATES are placeholders — fill from real vendor "
            f"invoices before trusting any cost figure. Missing: {missing or ['RATE_TABLE_VERSION']}"
        )


# ---------------------------------------------------------------------------
# Component costs — each returns GBP pence as a float; rounding happens once,
# at the end, so we do not accumulate rounding error across five components.
# ---------------------------------------------------------------------------

def _usd_to_pence(usd: float) -> float:
    return usd * float(RATES["usd_gbp"]) * 100.0


def _twilio_pence(duration_s: Optional[int]) -> float:
    if not duration_s or duration_s <= 0:
        return 0.0
    # Per STARTED minute: a 61-second call bills 2 minutes, not 1.02.
    minutes = math.ceil(duration_s / 60.0)
    per_min = float(RATES["twilio_inbound_per_min"]) + float(RATES["twilio_stream_per_min"])
    return _usd_to_pence(minutes * per_min)


def _assemblyai_pence(duration_s: Optional[int]) -> float:
    if not duration_s or duration_s <= 0:
        return 0.0
    # Priced on audio duration. This is CALL duration, which is longer than
    # speech duration — correct, the stream is open for the whole call.
    return _usd_to_pence((duration_s / 3600.0) * float(RATES["assemblyai_per_hour"]))


def assistant_chars(transcript: Optional[Iterable[Dict[str, Any]]]) -> int:
    """Characters of assistant text — the TTS billing basis.

    ⚠️  Known upward bias. call_logger.py records that the obs transcript is
    built from `full_reply`, which llm_stream assembles RAW, while Gate 5f runs
    on the TTS path only. So a stored turn can contain text that was never
    synthesised (the guard re-steered instead). This over-counts ElevenLabs on
    calls where Gate 5f fired. Quantify it during reconciliation (spec §5); if
    it exceeds ~10%, bill TTS off a counter in tts_stream.py instead of off the
    transcript.
    """
    if not transcript:
        return 0
    total = 0
    for turn in transcript:
        if not isinstance(turn, dict):
            continue
        if str(turn.get("role", "")).lower() in _ASSISTANT_ROLES:
            total += len(turn.get("text") or "")
    return total


def _elevenlabs_pence(chars: int) -> float:
    if chars <= 0:
        return 0.0
    return _usd_to_pence((chars / 1000.0) * float(RATES["elevenlabs_per_1k_chars"]))


def _llm_pence(
    llm_usage: Optional[Dict[str, Any]],
    transcript: Optional[Iterable[Dict[str, Any]]],
) -> tuple[float, bool]:
    """Return (pence, estimated). Prefers real token counts."""
    in_tok = out_tok = None
    if llm_usage:
        in_tok = llm_usage.get("input_tokens")
        out_tok = llm_usage.get("output_tokens")

    estimated = in_tok is None or out_tok is None
    if estimated:
        # Crude chars÷4 fallback. Flagged so cost_report can exclude these rows
        # from the margin analysis rather than quietly averaging guesses in.
        turns = list(transcript or [])
        all_chars = sum(len((t.get("text") or "")) for t in turns if isinstance(t, dict))
        asst_chars = assistant_chars(turns)
        out_tok = asst_chars / _CHARS_PER_TOKEN
        # Input is the growing conversation re-sent each turn, so it scales with
        # turns², not chars. Approximated as chars × turns / 2.
        in_tok = (all_chars / _CHARS_PER_TOKEN) * max(len(turns), 1) / 2.0

    usd = (
        (float(in_tok) / 1_000_000.0) * float(RATES["llm_input_per_1m_tokens"])
        + (float(out_tok) / 1_000_000.0) * float(RATES["llm_output_per_1m_tokens"])
    )
    return _usd_to_pence(usd), estimated


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def estimate_call_cost(
    duration_s: Optional[int],
    transcript: Optional[List[Dict[str, Any]]] = None,
    llm_usage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Cost one call. Never raises.

    Returns:
        {
          "total_pence": int | None,   # None when rates are unconfigured
          "breakdown":   {"twilio": int, "assemblyai": int, "elevenlabs": int,
                          "llm": int, "llm_estimated": bool,
                          "assistant_chars": int, "duration_s": int|None},
          "version":     str,
          "error":       str | None,
        }

    total_pence is None — not 0 — when costing could not be performed. Zero is a
    real cost; absence is not, and the report must be able to tell them apart.
    """
    breakdown: Dict[str, Any] = {
        "duration_s": duration_s,
        "assistant_chars": 0,
    }
    try:
        require_rates()

        chars = assistant_chars(transcript)
        twilio = _twilio_pence(duration_s)
        asr = _assemblyai_pence(duration_s)
        tts = _elevenlabs_pence(chars)
        llm, llm_estimated = _llm_pence(llm_usage, transcript)

        breakdown.update(
            {
                "twilio": round(twilio),
                "assemblyai": round(asr),
                "elevenlabs": round(tts),
                "llm": round(llm),
                "llm_estimated": llm_estimated,
                "assistant_chars": chars,
            }
        )
        # Round once, from the unrounded sum.
        return {
            "total_pence": int(round(twilio + asr + tts + llm)),
            "breakdown": breakdown,
            "version": RATE_TABLE_VERSION,
            "error": None,
        }

    except RatesNotConfigured as exc:
        # Expected until the invoices are in. Debug, not warning — this would
        # otherwise fire on every single call and drown the log.
        _log.debug("[obs.cost] rates not configured: %s", exc)
        return {"total_pence": None, "breakdown": breakdown,
                "version": RATE_TABLE_VERSION, "error": "rates_not_configured"}

    except Exception as exc:  # pragma: no cover - defensive
        # A pricing bug must never cost us a call record. See spec §3.2.
        _log.warning("[obs.cost] cost estimation failed: %r", exc)
        return {"total_pence": None, "breakdown": breakdown,
                "version": RATE_TABLE_VERSION, "error": repr(exc)}
