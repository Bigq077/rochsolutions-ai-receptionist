"""
Regression: Susie booked 6pm and told the caller 9am, three times over.

B-126. `CA44f1bdbe5bb2d2b06d138483ec4b3cab` (31 Aug 2026, theorem_v3, build
`8c04a26fe80c`, Alcester). The first real call after Theorem was folded onto
canonical, so this is a canonical-engine defect on all three clinics.

    20:39:03  "Here's what we've got coming up - Number 1, Monday 7th September
               - ten in the morning or five in the evening. Number 2, Tuesday
               8th September - nine in the morning or four in the afternoon.
               Number 3, Wednesday 9th September - NINE IN THE MORNING OR SIX
               IN THE EVENING. Any of those suit you?"
              slot buf: could not resolve spoken option(s) ... offer record
              left unchanged
    20:39:22  caller: "um yeah the wednesday at 6 in the evening suits me"
    20:39:24  read-back time corrected: 'six in the evening' -> 'nine in the
              morning' for Wednesday 9th September
    20:39:50  ... corrected again
    20:40:11  book_appointment slot_iso=2026-09-09T18:00:00  -> Acuity 200
    20:40:17  "All booked - you're in for Wednesday the 9th of September at"
              read-back time corrected: 'six in the evening' -> 'nine in the
              morning'

THE DIARY WAS RIGHT AND THE CALLER WAS TOLD THE WRONG TIME. 18:00 is what
Acuity holds; "nine in the morning" is what the caller heard at the read-back,
at the name confirmation, and in the closing. That is the first failure mode in
the definition of production-ready - the caller believes a booking that is not
the one that exists - and this guard produced it, on a sentence the model had
written CORRECTLY.

WHY THE GUARD WAS WRONG. `reconcile_readback_time` corrects only when exactly
ONE time is recorded as spoken for the named day, "so there is nothing to
choose between". Wednesday had two spoken. The record only knew one, because
the record was never written from the readout:

  * `_flush_slot_buf` could not resolve "ten in the morning or five in the
    evening" back to a slot - `resolve_spoken_options` AND
    `resolve_all_spoken_times` both return [] for a two-times-in-one-option
    label - so it logged "could not resolve" and recorded nothing.
  * `try_unspoken_followup_speech` then ran pre-LLM on the caller's next turn,
    as it does on every turn while a time is still being chosen, and its
    `remaining_unspoken(session)` call records `last_offered_slots` as heard.
  * On multi_day `_sync_last_offered_to_spoken` writes `slots[0]` - ONE slot
    per day, deliberately, because `_resolve_slot_iso` indexes that list BY
    POSITION for an ordinal choice.

So the record learned Wednesday 09:00 and nothing else, and the guard read a
lossy positional projection as "everything the caller heard".

THE UNDERLYING CONTRADICTION, which this fix does NOT close. The formatter
prompt instructs TWO times per day - "For each day include up to TWO times from
that day's slot_times_spoken: the earliest, plus one later option that day" -
while `_cap_presented_slots` sets `per_day = 1` for multi_day under the comment
"multi_day stays at ONE time per day", and the B-95 test docstring states the
same as fact. The model obeyed the prompt and reached past the trimmed list into
`available_days`, which the prompt also permits. Both times were real and
bookable; the second was simply never in any machine-readable record. Aligning
those two owners is a larger change with `_resolve_slot_iso`'s positional
indexing to audit, and it is not what should ship at 21:00 to three live lines.

THE FIX IS TO STOP THE GUARD ASSERTING WHAT IT CANNOT KNOW. Where the offer was
a multi_day projection that dropped times from a day, that day is marked, and
the guard reports "mismatch" instead of rewriting. Deny by default - the module's
own stated rule, applied to its own evidence. B-95's protection survives
everywhere the record IS complete, which includes B-95's own call: Friday 4
September held exactly one bookable time, so nothing was dropped and nothing is
marked.

Theorem verdict: APPLIES - found on theorem_v3, and the code is engine-generic,
so it applies to Vital Edge and Joint Venture identically.
"""
from __future__ import annotations

from app.tools.slot_followup import (
    LOSSY_SPOKEN_DAYS_KEY,
    reconcile_readback_time,
    record_spoken_slots,
)


def _day(date, label, times, spoken):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": list(spoken),
        "slots": [{"start": f"{date}T{t}:00", "end": ""} for t in times],
    }


WEDNESDAY = _day(
    "2026-09-09",
    "Wednesday 9th September",
    ["09:00", "18:00"],
    ["nine in the morning", "six in the evening"],
)

READBACK = (
    "So that's Wednesday the 9th of September at six in the evening "
    "— could I take your first name and surname?"
)


def _session(lossy):
    session = {"available_days": [WEDNESDAY]}
    # What the live call's record actually held: the positional projection,
    # Wednesday's FIRST time and nothing else.
    record_spoken_slots(session, [{"start": "2026-09-09T09:00:00"}])
    if lossy:
        session[LOSSY_SPOKEN_DAYS_KEY] = ["2026-09-09"]
    return session


def test_the_caller_s_own_choice_is_not_rewritten_to_the_projected_time():
    """The live P1. 18:00 was offered, chosen, and booked; leave the sentence."""
    out, action, _ = reconcile_readback_time(READBACK, _session(lossy=True))
    assert "six in the evening" in out
    assert "nine in the morning" not in out
    assert action == "mismatch"


def test_a_day_whose_record_is_incomplete_is_still_reported():
    """Silence would be worse than a wrong time. It stays in the call record."""
    _, action, detail = reconcile_readback_time(READBACK, _session(lossy=True))
    assert action == "mismatch"
    assert "Wednesday 9th September" in detail


def test_b95_still_corrects_where_the_record_is_complete():
    """The founding case: one time spoken that day, nothing dropped, correct it."""
    out, action, _ = reconcile_readback_time(READBACK, _session(lossy=False))
    assert action == "corrected"
    assert "nine in the morning" in out


def test_a_time_that_really_was_offered_is_left_alone_either_way():
    """Naming a spoken time is never a mismatch, marked or not."""
    text = "So that's Wednesday the 9th of September at nine in the morning."
    for lossy in (True, False):
        out, action, _ = reconcile_readback_time(text, _session(lossy=lossy))
        assert out == text
        assert action == "unchanged"


def test_the_executor_marks_a_day_whose_times_it_dropped():
    """The live payload shape: three days, two bookable times each, one shown."""
    from app.tools.receptionist_tools import _sync_last_offered_to_spoken

    days = [
        _day("2026-09-07", "Monday 7th September", ["10:00", "17:00"],
             ["ten in the morning", "five in the evening"]),
        WEDNESDAY,
    ]
    # presented_days is what _cap_presented_slots leaves: ONE time per day.
    presented = [dict(d, slot_times=d["slot_times"][:1],
                      slot_times_spoken=d["slot_times_spoken"][:1],
                      slots=d["slots"][:1]) for d in days]
    session = {}
    _sync_last_offered_to_spoken(session, {
        "presentation_mode": "multi_day",
        "available_days": days,
        "presented_days": presented,
    })
    assert session[LOSSY_SPOKEN_DAYS_KEY] == ["2026-09-07", "2026-09-09"]
    # And the positional record is untouched - one per day, by design.
    assert [s["start"] for s in session["last_offered_slots"]] == [
        "2026-09-07T10:00:00", "2026-09-09T09:00:00",
    ]


def test_a_day_shown_in_full_is_not_marked():
    """Nothing was dropped, so the record is a transcript and B-95 may act."""
    from app.tools.receptionist_tools import _sync_last_offered_to_spoken

    one = _day("2026-09-04", "Friday 4th September", ["13:00"],
               ["one in the afternoon"])
    session = {LOSSY_SPOKEN_DAYS_KEY: ["stale"]}
    _sync_last_offered_to_spoken(session, {
        "presentation_mode": "multi_day",
        "available_days": [one],
        "presented_days": [one],
    })
    assert LOSSY_SPOKEN_DAYS_KEY not in session
