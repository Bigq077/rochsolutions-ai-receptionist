# app/tools/actionable_summary.py
"""
Build actionable call summaries for Mark.

Enhanced with Claude Sonnet 4.6:
  - Column 1  (Summary):        Sharp, context-aware one-liner
  - Column 11 (Followup Notes): Actionable notes for Mark, not just a raw reason string

Falls back to rule-based logic if the LLM call fails — nothing breaks.

IMPORTANT: build_actionable_summary_row is now async.
Callers must await it. In handoff.py / twilio.py you're already in async context,
so just change:
    row = build_actionable_summary_row(summary)
to:
    row = await build_actionable_summary_row(summary)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)


def _get_confirmed_phone(session: dict) -> str:
    """
    Return a phone number safe for downstream SMS/summary use.
    - phone_confirmed=True  → use collected["phone"] (explicitly confirmed)
    - phone_confirmed=False → return "" (caller explicitly rejected — do not use)
    - phone_confirmed=None  → use twilio_from only if phone_from_twilio=True
                               (caller-ID available but phone step not reached)
    """
    collected = session.get("collected", {}) or {}
    confirmed = session.get("phone_confirmed")

    if confirmed is True:
        return (
            collected.get("phone")
            or session.get("phone_number")
            or ""
        )
    if confirmed is False:
        return ""
    # confirmed is None: flow never reached phone step (early hangup etc.)
    # Fall back to caller-ID only when it was auto-populated from Twilio.
    if session.get("phone_from_twilio"):
        raw = session.get("twilio_from_local") or session.get("twilio_from") or ""
        if raw and not raw.startswith("client:"):
            return raw
    return ""

# ---------------------------------------------------------------------------
# Anthropic config
# ---------------------------------------------------------------------------
_ANTHROPIC_URL   = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_MODEL = "claude-sonnet-4-6"
_ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY", "")

_SYSTEM_PROMPT = """\
You are a clinical receptionist assistant for Theorem Health, a physiotherapy clinic in the UK.
You summarise AI receptionist call logs so the clinic owner (Mark) can quickly scan a spreadsheet and know what happened and what to do next.

Tone: concise, professional, practical. Written for someone scanning rows in Google Sheets.
Never use filler like "it appears that" or "the patient seemed to". State facts directly.
Always use the patient's first name if known. If unknown, say "Caller".

Clinic context:
- Sessions: £75 / 50 min, self-pay (patients claim back from their insurer)
- Does NOT accept Bupa direct billing
- Locations: Alcester (Mon–Fri 8:30am–9pm) and Redditch (flexible)
- Owner direct line: 07870 166861
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def build_actionable_summary_row(summary: Dict[str, Any]) -> List[Any]:
    """
    Convert a call summary dict into a 15-column Google Sheets row.

    Columns (EXACT ORDER — must match sheet headers A–O):
     1.  Summary             Claude one-liner with emoji
     2.  Outcome             abandoned / booked / rescheduled / faq_only / manual_followup / failed
     3.  Booked?             YES / NO
     4.  Patient Name
     5.  Phone
     6.  Service/Reason
     7.  Asked About Price?  YES / NO
     8.  Has Insurance       YES / NO
     9.  Insurance Company
    10.  Manual Followup     YES / NO
    11.  Followup Notes      Claude actionable notes for Mark
    12.  Call Date           YYYY-MM-DD HH:MM
    13.  Call Duration (sec)
    14.  Call SID
    15.  Full Details (JSON)
    """

    raw_session    = summary.get("_raw_session", {}) or {}
    collected      = raw_session.get("collected", {}) or {}
    meta           = summary.get("meta", {}) or {}
    patient_info   = summary.get("patient", {}) or {}
    appt_info      = summary.get("appointment", {}) or {}
    insurance_info = summary.get("insurance", {}) or {}
    handoff_info   = summary.get("handoff", {}) or {}

    # ── Structured fields (rule-based, always reliable) ────────────────────

    outcome = summary.get("outcome", "unknown")
    booked  = "YES" if outcome in ("booked", "rescheduled") else "NO"

    patient_name = collected.get("name") or patient_info.get("name") or ""

    phone = _get_confirmed_phone(raw_session)

    service = (
        collected.get("reason")
        or appt_info.get("reason")
        or appt_info.get("service")
        or ""
    )

    asked_price = _detect_price_question(summary, raw_session)

    insurer = collected.get("insurer") or insurance_info.get("insurer_name") or ""
    has_insurance     = "YES" if insurer else "NO"
    insurance_company = insurer

    manual_followup = "YES" if handoff_info.get("manual_followup_needed") else "NO"

    call_date = meta.get("ended_at_utc", "")
    if call_date:
        try:
            dt = datetime.fromisoformat(call_date.replace("Z", "+00:00"))
            call_date = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

    duration = meta.get("duration_sec", "") or raw_session.get("twilio_duration_sec", "")
    call_sid = meta.get("call_sid", "") or raw_session.get("call_sid", "")

    full_details = json.dumps(summary, ensure_ascii=False)
    if len(full_details) > 45000:
        full_details = full_details[:45000] + "...truncated"

    # ── LLM-enhanced fields (columns 1 and 11) ─────────────────────────────

    llm = await _llm_enhance(
        summary        = summary,
        raw_session    = raw_session,
        outcome        = outcome,
        patient_name   = patient_name,
        service        = service,
        insurer        = insurer,
        asked_price    = asked_price,
        duration       = duration,
        handoff_reason = handoff_info.get("reason", ""),
    )

    # ── Assemble final row ─────────────────────────────────────────────────

    row = [
        llm["summary_text"],    #  1. Summary
        outcome,                #  2. Outcome
        booked,                 #  3. Booked?
        patient_name,           #  4. Patient Name
        phone,                  #  5. Phone
        service,                #  6. Service/Reason
        asked_price,            #  7. Asked About Price?
        has_insurance,          #  8. Has Insurance
        insurance_company,      #  9. Insurance Company
        manual_followup,        # 10. Manual Followup Needed
        llm["followup_notes"],  # 11. Followup Notes
        call_date,              # 12. Call Date
        duration,               # 13. Call Duration
        call_sid,               # 14. Call SID
        full_details,           # 15. Full Details
    ]

    logger.info(
        "📊 Row built — outcome=%s name=%s phone=%s dur=%ss source=%s",
        outcome,
        patient_name or "None",
        "yes" if phone else "no",
        duration,
        llm["source"],
    )

    return row


# ---------------------------------------------------------------------------
# LLM enhancement
# ---------------------------------------------------------------------------

async def _llm_enhance(
    *,
    summary: Dict[str, Any],
    raw_session: Dict[str, Any],
    outcome: str,
    patient_name: str,
    service: str,
    insurer: str,
    asked_price: str,
    duration: Any,
    handoff_reason: str,
) -> Dict[str, str]:
    """
    Call Claude Sonnet 4.6 to generate the summary one-liner and followup notes.
    Returns dict with keys: summary_text, followup_notes, source ("llm" or "fallback").
    """

    if not _ANTHROPIC_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — using rule-based fallback for summary")
        return _fallback(outcome, patient_name, service, duration, handoff_reason)

    faq_turns   = summary.get("faq", []) or []
    convo_stats = summary.get("conversation", {}) or {}
    turns       = raw_session.get("turns", []) or []

    faq_topics = ", ".join(
        item.get("question", "") for item in faq_turns if isinstance(item, dict)
    ) or "none"

    context = {
        "outcome":          outcome,
        "patient_name":     patient_name or "unknown",
        "service_reason":   service or "not stated",
        "insurer":          insurer or "none mentioned",
        "asked_price":      asked_price,
        "duration_sec":     duration,
        "turn_count":       convo_stats.get("turn_count", 0),
        "miss_count":       convo_stats.get("miss_count", 0),
        "faq_topics":       faq_topics,
        "handoff_reason":   handoff_reason or "none",
        "calendar_status":  (summary.get("appointment", {}) or {}).get("calendar", {}).get("status", "none"),
        "confidence_score": summary.get("confidence_score"),
        "conversation":     _format_turns(turns, max_turns=10) or "not available",
    }

    user_prompt = f"""\
Call data:
{json.dumps(context, indent=2, ensure_ascii=False)}

Return ONLY a JSON object with exactly these two keys — no markdown, no explanation:

{{
  "summary_text": "<emoji> <name> – <specific one-liner, max 120 chars>",
  "followup_notes": "<2–3 sentence actionable note for Mark, or empty string>"
}}

Rules for summary_text:
- Start with ONE emoji: ✅ booked  🔄 rescheduled  ⚠️ abandoned mid-flow  ❌ hung up immediately
  📞 needs callback  ❓ info only, no booking  🔧 technical failure
- Use the patient's first name (or "Caller" if unknown)
- Include the specific condition/service if known
- Include ONE key differentiator — e.g. "asked about Bupa", "price concern", "couldn't find a time",
  "post-op shoulder", "existing patient rescheduling"
- Max 120 characters

Rules for followup_notes:
- Write directly to Mark, not in third person
- If booked: confirm what was booked and any clinical notes mentioned
- If abandoned / faq_only: state what blocked the booking and suggest the best next action
- If manual_followup: state exactly what needs doing and any urgency
- If nothing actionable: return empty string ""
- Do NOT start with "The patient" — use their name or "Caller"
- Max 3 sentences
"""

    for _attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(
                    _ANTHROPIC_URL,
                    headers={
                        "x-api-key":         _ANTHROPIC_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type":      "application/json",
                    },
                    json={
                        "model":      _ANTHROPIC_MODEL,
                        "max_tokens": 300,
                        "system":     _SYSTEM_PROMPT,
                        "messages":   [{"role": "user", "content": user_prompt}],
                    },
                )
        except httpx.TimeoutException:
            logger.warning("Anthropic API timed out — using rule-based fallback")
            return _fallback(outcome, patient_name, service, duration, handoff_reason)
        except Exception as e:
            logger.warning("LLM summary network error (%s) — using rule-based fallback", repr(e))
            return _fallback(outcome, patient_name, service, duration, handoff_reason)

        if resp.status_code == 529:
            if _attempt < 2:
                import asyncio as _aio
                logger.warning("Anthropic overloaded (529), retry %d/2...", _attempt + 1)
                await _aio.sleep(1)
                continue
            logger.warning("Anthropic overloaded (529) after retries — using rule-based fallback")
            return _fallback(outcome, patient_name, service, duration, handoff_reason)

        if resp.status_code != 200:
            logger.error("Anthropic API error %s — %s", resp.status_code, resp.text[:200])
            return _fallback(outcome, patient_name, service, duration, handoff_reason)

        break  # success

    data     = resp.json()
    raw_text = data["content"][0]["text"].strip()

    # Strip markdown fences if present
    if raw_text.startswith("```"):
        parts    = raw_text.split("```")
        raw_text = parts[1].lstrip("json").strip() if len(parts) > 1 else raw_text

    try:
        parsed = json.loads(raw_text)
    except Exception as e:
        logger.warning("LLM summary JSON parse failed (%s) — using rule-based fallback", repr(e))
        return _fallback(outcome, patient_name, service, duration, handoff_reason)

    return {
        "summary_text":   str(parsed.get("summary_text", "")).strip()[:150],
        "followup_notes": str(parsed.get("followup_notes", "")).strip()[:500],
        "source":         "llm",
    }


# ---------------------------------------------------------------------------
# Rule-based fallback (original logic preserved as safety net)
# ---------------------------------------------------------------------------

def _fallback(
    outcome: str,
    name: str,
    service: str,
    duration: Any,
    handoff_reason: str,
) -> Dict[str, str]:
    return {
        "summary_text":   _build_summary_text(outcome, name, service, duration),
        "followup_notes": handoff_reason or "",
        "source":         "fallback",
    }


def _build_summary_text(outcome: str, name: str, service: str, duration: Any) -> str:
    """Original rule-based one-liner — fallback only."""
    n = name or "Anonymous caller"

    if outcome == "booked":
        return f"✅ {n} BOOKED{' – ' + service if service else ''}"
    if outcome == "rescheduled":
        return f"🔄 {n} RESCHEDULED appointment"
    if outcome == "manual_followup":
        return f"📞 {n} – NEEDS CALLBACK"
    if outcome == "faq_only":
        return f"❓ {n} – asked about {service or 'general info'} but didn't book"
    if outcome == "abandoned":
        try:
            dur = int(duration) if duration else 0
        except Exception:
            dur = 0
        if dur < 10:
            return f"❌ {n} – hung up immediately ({dur}s)"
        if dur < 30:
            return f"⚠️ {n} – abandoned after {dur}s"
        return f"⚠️ {n} – abandoned{' during ' + service + ' booking' if service else ''} ({dur}s)"
    if outcome == "failed":
        return f"🔧 {n} – call failed (technical issue)"
    return f"{n} – {outcome}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_turns(turns: List[Any], max_turns: int = 10) -> str:
    """Format the last N conversation turns into a readable string for the LLM."""
    lines = []
    for turn in turns[-max_turns:]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role", turn.get("speaker", ""))
        text = turn.get("text", turn.get("user", turn.get("content", "")))
        if not text:
            continue
        label = "Susie" if str(role).lower() in ("assistant", "ai", "bot", "susie") else "Patient"
        lines.append(f"{label}: {str(text).strip()}")
    return "\n".join(lines)


def _detect_price_question(summary: Dict, session: Dict) -> str:
    """Detect if the patient asked about pricing during the call."""
    keywords = ["price", "cost", "how much", "fee", "charge", "£", "pound", "expensive"]

    for item in summary.get("faq", []) or []:
        if isinstance(item, dict):
            if any(kw in item.get("question", "").lower() for kw in keywords):
                return "YES"

    for turn in (session.get("turns", []) or [])[-5:]:
        if isinstance(turn, dict):
            msg = (turn.get("user", "") or turn.get("text", "") or "").lower()
            if any(kw in msg for kw in keywords):
                return "YES"

    return "NO"
