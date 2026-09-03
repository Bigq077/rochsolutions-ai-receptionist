"""The generated-diary sweep runs in the suite, not only by hand.

`scripts/sweep_slot_offer.py` is Phase 1b of the convergence plan: instead of
replaying a corpus that does not hold availability payloads, it GENERATES the
payload space and crosses it with generated caller utterances, then asserts
invariants that must hold of every offer.

WHY IT IS A TEST AND NOT JUST A SCRIPT. Three defects in the week to
2026-09-03 were found by a phone call and none by the suite. A script that has
to be remembered is a script that gets run after the defect ships. This runs
the bounded sweep on every commit.

PROVEN TO FAIL. Neutering `_time_contradicts` -- restoring the 8pm defect --
makes the sweep report 68 MERIDIEM violations across 24 diaries, including the
live log line verbatim:

    'yeah monday at 8 pm works' -> 2026-09-07T08:00:00+01:00

That defect reached two real callers before a phone call found it. The sweep
finds it offline, in seconds, on a diary shape the corpus does not contain.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sweep_slot_offer.py"


def _load():
    spec = importlib.util.spec_from_file_location("sweep_slot_offer", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_sweep_script_is_present_and_importable():
    assert _SCRIPT.exists(), f"{_SCRIPT} is gone — Phase 1b has no harness"
    assert _load() is not None


def test_no_invariant_is_violated_across_the_generated_diaries():
    """The bounded sweep. Any violation is a real defect or a harness bug, and
    both need looking at before the change ships."""
    sweep = _load()

    failures = []
    swept = 0
    for name, days in sweep.diaries(quick=True):
        swept += 1
        for rule, detail in sweep.violations_for(days, quick=True):
            failures.append(f"[{rule}] {name}: {detail}")

    assert swept >= 20, f"the sweep only generated {swept} diaries"
    assert not failures, (
        f"{len(failures)} invariant violation(s) across {swept} generated "
        f"diaries:\n  " + "\n  ".join(failures[:20])
    )


def test_the_generated_space_contains_the_shapes_that_broke_us():
    """A generator is only as good as its space.

    Each of these is a shape a real defect needed, and `am_and_pm_8` is the one
    the CORPUS DOES NOT CONTAIN -- two labels an hour apart in name that fold to
    the same digit, where the meridiem is the only thing separating them. The
    first version of the meridiem guard was wrong on exactly that day and no
    replay could have found it.
    """
    sweep = _load()
    assert "am_and_pm_8" in sweep.DAY_SHAPES
    assert "08:00" in sweep.DAY_SHAPES["am_and_pm_8"]
    assert "20:00" in sweep.DAY_SHAPES["am_and_pm_8"]
    # A full day, so "presented != bookable" is exercised.
    assert len(sweep.DAY_SHAPES["full_day"]) >= 10
    # A day with one slot, where a band-contradicting pick must still decline.
    assert len(sweep.DAY_SHAPES["single_slot"]) == 1


def test_the_sweep_can_actually_fail(monkeypatch):
    """A harness that only ever reports zero is worthless.

    Restore the 8pm defect by blinding the meridiem guard and confirm the sweep
    reports it. This is the sensitivity check the whole file rests on.
    """
    sweep = _load()
    from app.tools import slot_followup

    monkeypatch.setattr(slot_followup, "_time_contradicts", lambda text, start: False)

    hits = []
    for name, days in sweep.diaries(quick=True):
        for rule, detail in sweep.violations_for(days, quick=True):
            if rule == "MERIDIEM":
                hits.append(detail)

    assert hits, (
        "the sweep reported nothing with the meridiem guard disabled — it "
        "cannot detect the defect it was built for"
    )
    assert any("08:00:00" in h and "said 20" in h for h in hits), (
        f"the 8pm case specifically was not reported; got {hits[:3]}"
    )
