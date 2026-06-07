# app/media_streams/authoritative_policy.py
"""
Authoritative clinic-policy lane.

Centralises the three concerns the flow used to conflate:

  1. service_fit            — "do you treat this condition?"
  2. policy_eligibility     — "is this patient age/category allowed?"
  3. booking_intent         — "does the caller actually want to book?"

After any deterministic, policy-grounded answer is delivered to the caller,
flow code calls ``set_lock(...)`` to mark the active authoritative topic and
the resulting ``eligibility_status``.  While the lock is fresh, clarifying
follow-ups ("even for 16?", "what about my son then?", "are you sure?") are
intercepted here and answered deterministically from the clinic config — the
LLM is never allowed to overwrite a clinic-critical fact.

Pure helpers — no I/O, no network, no TTS.  The caller decides when and how
to speak the returned response_text.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple


# ── Lock topic constants ────────────────────────────────────────────────────────

TOPIC_CHILDREN_AGE = "children_age"

# ── Eligibility status constants ────────────────────────────────────────────────

ELIGIBILITY_ALLOWED     = "allowed"
ELIGIBILITY_DISALLOWED  = "disallowed"
ELIGIBILITY_PENDING_AGE = "pending_age"
ELIGIBILITY_UNKNOWN     = "unknown"

# ── Lock lifetime ───────────────────────────────────────────────────────────────
# Clarifying follow-ups almost always arrive within two turns; keep the lock
# short so stale context never leaks into an unrelated later topic.
_LOCK_TTL_SECONDS = 90.0


# ── Lock management ─────────────────────────────────────────────────────────────

def set_lock(
    session: Dict[str, Any],
    topic: str,
    eligibility_status: str,
    *,
    min_age: Optional[int] = None,
    last_age: Optional[int] = None,
) -> None:
    """Stamp an authoritative-topic lock on the session."""
    session["_authoritative_policy_topic"]       = topic
    session["_authoritative_policy_set_at"]      = time.time()
    session["_authoritative_policy_eligibility"] = eligibility_status
    if min_age is not None:
        session["_authoritative_policy_min_age"] = int(min_age)
    if last_age is not None:
        session["_authoritative_policy_last_age"] = int(last_age)
    # Session-wide eligibility mirror so other subsystems (booking gate,
    # service-fit router) can read it without caring about lock freshness.
    session["eligibility_status"] = eligibility_status


def clear_lock(session: Dict[str, Any]) -> None:
    for k in (
        "_authoritative_policy_topic",
        "_authoritative_policy_set_at",
        "_authoritative_policy_eligibility",
        "_authoritative_policy_min_age",
        "_authoritative_policy_last_age",
    ):
        session.pop(k, None)


def get_active_lock(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return lock dict if fresh, else ``None`` (and clears stale lock)."""
    topic = session.get("_authoritative_policy_topic")
    if not topic:
        return None
    set_at = float(session.get("_authoritative_policy_set_at") or 0.0)
    if (time.time() - set_at) > _LOCK_TTL_SECONDS:
        clear_lock(session)
        return None
    return {
        "topic":              topic,
        "set_at":             set_at,
        "eligibility_status": session.get("_authoritative_policy_eligibility"),
        "min_age":            session.get("_authoritative_policy_min_age"),
        "last_age":           session.get("_authoritative_policy_last_age"),
    }


# ── Policy-config resolution ────────────────────────────────────────────────────

def resolve_min_age(clinic: Dict[str, Any]) -> Optional[int]:
    """Single authoritative reader for clinic minimum patient age."""
    pol = clinic.get("patient_policies", {}) or {}
    raw = pol.get("min_patient_age")
    if raw is None and pol.get("no_children") is True:
        raw = pol.get("adult_age_threshold", 18)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def children_policy_text(clinic: Dict[str, Any]) -> str:
    """Short authoritative answer used when the caller asks the raw policy."""
    faq = clinic.get("faq", {}) or {}
    txt = (faq.get("children_policy") or "").strip()
    if txt:
        return txt
    min_age = resolve_min_age(clinic)
    if min_age is not None:
        return f"Yes \u2014 we see patients aged {min_age} and over."
    return "We see adult patients; I can check age eligibility for you."


# ── Clinic-critical topic detection (for no-LLM guard) ──────────────────────────

_AGE_CRITICAL_KEYWORDS: Tuple[str, ...] = (
    "child", "children", "kid", "kids", "minor", "minors", "paediatric",
    "pediatric", "teenager", "teen", "young", "son", "daughter", "age",
    "old enough", "too young", "under ", "years old", "year old", "yr old",
    "how young",
)


def is_clinic_critical_age_topic(text: str) -> bool:
    """True if the utterance touches age/child eligibility in any form."""
    if not text:
        return False
    lo = text.lower()
    return any(k in lo for k in _AGE_CRITICAL_KEYWORDS)


# ── Clarification detection ─────────────────────────────────────────────────────

_CLARIFY_PATTERNS: Tuple[str, ...] = (
    "even for", "what about", "how about", "and for", "does that include",
    "is that including", "what if", "but what about", "so for",
    "are you sure", "you sure", "definitely", "really",
    "so can i", "can i still", "can we still", "could i still",
    "can i book", "can we book", "could i book", "can i go ahead",
    "should i still", "just to check", "just to be sure",
    "to confirm", "to be clear",
)

_AGE_REFERENCE_PATTERNS: Tuple[str, ...] = (
    "14", "15", "16", "17", "18", "19", "20",
    "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty",
    "my son", "my daughter", "my child", "my kid", "my teenager", "my teen",
    "paediatric", "pediatric",
    "younger", "older", "youngest",
)


def is_age_clarification(text: str) -> bool:
    """Heuristic: caller is clarifying the previously-answered age policy."""
    if not text:
        return False
    lo = text.lower().strip()
    if not lo:
        return False
    # Pure "yes"/"no" is not a clarification — let normal routing handle it.
    if lo in {"yes", "yeah", "yep", "no", "nope", "ok", "okay"}:
        return False
    has_clarify_cue = any(p in lo for p in _CLARIFY_PATTERNS)
    has_age_ref     = any(p in lo for p in _AGE_REFERENCE_PATTERNS)
    # Either a clarifying cue plus any age/child reference, or a bare numeric
    # age inside the clinic-critical band counts as a clarification.
    if has_clarify_cue and (has_age_ref or is_clinic_critical_age_topic(lo)):
        return True
    return False


# ── Deterministic clarification answers ─────────────────────────────────────────

_NUMERIC_WORDS: Dict[str, int] = {
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty-one": 21, "twenty one": 21,
}


def _extract_age_reference(text: str) -> Optional[int]:
    lo = text.lower()
    for word, val in _NUMERIC_WORDS.items():
        if word in lo:
            return val
    import re
    m = re.search(r"\b(\d{1,2})\b", lo)
    if m:
        try:
            n = int(m.group(1))
            if 0 < n < 100:
                return n
        except ValueError:
            pass
    return None


def answer_age_clarification(
    text: str,
    clinic: Dict[str, Any],
    session: Dict[str, Any],
) -> Optional[str]:
    """
    Deterministic reply to a child/age-eligibility clarification.

    Returns ``None`` if the text doesn't look like one — caller should fall
    through to normal routing.  Returns a short, authoritative sentence when
    it does.  Never consults the LLM.
    """
    if not is_age_clarification(text):
        return None

    min_age = resolve_min_age(clinic)
    if min_age is None:
        return None

    age_ref = _extract_age_reference(text)
    lo = text.lower()

    # Caller is asking whether booking is still possible despite the policy
    # conversation ("so can I book?", "can I still book him in?").
    if any(p in lo for p in (
        "can i book", "can we book", "could i book", "can i still",
        "can we still", "can i go ahead", "should i still", "so can i",
    )):
        elig = (session.get("eligibility_status")
                or session.get("_authoritative_policy_eligibility"))
        last_age = session.get("_authoritative_policy_last_age")
        if elig == ELIGIBILITY_DISALLOWED:
            return (
                f"I\u2019m afraid not \u2014 we see patients aged {min_age} "
                "and over, so we wouldn\u2019t be able to book them in."
            )
        if elig == ELIGIBILITY_ALLOWED:
            return "Yes \u2014 that\u2019s fine. Would you like me to book an appointment?"
        if elig == ELIGIBILITY_PENDING_AGE:
            return f"I\u2019d just need to check their age first \u2014 are they {min_age} or over?"

    # Caller named a specific age — give a direct allow/disallow answer.
    if age_ref is not None and 0 < age_ref < 100:
        if age_ref >= min_age:
            return f"Yes \u2014 {age_ref} is fine, we see patients aged {min_age} and over."
        return (
            f"I\u2019m afraid not \u2014 {age_ref} is under our minimum age. "
            f"We see patients aged {min_age} and over."
        )

    # "Are you sure?" / "really?" — restate policy.
    if any(p in lo for p in ("are you sure", "you sure", "definitely", "really")):
        return f"Yes \u2014 we see patients aged {min_age} and over."

    # "what about my son/daughter/child" with no age — ask for age.
    if any(p in lo for p in ("my son", "my daughter", "my child", "my kid",
                             "my teenager", "my teen")):
        return f"Of course \u2014 how old are they? We see patients aged {min_age} and over."

    # Generic clarification cue without an age — restate.
    return f"We see patients aged {min_age} and over."


# ── Booking gate ────────────────────────────────────────────────────────────────

def booking_allowed(session: Dict[str, Any]) -> bool:
    """False only when eligibility is explicitly disallowed."""
    elig = (session.get("eligibility_status")
            or session.get("_authoritative_policy_eligibility"))
    return elig != ELIGIBILITY_DISALLOWED


def booking_blocked_response(clinic: Dict[str, Any]) -> str:
    min_age = resolve_min_age(clinic)
    if min_age is not None:
        return (
            f"I\u2019m afraid we can\u2019t book them in \u2014 we see patients "
            f"aged {min_age} and over."
        )
    return "I\u2019m afraid we can\u2019t book them in under our current policy."
