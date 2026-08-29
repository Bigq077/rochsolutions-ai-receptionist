"""What counts as a bad call. Deterministic, and never the caller's decision.

THE SEPARATION THIS FILE EXISTS TO ENFORCE
------------------------------------------
`caller.py` drives the conversation and has no opinion about it. Everything here
is a pure function of the transcript, the fake diary and the recorded tool
calls. Nothing consults a model. An LLM that both drives a test and marks it is
not a test, and the failure mode is silent: the suite goes green because the
caller was in a generous mood.

So a finding here is reproducible from a saved transcript alone -- which is also
what lets a failure be pasted into a bug report and re-checked later.

WHAT IS AND IS NOT ASSERTED
---------------------------
The universal checks are invariants: things that must hold on EVERY call
whatever the caller wanted. They are drawn from defects that were live, and
several are the same predicates `scripts/detect_defects.py` runs over the obs
corpus -- the same rule applied to a generated call instead of a recorded one.

The per-persona expectations are narrower and are deliberately few. A suite that
asserts a specific sentence is a suite that fails on a reword, and this engine
gets reworded constantly.
"""
from __future__ import annotations

import dataclasses
import re
from datetime import datetime
from typing import Any, Dict, List, Sequence, Tuple

#: Susie apologising for herself. Mirrors app/obs/regress.py so a phrase that is
#: a defect in the corpus is a defect here too.
_TECHNICAL_ERROR = (
    "technical issue", "having a small technical", "technical difficulty",
    "i apologise for the technical",
)

#: The model's opener welded to the payload, spoken without a breath.
_RUN_ON = re.compile(r"[a-z][.!?][A-Z]")

#: A subordinate clause left standing as a sentence. Full stop only -- allowing
#: "?" matched 103 healthy questions in the corpus.
_DANGLING = re.compile(
    r"(?:^|(?<=[.!]) )(?:While|Whilst|Until|As I|So I)\b[^.!?]{0,70}\."
)

_ESCALATION = re.compile(
    r"\b(?:999|a and e|a&e|emergency department|urgent|nhs 111|111|"
    r"straight to|as soon as possible today|same day)\b",
    re.IGNORECASE,
)


@dataclasses.dataclass
class Finding:
    """One thing wrong with one call."""

    rule: str
    detail: str
    severity: str = "defect"   # "defect" | "note"

    def __str__(self) -> str:
        return f"[{self.severity}] {self.rule}: {self.detail}"


def _bot_turns(transcript: Sequence[Tuple[str, str]]) -> List[str]:
    return [heard for _said, heard in transcript if heard]


# ── Universal invariants ────────────────────────────────────────────────────

def check_no_technical_error(transcript, **_) -> List[Finding]:
    joined = " ".join(_bot_turns(transcript)).lower()
    hit = next((p for p in _TECHNICAL_ERROR if p in joined), None)
    return [Finding("no_technical_error", f"said {hit!r}")] if hit else []


def check_no_banned_sentence(transcript, **_) -> List[Finding]:
    """Nothing Gate 5b deletes from the model may reach the caller.

    Imported rather than copied: the ban list is edited often, and a stale copy
    here would quietly stop enforcing the newest entry.
    """
    from app.media_streams.turn_handler import _BANNED_SENTENCE_RE

    out: List[Finding] = []
    for turn in _bot_turns(transcript):
        for name, rx in _BANNED_SENTENCE_RE:
            if rx.search(turn):
                out.append(Finding(
                    "no_banned_sentence",
                    f"Gate 5b/{name} should have deleted: {turn[:80]!r}",
                ))
                break
    return out


def check_no_run_on(transcript, **_) -> List[Finding]:
    for turn in _bot_turns(transcript):
        m = _RUN_ON.search(turn)
        if m:
            return [Finding(
                "no_run_on",
                f"welded sentence: ...{turn[max(0, m.start() - 25):m.start() + 25]}...",
            )]
    return []


def check_no_dangling_clause(transcript, **_) -> List[Finding]:
    for turn in _bot_turns(transcript):
        m = _DANGLING.search(turn)
        if m:
            return [Finding("no_dangling_clause", f"spoke a fragment: {m.group(0)!r}")]
    return []


def check_susie_always_says_something(transcript, **_) -> List[Finding]:
    """A caller turn that produced no speech at all is dead air on a real call."""
    out = []
    for i, (said, heard) in enumerate(transcript):
        if said and not (heard or "").strip():
            out.append(Finding(
                "no_silent_turn", f"turn {i + 1}: caller said {said[:50]!r}, Susie said nothing"
            ))
    return out


def check_no_repeated_question(transcript, **_) -> List[Finding]:
    """The same question three times is the loop the corpus is full of.

    Compared on the question's WORDS rather than the whole turn, because the
    surrounding sentence changes while the question does not.
    """
    seen: Dict[str, int] = {}
    for turn in _bot_turns(transcript):
        for sentence in re.split(r"(?<=[.!?])\s+", turn):
            if "?" not in sentence:
                continue
            key = re.sub(r"[^a-z ]", "", sentence.lower())
            key = " ".join(sorted(key.split()))[:80]
            if not key:
                continue
            seen[key] = seen.get(key, 0) + 1
    worst = [(k, n) for k, n in seen.items() if n >= 3]
    return [Finding("no_repeated_question", f"asked {n}x: {k[:60]!r}") for k, n in worst]


def _booking_start(booking):
    """A Booking's `start` is an ISO STRING, not a datetime.

    Worth stating because the first version of this file read `start.hour`
    straight off it. That silently skipped every real booking -- and the test
    passed, because the test built its own fake Booking with a datetime. A stub
    that does not match the real shape tests the stub.
    """
    raw = getattr(booking, "start", None)
    if isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "").split("+")[0])
    except ValueError:
        return None


_NUMBER_WORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
                7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
                12: "twelve"}


def _spoken_forms(start: datetime):
    """Every way the engine might have SAID this time, lower-cased.

    Susie speaks times as words ("half past nine"), so comparing against
    "09:30" alone would report every correct booking as unspoken.
    """
    hour24, hour12, minute = start.hour, start.hour % 12 or 12, start.minute
    forms = {f"{hour24}:{minute:02d}", f"{hour12}:{minute:02d}"}
    if minute == 0:
        forms.add(_NUMBER_WORD[hour12])
    elif minute == 15:
        forms.add(f"quarter past {_NUMBER_WORD[hour12]}")
    elif minute == 30:
        forms.add(f"half past {_NUMBER_WORD[hour12]}")
    elif minute == 45:
        nxt = (hour12 % 12) + 1
        forms.add(f"quarter to {_NUMBER_WORD[nxt]}")
    return {f for f in forms if f}


def check_booking_was_offered(transcript, diary=None, **_) -> List[Finding]:
    """Every booking's time must have been SPOKEN before it was written.

    The worst failure class in this system: the call sounds perfect and the
    diary says something else. Checked against what Susie actually said rather
    than against the offer record, because the offer record is the thing that
    has been wrong.
    """
    if diary is None or not getattr(diary, "bookings", None):
        return []
    spoken = " ".join(_bot_turns(transcript)).lower()
    out = []
    for booking in diary.bookings:
        start = _booking_start(booking)
        if start is None:
            out.append(Finding(
                "booking_was_offered",
                f"a booking was written with an unreadable start: "
                f"{getattr(booking, 'start', None)!r}",
            ))
            continue
        if not any(form in spoken for form in _spoken_forms(start)):
            out.append(Finding(
                "booking_was_offered",
                f"wrote {start:%a %d %b %H:%M} but no form of that time was spoken",
            ))
    return out


def check_booking_has_a_name(transcript, diary=None, **_) -> List[Finding]:
    if diary is None:
        return []
    out = []
    for booking in getattr(diary, "bookings", []) or []:
        name = (getattr(booking, "name", "") or "").strip()
        if not name:
            out.append(Finding("booking_has_a_name", "a booking was written with no name"))
        elif name.upper().startswith(("PENDING", "CONFIRM")):
            out.append(Finding(
                "booking_has_a_name",
                f"status marker written as the patient name: {name!r}",
            ))
    return out


UNIVERSAL = [
    check_no_technical_error,
    check_no_banned_sentence,
    check_no_run_on,
    check_no_dangling_clause,
    check_susie_always_says_something,
    check_no_repeated_question,
    check_booking_was_offered,
    check_booking_has_a_name,
]


# ── Per-persona expectations ────────────────────────────────────────────────

def _expect_red_flag(transcript, diary=None, **_) -> List[Finding]:
    """A disclosed cauda equina pattern must escalate, and must NOT be booked."""
    out = []
    spoken = " ".join(_bot_turns(transcript))
    if not _ESCALATION.search(spoken):
        out.append(Finding(
            "red_flag_escalated",
            "caller disclosed saddle numbness AND bladder trouble; nothing in "
            "the call urged urgent care",
        ))
    if diary is not None and getattr(diary, "bookings", None):
        out.append(Finding(
            "red_flag_not_booked",
            "a routine appointment was written for a disclosed red flag",
        ))
    return out


def _expect_no_booking(transcript, diary=None, **_) -> List[Finding]:
    if diary is not None and getattr(diary, "bookings", None):
        return [Finding(
            "no_booking_expected",
            f"{len(diary.bookings)} booking(s) written on a call that asked for none",
        )]
    return []


def _expect_surname_correct(transcript, diary=None, **_) -> List[Finding]:
    """The caller corrected their surname; the diary must carry the corrected one.

    Seven separate fixes have landed on names being written wrong, and the
    surname is the half that is never read back -- so it is the half a live
    call cannot check.
    """
    if diary is None:
        return []
    out = []
    for booking in getattr(diary, "bookings", []) or []:
        name = (getattr(booking, "name", "") or "").lower()
        if name and "rook" not in name:
            out.append(Finding(
                "surname_as_corrected",
                f"caller spelled out R-O-O-K; diary says {getattr(booking, 'name', '')!r}",
            ))
    return out


EXPECTATIONS = {
    "red_flag_cauda_equina": [_expect_red_flag],
    "faq_several": [_expect_no_booking],
    "wants_a_human": [_expect_no_booking],
    "misheard_name": [_expect_surname_correct],
}


def judge(persona_id: str, transcript, diary=None, tool_calls=None) -> List[Finding]:
    """Every finding for one call. PURE, and never asks a model."""
    findings: List[Finding] = []
    for check in UNIVERSAL + EXPECTATIONS.get(persona_id, []):
        findings.extend(
            check(transcript, diary=diary, tool_calls=tool_calls) or []
        )
    return findings
