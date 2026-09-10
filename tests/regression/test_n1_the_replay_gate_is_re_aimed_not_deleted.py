# tests/regression/test_n1_the_replay_gate_is_re_aimed_not_deleted.py
"""
N1 - `replay_presented_times`' third gate forbade the fix, so it was RE-AIMED.

`re_offered_a_heard_time MUST be 0` counted any selection that put back a time
already read out ON THAT DAY. That is verbatim what N1's fix does for a caller
who asks "what about Monday" -- so the gate, as written, would have read red on
the first corpus that contained a named-day readout, and the next reader would
have reverted a correct change.

This is the third measurement in the project to encode a rule a later defect
proved too broad (rev. 6 section 7): "find what the assertion was protecting,
assert that, and keep the old number visible". So:

  * `re_offered` still gates -- for every row that is not a named-day readout;
  * named-day rows get their own gate, `named_day_withheld_all`, which is the
    N1 defect itself and MUST be 0;
  * what they kept is printed, not gated.

And the harness must replay a named-day row down the path that ANSWERED it, or
it is blind: replayed without `named_day`, those rows read UNCHANGED while the
live readout changed -- "a harness that said CHANGED: 0 while the live readout
was broken" is section 5's own warning about this very script.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "replay_presented_times.py"

MON, TUE, WED = "2026-09-14", "2026-09-15", "2026-09-16"
GRID = [
    "08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
    "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
]


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("replay_presented_times", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload():
    return [{"date": d, "slot_times": list(GRID)} for d in (MON, TUE, WED)]


def _offer(chunk, starts, **extra):
    return {
        "payload": _payload(),
        "offer": {"chunks": [chunk], "slots": [{"start": s} for s in starts]},
        **extra,
    }


def _row(producer):
    """A stored call: the spread, then a producer readout of Monday."""
    spread = _offer("I've got a few days", [
        f"{MON}T08:00:00+01:00", f"{MON}T17:10:00+01:00",
        f"{TUE}T08:50:00+01:00", f"{TUE}T16:20:00+01:00",
        f"{WED}T09:40:00+01:00", f"{WED}T15:30:00+01:00",
    ], seq=0, source="gate5", producer="")
    monday = _offer("Monday 14th September", [
        f"{MON}T08:00:00+01:00", f"{MON}T14:40:00+01:00", f"{MON}T17:10:00+01:00",
    ], seq=1, source="producer", **({"producer": producer} if producer is not None else {}))
    transcript = [
        {"role": "assistant", "text": "I've got a few days - Number 1 ..."},
        {"role": "user", "text": "um what about monday"},
        {"role": "assistant", "text": "Monday 14th September - eight in the morning ..."},
    ]
    return ("CAtest", "northgate", [spread, monday], transcript)


def _rec(**kw):
    base = {
        "call": "CAx", "clinic": "northgate", "seq": 1, "date": MON, "limit": 3,
        "named_day": False, "day_times": list(GRID), "heard_day": True,
        "any_heard": True, "heard_clocks": ["08:00", "17:10"],
        "heard_clocks_this_day": ["08:00", "17:10"], "stored": [],
    }
    base.update(kw)
    return base


def _diff(harness, tmp_path, base, cand):
    b, c = tmp_path / "base.json", tmp_path / "cand.json"
    b.write_text(json.dumps(base))
    c.write_text(json.dumps(cand))
    return harness.diff(str(b), str(c))


# ---------------------------------------------------------------------------
# collect -- replays the path that answered
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("producer", ["D-B", "B-145"])
def test_a_named_day_row_is_replayed_as_a_named_day(harness, producer):
    records = [r for r in harness.collect([_row(producer)]) if r["seq"] == 1]
    monday = next(r for r in records if r["date"] == MON)

    assert monday["named_day"] is True, monday
    assert {"08:00", "17:10"} <= set(monday["chosen"]), monday


def test_only_the_day_it_spoke_is_named(harness):
    """The other days of the same payload were not what the caller asked about."""
    records = [r for r in harness.collect([_row("D-B")]) if r["seq"] == 1]

    assert {r["date"]: r["named_day"] for r in records} == {
        MON: True, TUE: False, WED: False,
    }


@pytest.mark.parametrize("producer", [None, "", "gate5-ish", "more_days"])
def test_a_row_without_a_named_day_producer_replays_as_before(harness, producer):
    """Forward-only field: absent means unknown and is replayed exactly as it
    was before this commit -- down the 'what else' path, heard times withheld."""
    records = [r for r in harness.collect([_row(producer)]) if r["seq"] == 1]
    monday = next(r for r in records if r["date"] == MON)

    assert monday["named_day"] is False, monday
    assert not {"08:00", "17:10"} & set(monday["chosen"]), monday


# ---------------------------------------------------------------------------
# diff -- the re-aimed gates
# ---------------------------------------------------------------------------
def test_a_what_else_row_that_re_offers_still_fails(harness, tmp_path):
    """The rule the gate was protecting is still enforced where it applies."""
    base = [_rec(chosen=["10:30", "11:20", "14:40"])]
    cand = [_rec(chosen=["08:00", "11:20", "14:40"])]

    assert _diff(harness, tmp_path, base, cand) == 1


def test_a_named_day_row_that_keeps_its_offered_times_passes(harness, tmp_path, capsys):
    base = [_rec(named_day=True, chosen=["10:30", "11:20", "14:40"])]
    cand = [_rec(named_day=True, chosen=["08:00", "14:40", "17:10"])]

    assert _diff(harness, tmp_path, base, cand) == 0
    out = capsys.readouterr().out
    assert "named-day kept offered" in out and "1" in out.split("named-day kept offered")[1][:12]


def test_a_named_day_row_that_withholds_them_all_fails(harness, tmp_path):
    """N1 itself, as a gate. This is the 21:57 readout."""
    base = [_rec(named_day=True, chosen=["08:00", "14:40", "17:10"])]
    cand = [_rec(named_day=True, chosen=["10:30", "11:20", "14:40"])]

    assert _diff(harness, tmp_path, base, cand) == 1


def test_a_named_day_heard_in_full_is_not_counted_either_way(harness, tmp_path):
    """Once `limit` or more were heard on the day, the fix stands down, and the
    gate must not demand what the fix deliberately does not do."""
    heard = ["08:00", "10:30", "13:00", "17:10"]
    base = [_rec(named_day=True, heard_clocks_this_day=heard, chosen=["08:50", "12:10", "15:30"])]
    cand = [_rec(named_day=True, heard_clocks_this_day=heard, chosen=["08:50", "12:10", "16:20"])]

    assert _diff(harness, tmp_path, base, cand) == 0


def test_lost_and_invented_still_gate_named_day_rows(harness, tmp_path):
    """N1 re-aims ONE gate. The safety line -- a slot lost, a slot invented --
    is untouched and applies to every row."""
    base = [_rec(named_day=True, chosen=["08:00", "14:40", "17:10"])]
    lost = [_rec(named_day=True, chosen=["08:00", "17:10"])]
    invented = [_rec(named_day=True, chosen=["08:00", "14:45", "17:10"])]

    assert _diff(harness, tmp_path, base, lost) == 1
    assert _diff(harness, tmp_path, base, invented) == 1


def test_the_gate_is_still_documented_as_a_gate(harness):
    """Re-aimed, never deleted: the module still states it, and says why it moved."""
    doc = harness.__doc__ or ""
    assert "re_offered_a_heard_time" in doc
    assert "named_day_withheld_all" in doc
    assert "N1" in doc
