"""
app/obs/redact.py
-----------------
PII redaction for the failure→regression pipeline (spec §5.4 / §7).

Call transcripts are special-category health data. Before a real call can become a
committed test scenario, every name, phone number, and email must be stripped. This
module does that and — critically — provides a HARD assertion (`assert_no_pii`) that
raises loudly if any phone/email pattern survives, so a leak fails the pipeline
rather than silently landing PII in the repo.

Scope + honesty:
- Phone numbers and emails are detected by pattern and are hard-guaranteed to be
  removed (assert_no_pii enforces it).
- Names are removed only where we KNOW them — the names captured on the call record
  (collected.name / full_name / caller name) are passed in and struck out. Arbitrary
  names embedded in free speech are not reliably detectable and are NOT claimed to be.
- Free-text clinical detail cannot be reliably auto-detected. The to_scenario builder
  therefore DROPS structured free-text fields (collected.reason, judge evidence)
  rather than pretend to scrub prose — see to_scenario.py.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List

# --- Detection patterns -----------------------------------------------------
# UK numbers: +44…, 0044…, 07… mobiles, and generic grouped runs of 6+ digits
# (with optional spaces/dashes) that read as a phone number.
_PHONE_RE = re.compile(
    r"""(?x)
    (?:
        (?:\+|00)\s?44\s?\d(?:[\s-]?\d){8,10}   # +44 / 0044 international
      | 0\d(?:[\s-]?\d){8,10}                    # 0-led national (07…, 01…, 02…)
      | (?<!\d)\d(?:[\s-]?\d){5,}(?!\d)          # any run of 6+ digits, grouped
    )
    """
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_PHONE_PLACEHOLDER = "[PHONE]"
_EMAIL_PLACEHOLDER = "[EMAIL]"
_NAME_PLACEHOLDER = "[NAME]"


class PIILeakError(AssertionError):
    """Raised when PII survives redaction — the pipeline must fail loudly."""


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def _name_tokens(names: Iterable[str]) -> List[str]:
    """Split provided full names into individual word tokens worth redacting."""
    tokens: List[str] = []
    for n in names or []:
        if not n:
            continue
        for part in re.split(r"\s+", str(n).strip()):
            part = part.strip()
            if len(part) >= 2:  # skip initials/noise
                tokens.append(part)
    # Longest first so "Anne Marie" is struck before "Anne".
    return sorted(set(tokens), key=len, reverse=True)


def redact_text(text: str, names: Iterable[str] = ()) -> str:
    """Redact emails, phone numbers, and known name tokens from a string."""
    if not text:
        return text
    out = _EMAIL_RE.sub(_EMAIL_PLACEHOLDER, text)
    out = _PHONE_RE.sub(_PHONE_PLACEHOLDER, out)
    for tok in _name_tokens(names):
        out = re.sub(rf"\b{re.escape(tok)}\b", _NAME_PLACEHOLDER, out, flags=re.IGNORECASE)
    return out


def redact_transcript(turns: List[Dict[str, str]], names: Iterable[str] = ()) -> List[Dict[str, str]]:
    """Return a redacted copy of an ordered transcript (list of {role,text})."""
    redacted: List[Dict[str, str]] = []
    for turn in turns or []:
        redacted.append({
            "role": turn.get("role", "?"),
            "text": redact_text(turn.get("text", ""), names),
        })
    return redacted


# ---------------------------------------------------------------------------
# Hard assertion — the safety net
# ---------------------------------------------------------------------------

def find_pii(text: str) -> List[str]:
    """Return any phone/email substrings still present in text (empty = clean)."""
    if not text:
        return []
    return _EMAIL_RE.findall(text) + _PHONE_RE.findall(text)


def assert_no_pii(text: str, *, where: str = "") -> None:
    """Raise PIILeakError if any phone/email pattern remains. Fail loudly."""
    leaks = find_pii(text)
    if leaks:
        loc = f" in {where}" if where else ""
        raise PIILeakError(
            f"PII survived redaction{loc}: {leaks!r}. Refusing to emit a scenario."
        )


def assert_transcript_clean(turns: List[Dict[str, str]]) -> None:
    for i, turn in enumerate(turns or []):
        assert_no_pii(turn.get("text", ""), where=f"turn[{i}]")
