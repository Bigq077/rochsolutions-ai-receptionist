# tests/regression/test_location_defaults_to_primary_site.py
"""Owner decision 2026-08-06 — an unresolvable clinic answer defaults to the
primary site instead of sending the caller to the keypad.

This REVERSES the "TWO RUNGS ONLY / straight to the keypad" instruction of
2026-08-04, whose reasoning is still recorded at the keypad site in
connection.py. Read that comment before changing this back.

What changed is the evidence. On the first day of real traffic the clinic
question was the single biggest source of friction — four of five booking
attempts reached the keypad ladder, each costing 10-25 seconds, and callers
answered it with 'ofter', 'okej', "i'll send me" and 'uh your oosterkliniek'.

The failure is asymmetric: "Redditch" is phonetically distinctive and survives
STT ('gedditch' resolved correctly at 17:14:03), while "Alcester" is the one
STT destroys. So an unresolvable answer is far more likely to have been the
primary site than the secondary one.

Two things make the reversal safe, and both are pinned here:

  * the default is CONFIG-DRIVEN, not a clinic name in engine code — it is the
    first bookable entry in clinic.json's `locations`;
  * the default is AUDIBLE when there is anywhere else to go. A silent default
    would send a garbled Redditch caller twenty miles to the wrong site, which
    is exactly what the 08-04 instruction was protecting against.

For theorem_v3 today Redditch is bookable=False, so Alcester is the only
bookable site and there is no wrong-clinic outcome to protect against at all —
the correction phrase is inert here and the ack stays the plain "Alcester." it
already was. It arms itself automatically for any future clinic with two live
sites, which is why it is written generically rather than skipped.
"""

import inspect

import pytest

from app.media_streams import connection as conn_mod


# ---------------------------------------------------------------------------
# The default comes from config, not from a clinic name in engine code
# ---------------------------------------------------------------------------

def test_primary_location_is_the_first_bookable_site_in_config():
    assert conn_mod._primary_location("theorem_v3") == "alcester"


def test_unknown_clinic_yields_no_default():
    """No config, no defaulting — the caller must be asked rather than guessed
    at. The call site treats "" as 'do not default'."""
    assert conn_mod._primary_location("no_such_clinic") == ""
    assert conn_mod._primary_location("") == ""


def test_primary_location_skips_a_non_bookable_site(monkeypatch):
    """If the first listed location is closed to booking, the default must move
    on rather than sending every caller to a site we cannot book."""
    import app.clinic_config as cfg

    monkeypatch.setattr(
        cfg, "THEOREM_LOCATIONS",
        {"alcester": {"bookable": False}, "redditch": {"bookable": True}},
        raising=False,
    )
    assert conn_mod._primary_location("theorem_v3") == "redditch"


# ---------------------------------------------------------------------------
# The audible-correction path
# ---------------------------------------------------------------------------

def test_redditch_is_not_currently_bookable():
    """Pins the fact the safety argument rests on. If Redditch is ever opened
    for booking, the correction phrase starts firing — and the reasoning in
    this file's docstring needs re-reading, because a wrong-clinic outcome
    becomes possible again."""
    from app.clinic_config import THEOREM_LOCATIONS
    assert THEOREM_LOCATIONS["redditch"]["bookable"] is False
    assert conn_mod._other_bookable_locations("theorem_v3", "alcester") == []


def test_correction_phrase_arms_when_there_is_somewhere_else_to_go(monkeypatch):
    """The generic case: two live sites means the caller must be offered the
    other one by name."""
    import app.clinic_config as cfg

    monkeypatch.setattr(
        cfg, "THEOREM_LOCATIONS",
        {"alcester": {"bookable": True}, "redditch": {"bookable": True}},
        raising=False,
    )
    assert conn_mod._other_bookable_locations("theorem_v3", "alcester") == ["Redditch"]


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def _loop_src():
    return inspect.getsource(conn_mod.WebSocketCallHandler._llm_loop)


def test_unresolved_answer_defaults_instead_of_arming_the_keypad():
    src = _loop_src()
    assert "_primary_location(" in src, (
        "an unresolvable clinic answer no longer defaults to the primary site"
    )


def test_questions_are_excluded_from_defaulting():
    """"What's the difference between the clinics?" is not a garbled answer.
    Defaulting on a question would book the caller somewhere in response to
    them asking for information."""
    src = _loop_src()
    i = src.index("_primary_location(")
    window = src[max(0, i - 400):i]
    assert "_transcript_is_question(utterance)" in window, (
        "the default must be gated on the utterance not being a question"
    )


def test_default_is_decided_before_the_bookable_guard_runs():
    """Order matters: a defaulted site still has to pass the not-bookable
    redirect, so the assignment must sit upstream of the guard that follows it.

    Compares against the NEXT bookable check after the default, not the first
    one in the method — an earlier guard already covers the caller-said-alias
    path and legitimately precedes this code.
    """
    src = _loop_src()
    default_at = src.index("_primary_location(")
    guard_after = src.index("_location_is_bookable(", default_at)
    assert default_at < guard_after


def test_defaulting_is_announced_to_the_caller():
    """The whole safety argument is that the caller hears which clinic they
    were put down for and can correct it in one word."""
    src = _loop_src()
    assert "_loc_defaulted" in src
    i = src.index("if _loc_defaulted:")
    # Generous window: this arm sits ~44 columns deep, so most of each line is
    # indentation.
    arm = src[i:i + 2500]
    assert "_other_bookable_locations(" in arm
    assert "rather that one" in arm


def test_question_path_to_the_llm_is_untouched():
    """Questions must still reach the LLM rather than being defaulted."""
    src = _loop_src()
    assert "question detected," in src
