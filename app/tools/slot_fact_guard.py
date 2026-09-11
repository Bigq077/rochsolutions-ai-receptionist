"""The last thing between a slot fact and the caller's ear.

INVARIANT 1 of `docs/plan/SLOT_PRESENTATION_SPEC.md`, and the only one in that
document that can be enforced without touching a selection rule:

    No day, date or clock time is SPOKEN that the payload does not hold.

WHY THIS EXISTS
---------------
`SLOT_PRESENTATION_ANALYSIS_2026-09-11.md` §2.4 and §3.2: slot facts have TWO
authors. The deterministic producers build the sentence and its record
together, so on that path the invariant holds by construction. On every turn no
producer claims -- 74 of 104 follow-up turns since 3 Sep -- the MODEL writes
the sentence, from whatever times its context happens to hold, and the
invariant becomes a hope.

`CA7ebc00839bf773bcf7cbaa52d7c60f7e` (11 Sep 2026 00:04, northgate, build
66f7dec3) is what that costs. The caller said "10 to 12 works". Susie replied,
three times:

    "So that's Monday the 14th of September at twenty to twelve"

Twenty to twelve is 11:40. northgate's diary is a 50-minute grid from 08:00 --
08:00, 08:50, 09:40, 10:30, 11:20, 12:10 -- so 11:40 has never been bookable
there, in any diary, on any day. Nothing in the system objected. Gate 5, the
reverse-parse layer and the read-back guards all sit on the producer path or
reason from a record the model never wrote; a sentence with no offer behind it
passes them all untouched.

WHAT IT CHECKS, AND WHAT IT DELIBERATELY DOES NOT
-------------------------------------------------
It answers one question: does this outgoing sentence name a clock time that no
payload seen on this call holds? That is a question about DATA, and it can be
answered from data alone.

It does NOT judge whether the right slot was chosen. The first defect on that
same call -- "the nearest to twelve o'clock is twenty to three on Monday, or
twenty past four on Tuesday" -- named two times that were both genuinely
bookable and both irrelevant to what the caller asked for. That is invariant 4,
a relevance failure, and it belongs to the decision table, not here. Saying so
plainly matters: a guard that is believed to cover selection is worse than no
guard, because it retires the vigilance that was doing the work.

TWO SEVERITIES, AND ONLY ONE IS ENFORCED
----------------------------------------
`unknown_time`  the clock time is in no day of the call's known-bookable set.
                High confidence: a time is either on the grid or it is not, and
                the answer does not depend on reading the sentence correctly.
                ENFORCED.

`wrong_day`     the time is bookable somewhere, but not on the day this clause
                names. Real, and the shape behind B-126 and P6 -- but it rests
                on attributing a day to a clause from text, which is exactly
                the reverse-parse this project has already measured as
                unreliable. RECORDED, NEVER ENFORCED.

FAIL OPEN, ALWAYS
-----------------
This runs in `_tts_loop`, on the hot path, between the words and the caller. A
guard that can raise is a guard that can cost a caller their booking, so every
entry point swallows everything and the failure mode is "speak it anyway". The
worst thing this module may ever do is nothing.

MODES -- `SLOT_FACT_GUARD`, default `log`
-----------------------------------------
`off`      no work, no rows.
`log`      detect and record; speech is untouched. THE CODE DEFAULT, and it
           ships this way on purpose. The same commit reaches three live
           patient lines by fast-forward, and a brand-new interceptor that can
           replace a sentence must not arrive on them ahead of a real call.
           It is also the measurement that Stage C could not take:
           `record_model_readout` only fires inside `_flush_slot_buf`, i.e. on
           turns where `check_availability` ran, so the no-tool turns -- both
           of last night's -- leave no row and it has recorded zero. This runs
           on every outgoing chunk and has no such blind spot.
`enforce`  the offending chunk is replaced with a recovery line and the rest of
           the turn's speech is dropped. Flip it per Render SERVICE after a
           confirming call, demo line first.

WHY REPLACE RATHER THAN REPAIR
------------------------------
Substituting the nearest real time would be inventing a different claim on the
caller's behalf, and this codebase has a register of what happens when a layer
corrects a slot fact it does not own (B-102: a guard reading a projection
overwrote a correct read-back, and the caller was told nine in the morning
while Acuity held 18:00). A receptionist who has lost track says so and checks.
So does this.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.tools.slot_followup import (
    _fold_clock_words,
    _mask_dates,
    flatten_bookable_slots,
    offer_clauses,
    requested_clock_times,
)

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_LOG = "log"
MODE_ENFORCE = "enforce"

#: Session keys. Underscore-prefixed, engine-internal, never read by the flow.
#:
#: `_KNOWN` is the call's cumulative known-bookable set, day -> {"HH:MM"}, and
#: cumulative is the point. `session["available_days"]` is one fetch and may be
#: a BAND-FILTERED view of a day (`_days_showing_a_filtered_view`): a caller
#: who asked for mornings and is later read an afternoon slot they had already
#: been offered would fail a check against the filtered payload alone. Absence
#: from one fetch is not absence from the diary -- the same sentence B-102 is
#: about -- so the set only ever grows, within one call.
_KNOWN = "_slot_guard_known"
#: Clock times the CALLER named, in candidate form. Legitimately speakable even
#: when unbookable, because Step 5 REQUIRES naming them: "I haven't got
#: anything from half five to nine -- I do have half four." Naming the window
#: the caller asked for is honest; it is the alternative that must be real.
_ASKED = "_slot_guard_asked"
#: Set when a violation has been enforced this turn, cleared at the turn
#: boundary. One poisoned sentence should not be followed by the rest of its
#: own paragraph.
_BLOCKED = "_slot_guard_blocked"
#: Violation rows, for the call record and the digest.
_ROWS = "_slot_guard_rows"
_MAX_ROWS = 12

#: What Susie says instead. No time, no day, no promise, and it hands the turn
#: back to a producer: the next caller utterance re-enters selection with the
#: offer still on the table.
RECOVERY_SENTENCE = "Sorry — let me just double-check that one for you."

#: The mode is announced ONCE per process, the first time it is resolved. Two
#: demo calls on 2026-09-11 produced zero `[slot_guard]` lines -- which is the
#: expected result of a clean call in EVERY mode, so the log could not say
#: whether the `enforce` flip had taken. Same reasoning as `[build_info]`: the
#: process knows; it should say so rather than have it inferred.
_announced_mode: Optional[str] = None


def _resolve_mode() -> str:
    try:
        raw = str(os.getenv("SLOT_FACT_GUARD") or "").strip().lower()
    except Exception:                      # pragma: no cover - defensive
        return MODE_LOG
    if raw in (MODE_OFF, MODE_LOG, MODE_ENFORCE):
        return raw
    if raw in ("0", "false", "no"):
        return MODE_OFF
    if raw in ("1", "true", "yes"):
        return MODE_ENFORCE
    return MODE_LOG


def guard_mode() -> str:
    """`off` / `log` / `enforce`, from the environment. Default `log`.

    Anything unrecognised reads as `log` rather than `enforce`: a typo in a
    Render env var must not be able to start replacing live speech.
    """
    global _announced_mode
    mode = _resolve_mode()
    if mode != _announced_mode:
        _announced_mode = mode
        try:
            logger.info("[slot_guard] mode=%s (SLOT_FACT_GUARD=%r)",
                        mode, os.getenv("SLOT_FACT_GUARD"))
        except Exception:                  # pragma: no cover - defensive
            pass
    return mode


# ───────────────────────────────────────────────────────────────────────────
# The allowed set
# ───────────────────────────────────────────────────────────────────────────

def note_payload(session: Any) -> None:
    """Fold the payload in hand into the call's known-bookable set. NEVER RAISES.

    Called by the guard itself on every outgoing chunk rather than wired into
    the availability executors, and that is deliberate: there are FIVE
    `check_availability` refusal branches and four availability readers
    (analysis §2.5), a payload-answered turn runs no tool at all, and anything
    written only inside a tool is dead on those turns -- which is S-13,
    `_slot_presentation_mode` and Stage C, three times the same defect. A
    payload is always in the session BEFORE the speech it produced reaches TTS,
    so reading it here catches every path by construction, including the ones
    nobody has written yet.
    """
    try:
        if not isinstance(session, dict):
            return
        known = session.get(_KNOWN)
        if not isinstance(known, dict):
            known = {}
            session[_KNOWN] = known
        for slot in flatten_bookable_slots(session.get("available_days")):
            day = str(slot.get("date") or "")[:10]
            hhmm = str(slot.get("time") or "")[:5]
            if not hhmm and len(str(slot.get("start") or "")) >= 16:
                hhmm = str(slot["start"])[11:16]
            if not re.fullmatch(r"\d{2}:\d{2}", hhmm):
                continue
            known.setdefault(day or "?", [])
            if hhmm not in known[day or "?"]:
                known[day or "?"].append(hhmm)
    except Exception:                      # pragma: no cover - defensive
        logger.warning("[slot_guard] payload not folded", exc_info=True)


def note_caller_speech(session: Any, text: Any) -> None:
    """Record the clock times the CALLER named. NEVER RAISES.

    `requested_clock_times` and not a fresh parser, because that function is
    already the hardened one: dates masked before a digit is read as an hour
    (B-126), durations and ages excluded, the 12-hour twin emitted so an
    impossible reading simply never matches. Invariant 18 is its contract and
    it is tested there; a second implementation here would be a second answer.
    """
    try:
        if not isinstance(session, dict):
            return
        asked = session.get(_ASKED)
        if not isinstance(asked, list):
            asked = []
            session[_ASKED] = asked
        for hhmm in requested_clock_times(text):
            if hhmm not in asked:
                asked.append(hhmm)
    except Exception:                      # pragma: no cover - defensive
        logger.warning("[slot_guard] caller times not folded", exc_info=True)


def allowed_times(session: Any) -> Set[str]:
    """Every HH:MM this call may legitimately speak, across all days."""
    out: Set[str] = set()
    try:
        known = (session or {}).get(_KNOWN)
        if isinstance(known, dict):
            for times in known.values():
                for t in times or []:
                    out.add(str(t)[:5])
        asked = (session or {}).get(_ASKED)
        if isinstance(asked, list):
            for t in asked:
                out.add(str(t)[:5])
    except Exception:                      # pragma: no cover - defensive
        return out
    return out


def allowed_on_day(session: Any, day_iso: str) -> Set[str]:
    """The HH:MM bookable on one ISO date, for the `wrong_day` severity."""
    try:
        known = (session or {}).get(_KNOWN) or {}
        return {str(t)[:5] for t in (known.get(str(day_iso)[:10]) or [])}
    except Exception:                      # pragma: no cover - defensive
        return set()


# ───────────────────────────────────────────────────────────────────────────
# Reading a clock time out of OUTGOING speech
#
# The extractor is built from the GENERATOR's grammar, not from English.
# `receptionist_tools._spoken_slot_time` is a closed function -- midday,
# midnight, "{hour}", "quarter|half past {hour}", "{five|ten|twenty|
# twenty-five} past {hour}", "{...} to {hour}", "quarter to {hour}", and a
# digit fallback "{hour} MM" for an off-grid minute -- so the set of shapes a
# slot fact can arrive in is finite and known. Matching that instead of "any
# number that could be a time" is what keeps the false-positive rate near zero
# on a channel where a false positive is a replaced sentence.
#
# The rule this project has recorded four times is `the matcher shape is the
# bug, not the phrase`. So nothing here matches a hand-written literal of
# generated speech: every arm matches a STRUCTURE the generator can emit.
# ───────────────────────────────────────────────────────────────────────────

#: EVERYTHING BELOW READS THE FOLDED TEXT, in which every clock word has
#: already become a digit. `_fold_clock_words` turns "twenty to twelve" into
#: "20 to 12" and "ten in the morning" into "10 in the morning", so matching the
#: word forms here would match nothing at all -- which is exactly the bug the
#: first version of this module shipped with and the regression test caught.
#: `half` and `quarter` are the two the fold deliberately leaves alone, so they
#: are the two that stay spelled out.
_MINUTE_UNITS = {"half": 30, "quarter": 15}
_MIN_ALT = r"half|quarter|\d{1,2}"
_HOUR_ALT = r"\d{1,2}"

_PAST_RE = re.compile(
    r"\b(?P<unit>" + _MIN_ALT + r")\s+past\s+(?P<hour>" + _HOUR_ALT + r")\b", re.I
)
_TO_RE = re.compile(
    r"(?P<pre>[a-z]+\s+)?\b(?P<unit>" + _MIN_ALT + r")\s+to\s+(?P<hour>" + _HOUR_ALT + r")\b",
    re.I,
)
_OCLOCK_RE = re.compile(r"\b(?P<hour>" + _HOUR_ALT + r")\s*o'?\s?clock\b", re.I)
_DIGITS_RE = re.compile(r"\b(?P<h>\d{1,2})\s*[:.]\s*(?P<m>[0-5]\d)\b")
_MIDDAY_RE = re.compile(r"\b(?P<w>midday|noon|midnight)\b", re.I)
#: A bare hour counts as a time ONLY with its band, and so does the generator's
#: off-grid fallback ("nine 07 in the morning"). After the fold a bare hour IS
#: just a digit, and a digit with no band is "Number 1", a date, an age, a house
#: number and "give me 5" -- every one of which a bare-hour arm would read as a
#: slot. The band suffix is the generator's own marker and `speaks_part_of_day`
#: defaults TRUE on every clinic, so this arm sees the overwhelming majority of
#: real labels; a clinic that opts out loses this arm only, not the module.
_BARE_BAND_RE = re.compile(
    r"\b(?P<hour>\d{1,2})(?:\s+(?P<m>[0-5]\d))?\s+in\s+the\s+"
    r"(?P<band>morning|afternoon|evening)\b", re.I
)

#: A clause about opening hours, a duration or a window is not an offer, and
#: its times are not claims about the diary. "We're open from nine until half
#: past five" names two times the diary need not hold on the day in question;
#: so does "the appointment runs about fifty minutes". Skipped whole, which
#: costs at most a missed violation -- the trade this module takes everywhere.
_NOT_AN_OFFER = (
    " open", "opens", "opening", " close", "closes", "closing", " hours",
    " until ", " till ", " between ", "minute", "runs ", "lasts", "takes about",
    "we're here", "we are here",
)

#: Immediately before a "{unit} to {hour}", these words mean it is not one.
#: "from half five to nine" is a WINDOW whose tail, "five to nine", is a
#: perfectly good 08:55 to a regex and nothing at all to a listener.
_NOT_A_TO_TIME = {"half", "quarter", "from", "between", "past"}

_BANDS = {
    "morning": lambda h: h < 12,
    "afternoon": lambda h: 12 <= h < 17,
    "evening": lambda h: h >= 17,
}


def _minute_value(raw: Any) -> Optional[int]:
    """A minute count from the folded text: a digit, or `half`/`quarter`."""
    s = str(raw or "").strip().lower()
    if s in _MINUTE_UNITS:
        return _MINUTE_UNITS[s]
    try:
        v = int(s)
    except (TypeError, ValueError):
        return None
    return v if 1 <= v <= 59 else None


def _twins(h: int, m: int) -> Set[str]:
    """A 12-hour reading and its afternoon twin, as HH:MM.

    Both, always, unless a band word settles it. A guard must not invent
    certainty it does not have: a mention is a violation only when EVERY
    reading of it is absent from the diary, so an ambiguous mention is
    self-acquitting and that is the correct bias for this channel.
    """
    out: Set[str] = set()
    for hh in {h % 12, (h % 12) + 12, h}:
        if 0 <= hh <= 23 and 0 <= m <= 59:
            out.add("%02d:%02d" % (hh, m))
    return out


def _band_after(text: str, end: int) -> Tuple[Optional[str], int]:
    """The band word that follows a mention, and where it ENDS.

    Bounded to the next few words: "ten in the morning" binds, "ten, and I've
    a slot in the afternoon on Friday" does not, and an unbounded search would
    let the second sentence's band resolve the first sentence's hour.

    The end offset is returned, not just the word, because the band belongs to
    the LABEL'S SPAN. "twenty to three in the afternoon" folds to "20 to 3 in
    the afternoon": the `_TO_RE` match is only the "20 to 3" part, so without
    this the fragment "3 in the afternoon" is the textually LONGER match and
    wins the overlap -- convicting the sentence on 15:00, a time the generator
    never said. See `_dedupe`; this is the same defect from the other side.
    """
    tail = text[end:end + 28].lower()
    m = re.match(r"\W*in\s+the\s+(morning|afternoon|evening)\b", tail)
    return (m.group(1), end + m.end()) if m else (None, end)


def spoken_time_mentions(text: Any) -> List[Tuple[str, Set[str]]]:
    """Every clock time `text` names, each as (phrase, candidate HH:MM set). PURE.

    Candidates rather than an answer, for the same reason
    `requested_clock_times` returns candidates: "twenty past four" is 04:20 or
    16:20 and only the diary knows which. The caller of this function treats a
    mention as a violation only when the diary holds NONE of its readings.
    """
    found: List[Tuple[int, int, str, Set[str]]] = []
    if not isinstance(text, str) or not text.strip():
        return []
    # Dates first, always. "Monday the 14th of September" holds a 14 and a
    # 9, and this repo has twice turned a date into a time by reading one of
    # them (B-126, D8). Masking with spaces keeps every other offset intact,
    # which is what lets the spans below be compared at all.
    raw = _mask_dates(text.lower())
    folded = _fold_clock_words(raw)

    def _add(m: "re.Match[str]", cands: Set[str], group: Optional[str] = None) -> None:
        """Record one mention. `group` narrows the span past a lookaround-ish
        prefix: `_TO_RE` captures the word BEFORE the time so it can veto a
        window ("from half five to nine"), and that word is no part of the
        label, so it must not be in the span or the phrase either.

        The phrase is the FOLDED text -- "at 20 to 12", not "at twenty to
        twelve". Folding changes lengths, so there is no honest mapping back to
        the original offsets, and inventing one would be a worse lie than the
        one the log line already avoids: the full sentence is logged verbatim
        alongside, so an operator always sees what was actually said.
        """
        start = m.start(group) if group else m.start()
        band, end = _band_after(folded, m.end())
        if band and band in _BANDS:
            test = _BANDS[band]
            cands = {c for c in cands if test(int(c[:2]))} or cands
        if cands:
            found.append((start, end, folded[start:end].strip(), cands))

    for m in _MIDDAY_RE.finditer(folded):
        w = m.group("w").lower()
        _add(m, {"00:00"} if w == "midnight" else {"12:00"})

    for m in _DIGITS_RE.finditer(folded):
        h, mm = int(m.group("h")), int(m.group("m"))
        if 0 <= h <= 23:
            _add(m, {"%02d:%02d" % (h, mm)})

    for m in _PAST_RE.finditer(folded):
        unit = _minute_value(m.group("unit"))
        hour = _hour_value(m.group("hour"))
        if unit is None or hour is None:
            continue
        _add(m, _twins(hour, unit))

    for m in _TO_RE.finditer(folded):
        pre = (m.group("pre") or "").strip().lower()
        if pre in _NOT_A_TO_TIME:
            continue
        unit = _minute_value(m.group("unit"))
        hour = _hour_value(m.group("hour"))
        if unit is None or hour is None:
            continue
        prev = (hour - 1) if hour > 1 else 12
        _add(m, _twins(prev, 60 - unit), group="unit")

    for m in _OCLOCK_RE.finditer(folded):
        hour = _hour_value(m.group("hour"))
        if hour is not None:
            _add(m, _twins(hour, 0))

    for m in _BARE_BAND_RE.finditer(folded):
        hour = _hour_value(m.group("hour"))
        if hour is None:
            continue
        mm = int(m.group("m")) if m.group("m") else 0
        test = _BANDS.get(m.group("band").lower())
        cands = _twins(hour, mm)
        if test:
            cands = {c for c in cands if test(int(c[:2]))} or cands
        _add(m, cands)

    return _dedupe(found)


def _hour_value(raw: Any) -> Optional[int]:
    """An hour from the folded text, which is always a digit by this point."""
    try:
        h = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return None
    return h if 0 <= h <= 23 else None


def _dedupe(
    found: List[Tuple[int, int, str, Set[str]]]
) -> List[Tuple[str, Set[str]]]:
    """The longest match wins every overlap. By SPAN, not by substring.

    THE ARMS OVERLAP BY DESIGN, and the overlap is not always a containment of
    one string in another -- which is the bug the first version of this shipped
    with and the regression test caught within the hour.

        "twenty to three in the afternoon"   folds to   "20 to 3 in the afternoon"

    `_TO_RE` reads that as 14:40, which is what the generator meant by it and a
    real slot. `_BARE_BAND_RE` reads the TAIL of the same label, "3 in the
    afternoon", as 15:00 -- and "20 to 3" does not contain "3 in the afternoon",
    so a substring test keeps both and the fragment convicts the sentence. On
    northgate's grid that is a false positive on nine of eleven bookable times,
    i.e. the guard would have replaced almost every correct readout.

    Spans cannot miss it: the fragment sits INSIDE the label's span, so the
    longer span takes it. The generator emits whole labels, so the longest
    match is the label, always.
    """
    kept: List[Tuple[int, int, str, Set[str]]] = []
    for start, end, phrase, cands in sorted(found, key=lambda x: (x[0] - x[1], x[0])):
        if any(start < k_end and k_start < end for k_start, k_end, _, _ in kept):
            continue
        kept.append((start, end, phrase, cands))
    kept.sort(key=lambda x: x[0])
    return [(phrase, cands) for _, _, phrase, cands in kept]


# ───────────────────────────────────────────────────────────────────────────
# The check
# ───────────────────────────────────────────────────────────────────────────

class Verdict:
    """What the guard decided about one outgoing chunk."""

    __slots__ = ("text", "violations", "warnings", "blocked", "mode")

    def __init__(self, text, violations, warnings, blocked, mode):
        self.text = text
        self.violations = violations
        self.warnings = warnings
        self.blocked = blocked
        self.mode = mode

    @property
    def clean(self) -> bool:
        return not self.violations and not self.warnings

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Verdict(mode={!r}, {} violation(s), {} warning(s), blocked={})".format(
            self.mode, len(self.violations), len(self.warnings), self.blocked
        )


def turn_boundary(session: Any) -> None:
    """A new caller turn begins: the poisoned-turn flag lapses. NEVER RAISES."""
    try:
        if isinstance(session, dict):
            session[_BLOCKED] = False
    except Exception:                      # pragma: no cover - defensive
        pass


def check_outgoing(session: Any, text: Any) -> Verdict:
    """Judge one outgoing chunk. NEVER RAISES; on any doubt, speaks it.

    The order matters and is the cheap-first order: mode, then whether this
    call has any diary knowledge at all, then whether the clause is even an
    offer, and only then the regex work. A call that never looked up
    availability pays one env read and one dict lookup.
    """
    mode = MODE_LOG
    try:
        mode = guard_mode()
        if mode == MODE_OFF or not isinstance(session, dict):
            return Verdict(text, [], [], False, mode)

        if session.get(_BLOCKED) and mode == MODE_ENFORCE:
            # The sentence this chunk belongs to was already replaced. Speaking
            # its tail would put the caller halfway through a retracted offer.
            return Verdict("", [], [], True, mode)

        note_payload(session)
        allowed = allowed_times(session)
        if not allowed:
            # Nothing has been looked up yet, so there is no diary to be wrong
            # about. Silence here is correct, not a gap: the guard's whole
            # authority comes from the payload.
            return Verdict(text, [], [], False, mode)

        violations: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []
        # The caller's own times are speakable on ANY day, not just allowed:
        # "the nearest I've got to midday is ten past twelve on Monday" names
        # 12:00 in a Monday clause, and 12:00 is bookable nowhere. That is
        # DT-8's sentence, not a wrong-day attribution.
        _asked_any = (session or {}).get(_ASKED)
        asked_set: Set[str] = (
            {str(t)[:5] for t in _asked_any} if isinstance(_asked_any, list) else set()
        )

        for clause in offer_clauses(text):
            padded = " " + clause.lower() + " "
            if any(tok in padded for tok in _NOT_AN_OFFER):
                continue
            day_iso = _day_named(session, clause)
            for phrase, cands in spoken_time_mentions(clause):
                if cands & allowed:
                    if day_iso and not (cands & asked_set):
                        on_day = allowed_on_day(session, day_iso)
                        if on_day and not (cands & on_day):
                            warnings.append({
                                "kind": "wrong_day",
                                "phrase": phrase,
                                "candidates": sorted(cands),
                                "day": day_iso,
                                "clause": clause[:160],
                            })
                    continue
                violations.append({
                    "kind": "unknown_time",
                    "phrase": phrase,
                    "candidates": sorted(cands),
                    "day": day_iso or "",
                    "clause": clause[:160],
                })

        if not violations and not warnings:
            return Verdict(text, [], [], False, mode)

        _record(session, text, violations, warnings, mode)

        if violations and mode == MODE_ENFORCE:
            session[_BLOCKED] = True
            return Verdict(RECOVERY_SENTENCE, violations, warnings, True, mode)
        return Verdict(text, violations, warnings, False, mode)
    except Exception:                      # pragma: no cover - defensive
        logger.warning("[slot_guard] check failed; speaking unchanged", exc_info=True)
        return Verdict(text, [], [], False, mode)


def _day_named(session: Any, clause: str) -> str:
    """The ISO date this clause names, via the payload's own day labels.

    Both halves of the comparison come from the payload -- its `day_label`
    against the clause -- so this is not a match against a phrase written by
    hand, which is the rule `payload_slots_named_in` records and the reason it
    is safe to reuse the idea here.
    """
    try:
        from app.tools.slot_followup import _readback_norm
        norm = _readback_norm(clause)
        if not norm:
            return ""
        best_label, best_day = "", ""
        for slot in flatten_bookable_slots(session.get("available_days")):
            label = _readback_norm(slot.get("day_label"))
            if label and label in norm and len(label) > len(best_label):
                best_label = label
                best_day = str(slot.get("date") or "")[:10]
        return best_day
    except Exception:                      # pragma: no cover - defensive
        return ""


def _record(session: Any, text: Any, violations, warnings, mode: str) -> None:
    """One Render line per violation, and a capped row on the session.

    The LOG LINE is the primary instrument, not a convenience. This repo's only
    proof of what is running in production is a greppable line in the Render
    log (`[build_info] running build <sha>`), the same is true of everything
    the obs store has no column for, and a violation is exactly the event an
    operator needs to find without a database. `[slot_guard]` is the tag.
    """
    try:
        for v in violations:
            logger.error(
                "[slot_guard] SPOKEN SLOT FACT NOT IN THE DIARY (%s): %r reads as "
                "%s, and no payload on this call holds any of them%s. clause=%r "
                "text=%r",
                mode, v.get("phrase"), ",".join(v.get("candidates") or []),
                (" on " + v["day"]) if v.get("day") else "",
                v.get("clause"), str(text)[:200],
            )
        for w in warnings:
            logger.warning(
                "[slot_guard] slot fact on the WRONG DAY (%s, recorded only): "
                "%r reads as %s, bookable somewhere but not on %s. clause=%r",
                mode, w.get("phrase"), ",".join(w.get("candidates") or []),
                w.get("day"), w.get("clause"),
            )
        if not isinstance(session, dict):
            return
        rows = session.get(_ROWS)
        if not isinstance(rows, list):
            rows = []
            session[_ROWS] = rows
        if len(rows) >= _MAX_ROWS:
            return
        rows.append({
            "seq": len(rows),
            "mode": mode,
            "violations": violations,
            "warnings": warnings,
            "text": str(text)[:400],
        })
    except Exception:                      # pragma: no cover - defensive
        pass


def guard_block(session: Any) -> "list | None":
    """What a future `calls` column would hold, or None. Same convention as
    `slot_offers.offers_block`: None rather than [] so "never checked" stays
    distinguishable from "checked, and clean".
    """
    try:
        rows = (session or {}).get(_ROWS)
        return list(rows) if rows else None
    except Exception:                      # pragma: no cover - defensive
        return None
