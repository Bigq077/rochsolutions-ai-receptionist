"""
Regression: "the second one please" was read back with the other option's time.

B-95. `CA1cd253cb08b7dcee6b3ffb639f9c6d47` (26 Aug 2026, theorem_v3, build
`bbfee8f3`, Alcester). Found by a smoke call that was checking something else.

    20:50:05  Number 1, Wednesday 2nd September - two in the afternoon.
              Number 2, Friday 4th September - one in the afternoon.
              slot buf: spoken options span 2 days - offer record left unchanged
    20:50:17  caller: "uh the second one please"
    20:50:19  "So that's Friday the 4th of September at TWO in the afternoon
               - could I take your first name and surname?"

Option 2's DAY with option 1's TIME. Friday 4 September held one afternoon slot
- the later lookup at 20:51:29 returns slot_times ["13:00"] - so the sentence
the caller was being asked to agree to named a time that was never offered.
They hung up at the name request, so nothing was written.

WHY IT REACHED THE CALLER: a multi-day readout deliberately does not write the
position-indexed offer record (`last_offered_slots` / `slot_labels` are skipped
in the multi_day branch, by a comment that calls widening them "a separate
change with its own consumers to audit"). So an ordinal choice is resolved by
the model, not from data - and nothing downstream compared the confirmation
sentence against the option that had been selected. Gate 5 checked banned
phrases and tracked chunks; no rung asserted that the time being confirmed was
one the caller had been offered.

THE FIX is a gate rung, not a widening of that record: check the sentence
against the payload on the way out.

THE DENOMINATOR IS WHAT WAS SPOKEN, NOT WHAT IS BOOKABLE. `_cap_presented_slots`
trims only the SPOKEN list and says so - "available_days stays the FULL bookable
set ... Does not touch session['available_days']" - and multi_day speaks exactly
ONE time per day. A first cut of this guard compared the sentence against
`available_days` and was inert on this very call in both directions at once:
Friday holds more than one bookable time, so the live sentence was "ambiguous,
nothing to choose between" and went uncorrected; and had the wrongly-named time
been bookable-but-unspoken that day, it would have been waved through as
correct. `test_a_time_bookable_but_never_spoken_is_still_wrong` is that case and
fails against the available_days version.

THE TIME IS CORRECTED TO THE DAY, NEVER THE REVERSE - the same one-way rule the
weekday corrector states, for the same reason. The caller picked an option and
the slot map corroborates which DAY that option was; nothing corroborates the
time. Rewriting the day to suit an invented time would move the appointment
instead of repairing the sentence.

Deny by default, exactly as `_correct_weekday_against_known_dates` does it: one
known day named, no time SPOKEN for it named, and exactly one time spoken for it
so there is nothing to choose between. Everything else is returned untouched and
logged as a mismatch, because a wrong time the code cannot safely fix is still
worth having in the call record.
"""
from __future__ import annotations

import inspect

import pytest

from app.tools.slot_followup import record_spoken_slots, reconcile_readback_time


def _day(date, label, times):
    """A day as check_availability leaves it in session["available_days"]:
    the FULL bookable set, whatever subset was spoken."""
    return {
        "date": date,
        "day_label": label,
        "slot_times": [t for t, _ in times],
        "slot_times_spoken": [s for _, s in times],
        "slots": [{"start": f"{date}T{t}:00", "end": ""} for t, _ in times],
    }


# The offer at 20:50:05. Wednesday and Friday each spoke ONE time (multi_day
# caps at one per day) while the diary held more.
OFFER_DAYS = [
    _day("2026-09-02", "Wednesday 2nd September",
         [("14:00", "two in the afternoon"), ("15:00", "three in the afternoon")]),
    _day("2026-09-04", "Friday 4th September",
         [("13:00", "one in the afternoon")]),
]

SPOKEN = ["2026-09-02T14:00:00", "2026-09-04T13:00:00"]

# The sentence the caller actually heard.
LIVE_READBACK = (
    "So that's Friday the 4th of September at two in the afternoon "
    "- could I take your first name and surname?"
)


def _session(days=None, spoken=None):
    days = OFFER_DAYS if days is None else days
    session = {"available_days": days}
    record_spoken_slots(session, [{"start": s} for s in (SPOKEN if spoken is None else spoken)])
    return session


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
def test_the_live_readback_is_corrected_to_the_offered_time():
    out, action, _ = reconcile_readback_time(LIVE_READBACK, _session())
    assert action == "corrected"
    assert "one in the afternoon" in out
    assert "two in the afternoon" not in out


def test_a_time_bookable_but_never_spoken_is_still_wrong():
    """The denominator test, and the reason this guard reads the spoken record.

    Friday really does have 14:00 free - it just was not offered. Checking the
    sentence against `available_days` finds "two in the afternoon" among
    Friday's times and calls the read-back correct, leaving the caller
    confirming a slot they were never given. Against the SPOKEN record it is
    still wrong, and still fixable.
    """
    days = [
        OFFER_DAYS[0],
        _day("2026-09-04", "Friday 4th September",
             [("13:00", "one in the afternoon"), ("14:00", "two in the afternoon")]),
    ]
    out, action, _ = reconcile_readback_time(LIVE_READBACK, _session(days))
    assert action == "corrected", "a bookable-but-unspoken time is not an offer"
    assert "one in the afternoon" in out


def test_the_day_is_never_rewritten():
    """The one-way rule. The caller's ordinal corroborates the day; nothing
    corroborates the time, so the day is the fixed point."""
    out, _, _ = reconcile_readback_time(LIVE_READBACK, _session())
    assert "Friday the 4th of September" in out
    assert "Wednesday" not in out


def test_the_correction_leaves_the_rest_of_the_sentence_alone():
    out, _, _ = reconcile_readback_time(LIVE_READBACK, _session())
    assert out.startswith("So that's Friday")
    assert out.endswith("could I take your first name and surname?")


# ---------------------------------------------------------------------------
# Deny by default
# ---------------------------------------------------------------------------
def test_a_correct_readback_is_untouched():
    good = (
        "So that's Friday the 4th of September at one in the afternoon "
        "- could I take your name?"
    )
    out, action, _ = reconcile_readback_time(good, _session())
    assert action == "unchanged"
    assert out == good


def test_a_time_offered_earlier_in_the_call_is_a_valid_confirmation():
    """The record is cumulative on purpose. A caller may come back to a time
    from an earlier offer, and that is a confirmation, not a mismatch."""
    days = [
        OFFER_DAYS[0],
        _day("2026-09-04", "Friday 4th September",
             [("13:00", "one in the afternoon"), ("16:00", "four in the afternoon")]),
    ]
    session = _session(days, spoken=SPOKEN + ["2026-09-04T16:00:00"])
    text = "So that's Friday the 4th of September at four in the afternoon?"
    out, action, _ = reconcile_readback_time(text, session)
    assert action == "unchanged"
    assert out == text


def test_an_ambiguous_day_is_reported_not_guessed():
    """Two times SPOKEN that day means there is nothing to choose between, so
    the sentence stands and the mismatch is logged instead."""
    days = [
        OFFER_DAYS[0],
        _day("2026-09-04", "Friday 4th September",
             [("13:00", "one in the afternoon"), ("15:00", "three in the afternoon")]),
    ]
    session = _session(days, spoken=SPOKEN + ["2026-09-04T15:00:00"])
    out, action, detail = reconcile_readback_time(LIVE_READBACK, session)
    assert action == "mismatch"
    assert out == LIVE_READBACK
    assert "Friday 4th September" in detail


def test_a_sentence_naming_no_known_day_is_untouched():
    out, action, _ = reconcile_readback_time(
        "So that's two in the afternoon then?", _session()
    )
    assert action == "unchanged"
    assert out == "So that's two in the afternoon then?"


def test_a_sentence_naming_two_days_is_not_a_confirmation():
    """The offer readout itself names both days. It must pass through."""
    both = (
        "Number 1, Wednesday 2nd September - two in the afternoon. "
        "Number 2, Friday 4th September - one in the afternoon."
    )
    out, action, _ = reconcile_readback_time(both, _session())
    assert action == "unchanged"
    assert out == both


@pytest.mark.parametrize(
    "text",
    [
        "Let me look at Friday 4th September for you.",
        "Friday 4th September it is - and can I take a contact number?",
        "I've got you down for Friday 4th September.",
    ],
)
def test_a_day_named_without_a_time_is_not_a_readback(text):
    """These name a known day and none of its offered times, which is the exact
    shape of the defect minus the thing that makes it one. Reporting them would
    put a WARNING on every ordinary sentence that mentions a date, on the very
    surface an operator uses to find the real ones."""
    out, action, _ = reconcile_readback_time(text, _session())
    assert action == "unchanged"
    assert out == text


def test_a_time_the_payload_does_not_contain_is_not_invented_over():
    """If the wrong time is not a label the payload holds, it cannot be located
    safely - report, do not rewrite."""
    odd = "So that's Friday the 4th of September at half past four - your name?"
    out, action, _ = reconcile_readback_time(odd, _session())
    assert action == "mismatch"
    assert out == odd


def test_a_stale_spoken_record_is_declined_not_trusted():
    """The record is fingerprinted against the availability set. A record left
    over from a previous fetch says nothing about this offer."""
    session = _session()
    session["slot_starts_spoken_fp"] = "stale"
    out, action, _ = reconcile_readback_time(LIVE_READBACK, session)
    assert action == "unchanged"
    assert out == LIVE_READBACK


def test_the_guard_does_not_reset_the_spoken_record():
    """`_spoken_key_set` clears the record when the fingerprint moves. A Gate 5
    text guard must never be the thing that wipes it."""
    session = _session()
    session["slot_starts_spoken_fp"] = "stale"
    reconcile_readback_time(LIVE_READBACK, session)
    assert session["slot_starts_spoken"] == SPOKEN


def test_no_spoken_record_means_no_opinion():
    session = {"available_days": OFFER_DAYS}
    out, action, _ = reconcile_readback_time(LIVE_READBACK, session)
    assert action == "unchanged"
    assert out == LIVE_READBACK


@pytest.mark.parametrize(
    "text",
    ["", None],
    ids=["empty-text", "no-text"],
)
def test_bad_text_is_safe(text):
    out, action, _ = reconcile_readback_time(text, _session())
    assert action == "unchanged"
    assert out == text


@pytest.mark.parametrize(
    "session",
    [{}, {"available_days": []}, {"available_days": None}, None, "not a session"],
    ids=["empty", "no-days", "null-days", "no-session", "wrong-type"],
)
def test_bad_session_is_safe(session):
    out, action, _ = reconcile_readback_time(LIVE_READBACK, session)
    assert action == "unchanged"
    assert out == LIVE_READBACK


def test_one_and_two_are_not_folded_together():
    """The normaliser drops filler only. If it ever dropped digits or number
    words, every readback would 'match' and the guard would be inert."""
    from app.tools.slot_followup import _readback_norm
    assert _readback_norm("one in the afternoon") != _readback_norm(
        "two in the afternoon"
    )


def test_the_day_match_survives_the_models_phrasing():
    """The payload labels the day "Friday 4th September"; the model says
    "Friday the 4th of September". A literal substring test misses that, which
    is why the day is matched on the normalised form."""
    from app.tools.slot_followup import _readback_norm
    assert _readback_norm("Friday 4th September") in _readback_norm(LIVE_READBACK)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def test_gate5_runs_the_reconciler_before_stripping():
    """Sited beside the weekday corrector for the reason that comment gives: a
    strip below can remove the sentence carrying the correction."""
    from app.media_streams import turn_handler

    src = inspect.getsource(turn_handler)
    i = src.index("_correct_weekday_against_known_dates(text, session)")
    j = src.index("Gate 5b: sentence-level stripping")
    assert "reconcile_readback_time" in src[i:j], (
        "the read-back reconciler does not run on the same rung as the weekday "
        "corrector - a later strip can remove the corrected sentence"
    )


def test_the_reconciler_is_given_the_session_not_just_the_days():
    """It needs the spoken record, which lives on the session. Passing
    available_days alone is the denominator bug this guard exists to avoid."""
    from app.media_streams import turn_handler

    src = inspect.getsource(turn_handler)
    i = src.index("reconcile_readback_time(")
    window = src[i:i + 200]
    assert "available_days" not in window, (
        "the reconciler is being handed available_days - the FULL bookable set, "
        "not what the caller was offered"
    )


def test_the_reconciler_cannot_kill_a_confirmation():
    """A guard on the confirmation sentence must never be the thing that ends
    the call."""
    from app.media_streams import turn_handler

    src = inspect.getsource(turn_handler)
    i = src.index("reconcile_readback_time")
    window = src[i - 400:i + 1200]
    assert "except Exception" in window and "text unchanged" in window
