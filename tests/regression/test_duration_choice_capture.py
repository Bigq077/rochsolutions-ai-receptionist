# tests/regression/test_duration_choice_capture.py
"""
The caller's session length is captured deterministically, not carried by the LLM.

`CA86c320ef036b39946ed0bbc47d2b0c14`, 4 Aug 2026, Vital Edge, LIVE, build
`205c257`:

    15:01:14  tts   "would you like a 60-minute session at one hundred and
                     twenty five ... or a 90-minute at one hundred and eighty?"
    15:01:25  FINAL 'um £180'                        <- the 90-minute option
    15:01:27  tts   "Right — do you have a preference for when you'd like to
                     come"                           <- choice never acknowledged
    15:01:43  check_availability  {service: deep_tissue_massage, ...}   <- no duration
    15:03:40  book_appointment    {..., "duration_minutes": 60}         <- WRONG

The caller expected 90 minutes at £180. The owner was notified of 60. The price
gap is £55 and nothing in the system disagreed with itself, because:

  * `_service_duration_minutes` falls back to `opts[0]` — the SHORTEST — when no
    choice has been captured, and 60 is a legitimate option, so the resolver
    honoured it without complaint;
  * `_service_duration_choice` is set ONLY from a tool argument the model
    passes, so the choice existed nowhere except the model's context across
    eight turns and two minutes;
  * the caller answered with a PRICE, which nothing mapped to a length.

Compounding: that resolver is, in its own words, "THE single source of truth for
BOTH the slot grid (check_availability) and the calendar-event length" — so
every slot offered on that call was computed on the 60-minute grid too.

**Not a Vital Edge bug.** `receptionist_tools.py` is one blob shared by every
branch, and `jv_v1`'s Sports Massage is `[30, 60]` at £40/£55 — a JV caller who
chooses 60 and is not captured is silently booked for 30.

What this file pins:
  1. the live utterance now yields 90, for both clinics' real configs
  2. answers by price count, because that is what the question invites
  3. it does NOT fire on dates, times, or ordinary speech — a false positive
     books the wrong length at the wrong price, i.e. re-creates the defect
  4. ambiguity captures nothing rather than guessing
  5. the capture actually reaches `_service_duration_minutes`, which is the
     only thing that makes any of the above matter
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.tools.receptionist_tools import (
    _options_services,
    _service_duration_minutes,
    duration_choice_from_utterance,
)

VE = "vital_edge"
JV = "jv_v1"


def _clinic(cid):
    return get_clinic(cid)


# ── 1. the live call ───────────────────────────────────────────────────────

def test_the_verbatim_utterance_from_the_live_call_is_captured():
    """'um £180' — exactly as STT delivered it on CA86c320ef."""
    assert duration_choice_from_utterance(_clinic(VE), "um £180") == 90, (
        "the caller's own words still do not yield 90 minutes — this is "
        "CA86c320ef unfixed, and it books £125 for an £180 session"
    )


def test_the_captured_choice_reaches_the_resolver():
    """Capture is worthless unless _service_duration_minutes honours it. This is
    the join the original defect fell through."""
    clinic = _clinic(VE)
    captured = duration_choice_from_utterance(clinic, "um £180")
    assert _service_duration_minutes(
        clinic, "deep_tissue_massage", 60, preferred=captured
    ) == 90


def test_without_a_capture_the_resolver_still_defaults_short():
    """The behaviour that caused it, pinned deliberately. If this ever changes
    to re-ask instead, that is a real improvement — and it must be a decision
    with its own measurement, not a side effect."""
    assert _service_duration_minutes(
        _clinic(VE), "deep_tissue_massage", 60, preferred=None
    ) == 60


# ── 2. the forms a caller actually uses ────────────────────────────────────

@pytest.mark.parametrize("utterance,expected", [
    ("um £180", 90),
    ("180", 90),
    ("£125", 60),
    ("90", 90),
    ("ninety minutes", 90),
    ("60 minutes", 60),
    ("the 90 minute one", 90),
    ("an hour and a half", 90),
    ("an hour", 60),
    ("hour and a half please", 90),
])
def test_vital_edge_answer_forms(utterance, expected):
    assert duration_choice_from_utterance(_clinic(VE), utterance) == expected


@pytest.mark.parametrize("utterance,expected", [
    ("60 please", 60),
    ("£55", 60),
    ("30 minutes", 30),
    ("half an hour", 30),
    ("£40", 30),
])
def test_jv_answer_forms(utterance, expected):
    """Different options and different prices — the mapping is derived from each
    clinic's own config, never hardcoded."""
    assert duration_choice_from_utterance(_clinic(JV), utterance) == expected


def test_longer_phrases_win_over_the_shorter_ones_they_contain():
    """'an hour and a half' CONTAINS 'an hour'; 'half an hour' does too. Scored
    independently both read as ambiguous 60-or-90 and captured nothing — the
    safe failure, but the wrong answer."""
    assert duration_choice_from_utterance(_clinic(VE), "an hour and a half") == 90
    assert duration_choice_from_utterance(_clinic(JV), "half an hour") == 30


# ── 3. false positives are the dangerous direction ─────────────────────────

@pytest.mark.parametrize("utterance", [
    "friday the 30th of august",      # a DATE — 30 is in jv_v1's options
    "can i come at 11",
    "yeah go for it",
    "quentin rook",
    "just general stress",
    "anytime next week",
    "",
    None,
])
def test_ordinary_speech_captures_nothing(utterance):
    """A false capture books the wrong length at the wrong price — precisely the
    defect being closed. Bias runs the wrong way, so this list is the guard."""
    for cid in (VE, JV):
        assert duration_choice_from_utterance(_clinic(cid), utterance) is None


def test_an_ambiguous_answer_captures_nothing():
    """Naming both is not a choice. Asking again costs a turn; guessing costs
    £55 and a wrong appointment length."""
    assert duration_choice_from_utterance(
        _clinic(VE), "i said 60 or was it 90"
    ) is None


def test_a_clinic_with_no_options_service_never_captures():
    """theorem and demo have no length-choice service. The extractor must be
    inert for them rather than inventing a preference from a stray number."""
    for cid in ("theorem", "demo"):
        clinic = _clinic(cid)
        assert _options_services(clinic) == []
        assert duration_choice_from_utterance(clinic, "90 minutes") is None


# ── 4. the wiring — without it every test above passes vacuously ───────────
#
# The extractor is inert unless connection.py actually calls it on each
# utterance. Measured on the B-36 R6 fix earlier the same day: a test file that
# exercised the helper directly stayed fully green while the call site was
# reverted and the fix never reached a caller. Same technique as
# test_b44_ambiguous_lookup_readback_rule uses on _execute_tools.


def test_the_capture_writes_the_key_the_resolver_reads():
    """The whole join, on a real session dict."""
    from app.media_streams.connection import capture_duration_choice
    session = {"clinic_id": VE}
    assert capture_duration_choice(session, "um £180") == 90
    assert session["_service_duration_choice"] == 90
    assert _service_duration_minutes(
        _clinic(VE), "deep_tissue_massage", 60,
        preferred=session.get("_service_duration_choice"),
    ) == 90


def test_the_capture_does_not_overwrite_an_existing_choice():
    """A caller who chooses 90 and then discusses times must not have it
    re-derived from a later utterance. A real change of mind arrives as an
    explicit duration_minutes at the tool and overwrites there."""
    from app.media_streams.connection import capture_duration_choice
    session = {"clinic_id": VE, "_service_duration_choice": 90}
    assert capture_duration_choice(session, "£125") is None
    assert session["_service_duration_choice"] == 90


@pytest.mark.parametrize("utterance", ["yeah go for it", "", None])
def test_the_capture_is_silent_on_ordinary_speech(utterance):
    from app.media_streams.connection import capture_duration_choice
    session = {"clinic_id": VE}
    assert capture_duration_choice(session, utterance) is None
    assert "_service_duration_choice" not in session


def test_the_capture_never_raises():
    """A failure here must cost the caller a re-ask, not the call."""
    from app.media_streams.connection import capture_duration_choice
    for session in ({}, {"clinic_id": None}, {"clinic_id": "no-such-clinic"},
                    {"clinic_id": "theorem_v3"}):
        assert capture_duration_choice(session, "90 minutes") is None


def test_the_capture_is_wired_into_the_turn_loop():
    """Extraction makes the behaviour testable; this pins that it is still
    CALLED. Together they cover both failure modes — a broken helper and an
    orphaned one."""
    import inspect
    from app.media_streams import connection as conn
    assert "capture_duration_choice(self.session, utterance)" in inspect.getsource(conn), (
        "the extractor is orphaned — every behavioural test above now passes "
        "while the choice never reaches a real call"
    )


# ── 5. the configs this all rests on ───────────────────────────────────────

def test_the_options_services_are_where_the_code_expects():
    """If a clinic's options move or its pricing keys are renamed, the capture
    silently stops working and the shortest option quietly wins again. Fail
    here instead."""
    ve = {s["service_id"]: s for s in _options_services(_clinic(VE))}
    assert set(ve) == {"deep_tissue_massage", "sports_massage"}
    for s in ve.values():
        assert [int(o) for o in s["typical_duration_minutes_options"]] == [60, 90]
        assert s["pricing"]["60min_in_clinic_gbp"] == 125
        assert s["pricing"]["90min_in_clinic_gbp"] == 180

    jv = {s["service_id"]: s for s in _options_services(_clinic(JV))}
    assert "sports_massage" in jv
    assert [int(o) for o in
            jv["sports_massage"]["typical_duration_minutes_options"]] == [30, 60]
