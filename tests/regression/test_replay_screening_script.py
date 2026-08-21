# tests/regression/test_replay_screening_script.py
"""Tests for scripts/replay_screening.py — the offline screening harness.

The harness exists to answer "does Layer 1 arm when a physiotherapist would?"
against stored calls rather than by guesswork. That answer is worthless, and
worse than worthless because it looks quantitative, if the corpus it measures is
a round of our own test calls.

Measured on jv_v1, 2026-08-21: of 214 stored calls, 204 come from two dev
handsets, and of the 38 that touch a screen, 37 do. The plan called for
splitting by build_sha branch membership; that axis cannot do it —

    * 77 of the 214 calls (36%) carry no build_sha at all;
    * 50 of the 58 shas present ARE on a JV live branch, because the demo line
      runs the same builds.

Nor can the number dialled: both Susie lines are rung by the same handsets. The
caller is the discriminator, which is what audience_of uses.

These tests pin the split and the reporting of it. They deliberately do NOT
touch the obs store — the harness's corpus access is exercised by running it,
and a test that needs a database is a test that gets skipped.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path("scripts/replay_screening.py").resolve()


@pytest.fixture(scope="module")
def rs():
    spec = importlib.util.spec_from_file_location("replay_screening", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["replay_screening"] = module
    spec.loader.exec_module(module)
    return module


# ── the split ─────────────────────────────────────────────────────────────────

def test_known_dev_handsets_classify_as_test(rs):
    for number in rs._TEST_CALLERS:
        assert rs.audience_of({"caller_number": number}) == "test", number


def test_an_unknown_number_classifies_as_real(rs):
    assert rs.audience_of({"caller_number": "+441204000000"}) == "real"


def test_a_withheld_number_classifies_as_real(rs):
    """A withheld caller ID must not be swept into 'test'.

    'real' is the RESIDUAL — it means "not from a handset we know is ours",
    never a positive identification of a patient. Defaulting the unknown case to
    'test' would quietly delete real calls from every figure the harness prints.
    """
    assert rs.audience_of({"caller_number": None}) == "real"
    assert rs.audience_of({}) == "real"


def test_the_test_caller_set_can_be_extended_per_run(rs):
    extra = rs._TEST_CALLERS | {"+441204000000"}
    assert rs.audience_of({"caller_number": "+441204000000"}, extra) == "test"
    # ...without mutating the module default.
    assert rs.audience_of({"caller_number": "+441204000000"}) == "real"


def test_the_dialled_number_is_not_used_to_split(rs):
    """Both Susie lines are rung by the same handsets, so the line a call came
    in on says nothing about whether it was a test."""
    demo = {"caller_number": "+441204000000", "dialled_number": "+447366263180"}
    live = {"caller_number": "+441204000000", "dialled_number": "+447367002651"}
    assert rs.audience_of(demo) == rs.audience_of(live) == "real"


def test_both_susie_lines_are_labelled(rs):
    assert rs._SUSIE_LINES["+447366263180"] == "demo"
    assert rs._SUSIE_LINES["+447367002651"] == "live"


def _arm(screen: str = "cauda_equina") -> dict:
    """An arm event shaped exactly as replay_call emits one."""
    return {"kind": "arm", "screen": screen, "path": "trigger",
            "utterance": "my back is agony"}


# ── provenance reaches the output ─────────────────────────────────────────────

def test_replay_call_records_provenance(rs):
    """--json consumers need to re-derive the split without the report."""
    call = {
        "call_sid": "CAtest", "clinic_id": "jv_v1",
        "caller_number": "+33617769867", "dialled_number": "+447366263180",
        "transcript": [], "screening": None,
    }
    out = rs.replay_call(call, {"clinical_screening": {"enabled": True, "screens": []}})
    assert out["audience"] == "test"
    assert out["line"] == "demo"
    assert out["caller_number"] == "+33617769867"


def test_an_unrecognised_line_is_marked_not_guessed(rs):
    call = {
        "call_sid": "CAtest", "clinic_id": "jv_v1",
        "caller_number": "+441204000000", "dialled_number": "+441111111111",
        "transcript": [], "screening": None,
    }
    out = rs.replay_call(call, {"clinical_screening": {"enabled": True, "screens": []}})
    assert out["line"] == "?"
    assert out["audience"] == "real"


def test_report_warns_when_no_armed_call_is_real(rs, capsys):
    """The headline failure this harness exists to prevent.

    A trigger tuned to a corpus of our own test calls will look measured and be
    fiction. If every armed call is from a dev handset, the report must say so
    rather than printing a confident table.
    """
    results = [
        {"call_sid": "CA1", "audience": "test", "line": "demo",
         "events": [_arm()]},
        {"call_sid": "CA2", "audience": "real", "line": "live", "events": []},
    ]
    summary = rs.summarise(results)
    rs._print_report(results, summary)
    out = capsys.readouterr().out
    assert "test 1 / real 1" in out
    assert "no real traffic in this corpus" in out


def test_report_is_quiet_when_a_real_call_armed(rs, capsys):
    results = [
        {"call_sid": "CA1", "audience": "real", "line": "live",
         "events": [_arm()]},
    ]
    rs._print_report(results, rs.summarise(results))
    out = capsys.readouterr().out
    assert "no real traffic in this corpus" not in out


# ── the branch filter, and what it silently drops ─────────────────────────────

def test_sha_on_branch_drops_calls_with_no_sha_and_says_so(rs, capsys):
    """36% of the corpus has no build_sha. A corpus that quietly shrinks by a
    third is how a replay comes to measure something other than what was asked,
    so the drop is reported on stderr rather than swallowed."""
    calls = [{"call_sid": "CA1", "build_sha": ""}, {"call_sid": "CA2"}]
    kept = rs._filter_sha_on_branch(calls, "HEAD")
    assert kept == []
    assert "carrying no build_sha" in capsys.readouterr().err


def test_sha_on_branch_keeps_an_ancestor_of_the_ref(rs):
    import subprocess
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=rs._ROOT, text=True).strip()
    kept = rs._filter_sha_on_branch([{"call_sid": "CA1", "build_sha": sha}], "HEAD")
    assert [c["call_sid"] for c in kept] == ["CA1"]


def test_sha_on_branch_rejects_a_sha_not_on_the_ref(rs):
    kept = rs._filter_sha_on_branch(
        [{"call_sid": "CA1", "build_sha": "0" * 40}], "HEAD")
    assert kept == []
