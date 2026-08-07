"""
app/obs/judge.py
----------------
LLM-as-judge scoring (spec §5.3).

An async post-call step that sends a stored call's transcript to Claude and gets
back a structured quality judgement (outcome, quality_score 1-5, intent_resolved,
failure_tags, evidence, rubric_version). Runs after teardown, never on the live
path. Gated on config.OBS_JUDGE_ENABLED (default OFF) and an ANTHROPIC_API_KEY —
with either missing, judging is a no-op.

The judgement is stored on the call row (app/obs/store.save_judgement) and, when
it indicates a bad call, raises a review alert through the Phase 2 router.

Keep the prompt and RUBRIC_VERSION in lockstep: whenever the rubric text changes,
bump RUBRIC_VERSION so scores stay comparable and calibration can be re-run.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from app import config

_log = logging.getLogger(__name__)

# Bump whenever the rubric/prompt below changes so scores remain comparable and
# calibration (app/obs/calibrate.py) is re-run.
RUBRIC_VERSION = "v2"

ALLOWED_OUTCOMES = {"booked", "resolved", "no_booking", "abandoned", "misrouted"}
ALLOWED_TAGS = {
    "hallucination", "wrong_info", "dead_end", "caller_frustration",
    "wrong_service_fit", "booking_error", "missed_escalation", "loop",
}
# What needs to happen now (tiered alerting, v2):
#   callback — a human should ring this caller back (immediate SMS to the operator)
#   review   — a system issue to fix later (goes into the daily digest, no per-call SMS)
#   none     — the call was fine
ALLOWED_ACTIONS = {"callback", "review", "none"}
REVIEW_SCORE_THRESHOLD = 2
# A missed clinical/emergency escalation is always callback-worthy — never downgraded.
CALLBACK_FORCE_TAGS = {"missed_escalation"}

_JUDGE_SYSTEM = (
    "You are a strict but fair quality auditor for Susie, an AI phone receptionist "
    "for UK healthcare clinics. You judge a single completed call from its transcript."
)

_JUDGE_PROMPT = """\
Judge this Susie call. Score the CALLER's experience, not Susie's effort.

Rubric anchors for quality_score (1-5):
- 5 = the caller was booked or their need resolved cleanly, and the exchange felt natural.
- 3 = the need was resolved, but clumsily or slowly (repetition, confusion, awkward phrasing).
- 1 = the caller left worse off: given wrong information, hit a dead end, made more frustrated,
      or — most serious — a clinical red-flag / emergency was mishandled or an escalation missed.

failure_tags — include every one that applies, else an empty list:
- hallucination: Susie stated something not grounded in the clinic's info.
- wrong_info: Susie gave incorrect factual info (price, hours, service, address).
- dead_end: the caller was left with no resolution and no follow-up path.
- caller_frustration: the caller shows clear irritation/confusion caused by Susie.
- wrong_service_fit: Susie booked/steered to a service that doesn't fit the caller's need.
- booking_error: a booking failed, was wrong, or was left unconfirmed when it should be confirmed.
- missed_escalation: a human/clinical escalation was needed (e.g. 999/A&E, red-flag symptom) and not handled.
- loop: the conversation went in circles on the same point.

action_needed — does a HUMAN need to do something now? This decides whether the operator
is texted immediately or the call just goes into a daily review digest. Judge it carefully:
- callback: a real caller was left needing something only a human can now resolve — they
    wanted to book, asked for help, or asked to speak to a person, and hung up or were left
    stuck unresolved, and it is genuinely worth someone ringing them back. ALWAYS use
    callback if a clinical red-flag / emergency was mishandled or an escalation was missed.
- review: the call had quality problems (clumsy, looped, wrong info, re-asked a detail) but
    the caller does NOT need a human to follow up — it is a system issue to improve later.
- none: the call was fine; nothing to do.
Be conservative: choose "callback" only when a person contacting the caller would genuinely
help (e.g. a stranded would-be patient, a missed emergency). A merely clumsy call is "review".

Call metadata (context only — judge from the transcript):
clinic_id: {clinic_id}
flow-reported reason: {reason}   booking_confirmed: {booking_confirmed}   transfer_attempted: {transfer_attempted}
duration_s: {duration_s}   turn_count: {turn_count}

Transcript (in order):
{transcript}

Respond with ONLY a single JSON object, no preamble and no code fences, exactly:
{{"outcome": "booked|resolved|no_booking|abandoned|misrouted", "quality_score": 1-5, \
"intent_resolved": true|false, "failure_tags": [..], "action_needed": "callback|review|none", \
"evidence": "1-2 sentences quoting the turns", "rubric_version": "{rubric_version}"}}
"""


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _format_transcript(turns: List[Dict[str, str]]) -> str:
    if not turns:
        return "(no transcript)"
    lines = []
    for t in turns:
        role = str(t.get("role", "?")).upper()
        lines.append(f"{role}: {t.get('text', '')}")
    return "\n".join(lines)


def build_prompt(call: Dict[str, Any]) -> str:
    return _JUDGE_PROMPT.format(
        clinic_id=call.get("clinic_id"),
        reason=call.get("reason"),
        booking_confirmed=call.get("booking_confirmed"),
        transfer_attempted=call.get("transfer_attempted"),
        duration_s=call.get("duration_s"),
        turn_count=call.get("turn_count"),
        transcript=_format_transcript(call.get("transcript") or []),
        rubric_version=RUBRIC_VERSION,
    )


# ---------------------------------------------------------------------------
# Parse + validate (pure, testable — no API call)
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of the model's text, tolerating stray prose/fences."""
    if not text:
        return None
    # Strip code fences if present.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        # Fall back to the first {...} span.
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = brace.group(0) if brace else None
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except (ValueError, TypeError):
        return None


def parse_and_validate(text: str, call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Coerce the model's raw text into a valid judgement dict, or None if unusable.

    Clamps quality_score to 1-5, filters failure_tags to the allowed set, and forces
    outcome / rubric_version into range — so a slightly-off model response is still
    stored as clean, comparable data.
    """
    data = _extract_json(text)
    if not isinstance(data, dict):
        _log.warning("[obs.judge] no JSON in judge output for call_sid=%s",
                     call.get("call_sid"))
        return None

    # quality_score → int in 1..5
    try:
        score = int(round(float(data.get("quality_score"))))
    except (TypeError, ValueError):
        return None
    score = max(1, min(5, score))

    outcome = data.get("outcome")
    if outcome not in ALLOWED_OUTCOMES:
        outcome = None  # keep the row, but don't store a bogus label

    tags = data.get("failure_tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [t for t in tags if t in ALLOWED_TAGS]

    intent = data.get("intent_resolved")
    if not isinstance(intent, bool):
        intent = None

    evidence = data.get("evidence")
    if not isinstance(evidence, str):
        evidence = None

    action = data.get("action_needed")
    if action not in ALLOWED_ACTIONS:
        # Fall back from the score when the model omitted/mangled the field.
        action = "review" if score <= REVIEW_SCORE_THRESHOLD else "none"
    # Safety floor: a missed escalation is always callback-worthy, whatever the model said.
    if CALLBACK_FORCE_TAGS.intersection(tags):
        action = "callback"

    return {
        "call_sid": call.get("call_sid"),
        "clinic_id": call.get("clinic_id"),
        "outcome": outcome,
        "quality_score": score,
        "intent_resolved": intent,
        "failure_tags": tags,
        "action_needed": action,
        "evidence": evidence,
        "rubric_version": RUBRIC_VERSION,
    }


def needs_callback(judgement: Dict[str, Any]) -> bool:
    """True when a human should ring the caller back (immediate operator SMS)."""
    if not judgement:
        return False
    if judgement.get("action_needed") == "callback":
        return True
    # Belt-and-braces: never let a missed escalation slip to the digest.
    return bool(CALLBACK_FORCE_TAGS.intersection(judgement.get("failure_tags") or []))


def needs_review(judgement: Dict[str, Any]) -> bool:
    """True when a call is a system issue to batch into the daily digest (not a callback)."""
    if not judgement or needs_callback(judgement):
        return False
    if judgement.get("action_needed") == "review":
        return True
    score = judgement.get("quality_score")
    return isinstance(score, int) and score <= REVIEW_SCORE_THRESHOLD


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    return bool(config.OBS_JUDGE_ENABLED and os.getenv("ANTHROPIC_API_KEY"))


async def _call_model(prompt: str) -> Optional[str]:
    """Send the judge prompt to Claude and return the raw text response."""
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    # claude-opus-5 (default): thinking is ON by default — unlike claude-opus-4-8,
    # where omitting the `thinking` parameter meant no thinking at all. That
    # matters here because max_tokens caps thinking AND response text together:
    # the old 1024 was sized for the JSON verdict alone, and on Opus 5 the model
    # can spend most of it reasoning and return truncated or empty JSON. Hence
    # the larger budget.
    #
    # Thinking is deliberately left ON rather than disabled. Disabling it is
    # allowed (at effort `high` or below, which is the default) but Opus 5 with
    # thinking off is documented to occasionally leak `<thinking>` tags into the
    # visible response — which would break JSON parsing on a judge whose entire
    # output contract is a JSON object. Thinking blocks come back with empty
    # text by default and are filtered out below, so they cost budget but never
    # reach the parser.
    #
    # Sampling params (temperature / top_p / top_k) are unsupported on Opus 5
    # and 4.8 alike — none are passed. A safety-classifier refusal returns
    # stop_reason="refusal" with no text block, which falls through to None
    # below and simply leaves the call unjudged.
    resp = await client.messages.create(
        model=config.OBS_JUDGE_MODEL,
        max_tokens=8192,
        system=_JUDGE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip() or None


async def judge_call(call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Score one stored call. Returns the judgement dict, or None. Never raises."""
    if not is_enabled():
        return None
    try:
        text = await _call_model(build_prompt(call))
        if not text:
            return None
        return parse_and_validate(text, call)
    except Exception as exc:  # pragma: no cover - defensive
        _log.error("[obs.judge] judge_call failed call_sid=%s: %r",
                   call.get("call_sid"), exc)
        return None


# ---------------------------------------------------------------------------
# Operator SMS
# ---------------------------------------------------------------------------

# How the call ended, in words, keyed on the flow-reported reason.
_ENDING_PHRASE = {
    "no_audio_close":  "Susie ended it after dead air — caller went quiet",
    "caller_hung_up":  "caller hung up",
    "graceful_exit":   "call closed normally",
    "booked":          "ended on a confirmed booking",
    "transferred":     "transferred to a human",
    "pipeline_error":  "the call hit a pipeline error",
}

_QUOTE_CAP = 140


def _last(turns: Any, role: str) -> Optional[str]:
    if not isinstance(turns, list):
        return None
    for turn in reversed(turns):
        if isinstance(turn, dict) and turn.get("role") == role:
            text = (turn.get("text") or "").strip()
            if text:
                return text if len(text) <= _QUOTE_CAP else text[:_QUOTE_CAP - 1] + "…"
    return None


def build_callback_sms(call: Dict[str, Any], judgement: Dict[str, Any]) -> str:
    """The operator's CALL BACK text. Pure — no I/O, so it is testable.

    This used to be one line: a header plus the judge's free-text `evidence`
    field, which the prompt asks for as "1-2 sentences quoting the turns". So
    the whole of what an operator learned about a call was one model-written
    sentence of interpretation — and on CAe2120b that sentence named a redundant
    step Susie had not taken and an ending that had not happened.

    The facts an operator actually needs to decide whether to ring someone back
    are facts we hold: who, how far they got, what each side last said, how the
    line ended. Those are built from the record here. `evidence` is kept, last,
    labelled as the judge's opinion — useful colour, no longer the only content.
    """
    caller = call.get("caller_number") or "unknown number"
    tags = ", ".join(judgement.get("failure_tags") or []) or "unresolved"
    ending = _ENDING_PHRASE.get(call.get("reason")) or (call.get("reason") or "ended")
    booked = "booked" if call.get("booking_confirmed") else "NOT booked"

    lines = [
        f"[Susie] CALL BACK — {call.get('clinic_id')} {caller} "
        f"(score {judgement.get('quality_score')}/5, {booked})",
    ]

    said = _last(call.get("transcript"), "user")
    if said:
        lines.append(f'Caller last said: "{said}"')
    heard = _last(call.get("transcript"), "assistant")
    if heard:
        lines.append(f'Susie last said: "{heard}"')

    lines.append(f"Ended: {ending}.")

    evidence = (judgement.get("evidence") or "").strip()
    lines.append(f"Judge ({tags}): {evidence}" if evidence else f"Judge: {tags}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration: load → judge → store → alert
# ---------------------------------------------------------------------------

async def run_and_store(call_sid: str) -> Optional[Dict[str, Any]]:
    """Load a stored call, judge it, persist the result, and raise a review alert
    if the judgement is bad. Safe to fire-and-forget from teardown; never raises."""
    if not is_enabled():
        return None
    try:
        from app.obs import store
        call = store.get_call(call_sid)
        if call is None:
            _log.warning("[obs.judge] run_and_store: no stored call %s", call_sid)
            return None
        judgement = await judge_call(call)
        if judgement is None:
            return None
        await __import__("asyncio").to_thread(store.save_judgement, call_sid, judgement)

        # Tiered alerting (v2): only "callback" calls text the operator immediately.
        # "review" calls are stored and surfaced once a day by app/obs/digest.py, so a
        # merely-clumsy call never buzzes the phone.
        if needs_callback(judgement):
            try:
                from app.obs import alerts
                await alerts.review_alert(build_callback_sms(call, judgement))
            except Exception as _al_exc:  # pragma: no cover - defensive
                _log.error("[obs.judge] callback alert failed: %r", _al_exc)
        return judgement
    except Exception as exc:  # pragma: no cover - defensive
        _log.error("[obs.judge] run_and_store failed call_sid=%s: %r", call_sid, exc)
        return None
