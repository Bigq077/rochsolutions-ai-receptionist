# app/proactive_info.py
"""
Post-confirmation proactive information block for Susie.

After every successful booking, Susie volunteers useful details the patient
didn't think to ask (parking, address, first-visit prep). This module builds
that spoken block from a knowledge-base dict.

Entry points:
  get_proactive_info(session, kb) -> str   — build the TTS-ready info string
  append_if_fits(confirmation, extra, max_chars) -> str  — 400-char guard
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Info blocks
# Field names in {braces} must match keys in CLINIC_KB (knowledge_base.py).
# Blocks with missing KB keys are silently skipped.
# ---------------------------------------------------------------------------

PROACTIVE_INFO_BLOCKS = [
    {
        "id": "parking",
        "condition": "always",
        "text": "Good news on parking — {parking}",
    },
    {
        "id": "first_visit_prep",
        "condition": "first_visit",
        "text": "As it's your first visit, if you could arrive five minutes early that would be great.",
    },
    {
        "id": "address",
        "condition": "always",
        "text": "We're at {clinic_address} — I'll also send you a text with all the details.",
    },
]

CONNECTORS = ["Also, ", "And ", "One more thing — "]


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def evaluate_condition(condition: str, session: dict) -> bool:
    """Return True if this block should fire for the given session."""
    if condition == "always":
        return True
    if condition == "first_visit":
        return session.get("is_first_visit", True)
    if condition == "returning":
        return not session.get("is_first_visit", True)
    return False


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def get_proactive_info(session: dict, kb: dict) -> str:
    """
    Build the post-confirmation proactive info string.

    Evaluates each block's condition, formats its text against `kb`,
    joins with natural connectors, and enforces a 60-word cap via
    sentence-boundary truncation.

    Never raises — returns "" on any exception.

    Args:
        session: Must contain "is_first_visit" bool for condition checks.
        kb:      Dict matching CLINIC_KB field names.
    """
    try:
        passing = []
        for block in PROACTIVE_INFO_BLOCKS:
            if not evaluate_condition(block["condition"], session):
                continue
            try:
                text = block["text"].format(**kb)
                passing.append(text)
            except KeyError:
                continue  # silently drop blocks with missing KB keys

        if not passing:
            return ""

        parts = [passing[0]]
        for i, sentence in enumerate(passing[1:], 1):
            connector = CONNECTORS[i % len(CONNECTORS)]
            parts.append(connector + sentence[0].lower() + sentence[1:])
        result = " ".join(parts)

        # 60-word cap — truncate at last complete sentence boundary
        words = result.split()
        if len(words) > 60:
            logger.warning(
                "Proactive info exceeded 60 words (%d). Truncating.", len(words)
            )
            truncated = " ".join(words[:60])
            last_period = max(
                truncated.rfind("."),
                truncated.rfind("!"),
                truncated.rfind("?"),
            )
            result = truncated[:last_period + 1] if last_period > 0 else truncated

        return result

    except Exception as exc:
        logger.error("get_proactive_info failed: %r", exc)
        return ""


# ---------------------------------------------------------------------------
# 400-character guard (used at call site)
# ---------------------------------------------------------------------------

def append_if_fits(confirmation: str, extra: str, max_chars: int = 400) -> str:
    """
    Append `extra` to `confirmation` only if the combined string is ≤ max_chars.

    If the combined length exceeds the limit, logs a warning and returns
    `confirmation` unchanged — no mid-sentence truncation.
    """
    if not extra:
        return confirmation
    combined = f"{confirmation} {extra}"
    if len(combined) > max_chars:
        logger.warning(
            "Post-booking proactive info omitted: combined TTS string is %d chars (max %d).",
            len(combined),
            max_chars,
        )
        return confirmation
    return combined
