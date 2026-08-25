"""
Regression: Susie named a day that does not exist.

Live on Marcus's line twice in two days — `CAfcb3130c` (25 Aug, abandoned) and
`CAdf057714` (25 Aug, booked):

    caller: "um do you have any availability tomorrow tuesday"
    tool:   {after_date: "2026-08-26", day_window: 1} -> empty -> widened 1d -> 14d
    Susie:  "Tuesday 26th August is fully booked, I'm afraid - ..."

**26 August 2026 is a Wednesday**, and the same payload proves it: it labels the
27th "Thursday 27th August" and the 28th "Friday 28th August".

The payload was right. `requested_day_label` is built by
`_spoken_day_label("2026-08-26")`, which returns "Wednesday 26th August", and
SLOT_FORMATTER_SYSTEM_PROMPT says to use it verbatim and even supplies the
template `"[requested_day_label] is fully booked, I'm afraid - "`. The model
used the template and substituted a weekday lifted from the caller's garbled
"tomorrow tuesday".

So this is NOT fixable by telling the model again. It already had the right
string, an instruction to use it verbatim, and a worked example.

WHICH HALF GETS CORRECTED, and why it is not arbitrary: the weekday is
corrected to match the DATE, never the other way round. The date is what gets
booked — `_resolve_slot_iso` matches `available_days` on the date, and the
booking on the second call went in as `2026-08-28T16:30` with the caller never
having spoken a date at all. The weekday is decoration on top of it. Rewriting
the date to match a hallucinated weekday would move a real appointment.

DENY BY DEFAULT: a spoken day+month is corrected only when the session already
knows a date with that day and month, from the tool's own `date` fields. No
year is ever guessed, and an unknown or ambiguous date is left exactly as the
model said it.
"""
from __future__ import annotations

import pytest

from app.media_streams.turn_handler import _correct_weekday_against_known_dates as _fix


def _session(dates=(), requested=None):
    s = {"available_days": [{"date": d} for d in dates]}
    if requested:
        s["requested_day_iso"] = requested
    return s


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_live_sentence_is_corrected():
    """2026-08-26 is a Wednesday. The requested day is not in available_days —
    it was empty, which is the whole reason the sentence exists — so it has to
    be reachable from the session some other way."""
    s = _session(dates=["2026-08-27", "2026-08-28"], requested="2026-08-26")
    out = _fix(
        "Tuesday 26th August is fully booked, I'm afraid - Here's what we've "
        "got coming up - Number 1, Thursday 27th August at half past seven in "
        "the evening. Number 2, Friday 28th August - half past four in the "
        "afternoon.",
        s,
    )
    assert out.startswith("Wednesday 26th August is fully booked")
    # The two correct ones are untouched.
    assert "Thursday 27th August" in out
    assert "Friday 28th August" in out
    # Nothing else moved.
    assert "half past seven in the evening" in out
    assert "Number 1," in out and "Number 2," in out


def test_the_date_is_never_rewritten():
    """The booking is made from the date. Correcting it to match a wrong
    weekday would move a real appointment."""
    s = _session(requested="2026-08-26")
    out = _fix("Tuesday 26th August is fully booked", s)
    assert "26th August" in out
    assert "25th" not in out and "1st September" not in out


# ---------------------------------------------------------------------------
# Phrasings the model actually produces
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("said,want", [
    ("Tuesday 26th August",        "Wednesday 26th August"),
    ("Tuesday the 26th of August", "Wednesday the 26th of August"),
    ("Tuesday 26 August",          "Wednesday 26 August"),
    ("tuesday 26th august",        "Wednesday 26th august"),
    ("TUESDAY 26TH AUGUST",        "Wednesday 26TH AUGUST"),
])
def test_phrasings(said, want):
    assert _fix(said, _session(requested="2026-08-26")) == want


def test_a_correct_weekday_is_left_alone():
    s = _session(dates=["2026-08-27"])
    text = "Thursday 27th August at half past seven"
    assert _fix(text, s) == text


# ---------------------------------------------------------------------------
# Deny by default — the failure mode of a rewriter is rewriting the truth
# ---------------------------------------------------------------------------
def test_an_unknown_date_is_left_alone():
    """No year is guessed. If the session does not know the date, the model's
    wording stands — saying nothing beats being confidently wrong."""
    text = "Monday 3rd March is quiet"
    assert _fix(text, _session(dates=["2026-08-27"])) == text


def test_an_ambiguous_day_and_month_is_left_alone():
    """Two known dates sharing a day and month (different years) cannot be
    disambiguated from the spoken form, so nothing is touched."""
    s = _session(dates=["2026-08-26", "2027-08-26"])
    text = "Tuesday 26th August"
    assert _fix(text, s) == text


def test_text_with_no_date_is_untouched():
    text = "That's the only slot on that day - five in the evening. Does that work?"
    assert _fix(text, _session(dates=["2026-08-27"])) == text


def test_a_weekday_with_no_date_is_untouched():
    """'We're open Thursday' carries no date to check it against."""
    text = "We're open on Thursday and Friday."
    assert _fix(text, _session(dates=["2026-08-26"])) == text


# ---------------------------------------------------------------------------
# It runs on a live call, so it must never be the thing that kills the turn
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    {}, {"available_days": None}, {"available_days": "nonsense"},
    {"available_days": [{"date": "not-a-date"}]}, {"available_days": [None]},
    {"requested_day_iso": 12345}, {"available_days": [{}]},
])
def test_a_broken_session_returns_the_text_unchanged(bad):
    text = "Tuesday 26th August is fully booked"
    assert _fix(text, bad) == text


@pytest.mark.parametrize("bad", [None, "", 123])
def test_bad_text_is_survivable(bad):
    assert _fix(bad, _session(requested="2026-08-26")) == bad


def test_it_is_idempotent():
    """It runs on both the slot path and the gate chain; a second pass over
    already-corrected text must be a no-op."""
    s = _session(requested="2026-08-26")
    once = _fix("Tuesday 26th August is fully booked", s)
    assert _fix(once, s) == once


# ---------------------------------------------------------------------------
# Wiring — the function existing is not the fix
# ---------------------------------------------------------------------------
def test_the_gate_chain_calls_it():
    import inspect
    from app.media_streams import turn_handler as th
    src = inspect.getsource(th.sanitise_response)
    assert "_correct_weekday_against_known_dates" in src


def test_the_slot_buffer_calls_it():
    """The slot presentation is where the defect fired. sanitise_response runs
    per STREAMED chunk there, so a date split across two chunks would never
    match — the assembled text needs its own pass."""
    import inspect
    from app.media_streams import llm_stream as ls
    src = inspect.getsource(ls)
    assert "_correct_weekday_against_known_dates" in src


def test_the_requested_day_is_stashed_for_the_guard():
    """The requested day is EMPTY by definition here, so it is not in
    available_days. Without this the headline sentence is unreachable."""
    import inspect
    from app.tools import receptionist_tools as rt
    src = inspect.getsource(rt._exec_check_availability)
    assert 'session["requested_day_iso"]' in src
