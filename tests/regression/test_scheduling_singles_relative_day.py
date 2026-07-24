# tests/regression/test_scheduling_singles_relative_day.py
"""
Vital Edge abandoned booking (2026-07-24) — a single-word relative-day answer
to the timing question was dropped as a "non-scheduling single word".

Live-call trace (logs/…): Susie asked "Do you have a preference for when you'd
like to come in?" and the caller answered "today".  In connection.py the
single-word guard (CODE SPEC G, ~line 6266) checks membership in
`_SCHEDULING_SINGLES`; "today" was NOT in the set, matched no phone-number or
answer-expected exemption, so it hit the drop path and re-armed the silence
timer instead of dispatching the LLM.  The caller's only real answer of the
call was discarded, the watchdog looped "Sorry, I didn't catch that…" three
times, and the caller hung up — outcome logged `abandoned`, no booking made.

Root cause: `_SCHEDULING_SINGLES` covered weekday names (mon–fri), times of
day, numbers and yes/no words, but omitted every relative-day answer
("today", "tomorrow", "tonight", "now", …) and the weekend day names
("saturday", "sunday").

Fix: additive vocabulary only — extend the frozenset.  This is a pure data
change; it can only route MORE single words to the LLM (the safe direction)
and touches no `handle_transcript` logic.  The membership contract asserted
here is exactly what the fix guarantees.
"""

from app.media_streams.connection import _SCHEDULING_SINGLES


# The exact word from the abandoned call, plus its immediate family.  A caller
# asked "when would you like to come in?" routinely answers with one of these.
RELATIVE_DAY_SINGLES = [
    "today",     # the reproduced failure
    "tomorrow",
    "tonight",
    "now",
    "asap",
    "anytime",
    "whenever",
    "soon",
    "weekend",
]

# Weekend day names were also missing — only mon–fri were listed, so a caller
# saying "saturday" alone would have been dropped the same way.  These must
# reach the LLM even when the clinic is closed that day, so the LLM can say so.
WEEKEND_DAYS = ["saturday", "sunday"]

# Cross-referenced against app/vagueness_detector.py, the engine's own authority
# on timing/availability vocabulary: "flexible" is a VAGUE_PHRASE and "weekday"
# is in DAY_PATTERN, yet both were droppable single words.  "earliest"/"soonest"
# are the most natural one-word reply to "when would you like to come in?".
FLEXIBILITY_SINGLES = ["flexible", "either", "earliest", "soonest", "weekday"]


def test_relative_day_singles_are_scheduling():
    """Every relative-day answer must survive the single-word guard."""
    missing = [w for w in RELATIVE_DAY_SINGLES if w not in _SCHEDULING_SINGLES]
    assert not missing, (
        "relative-day answers dropped as non-scheduling single words: "
        f"{missing} — a caller answering the timing question with one of "
        "these would be silently discarded (Vital Edge abandoned booking)."
    )


def test_weekend_days_are_scheduling():
    """Saturday/Sunday must pass through like the weekday names already do."""
    missing = [w for w in WEEKEND_DAYS if w not in _SCHEDULING_SINGLES]
    assert not missing, f"weekend day names missing from scheduling singles: {missing}"


def test_flexibility_singles_are_scheduling():
    """Flexibility / first-slot answers must reach the LLM, not re-arm silence.

    In particular "flexible" must pass the guard so is_vague_availability()
    downstream can offer concrete times.
    """
    missing = [w for w in FLEXIBILITY_SINGLES if w not in _SCHEDULING_SINGLES]
    assert not missing, (
        "flexibility/first-slot answers dropped as non-scheduling single "
        f"words: {missing}"
    )


def test_today_specifically_reproduces_the_incident():
    """Guard the exact utterance from the 2026-07-24 abandoned call."""
    assert "today" in _SCHEDULING_SINGLES
