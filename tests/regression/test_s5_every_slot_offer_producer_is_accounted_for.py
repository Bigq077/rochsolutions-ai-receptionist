# tests/regression/test_s5_every_slot_offer_producer_is_accounted_for.py
"""
S-5 - five producers call `build_slot_offer`, four honoured the selection rule,
and nothing enforced it.

`build_slot_offer`'s docstring has stated the trim contract since it was
written:

    "PASS `more_times` when the days handed in have ALREADY been trimmed ...
     `choose_presented_indices` ... knowledge this function does not have and
     must not overrule."

It was violated at one of the five call sites -- `speak_one_day_from_payload`,
which handed in the whole day -- and nothing noticed until a caller heard
"eight in the morning" four times in one call (T1b-2, CAfb09f66e).

A contract written in a docstring is not a contract. Two things replace it:

  1. `pretrimmed`, a parameter, so a producer cannot fail to see the rule, and
     a runtime warning naming the offending file:line when it is broken.
  2. THIS CENSUS. The warning only fires on a path someone exercises; a sixth
     producer written next month and only reached by one caller wording would
     ship silently. The census is a static walk over `app/`, so a new call site
     fails in CI whether or not any test drives it.

WHAT THE CENSUS ASSERTS. Every call site must be ACCOUNTED FOR: named here,
with the reason its payload is safe to hand in. It deliberately does NOT try to
prove reachability -- "is this argument trimmed" is not decidable by walking
the AST, and a test that pretended otherwise would be the third inert change in
this area. What it guarantees is narrower and enough: nobody adds producer six
without writing down which of the three justifications it has.
"""
from __future__ import annotations

import ast
import os

APP = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "app")
)

#: (module, enclosing function) -> why this producer's payload is safe.
#:
#: Keyed on the FUNCTION, not the line number, so ordinary edits above a call
#: site do not fail this test for no reason -- and a call site that MOVES to a
#: different function is exactly the change that should fail it.
ACCOUNTED_FOR = {
    ("media_streams/llm_stream.py", "_execute_tools"): (
        "Gate 5's two deterministic readouts. Both are handed payload the "
        "RETRIEVAL path already capped -- `presented_days` for the multi-day "
        "readout, and the matching `available_days` entry for the single day "
        "-- and `_cap_presented_slots` in receptionist_tools.py is what "
        "selected those positions, through `choose_presented_indices`. Both "
        "pass `more_times` from `_slot_more_times`, which is B-97's answer "
        "from the layer that knows what it dropped."
    ),
    ("tools/slot_followup.py", "more_days_speech"): (
        "Trims in the loop immediately above the call: "
        "`choose_presented_indices(...)` per day, carrying "
        "`also_heard_clock_times` between iterations (T1b site 1), then "
        "`pick_by_index` narrows all three parallel arrays."
    ),
    ("tools/slot_followup.py", "speak_one_day_from_payload"): (
        "T1b-2 ITSELF. This is the site that violated the contract. It now "
        "calls `choose_presented_indices` and narrows the three arrays with "
        "`pick_by_index` before building, and passes `more_times` explicitly "
        "because B-97 requires it once the day is pre-trimmed."
    ),
    ("tools/slot_followup.py", "numbered_more_times_speech"): (
        "The ONE deliberate exception, and it declares itself with "
        "`pretrimmed=False`. P9's 'more times that day' batch is built from "
        "`all_remaining_on_next_day` / `next_slot_batch` -- the slots this "
        "caller has NOT been read -- so B-116's subtraction already happened "
        "upstream and only 'which three of the remainder' is left, which is "
        "`_pick_times_for_day`'s question. Owner decision 2026-09-02: three "
        "numbered, then a tail."
    ),
}


#: Parsed once per process. The first cut of this file re-walked all of `app/`
#: for each of five tests and took TWENTY-SIX MINUTES against a suite that runs
#: in six -- `flow.py` alone is 24,820 lines and the parent map over its AST is
#: millions of entries. A correctness guard that quadruples the suite is a
#: guard someone deletes.
_SITES_CACHE = None


def _call_sites():
    """Every `build_slot_offer(...)` call in `app/`, with its enclosing
    function. AST, never a regex: `assert "build_slot_offer" in source` is the
    presence-style check that passed against three inert changes on 9 Sep.

    The substring pre-filter below is not a shortcut past the AST -- it only
    decides which files are worth PARSING. A file that never mentions the name
    cannot call it, however it is written, so nothing is missed and two files
    are parsed instead of ninety."""
    global _SITES_CACHE
    if _SITES_CACHE is not None:
        return _SITES_CACHE
    found = []
    for dirpath, _dirs, names in os.walk(APP):
        for name in names:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            if "build_slot_offer" not in source:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:              # not ours to police
                continue
            parents = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (getattr(func, "id", None) or getattr(func, "attr", None)) \
                        != "build_slot_offer":
                    continue
                enclosing = "<module>"
                cur = node
                while cur in parents:
                    cur = parents[cur]
                    if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        enclosing = cur.name
                        break
                rel = os.path.relpath(path, APP).replace(os.sep, "/")
                found.append((rel, enclosing, node))
    _SITES_CACHE = found
    return found


def test_every_producer_is_accounted_for():
    """Producer number six fails here, by name, with the question it has to
    answer."""
    seen = {(mod, fn) for mod, fn, _node in _call_sites()}
    unaccounted = sorted(seen - set(ACCOUNTED_FOR))

    assert not unaccounted, (
        "new `build_slot_offer` producer(s) with no recorded justification: "
        f"{unaccounted}.\n"
        "Every producer must either trim through `choose_presented_indices` "
        "before calling, or pass `pretrimmed=False` and say why. Add it to "
        "ACCOUNTED_FOR in this file with the reason -- that sentence is the "
        "only thing standing between the next reader and T1b-2."
    )


def test_the_census_is_not_describing_producers_that_are_gone():
    """The other direction. A stale entry makes the census look thorough while
    covering nothing, which is how a guard quietly stops guarding."""
    seen = {(mod, fn) for mod, fn, _node in _call_sites()}
    stale = sorted(set(ACCOUNTED_FOR) - seen)

    assert not stale, (
        f"ACCOUNTED_FOR names producers that no longer exist: {stale}. "
        "Remove them, or the census is describing a codebase nobody is running."
    )


def test_there_are_still_exactly_five_producers():
    """Convergence is the plan (`SLOT_PRESENTATION_CONVERGENCE.md` Phase 2:
    one producer, one record). Five is where it stands today, and this number
    should only ever go DOWN. It failing upward is the signal."""
    assert len(_call_sites()) == 5, [
        (m, f, n.lineno) for m, f, n in _call_sites()
    ]


def test_only_the_p9_batch_opts_out_of_the_contract():
    """`pretrimmed=False` is the one way to be exempt, so it needs its own
    census: exactly one producer may say it, and it must be a literal False
    rather than a variable a future edit can flip."""
    optouts = []
    for mod, fn, node in _call_sites():
        for kw in node.keywords:
            if kw.arg == "pretrimmed":
                assert isinstance(kw.value, ast.Constant), (
                    f"{mod}:{fn} passes `pretrimmed` as an expression. It must "
                    "be a literal, so the exemption is readable at the call "
                    "site rather than decided at runtime."
                )
                if kw.value.value is False:
                    optouts.append((mod, fn))

    assert optouts == [("tools/slot_followup.py", "numbered_more_times_speech")], (
        f"unexpected `pretrimmed=False`: {optouts}. Anything else opting out "
        "of the trim contract is a defect wearing a keyword -- see the note on "
        "the parameter in `build_slot_offer`."
    )


# ---------------------------------------------------------------------------
# The guard itself, driven -- never `assert "S-5" in source`
# ---------------------------------------------------------------------------
DAY_ISO = "2026-09-14"
GRID = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10"]


def _untrimmed_day():
    return {
        "date": DAY_ISO,
        "day_label": "Monday 14th September",
        "slot_times": list(GRID),
        "slot_times_spoken": [f"spoken-{t}" for t in GRID],
        "slots": [
            {"start": f"{DAY_ISO}T{t}:00+01:00", "end": f"{DAY_ISO}T{t}:50+01:00"}
            for t in GRID
        ],
        "times_not_shown": 0,
    }


def test_an_untrimmed_day_is_reported(caplog):
    """The T1b-2 shape: six bookable times handed in, three spoken, so this
    function is choosing by position with no idea what the caller heard."""
    import logging

    from app.tools import slot_offer

    slot_offer._TRIM_WARNED.clear()      # one line per call site, per process
    with caplog.at_level(logging.WARNING, logger=slot_offer.__name__):
        offer = slot_offer.build_slot_offer([_untrimmed_day()], more_times=True)

    assert offer is not None
    assert any("S-5" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]


def test_a_trimmed_day_is_silent():
    """The normal case pays nothing and says nothing. A guard that fires on
    correct code is a guard that gets deleted."""
    import logging

    from app.tools import slot_offer

    day = _untrimmed_day()
    for key in ("slot_times", "slot_times_spoken", "slots"):
        day[key] = day[key][:3]

    slot_offer._TRIM_WARNED.clear()
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    log = logging.getLogger(slot_offer.__name__)
    log.addHandler(handler)
    try:
        offer = slot_offer.build_slot_offer([day], more_times=True)
    finally:
        log.removeHandler(handler)

    assert offer is not None
    assert not [r for r in records if "S-5" in r.getMessage()], records


def test_the_declared_exception_is_silent():
    """`pretrimmed=False` is the whole point of the parameter: P9's batch is
    untrimmed on purpose and must not produce a warning every time a caller
    asks what else there is."""
    import logging

    from app.tools import slot_offer

    slot_offer._TRIM_WARNED.clear()
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    log = logging.getLogger(slot_offer.__name__)
    log.addHandler(handler)
    try:
        offer = slot_offer.build_slot_offer(
            [_untrimmed_day()], more_times=True, pretrimmed=False
        )
    finally:
        log.removeHandler(handler)

    assert offer is not None
    assert not [r for r in records if "S-5" in r.getMessage()], records


def test_the_guard_never_changes_the_offer():
    """It reports; it does not repair. A contract check that silently altered
    the readout would be a second selection owner, which is the defect it
    exists to prevent."""
    from app.tools import slot_offer

    slot_offer._TRIM_WARNED.clear()
    loud = slot_offer.build_slot_offer([_untrimmed_day()], more_times=True)
    slot_offer._TRIM_WARNED.clear()
    quiet = slot_offer.build_slot_offer(
        [_untrimmed_day()], more_times=True, pretrimmed=False
    )

    assert loud.text == quiet.text
    assert loud.dtmf_map == quiet.dtmf_map
    assert [s["spoken"] for s in loud.slots] == [s["spoken"] for s in quiet.slots]
