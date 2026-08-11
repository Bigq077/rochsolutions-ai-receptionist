"""
Regression: requiring a reason to book belongs only to a clinic that ASKS for one.

`_exec_book_appointment` refused any booking with no reason on record (the A2
gate). That is correct for a clinic that asks the question. On a clinic that
never asks it is a closed loop:

    the prompt forbids the question
      -> Gate 5b-r strips it if the model improvises it anyway
        -> no reason is ever collected
          -> A2 refuses every booking

That loop is what made Theorem's bookings impossible. Theorem fixed it on its
own branch with `clinic_id != "theorem_v3"`. This is the same fix keyed on
CONFIG — `prompt_facts.reason_question`, the identical key the output gate
reads — so the two can never disagree about a clinic, and a clinic can change
its mind with a config edit and no engine change.

The tool-schema half matters as much as the gate. The stock `reason`
description says the tool REFUSES without one and to "ask for it before
checking availability". That text ships with every request and is not subject
to the system prompt, so suppressing the question at the output could never
hold on its own.
"""
from __future__ import annotations

import copy
import inspect

import pytest

from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import (
    TOOL_SCHEMAS,
    _apply_reason_policy,
    _clinic_asks_the_reason,
    build_tool_schemas,
)

_STOCK = "REQUIRED IN PRACTICE"
_NEVER_ASK = "NEVER ask the caller what"


def _reason_descs(schemas):
    out = []
    for t in schemas:
        r = ((t.get("input_schema") or {}).get("properties") or {}).get("reason")
        if isinstance(r, dict) and r.get("description"):
            out.append(r["description"])
    return out


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("clinic_id", ["jv_v1", "vital_edge"])
def test_a_clinic_that_asks_requires_a_reason(clinic_id):
    assert _clinic_asks_the_reason(clinic_id) is True


@pytest.mark.parametrize("clinic_id", ["theorem", "theorem_v3", "demo"])
def test_a_clinic_that_never_asks_does_not_require_one(clinic_id):
    assert _clinic_asks_the_reason(clinic_id) is False


def test_unknown_clinics_fail_OPEN():
    """The opposite direction to its sibling in turn_handler, deliberately.

    There, "unknown" must suppress the question — asking a banned question is
    the harm. Here, "unknown" must not REQUIRE a reason, because requiring one
    from a clinic that never asks refuses every booking. A booking carrying no
    reason is recoverable; a caller who cannot book at all is not.
    """
    for cid in (None, "", "does_not_exist"):
        assert _clinic_asks_the_reason(cid) is False


def test_the_predicate_reads_config_not_a_clinic_name():
    src = inspect.getsource(rt._clinic_asks_the_reason)
    assert "reason_question" in src
    for literal in ("theorem_v3", "jv_v1", "vital_edge"):
        assert literal not in src, (
            f"the predicate hardcodes {literal!r} — that is the bug this "
            "replaces, not the fix"
        )


# ---------------------------------------------------------------------------
# The booking gate
# ---------------------------------------------------------------------------
def test_the_A2_gate_is_conditional_on_the_predicate():
    src = inspect.getsource(rt._exec_book_appointment)
    assert "_clinic_asks_the_reason(" in src, (
        "the A2 gate must consult the predicate, or a clinic that never asks "
        "is unbookable"
    )
    assert "_reason_required and not _cg_reason" in src


def test_an_empty_reason_is_not_written_into_the_slots():
    """On a clinic that never asks, an empty reason reaches the commit block as
    a CORRECT outcome. Writing "" would make an absent reason look like a
    collected one to the call record and the SMS router."""
    src = inspect.getsource(rt._exec_book_appointment)
    assert "if _cg_reason:" in src


# ---------------------------------------------------------------------------
# The tool schema
# ---------------------------------------------------------------------------
def test_a_clinic_that_asks_keeps_the_stock_description():
    descs = _reason_descs(build_tool_schemas("jv_v1"))
    assert descs, "no reason field found — the fixture has gone stale"
    assert any(_STOCK in d for d in descs)
    assert not any(_NEVER_ASK in d for d in descs)


@pytest.mark.parametrize("clinic_id", ["theorem_v3", "demo"])
def test_a_clinic_that_never_asks_is_told_so_in_the_schema(clinic_id):
    descs = _reason_descs(build_tool_schemas(clinic_id))
    assert descs, "no reason field found — the fixture has gone stale"
    assert not any(_STOCK in d for d in descs), (
        f"{clinic_id} still receives 'REQUIRED IN PRACTICE — ask for it', which "
        "ships with every request and is not subject to the system prompt"
    )
    assert any(_NEVER_ASK in d for d in descs)


def test_the_policy_is_applied_on_BOTH_schema_paths():
    """Theorem's version keyed on the clinic id inside the non-template branch
    only, so a single-site TEMPLATE clinic that stopped asking would have kept
    the 'ask for it' text and walked back into the deadlock."""
    src = inspect.getsource(rt.build_tool_schemas)
    assert src.count("_apply_reason_policy(") == 2


def test_the_module_level_schemas_are_never_mutated():
    """TOOL_SCHEMAS is shared by every clinic in the process — rewriting it in
    place would hand the never-ask text to the clinics that DO ask."""
    before = copy.deepcopy(TOOL_SCHEMAS)
    build_tool_schemas("theorem_v3")
    build_tool_schemas("demo")
    build_tool_schemas("jv_v1")
    assert TOOL_SCHEMAS == before, "build_tool_schemas mutated the shared constant"


def test_rewriting_is_a_no_op_for_a_clinic_that_asks():
    same = _apply_reason_policy(list(TOOL_SCHEMAS), "jv_v1")
    assert same == list(TOOL_SCHEMAS)
