# tests/regression/test_b36_write_refusal_steering.py
"""
B-36 cause 2d — a refused write must tell the model it did NOT happen.

`CA23199d089` (3 Aug 2026): the reschedule gate blocked the write, and Susie told
the caller their appointment had been moved. It had not. The model was given an
instruction to ask a question (`"ask 'Shall I go ahead and move it for you?'"`)
and **no rule against announcing success** — Layer 2's `caller_message_rule` was
scoped to `book_appointment` alone.

This is the steering layer only. It fires on the already-failed path, so it can
never suppress a real write — which is why it is separable from the Gate 5f
restructure and lands first. It does not DETECT a claim; if the model announces
success anyway, that is Gate 5f's job (see
tests/regression/test_false_confirmation_guard.py).

The load-bearing detail is the refusal polarity. Every gate branch in
`_run_tools` returns `{"status": "..._required", "message": ...}` with **no
`success` key at all** — a `success is False` test would catch none of the five.
`test_every_real_gate_refusal_shape_is_steered` pins that.
"""
from __future__ import annotations

import pytest

from app.media_streams import llm_stream as ls


# The verbatim shapes the five gate branches in _run_tools construct. If a gate
# is reworded or added, this list is what should be updated — and the polarity
# assertion below will fail loudly if someone adds one carrying success=False
# semantics in a new shape.
_REAL_GATE_REFUSALS = [
    ("book_appointment",       {"status": "surname_required"}),
    ("book_appointment",       {"status": "phone_confirmation_required"}),
    ("book_appointment",       {"status": "confirmation_required"}),
    ("book_appointment",       {"status": "affirmation_required"}),
    ("reschedule_appointment", {"status": "reschedule_confirmation_required"}),
    ("cancel_appointment",     {"status": "cancellation_confirmation_required"}),
]


@pytest.mark.parametrize("tool_name,result", _REAL_GATE_REFUSALS)
def test_every_real_gate_refusal_shape_is_steered(tool_name, result):
    """No `success` key — the refusal must still be recognised as a refusal."""
    assert "success" not in result, "fixture drift: these shapes carry no success key"
    out = ls._note_write_result({}, tool_name, dict(result))
    rule = (out.get("caller_message_rule") or "").lower()
    assert rule, f"{tool_name}/{result['status']} got no do-not-claim rule"
    assert " not " in f" {rule} "


def test_reschedule_refusal_forbids_the_move_claim():
    out = ls._note_write_result(
        {}, "reschedule_appointment", {"status": "reschedule_confirmation_required"}
    )
    rule = (out.get("caller_message_rule") or "").lower()
    assert "was not moved" in rule
    # The whole reschedule phrase family, not just one word — B-36 cause 2b is
    # that "moved"/"rescheduled"/"changed"/"sorted" are all the same claim.
    for word in ("rescheduled", "moved", "changed", "sorted"):
        assert word in rule, f"rule does not name {word!r}"


def test_cancel_refusal_forbids_the_cancellation_claim():
    out = ls._note_write_result(
        {}, "cancel_appointment", {"status": "cancellation_confirmation_required"}
    )
    rule = (out.get("caller_message_rule") or "").lower()
    assert "was not cancelled" in rule
    # Cancel is destructive: the caller must be left holding their ORIGINAL
    # appointment, not an ambiguous "nothing happened".
    assert "still stands" in rule


def test_booking_rule_is_unchanged():
    """The measured booking wording is not disturbed by the generalisation."""
    out = ls._note_write_result(
        {}, "book_appointment", {"success": False, "status": "confirmation_required"}
    )
    rule = out.get("caller_message_rule") or ""
    assert "The booking was NOT made." in rule


@pytest.mark.parametrize(
    "tool_name", ["book_appointment", "reschedule_appointment", "cancel_appointment"]
)
def test_success_attaches_no_rule(tool_name):
    out = ls._note_write_result({}, tool_name, {"success": True})
    assert "caller_message_rule" not in out


def test_only_booking_success_sets_the_call_scoped_flag():
    """B-36 cause 2c, pinned from the other side.

    `booking_write_confirmed` is the BOOKING family's success signal and is
    call-scoped. A successful reschedule or cancellation must not set it — that
    would silently switch the booking guard off for the rest of the call.
    """
    s = {}
    ls._note_write_result(s, "book_appointment", {"success": True})
    assert s.get("booking_write_confirmed") is True

    for tool in ("reschedule_appointment", "cancel_appointment"):
        s2 = {}
        ls._note_write_result(s2, tool, {"success": True})
        assert "booking_write_confirmed" not in s2, (
            f"{tool} success must not set the booking flag"
        )


@pytest.mark.parametrize(
    "tool_name,result",
    [
        # R6 / the over-fire trap: the lookup family reports `found`, not
        # `success`. A generic falsiness test would read "I can't find your
        # appointment" as a refused write and steer against a claim that was
        # never made.
        ("lookup_patient",     {"found": False}),
        ("lookup_appointment", {"found": False}),
        ("check_availability", {"available_days": []}),
        ("get_clinic_info",    {"answer": "We're open until six."}),
        ("collect_and_store",  {"stored": False}),
    ],
)
def test_non_write_tools_are_untouched(tool_name, result):
    s = {}
    out = ls._note_write_result(s, tool_name, result)
    assert out is result, "non-write results must be passed through by identity"
    assert s == {}, "a non-write tool must not write any session state"


def test_an_existing_rule_is_not_overwritten():
    """setdefault, not assignment — a tool that already explained itself wins."""
    out = ls._note_write_result(
        {},
        "reschedule_appointment",
        {"success": False, "caller_message_rule": "Bespoke rule from the executor."},
    )
    assert out["caller_message_rule"] == "Bespoke rule from the executor."


def test_a_non_dict_result_is_passed_through():
    assert ls._note_write_result({}, "book_appointment", None) is None
    assert ls._note_write_result({}, "book_appointment", "boom") == "boom"
