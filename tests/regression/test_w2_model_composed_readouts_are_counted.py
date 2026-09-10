# tests/regression/test_w2_model_composed_readouts_are_counted.py
"""
Stage C's blocker: the population that leaves no row.

`STAGE_C_EVIDENCE_2026-09-10.md` §5, verbatim:

    Do not delete the repair layer on the strength of a clean grep. Site A is
    only reachable when no deterministic offer was built, and this corpus
    cannot show how often that happens -- `slot_offers` only records the turns
    where one WAS built, so the population that reaches site A leaves no row.
    That measurement needs its own instrumentation before the layer can be
    retired.

The ~900-line reverse-parse repair layer and its fifteen B-numbers cannot
honestly be deleted on a clean Render grep, because that grep only speaks about
turns nobody can count. `record_model_readout` makes "how often, on which
clinic, and did the parse succeed" a SQL question.

WRITTEN ON EVERY MODEL-COMPOSED READOUT, INCLUDING THE HEALTHY ONE. A row
written only on failure makes a quiet day and a dead instrument look identical
-- which is S-6 exactly, one layer out, and S-6 is why Stage C's gate had to be
amended in the first place.

`labels_read` and `labels_resolved` are counts rather than a verdict, because
zero-and-zero is healthy (the sentence named no options) while four-and-zero is
site A failing. Collapsing them to a boolean throws that distinction away.
"""
from __future__ import annotations

from app.obs.slot_offers import offers_block, record_model_readout

MON = "2026-09-14"
GRID = ["08:00", "09:40", "15:30"]


def _day():
    return {
        "date": MON,
        "day_label": "Monday 14th September",
        "slot_times": list(GRID),
        "slot_times_spoken": [f"spoken-{t}" for t in GRID],
        "times_not_shown": 0,
    }


def _slot(t):
    return {"start": f"{MON}T{t}:00+01:00", "spoken": f"spoken-{t}", "date": MON}


# ---------------------------------------------------------------------------
def test_a_model_composed_readout_leaves_a_row():
    """Before this, it left nothing at all."""
    session = {}

    record_model_readout(
        session, payload_days=[_day()],
        labels=["eight in the morning", "twenty to ten"],
        resolved=[_slot("08:00"), _slot("09:40")],
        text="Monday 14th - eight in the morning, or twenty to ten.",
    )

    rows = offers_block(session)
    assert rows and len(rows) == 1, rows
    assert rows[0]["source"] == "model", rows[0]
    assert rows[0]["mode"] == "model_composed", rows[0]


def test_the_row_says_it_was_spoken():
    """There is no build/speak split here. By the time this runs the model's
    sentence IS the readout, so a harness filtering on `spoken` must see it."""
    session = {}

    record_model_readout(session, payload_days=[_day()], labels=["a"],
                         resolved=[_slot("08:00")])

    assert offers_block(session)[0]["spoken"] is True


def test_a_healthy_readout_is_recorded_too_not_just_a_failing_one():
    """The rate is the finding. A row written only on failure makes a quiet day
    and a dead instrument indistinguishable -- S-6, one layer out."""
    session = {}

    record_model_readout(session, payload_days=[_day()], labels=["a", "b"],
                         resolved=[_slot("08:00"), _slot("09:40")])

    row = offers_block(session)[0]
    assert row["labels_read"] == 2, row
    assert row["labels_resolved"] == 2, row


def test_a_failed_parse_is_distinguishable_from_a_sentence_naming_nothing():
    """Zero-and-zero is healthy; four-and-zero is site A failing. A boolean
    would collapse them, which is the whole reason these are counts."""
    session = {}

    record_model_readout(session, payload_days=[_day()], labels=[], resolved=[])
    record_model_readout(session, payload_days=[_day()],
                         labels=["a", "b", "c", "d"], resolved=[])

    rows = offers_block(session)
    named_nothing, failed = rows[0], rows[1]
    assert (named_nothing["labels_read"], named_nothing["labels_resolved"]) == (0, 0)
    assert (failed["labels_read"], failed["labels_resolved"]) == (4, 0)


def test_presented_is_empty_because_nothing_trimmed_this_readout():
    """`presented` on the other rows means "what the trim chose". Here nothing
    trimmed anything, and an omitted key would read as "trimmed to nothing" --
    a different claim, and the one B-95 is about."""
    session = {}

    record_model_readout(session, payload_days=[_day()], labels=["a"],
                         resolved=[_slot("08:00")])

    row = offers_block(session)[0]
    assert row["presented"] == [], row
    assert row["payload"], row


def test_it_shares_the_column_with_the_other_two_sources():
    """One column, three writers, all queryable. `source` is what separates
    them, and every row carries it."""
    from app.obs.slot_offers import record_offer

    class _Offer:
        chunks, mode, slots, dtmf_map, more_times = ["x"], "multi_day", [], {}, False

    session = {}
    record_offer(session, payload_days=[_day()], offer=_Offer())
    record_model_readout(session, payload_days=[_day()], labels=["a"],
                         resolved=[_slot("08:00")])

    assert [r["source"] for r in offers_block(session)] == ["gate5", "model"]


def test_it_never_raises_on_junk():
    """Live call path. An observability row must not be able to cost a caller
    their readout, and `len()` on a non-sequence raises."""
    session = {}

    record_model_readout(session, payload_days=None, labels=12, resolved="nope")

    row = offers_block(session)[0]
    assert row["labels_read"] == 0 and row["labels_resolved"] == 0, row


# ---------------------------------------------------------------------------
# The wiring. Everything above calls the recorder directly, and that is exactly
# what let S-13 hide: `test_the_named_day_producer_honours_a_requested_time`
# wrote the session key itself and passed all week while the live path was
# dead. A recorder nothing calls measures nothing.
# ---------------------------------------------------------------------------
def _flush_slot_buf_ast():
    import ast
    import os

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "app", "media_streams", "llm_stream.py",
    )
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == "_flush_slot_buf":
            return ast, node
    raise AssertionError("_flush_slot_buf not found -- renamed? this guard is blind")


def test_site_a_actually_calls_the_recorder():
    """The live path, not a reimplementation of it."""
    ast, fn = _flush_slot_buf_ast()

    called = {
        (getattr(c.func, "id", None) or getattr(c.func, "attr", None))
        for c in ast.walk(fn) if isinstance(c, ast.Call)
    }

    assert called & {"_rec_model", "record_model_readout"}, (
        "`_flush_slot_buf` records no model-composed readout. Site A is the "
        "only place that population can be counted, and Stage C's ~900-line "
        "deletion is blocked on counting it."
    )


def test_the_recorder_is_not_hidden_behind_the_success_branch():
    """The rate is the finding. Recording only when `_spoken_opts` resolved
    would count the healthy readouts and miss every failing one -- the exact
    inversion of what this is for, and invisible in a green test run."""
    ast, fn = _flush_slot_buf_ast()

    parents = {}
    for node in ast.walk(fn):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    for call in ast.walk(fn):
        if not isinstance(call, ast.Call):
            continue
        if (getattr(call.func, "id", None)
                or getattr(call.func, "attr", None)) not in {
                    "_rec_model", "record_model_readout"}:
            continue
        cur = call
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, ast.If) and getattr(cur.test, "id", None) == "_spoken_opts":
                raise AssertionError(
                    f"the model-readout recorder at line {call.lineno} sits "
                    "inside `if _spoken_opts:` -- it would record only the "
                    "readouts that PARSED, which is the opposite of the "
                    "measurement Stage C needs."
                )
