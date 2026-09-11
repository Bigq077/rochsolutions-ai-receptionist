"""
Regression: the spec scorer must not rot silently.

`scripts/score_slot_spec.py` is a REPORTER, not a gate, so this test does NOT
assert that the engine passes every row -- that would turn the backlog into a
red suite and the reporter into something people switch off. The analysis §1.3
records three separate tests that pinned N1 as correct behaviour precisely
because they asserted current behaviour as a contract; this file exists to avoid
being a fourth.

What it asserts is that the harness still WORKS: it imports, every row executes,
and no row raises. A scorer that silently stopped reaching the engine would read
as "0 FAIL" -- indistinguishable from success, and the most expensive possible
failure for a measurement tool.

The one substantive assertion is that a row which cannot be scored says so.
UNREACHABLE must never collapse into PASS: N6 is invisible to this harness
(the defect is in the routing, and the rows call producers directly), and if
DT-14 ever starts reporting PASS then the only thing catching N6 -- a phone
call -- has been quietly retired.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "score_slot_spec.py"


@pytest.fixture(scope="module")
def scorer():
    spec = importlib.util.spec_from_file_location("score_slot_spec", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_harness_still_reaches_the_engine(scorer):
    results = scorer.run(None, None)
    assert results, "no rows ran at all"
    # A row that raised is reported as FAIL with the exception text. Those are
    # harness breakage, not engine defects, and they are what this test is for.
    raised = [r for r in results if r[2] == scorer.FAIL and "raised " in r[3]]
    assert not raised, f"rows raised rather than answering: {raised}"


def test_every_shape_and_row_is_exercised(scorer):
    results = scorer.run(None, None)
    seen_rows = {r[0] for r in results}
    seen_shapes = {r[1] for r in results}
    assert seen_rows == set(scorer.ROWS)
    assert seen_shapes == set(scorer.SHAPES)


def test_a_row_that_cannot_be_scored_says_so_and_never_passes(scorer):
    """An UNREACHABLE verdict always carries a reason, and is never counted
    as a pass. Until 11 Sep this pinned DT-14 as the example -- "N6 lives in
    the routing, the harness cannot see it". Half true: the routing was
    `try_unspoken_followup_speech`, which the harness drives, and once DT-14
    was driven that way it reproduced N6 on the first run and now scores it.
    The property outlives the example."""
    unreachable = [r for r in scorer.run(None, None) if r[2] == scorer.UNREACHABLE]
    assert unreachable, "no row is unreachable on any shape -- suspicious"
    for r in unreachable:
        assert r[3].strip(), f"{r[0]} on {r[1]}: UNREACHABLE with no reason"


def test_n6_is_now_scored_not_declared_invisible(scorer):
    """DT-14 drives the dispatcher and passes on the demo line's shape."""
    results = {r[1]: r[2] for r in scorer.run("DT-14", None)}
    assert results["uniform50"] == scorer.PASS, results


def test_every_diary_shape_builds_a_payload_the_engine_can_read(scorer):
    """Five of the six shapes have never been used by a phone verification, so
    a malformed one would quietly make its rows unreachable rather than fail."""
    from app.tools.slot_followup import flatten_bookable_slots

    for name, build in scorer.SHAPES.items():
        days = build()
        flat = flatten_bookable_slots(days)
        assert flat, f"{name} flattens to nothing"
        for slot in flat:
            assert slot["date"], f"{name}: slot with no date"
            assert slot["time"], f"{name}: slot with no time"
            assert slot["spoken"], f"{name}: slot with no spoken label"
            assert slot["start"].startswith(slot["date"]), f"{name}: start/date"
