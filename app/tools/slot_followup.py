# app/tools/slot_followup.py
"""
Deterministic unspoken-slot follow-up (V5).

After the first spoken offer, the model answers "anything later?" / a specific
unspoken time from what it already said — even when session["available_days"]
still holds the full day. Re-fetching check_availability cannot fix that: a
fresh fetch leads with the earliest times again, and the already_retrieved
guard tells the model to present "the existing slots".

These helpers compute remaining = available_days − last_offered and either:
  * offer the next two unspoken times, or
  * confirm a caller-named time that is still in remaining.

No LLM judgment about what exists.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _slot_start(slot: Dict[str, Any]) -> str:
    return str(slot.get("start") or "")


def _day_key(slot: Dict[str, Any]) -> str:
    """The calendar day a slot belongs to. `date` when present, else the ISO date.

    Never returns None, so slots with no usable date group together rather than
    each looking like its own day.
    """
    return str(slot.get("date") or "") or _slot_start(slot)[:10]


def flatten_bookable_slots(available_days: Any) -> List[Dict[str, Any]]:
    """Flatten available_days into ordered slot dicts with time + spoken labels."""
    if not isinstance(available_days, list):
        return []
    out: List[Dict[str, Any]] = []
    for day in available_days:
        if not isinstance(day, dict):
            continue
        times = day.get("slot_times") or []
        spoken = day.get("slot_times_spoken") or []
        slots = day.get("slots") or []
        n = max(len(times), len(slots))
        for i in range(n):
            raw = slots[i] if i < len(slots) and isinstance(slots[i], dict) else {}
            start = _slot_start(raw)
            time = times[i] if i < len(times) else (start[11:16] if len(start) >= 16 else "")
            label = spoken[i] if i < len(spoken) else time
            out.append({
                "start": start or (f"{day.get('date')}T{time}:00" if day.get("date") and time else ""),
                "end": str(raw.get("end") or ""),
                "time": time,
                "spoken": label,
                "date": day.get("date"),
                "day_label": day.get("day_label") or "",
            })
    return out


def remaining_slots_after_offer(
    available_days: Any,
    last_offered_slots: Any,
) -> List[Dict[str, Any]]:
    """Bookable slots in available_days whose start is not in last_offered."""
    offered_starts = set()
    if isinstance(last_offered_slots, list):
        for s in last_offered_slots:
            if isinstance(s, dict) and s.get("start"):
                offered_starts.add(str(s["start"])[:19])  # trim tz noise
    remaining = []
    for slot in flatten_bookable_slots(available_days):
        start = slot["start"][:19]
        if start and start not in offered_starts:
            remaining.append(slot)
    return remaining


# ───────────────────────────────────────────────────────────────────────────
# B-78b — what the caller has been offered ACROSS the whole day, not just now.
#
# `last_offered_slots` is the CURRENT offer: apply_next_batch_to_session
# REPLACES it. So subtracting it alone makes the previous batch unoffered
# again, and repeated "anything else?" walks a two-state loop:
#
#     ask 1 → half six, quarter past seven
#     ask 2 → five, quarter to six      ← already heard
#     ask 3 → half six, quarter past seven ...
#
# On CA7cd9bed5's Tuesday (5 slots) that loop never reaches 20:00 — the last
# slot of the day is unreachable no matter how many times the caller asks,
# while Susie keeps promising "a few others that day".
#
# So the cumulative record is kept separately. `last_offered_slots` keeps its
# meaning untouched — _resolve_slot_iso, the DTMF map and fast_path all read it
# as "the offer on the table".
# ───────────────────────────────────────────────────────────────────────────

_SPOKEN_KEY = "slot_starts_spoken"
_SPOKEN_FP_KEY = "slot_starts_spoken_fp"


def _availability_fingerprint(available_days: Any) -> str:
    """Identify the current availability set, so a NEW fetch resets the record.

    Self-healing on purpose: no call site has to remember to reset. A fresh
    check_availability produces a different fingerprint and the spoken record
    drops, so re-asking about a day the caller already explored offers it in
    full again rather than silently hiding times behind a stale record.
    """
    flat = flatten_bookable_slots(available_days)
    if not flat:
        return ""
    return f"{len(flat)}|{flat[0].get('start')}|{flat[-1].get('start')}"


def _spoken_key_set(session: Dict[str, Any]) -> set:
    fp = _availability_fingerprint(session.get("available_days") or [])
    if session.get(_SPOKEN_FP_KEY) != fp:
        session[_SPOKEN_KEY] = []
        session[_SPOKEN_FP_KEY] = fp
    return {str(s)[:19] for s in (session.get(_SPOKEN_KEY) or [])}


def record_spoken_slots(session: Dict[str, Any], slots: Any) -> None:
    """Add `slots` to the day's cumulative spoken record."""
    _spoken_key_set(session)  # resets first if availability changed
    current = list(session.get(_SPOKEN_KEY) or [])
    for s in (slots or []):
        start = str((s or {}).get("start") or "")[:19]
        if start and start not in current:
            current.append(start)
    session[_SPOKEN_KEY] = current


def remaining_unspoken(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Bookable slots the caller has not been offered at ANY point this day.

    Folds the current offer into the cumulative record as it goes, so the
    caller walks forward through the day instead of ping-ponging.
    """
    record_spoken_slots(session, session.get("last_offered_slots") or [])
    spoken = _spoken_key_set(session)
    return [
        slot
        for slot in flatten_bookable_slots(session.get("available_days") or [])
        if str(slot.get("start") or "")[:19] not in spoken
    ]


def remaining_unspoken_on_current_day(
    session: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """remaining_unspoken(), scoped to the day of the offer on the table.

    "Anything else THAT DAY?" means the day the caller is discussing, and the
    only record of which day that is, is the offer they were just given.
    `remaining_unspoken` flattens the whole sweep — a clinic on a fixed evening
    rota has four more days of it — so an unscoped batch takes remaining[0]'s
    day, which is whichever day sorts first, not the one under discussion.

    Found 24 Aug 2026 while testing B-79: a caller offered Wednesday times and
    asking "anything else that day?" was answered with TUESDAY, announced under
    Tuesday's own label. That is CA5c4fb14f's failure mode — a real patient
    sent to the clinic on the wrong day — reached through a different door.
    """
    remaining = remaining_unspoken(session)
    offered = session.get("last_offered_slots") or []
    day = str((offered[0] or {}).get("start") or "")[:10] if offered else ""
    if not day:
        return remaining
    return [slot for slot in remaining if _day_key(slot) == day]


def next_slot_batch(
    remaining: List[Dict[str, Any]], n: int = 2
) -> Tuple[List[Dict[str, Any]], bool]:
    """The next `n` unspoken slots, ALL ON ONE DAY.

    Single-day is a correctness requirement, not a preference. `remaining` is
    flattened across every day in available_days, so `remaining[:n]` could
    straddle a day boundary — and both consumers of this batch present it as one
    day, taking their label from batch[0]:

        format_next_batch_speech  -> "On {batch[0].day_label} I also have A, or B"
        build_followup_tool_result -> first_day.date/day_label from batch[0],
                                      first_day.slots from the WHOLE batch

    Each slot keeps its own true `start`, so the caller picking the second option
    books the day it really belongs to — while having been told the first slot's
    day. That is exactly how CA5c4fb14f (30 Jul 2026) told a caller "Tuesday the
    4th of August at seven in the evening" and booked 2026-08-05T19:00. Nothing
    downstream can catch it, because nothing downstream is wrong: the booking
    matches the slot, only the speech does not.

    So the batch is confined to remaining[0]'s day. `more` means "more times
    STILL ON THAT DAY", which is what the speech it feeds actually claims ("I've
    a few others that day"). Slots on later days are not lost — they are simply
    not announced under the wrong day's name.
    """
    if not remaining:
        return [], False
    day = _day_key(remaining[0])
    same_day = [s for s in remaining if _day_key(s) == day]
    batch = list(same_day[:n])
    more = len(same_day) > n
    return batch, more


def all_remaining_on_next_day(
    remaining: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], bool]:
    """EVERY unspoken slot on remaining[0]'s day.

    Owner rule, 24 Aug 2026: the first offer is capped (three times, so the
    caller is not read a wall of numbers), but a caller who has been told "I've
    a few others that day" and asks for them gets ALL of them — not another
    pair, and never a slot silently withheld. On CA6b90c3a2 the two-at-a-time
    batching meant three separate asks to walk one Tuesday.

    Delegates to next_slot_batch so the single-day confinement invariant has
    exactly one implementation. Slots on LATER days are still excluded —
    announcing them under this day's label is CA5c4fb14f.

    Ceiling of nine, and it is not a style choice: SLOT_OPTION_ANCHOR_RE and the
    DTMF map are single-digit, so a tenth option is spoken as "Number 10", which
    anchors as "Number 1" and points the keypad at the wrong time. A day that
    holds more than nine unspoken times therefore gets nine and `more=True`, so
    the caller is told the rest exist rather than being read a number they
    cannot press. `more` is False in every realistic case — the whole day is on
    the table.
    """
    batch, _ = next_slot_batch(remaining, n=len(remaining) or 1)
    if len(batch) > MAX_KEYPAD_OPTIONS:
        logger.info(
            "[slot_followup] %d unspoken times on that day — offering %d, "
            "the most the keypad can address",
            len(batch), MAX_KEYPAD_OPTIONS,
        )
        return batch[:MAX_KEYPAD_OPTIONS], True
    return batch, False


def utterance_requests_different_day(text: str) -> bool:
    t = (text or "").lower()
    return any(
        p in t
        for p in (
            "different day",
            "another day",
            "other day",
            "different date",
            "another date",
            "different week",
            "next week",
        )
    )


def utterance_requests_more_slots(text: str) -> bool:
    """True if caller wants more times on the *same* availability set."""
    t = (text or "").lower().strip()
    if not t or utterance_requests_different_day(t):
        return False
    signals = (
        "later",
        "else",
        "other",
        "another",
        "different",
        "instead",
        "any more",
        "anymore",
        "anything else",
        "any others",
        "any other",
        "more times",
        "more slots",
        "full list",
        "every slot",
        "all slots",
        "all the slots",
        "what else",
        "anything after",
    )
    return any(s in t for s in signals)


# Job 3c.1 / CAce1457d1: caller accepting an already-offered slot must not be
# steered to "present the existing slots" again (forced a second accept).
_SLOT_ACCEPT_PHRASES: frozenset = frozenset({
    "suits me", "any of them", "any of those", "that works",
    "fine with me", "any is fine", "any is good", "whatever",
    "any of those suit me", "they all work", "all good",
    "anytime", "any", "fine", "good", "okay", "ok",
    "that works for me", "works for me", "all fine", "all work",
    "either", "either works", "either of those", "both fine",
    "sounds good", "sounds fine", "any would work", "any works",
    "yes", "yeah", "yep", "yup", "sure", "perfect", "great",
    "go ahead", "go for it", "book that", "book it", "take that",
    "i'll take that", "ill take that", "the first one", "the second one",
    "number one", "number two", "option one", "option two",
})


def utterance_accepts_offered_slot(text: str) -> bool:
    """True when the caller is accepting / locking an already-offered slot.

    Excludes "more times" and "different day" requests — those still need the
    follow-up / re-fetch paths.
    """
    t = (text or "").lower().strip().strip(".,!?;:")
    if not t:
        return False
    if utterance_requests_more_slots(t) or utterance_requests_different_day(t):
        return False
    if t in _SLOT_ACCEPT_PHRASES:
        return True
    # Short affirmatives with filler ("yeah that works", "yes please")
    if len(t.split()) <= 5 and any(
        t == p or t.startswith(p + " ") or t.endswith(" " + p) or f" {p} " in f" {t} "
        for p in (
            "that works", "works for me", "sounds good", "go ahead",
            "book that", "book it", "perfect", "yes please", "yeah please",
        )
    ):
        return True
    return False


_BARE_HOUR_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def _candidate_hhmm_from_text(text: str) -> List[str]:
    """Pull possible HH:MM values the caller may have meant."""
    t = (text or "").lower()
    found: List[str] = []

    def _add_hour_variants(h: int, mm: int) -> None:
        if h > 23 or mm > 59:
            return
        found.append(f"{h:02d}:{mm:02d}")
        # Clinic evenings are 24h; callers say "730" meaning 19:30.
        if 1 <= h <= 12:
            found.append(f"{h + 12:02d}:{mm:02d}")

    for m in re.finditer(r"\b([01]?\d|2[0-3])[:\.]([0-5]\d)\b", t):
        _add_hour_variants(int(m.group(1)), int(m.group(2)))
    # bare "730" / "1930" without separator
    for m in re.finditer(r"\b([01]?\d|2[0-3])([0-5]\d)\b", t):
        _add_hour_variants(int(m.group(1)), int(m.group(2)))

    # half past / quarter past / quarter to
    for hour_word, h12 in _BARE_HOUR_WORDS.items():
        if f"half past {hour_word}" in t:
            _add_hour_variants(h12, 30)
        if f"quarter past {hour_word}" in t:
            _add_hour_variants(h12, 15)
        if f"quarter to {hour_word}" in t:
            prev = h12 - 1 if h12 > 1 else 12
            _add_hour_variants(prev, 45)

    # bare hour word "six" / "at six" — only useful if unique in remaining
    for hour_word, h12 in _BARE_HOUR_WORDS.items():
        if re.search(rf"\b{hour_word}\b", t):
            _add_hour_variants(h12, 0)

    # de-dupe preserving order
    seen = set()
    out = []
    for x in found:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def resolve_requested_time(
    text: str, remaining: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Match a caller time phrase to exactly one remaining slot, else None."""
    if not remaining or not (text or "").strip():
        return None
    t = text.lower()

    # Prefer full spoken-label containment (most precise)
    label_hits = [s for s in remaining if s.get("spoken") and s["spoken"].lower() in t]
    if len(label_hits) == 1:
        return label_hits[0]
    # partial: "half past seven" without "in the evening"
    soft_hits = []
    for s in remaining:
        spoken = (s.get("spoken") or "").lower()
        core = spoken.replace(" in the evening", "").replace(" in the afternoon", "").replace(" in the morning", "")
        if core and core in t:
            soft_hits.append(s)
    if len(soft_hits) == 1:
        return soft_hits[0]

    candidates = _candidate_hhmm_from_text(t)
    time_hits = [s for s in remaining if s.get("time") in candidates]
    if len(time_hits) == 1:
        return time_hits[0]
    return None


# ───────────────────────────────────────────────────────────────────────────
# The "there are more times that day" claim — one home for the literal.
#
# This sentence asserts WORLD STATE: that bookable times exist beyond the ones
# just read out. Only `more_times`, computed from the provider's own slot data,
# knows whether that is true. It was previously produced by the Haiku slot
# formatter copying a prompt example, and on 24 Aug 2026 (CA98557584dc) it told
# a caller "And I've a few others that day if neither suits" about a Tuesday
# that had exactly the two slots she had just been offered.
#
# So the tail is now appended by CODE from `more_times`, and stripped when the
# model emits it anyway. See reconcile_extra_slots_claim below.
# ───────────────────────────────────────────────────────────────────────────

MORE_TIMES_TAIL_ONE = "And I've a few others that day if that doesn't suit."
MORE_TIMES_TAIL_MANY = "And I've a few others that day if neither suits."
MORE_TIMES_TAIL_SEVERAL = "And I've a few others that day if none of those suit."


def more_times_tail(n_offered: int) -> str:
    """The canonical tail for `n_offered` times just read out.

    "neither" means two. _check_availability_acuity caps a single day's spoken
    times at THREE before setting more_times, so the two-option wording would
    have been read out over a three-option list — the grammar-does-not-match-
    the-data snag already logged against this sentence. Now that code emits it,
    code agrees with the count.
    """
    if n_offered <= 1:
        return MORE_TIMES_TAIL_ONE
    if n_offered == 2:
        return MORE_TIMES_TAIL_MANY
    return MORE_TIMES_TAIL_SEVERAL


# A sentence claiming further availability beyond what was just listed.
# Deliberately a FAMILY, not one literal: the failure being guarded against is
# a language model paraphrasing, and a guard that matches only the exact
# example sentence would be defeated by "I've got a couple more that day".
# Both halves must be present in the SAME sentence — an "extra quantity" word
# and a "further times" noun — which is what keeps it off the legitimate
# openers ("The available slots for Tuesday are —", "Any of those work?").
_EXTRA_QUANTITY_RE = re.compile(
    r"\b(?:a\s+few|a\s+couple|some|several|plenty|more|other|others)\b",
    re.IGNORECASE,
)
_FURTHER_TIMES_RE = re.compile(
    r"\b(?:others?|more|further|times|slots|openings|availability)\b",
    re.IGNORECASE,
)
# A claim that the listed times are the COMPLETE set. Used only to SUPPRESS an
# optional append (never to rewrite), so a false positive costs nothing.
_COMPLETENESS_RE = re.compile(
    r"\bthe\s+available\s+(?:slot|slots|time|times)\b[^.!?]{0,40}?\b(?:is|are)\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


# ---------------------------------------------------------------------------
# What was actually READ OUT — the one place that knows.
#
# The option count is a claim about how much the caller has to hold in their
# head, and until now nothing owned it. The tool capped its own payload, but
# the `already_retrieved` re-entry (llm_stream) hands the model the FULL
# available_days and says "present the existing slots" — so on CA6b90c3a2 a
# five-slot Tuesday was read out in one breath, five numbered options deep,
# after the first offer had been correctly capped at two.
#
# Capping in the prompt would be a fourth attempt to win an argument with a
# language model about a number. It is enforced here instead, on the assembled
# text, immediately before the DTMF map and the Number re-split are derived
# from it — so the keypad, the speech and the record cannot disagree.
# ---------------------------------------------------------------------------

MAX_SPOKEN_OPTIONS = 3

# The most options a single readout can carry, set by the KEYPAD, not by taste:
# SLOT_OPTION_ANCHOR_RE matches one digit, so "Number 10" anchors as "Number 1".
MAX_KEYPAD_OPTIONS = 9

# The closing question a capped readout is given when the trim removed the
# model's own. A slot readout that ends on a statement is dead air the caller
# has to break. Checked against turn_handler._BANNED_SENTENCE_RE.
CAPPED_READOUT_QUESTION = "Any of those work?"

# Numbered-option anchors. Defined HERE and imported by llm_stream so the trim,
# the DTMF map and the TTS re-split are derived from one pattern — a cap that
# counted options differently from the map would trim to a boundary the keypad
# does not share.
SLOT_OPTION_ANCHOR_RE = re.compile(
    r"Number\s+([1-9])\b|(?<!\d)([1-9])\s*[\u2014\u2013\-]\s*",
    re.IGNORECASE,
)

_OPTION_LABEL_STOP_RE = re.compile(r"[\u2014\u2013.]")


def _option_anchors(text: str) -> List[Tuple[int, int, str]]:
    return [
        (m.start(), m.end(), m.group(1) or m.group(2))
        for m in SLOT_OPTION_ANCHOR_RE.finditer(text or "")
    ]


def extract_slot_options(text: str) -> Dict[str, str]:
    """{digit: spoken label} for every numbered option in `text`, in order."""
    text = text or ""
    anchors = _option_anchors(text)
    out: Dict[str, str] = {}
    for i, (_start, end, digit) in enumerate(anchors):
        nxt = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
        label = text[end:nxt].lstrip(", ")
        label = _OPTION_LABEL_STOP_RE.split(label)[0].strip().rstrip(".,;- ")
        if label:
            out[digit] = label
    return out


def option_label_candidates(text: str) -> Dict[str, List[str]]:
    """{digit: [label candidates]} for every numbered option, best-effort first.

    `extract_slot_options` commits to the first segment before an em dash, which
    is right for the DTMF map — the keypad injects that label as a synthetic
    transcript, and "Thursday 27th August" is what a caller pressing 1 means.

    It is wrong for RESOLUTION. In the multi-day readout the option is
    "Thursday 27th August — half past seven in the evening", and the time is the
    only part that can match a slot: `_resolve_within` compares against the
    slot's `spoken` field by normalised equality, and that field holds a time.
    Truncating at the dash threw the time away, so every multi-day readout
    failed to resolve and the offer record was never written — on three
    consecutive live calls, every time the search widened.

    The single-day form put the day in the PREAMBLE and each option was already
    a bare time, which is why this was invisible until a widened search made
    Susie name a different day per option.

    Candidates are segments of what was actually spoken for that option, so a
    wrong match is not available to them: day segments match nothing (slot
    labels are times), leaving the time segment as the only thing that can hit.
    """
    out: Dict[str, List[str]] = {}
    text = text or ""
    anchors = _option_anchors(text)
    for i, (_start, end, digit) in enumerate(anchors):
        nxt = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
        whole = text[end:nxt].lstrip(", ").strip().rstrip(".,;- ")
        seen: List[str] = []
        for cand in [whole] + _OPTION_LABEL_STOP_RE.split(whole):
            cand = cand.strip().rstrip(".,;- ").lstrip(", ")
            if cand and cand not in seen:
                seen.append(cand)
        if seen:
            out[digit] = seen
    return out


def cap_spoken_options(
    text: str, cap: int = MAX_SPOKEN_OPTIONS
) -> Tuple[str, int, int]:
    """Trim a numbered readout to its first `cap` options.

    Returns `(text, n_before, n_after)`. `n_before != n_after` means the model
    read out more than it was allowed to and the excess was removed — which
    also means further times on that day now exist by construction, so the
    caller must set more_times True off this result.

    Whatever followed the LAST option's own sentence is re-attached: that is
    where the closing question lives ("... eight in the evening. Any of those
    work?"). Cutting at the anchor alone would take the question with it.
    """
    s = (text or "").strip()
    if not s or cap < 1:
        return text, 0, 0
    anchors = _option_anchors(s)
    n = len(anchors)
    if n <= cap:
        return text, n, n

    head = s[: anchors[cap][0]].rstrip()
    if not head:
        # Nothing at all before option cap+1 — not a shape we can safely
        # rewrite, so leave it and let the count mismatch be logged.
        return text, n, n
    if head[-1] not in ".!?":
        head += "."

    trailing = s[anchors[-1][1]:].strip()
    parts = _SENTENCE_SPLIT_RE.split(trailing, maxsplit=1)
    remainder = parts[1].strip() if len(parts) > 1 else ""
    return f"{head} {remainder or CAPPED_READOUT_QUESTION}".strip(), n, cap


def _norm_label(label: str) -> str:
    return " ".join(str(label or "").lower().split()).strip(" .,;:!?-")


def _norm_day(value: Any) -> str:
    """Normalise a day label for comparison, dropping the filler the model adds.

    The payload says "Monday 31st August"; the model often speaks "Monday the
    31st of August". Both must key the same day, or the scoping below silently
    stops applying and the ambiguity it exists to resolve comes back.
    """
    text = _norm_label(value if isinstance(value, str) else "")
    return " ".join(w for w in text.split() if w not in ("the", "of"))


def _resolve_within(
    slots: List[Dict[str, Any]], labels: List[str]
) -> Optional[List[Dict[str, Any]]]:
    if not slots or not labels:
        return None
    by_label: Dict[str, List[Dict[str, Any]]] = {}
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for slot in slots:
        by_label.setdefault(_norm_label(slot.get("spoken") or ""), []).append(slot)
        _dl = _norm_day(slot.get("day_label") or "")
        if _dl:
            by_day.setdefault(_dl, []).append(slot)
    out: List[Dict[str, Any]] = []
    for label in labels:
        # A label may be one string, or an ordered list of candidates for the
        # same spoken option (see option_label_candidates). Candidates are
        # segments of one option, so at most one of them can be a time — trying
        # them in order cannot widen what is reachable, only recover the part
        # the em-dash truncation used to discard.
        cands = [label] if isinstance(label, str) else list(label or [])
        # The option names its OWN day in the multi-day readout
        # ("Number 1, Monday 31st August - quarter past eight in the evening").
        # Scope this option's time lookup to that day.
        #
        # Without it the time is looked up across every day at once, and a
        # clinic running the same rota two evenings running makes "quarter past
        # eight in the evening" ambiguous — so the all-or-nothing rule denied
        # data it could actually have told apart. Live on CA9bd4ecf0 (25 Aug):
        # the candidates carried both halves and resolution still returned
        # nothing.
        #
        # `prefer_day` at the caller cannot do this job: it is ONE day, and a
        # multi-day readout presents several, so it can only ever rescue one.
        _scoped = by_label
        for _c in cands:
            _pool = by_day.get(_norm_day(_c))
            if _pool:
                _scoped = {}
                for _s in _pool:
                    _scoped.setdefault(
                        _norm_label(_s.get("spoken") or ""), []
                    ).append(_s)
                break
        hit = None
        for cand in cands:
            hits = _scoped.get(_norm_label(cand)) or []
            if len(hits) > 1:
                # Ambiguous is REFUSED, never retried with another candidate:
                # the same time on two days cannot be told apart from speech,
                # and picking one is how a caller is booked into a day they
                # never heard.
                return None
            if hits:
                hit = hits[0]
                break
        if hit is None:
            return None
        out.append(hit)
    return out


def resolve_spoken_options(
    available_days: Any, labels: Any, prefer_day: Optional[str] = None
) -> Optional[List[Dict[str, Any]]]:
    """Map spoken labels back to bookable slots. All-or-nothing, deny by default.

    Returns None unless EVERY label resolves to exactly one slot. A label that
    appears on more than one day counts as unresolvable rather than guessed:
    picking the wrong day would write a wrong `last_offered_slots`, and a wrong
    offer on the table is how a caller is booked into a day they never heard.

    `prefer_day` is what makes this usable rather than theoretical. A clinic
    running the same rota every evening has "five in the evening" on Tuesday
    AND Wednesday, and `available_days` holds the whole sweep — so a plain
    global lookup would be ambiguous on almost every real readout and quietly
    resolve nothing. The day being presented is known at the call site (the
    tool result's first_day, or the day of the offer already on the table), so
    the search is scoped to it first and only falls back to the whole set.
    """
    flat = flatten_bookable_slots(available_days)
    labels = list(labels or [])
    if not flat or not labels:
        return None
    if prefer_day:
        scoped = _resolve_within(
            [s for s in flat if _day_key(s) == prefer_day], labels
        )
        if scoped is not None:
            return scoped
    return _resolve_within(flat, labels)


def unspoken_remain_on_day(session: Dict[str, Any], day: str) -> bool:
    """True if `day` still holds a bookable slot the caller has never heard.

    Ground truth for the "a few others that day" tail, read from the CUMULATIVE
    spoken record rather than from one turn's tool flag. All three producers of
    more_times are subsumed: a follow-up batch that finishes a day reads False
    here even though its own payload knows only about its own slots.
    """
    spoken = _spoken_key_set(session)
    for slot in flatten_bookable_slots(session.get("available_days") or []):
        if _day_key(slot) != day:
            continue
        if str(slot.get("start") or "")[:19] not in spoken:
            return True
    return False


def day_key_of(slot: Dict[str, Any]) -> str:
    """Public alias — the calendar day a slot belongs to."""
    return _day_key(slot)


def _is_extra_slots_claim(sentence: str) -> bool:
    """True when `sentence` asserts further times beyond those listed."""
    s = sentence.strip()
    if not s:
        return False
    # A numbered option is never a claim, whatever words it contains — it is
    # parsed for keypad selection and must survive untouched.
    if re.search(r"\bNumber\s+[1-9]\b", s, re.IGNORECASE):
        return False
    return bool(_EXTRA_QUANTITY_RE.search(s) and _FURTHER_TIMES_RE.search(s))


def reconcile_extra_slots_claim(
    text: str, more_times: bool, n_offered: int = 2, allow_append: bool = True
) -> Tuple[str, str]:
    """Align a slot presentation's "more times that day" claim with the truth.

    Returns `(text, action)` where action is one of "stripped", "appended" or
    "unchanged", for logging.

    The two directions are deliberately NOT symmetric:

      more_times False → any such claim is REMOVED. Over-promising availability
        is the harm: the caller is told times exist that do not, and the
        follow-up path will contradict it one turn later with "I don't have any
        further times on that day".

      more_times True  → the tail is appended only when the reply does not
        already make the claim AND does not assert completeness. Under-informing
        is safe and recoverable — a caller who asks "anything else that day?"
        is served the real next batch by next_slot_batch(). Appending next to a
        completeness opener would make Susie contradict herself in one breath,
        which is worse than staying quiet.

    `allow_append` is False for a multi_day presentation. The tail says "that
    day", and a multi_day reply has just named TWO different days — there is no
    "that day" for it to refer to. The sentence only ever belonged to the
    single_day cases. Stripping stays unconditional: a false claim is wrong in
    either presentation mode.
    """
    if not (text or "").strip():
        return text, "unchanged"

    sentences = _SENTENCE_SPLIT_RE.split(text.strip())

    if not more_times:
        kept = [s for s in sentences if not _is_extra_slots_claim(s)]
        if len(kept) == len(sentences):
            return text, "unchanged"
        if not kept:
            # Never blank a reply. A presentation that is ENTIRELY an
            # availability claim is not something we can safely rewrite, so
            # leave it and let the caller hear it — the mismatch is logged.
            return text, "unchanged"
        return " ".join(kept).strip(), "stripped"

    if not allow_append:
        return text, "unchanged"
    if any(_is_extra_slots_claim(s) for s in sentences):
        return text, "unchanged"
    if _COMPLETENESS_RE.search(text):
        return text, "unchanged"

    tail = more_times_tail(n_offered)
    # BEFORE the closing question, not after it. "Any of those work? And I've a
    # few others that day" makes the caller's "yes" ambiguous between "yes, one
    # of those works" and "yes, tell me the others" — and it ends the readout on
    # a statement, which arms the watchdog BACKSTOP and reads as dead air.
    # Offering the extra times first and then asking is the order a receptionist
    # would use, and it is the order the caller can answer.
    if sentences and sentences[-1].rstrip().endswith("?"):
        head = " ".join(sentences[:-1]).strip()
        if head:
            return f"{head} {tail} {sentences[-1].strip()}", "appended"
    return f"{text.rstrip()} {tail}", "appended"


def _spoken_series(labels: List[str]) -> str:
    """"a", "a, or b", "a, b, or c" — a spoken list, not a written one.

    No Oxford comma before a two-item "or": "five, or half five" is how a
    receptionist says it, and it is what the two-slot form has always emitted.
    """
    labels = [l for l in labels if l]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])}, or {labels[-1]}"


def _closing_question(n: int) -> str:
    if n <= 1:
        return "Does that work?"
    if n == 2:
        return "Either of those work?"
    return "Any of those work?"


def format_next_batch_speech(batch: List[Dict[str, Any]], more: bool) -> str:
    """Speak a follow-up batch of ANY size on one day.

    Sizes 1 and 2 are byte-identical to the two-slot form this replaced. Sizes
    3+ exist because the owner rule (24 Aug 2026) is that the FIRST offer is
    capped at three and an explicit "tell me the others" is answered with every
    remaining time on that day — see all_remaining_on_next_day.
    """
    if not batch:
        return (
            "I don't have any further times on that day — would you like me "
            "to look at a different day?"
        )
    day = batch[0].get("day_label") or "that day"
    series = _spoken_series([s.get("spoken") or "" for s in batch])
    tail = f" {more_times_tail(len(batch))}" if more else ""
    return (
        f"On {day} I also have {series}.{tail} "
        f"{_closing_question(len(batch))}"
    )


def format_time_available_speech(slot: Dict[str, Any]) -> str:
    day = slot.get("day_label") or "that day"
    spoken = slot.get("spoken") or slot.get("time") or "that time"
    return (
        f"Yes — {spoken} on {day} is free. "
        f"Shall I book that in for you?"
    )


def _supersede_slot_map(session: Dict[str, Any]) -> None:
    """B-80 — the keypad map no longer describes what the caller just heard.

    `v3_dtmf_slot_map` is built in `_flush_slot_buf` from a NUMBERED readout.
    The deterministic follow-up paths below speak their times directly and
    UNNUMBERED, never reaching that function, so the map from the previous
    readout survives intact while the offer has moved on. On CA6b90c3a2
    (24 Aug, 12:24:39) the map still listed all five times while the follow-up
    had just offered 20:00 alone: a keypress would have booked a time the
    caller heard earlier and was no longer being offered.

    Marking rather than clearing, deliberately. `v3_dtmf_slot_map` is the
    OWNER of the slot window -- `_derive_slot_window` re-derives
    `v3_awaiting_slot_selection` from it every turn, and
    `_should_clear_slot_cache` reads its presence to decide whether the next
    turn may wipe `last_offered_slots`. Popping the map here would therefore
    hand the next turn permission to wipe the very input these follow-up
    paths open with (`if not offered: return None`) -- re-breaking B-78, the
    defect this whole module exists to fix. The window must stay open for
    VOICE; only the digit-to-label resolution is invalidated.

    Cleared again wherever a fresh map is armed, and when the window closes.
    """
    session["v3_slot_map_superseded"] = True


def apply_next_batch_to_session(
    session: Dict[str, Any],
    batch: List[Dict[str, Any]],
    more: bool,
) -> str:
    """Advance last_offered to this batch and return the spoken offer."""
    # Cumulative FIRST — last_offered_slots is about to be overwritten, and it
    # is the only record that this batch was ever spoken (B-78b).
    record_spoken_slots(session, batch)
    session["last_offered_slots"] = [
        {"start": s["start"], "end": s.get("end") or ""} for s in batch
    ]
    session["slot_labels"] = [s.get("spoken") or s.get("time") for s in batch]
    # B-80: these times are spoken UNNUMBERED, so 1..N no longer refer to them.
    _supersede_slot_map(session)
    return format_next_batch_speech(batch, more)


def apply_resolved_time_to_session(
    session: Dict[str, Any],
    slot: Dict[str, Any],
) -> str:
    """Present the resolved unspoken time as the current offer / selection."""
    offered = {"start": slot["start"], "end": slot.get("end") or ""}
    session["last_offered_slots"] = [offered]
    session["slot_labels"] = [slot.get("spoken") or slot.get("time")]
    # Mirror fast-path slot selection so the LLM / booking path sees it.
    session["selected_slot"] = offered
    try:
        from app.media_streams.config import F_SELECTED_SLOT
        session[F_SELECTED_SLOT] = offered
    except Exception:
        pass
    # B-80: the offer is now this single time; the numbered map is stale.
    _supersede_slot_map(session)
    return format_time_available_speech(slot)


def build_followup_tool_result(
    available_days: Any,
    batch: List[Dict[str, Any]],
    more: bool,
) -> Dict[str, Any]:
    """Shape a check_availability-like result for the Haiku slot formatter."""
    if not batch:
        return {
            "error": "No further times on that day.",
            "available_days": available_days if isinstance(available_days, list) else [],
        }
    # Fail-safe: this struct declares presentation_mode="single_day" and carries
    # ONE date/day_label, so every slot in it must belong to that day. next_slot_batch
    # guarantees that; this is the backstop for any future caller that does not.
    # Dropping the off-day slots is the safe direction — offering fewer times costs
    # the caller a follow-up question, whereas announcing a slot under the wrong
    # day's name sends a real patient to the clinic on a day they have no
    # appointment (CA5c4fb14f, 30 Jul 2026).
    _day = _day_key(batch[0])
    _same_day = [s for s in batch if _day_key(s) == _day]
    if len(_same_day) != len(batch):
        logger.error(
            "[slot_followup] multi-day batch reached build_followup_tool_result — "
            "dropping %d off-day slot(s) to protect the spoken day label. "
            "kept=%s dropped=%s",
            len(batch) - len(_same_day),
            [s.get("start") for s in _same_day],
            [s.get("start") for s in batch if _day_key(s) != _day],
        )
        batch = _same_day
        more = True  # the dropped slots still exist, just not on this day

    day_label = batch[0].get("day_label") or ""
    date = batch[0].get("date")
    first_day = {
        "date": date,
        "day_label": day_label,
        "slot_times": [s["time"] for s in batch],
        "slot_times_spoken": [s["spoken"] for s in batch],
        "slots": [{"start": s["start"], "end": s.get("end") or ""} for s in batch],
        "more_times": more,
    }
    return {
        "status": "next_unspoken_batch",
        "presentation_mode": "single_day",
        "first_day": first_day,
        "available_days": available_days if isinstance(available_days, list) else [],
        "total_days": 1,
        "message": (
            "Caller asked for other times. Present ONLY first_day "
            "(Number 1 / Number 2). more_times="
            + ("true" if more else "false")
            + ". Do NOT claim these are the only times if more_times is true."
        ),
    }


def try_unspoken_followup_speech(
    session: Dict[str, Any], user_text: str
) -> Optional[str]:
    """
    If this turn is an unspoken-slot follow-up, update session and return
    speech. Otherwise return None (caller falls through to the LLM).
    """
    # Only while the caller is still choosing a time — not during name/phone
    # or after a slot is locked.
    if session.get("v3_confirmed_slot_phrase"):
        return None
    _col = session.get("collected") or {}
    if _col.get("name") or _col.get("full_name") or session.get("patient_name"):
        return None
    if session.get("booking_write_confirmed") or session.get("booking_confirmed"):
        return None

    offered = session.get("last_offered_slots") or []
    days = session.get("available_days") or []
    if not offered or not days:
        return None

    # Cumulative, not just the current offer — see B-78b above.
    remaining = remaining_unspoken(session)
    if not remaining:
        # The day is genuinely exhausted. Say so HERE rather than falling to
        # the model: "have you got anything else?" with nothing left is the
        # exact prompt that produced "Those are the two available slots on that
        # day" while three sat unoffered. The honest answer is deterministic,
        # so it should not be generated.
        if utterance_requests_more_slots(user_text):
            return format_next_batch_speech([], False)
        return None

    # Specific unspoken time first (V5).
    hit = resolve_requested_time(user_text, remaining)
    if hit is not None:
        return apply_resolved_time_to_session(session, hit)

    if utterance_requests_more_slots(user_text):
        # Scoped to the day under discussion. `remaining` above stays whole-
        # sweep on purpose: resolve_requested_time names the slot's OWN day, so
        # a caller-named time on another day cannot mislead, and refusing it
        # would tell them a real time does not exist.
        batch, more = all_remaining_on_next_day(
            remaining_unspoken_on_current_day(session)
        )
        if not batch:
            # This DAY is exhausted even though other days remain. Say so
            # rather than falling to the model — the same reasoning as the
            # empty-remaining branch above, and the same sentence.
            return format_next_batch_speech([], False)
        return apply_next_batch_to_session(session, batch, more)

    return None
