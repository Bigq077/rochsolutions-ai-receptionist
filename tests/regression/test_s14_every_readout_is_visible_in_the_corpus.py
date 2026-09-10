# tests/regression/test_s14_every_readout_is_visible_in_the_corpus.py
"""
S-14 - CAb8ac636017de7d35370fd7951c54d3cf, 10 Sep 2026 13:43, northgate.

FIVE readouts were spoken on that call. ONE left a row in `calls.slot_offers`,
and it was the `multi_day` offer Gate 5 built. The four named-day readouts left
nothing - and they were the four that exposed S-13.

`record_offer` had exactly one call site, `llm_stream.py`'s Gate 5 branch, so
every harness reading that corpus was measuring the model's tool turn and
calling it the system. The payload-answered path - which is where most of this
month's defects have been found - was systematically absent. That is also why
S-8 could not be confirmed on a phone: the single_day producers recorded
nothing to confirm it in.

The corpus is FORWARD-ONLY. Nothing here can be back-filled, so a day the
producers do not record is a day of evidence that never exists.

TWO GUARDS, and they fail for different reasons:

  THE CENSUS - AST over `app/`. Any function that calls `build_slot_offer` must
  also record. This is the guard that survives a sixth producer being added by
  someone who has never read this file; the behavioural tests below cannot see
  a producer they do not know to call.

  THE BEHAVIOUR - the real entry points, driven end to end, asserting a row
  actually lands with both halves of B-95's split in it. The census can only
  prove a CALL exists; it cannot prove the call passes anything useful.
"""
from __future__ import annotations

import ast
import os

from app.obs.slot_offers import offers_block
from app.tools.slot_followup import (
    named_day_speech,
    numbered_more_times_speech,
    record_spoken_slots,
)

APP = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "app",
)

#: Either name counts. `slot_followup` reaches obs through a lazy `_rec_offer`
#: wrapper -- the module is imported by the pure-predicate replay harnesses,
#: which must keep running with no database behind them.
_RECORDERS = {"record_offer", "_rec_offer"}


def _functions_that_build_offers():
    """(module, function) -> the names it calls. AST, never a regex: a
    substring check for "record_offer" in the file passes on a module that
    records in one producer and not the other two, which is the exact shape of
    the defect this file exists for."""
    out = {}
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
            except SyntaxError:
                continue
            rel = os.path.relpath(path, APP).replace(os.sep, "/")
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                called = {
                    (getattr(c.func, "id", None) or getattr(c.func, "attr", None))
                    for c in ast.walk(node) if isinstance(c, ast.Call)
                }
                if "build_slot_offer" in called:
                    out[(rel, node.name)] = called
    return out


# ---------------------------------------------------------------------------
# The census
# ---------------------------------------------------------------------------
def test_every_offer_producer_also_records_one():
    """Producer number six fails here, by name."""
    silent = sorted(
        site for site, called in _functions_that_build_offers().items()
        if not (called & _RECORDERS)
    )

    assert not silent, (
        f"producer(s) that SPEAK an offer but record none: {silent}.\n"
        "The offer corpus is forward-only and cannot be back-filled, so a "
        "producer that does not record is a permanent hole in every harness "
        "that reads `calls.slot_offers`. Call `record_offer` beside the "
        "`apply_offer_to_session` that already owns 'anything that speaks an "
        "offer calls this'."
    )


def test_the_census_still_finds_the_producers():
    """The other direction, and the reason it is here: if the AST walk stops
    matching - a rename, a move, a decorator - the test above passes over an
    empty set and guards nothing at all."""
    found = _functions_that_build_offers()

    assert len(found) >= 4, sorted(found)


# ---------------------------------------------------------------------------
# The behaviour
# ---------------------------------------------------------------------------
MON, TUE = "2026-09-14", "2026-09-15"
GRID = [
    "08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
    "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
]
LABELS = {MON: "Monday 14th September", TUE: "Tuesday 15th September"}


def _day(day_iso):
    return {
        "date": day_iso,
        "day_label": LABELS[day_iso],
        "slot_times": list(GRID),
        "slot_times_spoken": [f"spoken-{t}" for t in GRID],
        "slots": [
            {"start": f"{day_iso}T{t}:00+01:00", "end": f"{day_iso}T{t}:50+01:00"}
            for t in GRID
        ],
        "times_found_on_day": len(GRID),
        "times_not_shown": 0,
    }


def _session_after_a_multi_day_offer():
    days = [_day(MON), _day(TUE)]
    session = {"available_days": days, "_slot_presentation_mode": "multi_day"}
    record_spoken_slots(session, [
        {"start": f"{MON}T08:00:00+01:00"}, {"start": f"{TUE}T16:20:00+01:00"},
    ])
    return session


def test_a_named_day_readout_lands_in_the_corpus():
    """The exhibit's step 3. Before this, it left no trace at all."""
    session = _session_after_a_multi_day_offer()

    assert named_day_speech(session, "tell me about monday")

    rows = offers_block(session)
    assert rows and len(rows) == 1, rows
    assert rows[0]["mode"] == "single_day", rows[0]


def test_the_row_carries_both_halves_of_the_split():
    """B-95: `presented` is what was SPOKEN, `payload` is what was BOOKABLE,
    and the gap between them is the thing the corpus exists to measure. A row
    holding only one of them would look complete and measure nothing."""
    session = _session_after_a_multi_day_offer()
    named_day_speech(session, "tell me about monday")

    row = offers_block(session)[0]
    payload_times = row["payload"][0]["slot_times"]
    presented_times = row["presented"][0]["slot_times"]

    assert len(payload_times) == 12, payload_times
    assert len(presented_times) == 3, presented_times
    assert set(presented_times) < set(payload_times)


def test_every_readout_of_a_call_is_recorded_not_just_the_first():
    """Four named-day readouts on the exhibit call, four rows. One row for a
    five-readout call is exactly what S-14 is."""
    session = _session_after_a_multi_day_offer()

    named_day_speech(session, "tell me about monday")
    named_day_speech(session, "and what about tuesday")
    named_day_speech(session, "anything around midday on tuesday")

    assert len(offers_block(session)) == 3, offers_block(session)


def test_the_p9_batch_records_that_its_gap_is_nil():
    """The one producer whose payload and presented are legitimately equal:
    `pretrimmed=False` says the batch arrived already subtracted. The row must
    say that rather than omit it, or a harness reads the omission as a trim."""
    session = _session_after_a_multi_day_offer()
    named_day_speech(session, "tell me about monday")
    before = len(offers_block(session))

    # The batch this producer takes is what `all_remaining_on_next_day` left --
    # the slots already subtracted upstream. Handed in directly, because the
    # subtraction is not this test's subject.
    batch = [
        {"start": f"{MON}T{t}:00+01:00", "end": f"{MON}T{t}:50+01:00",
         "spoken": f"spoken-{t}", "date": MON}
        for t in ("12:10", "13:00", "13:50")
    ]

    numbered_more_times_speech(session, batch, False)

    rows = offers_block(session)
    assert len(rows) == before + 1, rows
    assert rows[-1]["payload"], rows[-1]
    assert rows[-1]["presented"], rows[-1]


def test_recording_never_costs_the_caller_the_readout():
    """The standing rule for anything on the live call path. With obs
    unreachable the caller must still hear the offer."""
    import app.obs.slot_offers as mod

    session = _session_after_a_multi_day_offer()
    original = mod.record_offer
    mod.record_offer = lambda *a, **k: 1 / 0
    try:
        text = named_day_speech(session, "tell me about monday")
    finally:
        mod.record_offer = original

    assert text, "a failing obs write swallowed the caller's readout"
