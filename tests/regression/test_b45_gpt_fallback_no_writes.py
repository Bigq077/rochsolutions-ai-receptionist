# tests/regression/test_b45_gpt_fallback_no_writes.py
"""
B-45 — the GPT fallback could write to the calendar with no gates at all.

`_gpt_fallback` runs when Claude is overloaded and the retries are exhausted. It
calls `TOOL_EXECUTORS` **directly** and never reaches `_execute_tools`, so none
of the write gates applied on that path:

  * FM-01 booking confirmation + affirmation verdict
  * the surname and phone backstops
  * FM-23 move confirmation and cancel consent
  * B-42's identity check — so a cancellation there could not tell **whose**
    appointment it was destroying
  * `_note_write_result`, so Gate 5f never armed and a phantom could not be
    caught either

All three write tools were advertised to that model. The path activates under
load, which is exactly when a busy clinic can least afford it — the same
observation the Gate 5 comment on this function already makes about A1.

**The fix is not to replicate the gate chain.** The correct degraded behaviour is
the one `CLAUDE.md` §6 bar 3 already specifies: when the LLM is down, produce a
controlled outcome — take a message, promise a callback, transfer — never a
hallucinated confirmation. A missed booking is recoverable by a callback; a wrong
cancellation is not.

Three layers, deliberately not one:

  1. the write tools are **withheld from the schema** (`allow_writes=False`)
  2. the dispatch **refuses** them anyway, so the guarantee does not rest on a
     tool list that a later edit could widen
  3. the refusal is routed through `_note_write_result`, which arms Gate 5f and
     attaches the do-not-claim rule — and this path *does* sanitise its reply
     through Gate 5, so a narrated booking is still caught
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams import llm_stream as ls
from app.media_streams import turn_handler as th


_WRITES = ("book_appointment", "reschedule_appointment", "cancel_appointment")
_SESSION = {"clinic_id": "jv_v1"}


def _names(**kw):
    return [t["function"]["name"] for t in ls._build_openai_tools(_SESSION, **kw)]


# ── Layer 1: the schema ───────────────────────────────────────────────────
@pytest.mark.parametrize("tool", _WRITES)
def test_the_degraded_path_is_not_offered_write_tools(tool):
    assert tool not in _names(allow_writes=False)


@pytest.mark.parametrize("tool", _WRITES)
def test_the_normal_path_still_gets_them(tool):
    """The default must stay permissive — this is a fallback-only restriction."""
    assert tool in _names()


def test_the_read_tools_and_the_escape_hatch_survive():
    """The refusal message tells the model to take a message or transfer. Both
    have to still be possible, or the caller hits a dead end."""
    degraded = _names(allow_writes=False)
    for tool in ("check_availability", "lookup_patient", "transfer_to_human"):
        assert tool in degraded, f"{tool} withheld — the caller has no way out"


def test_the_withheld_set_is_exactly_the_write_family():
    """Derived from _WRITE_TOOL_FAMILIES, not a second hand-kept list."""
    withheld = set(_names()) - set(_names(allow_writes=False))
    assert withheld == set(ls._WRITE_TOOL_FAMILIES)


def test_the_fallback_actually_asks_for_the_restricted_set():
    src = inspect.getsource(ls.LLMStream._gpt_fallback)
    assert "_build_openai_tools(session, allow_writes=False)" in src


# ── Layer 2: the dispatch refuses regardless of the schema ────────────────
def test_the_dispatch_refuses_writes_before_reaching_an_executor():
    """The guarantee must not rest on the tool list. If someone widens the
    schema later, this branch is what still holds."""
    src = inspect.getsource(ls.LLMStream._gpt_fallback)
    refuse = src.find("_WRITE_TOOL_FAMILIES")
    execute = src.find("TOOL_EXECUTORS.get(tool_name)")
    assert refuse != -1, "the write refusal is gone from the fallback loop"
    assert execute != -1, "fixture drift: the executor dispatch moved"
    assert refuse < execute, (
        "a write tool can reach TOOL_EXECUTORS on the ungated fallback path"
    )


def test_the_refusal_never_calls_the_executor():
    """Structural: the write branch assigns a result, it does not await one."""
    src = inspect.getsource(ls.LLMStream._gpt_fallback)
    branch = src[src.find("if tool_name in _WRITE_TOOL_FAMILIES"):
                 src.find('elif tool_name == "escalate_to_claude"')]
    assert "await" not in branch, "the refusal branch awaits something — it must not execute"
    assert "_note_write_result" in branch


def test_the_log_line_uses_the_session_not_a_missing_attribute():
    """Regression on my own near-miss. `self.call_sid` does not exist on
    LLMStream; referencing it would raise inside the try, be swallowed by the
    broad `except Exception`, and silently downgrade a clean refusal into a
    generic error — losing the steering message AND the Gate 5f arming, at
    exactly the moment both matter."""
    assert not hasattr(ls.LLMStream(), "call_sid")
    assert "self.call_sid" not in inspect.getsource(ls.LLMStream._gpt_fallback)


# ── Layer 3: it composes with Gate 5f ─────────────────────────────────────
@pytest.mark.parametrize(
    "tool,family",
    [
        ("book_appointment", th.WRITE_FAMILY_BOOKING),
        ("reschedule_appointment", th.WRITE_FAMILY_RESCHEDULE),
        ("cancel_appointment", th.WRITE_FAMILY_CANCEL),
    ],
)
def test_the_refusal_arms_the_false_confirmation_guard(tool, family):
    session = {}
    result = ls._note_write_result(
        session, tool, {"status": "unavailable_degraded_mode", "message": "x"}
    )
    assert th._armed_write_families(session) == [family]
    assert (result.get("caller_message_rule") or "").strip(), (
        "no do-not-claim rule — the model is free to narrate the write"
    )


def test_a_phantom_after_a_degraded_refusal_is_caught():
    """End to end for the case that matters: the model ignores the prefix and
    the tool-result rule, and claims the cancellation happened anyway."""
    session = {"_clinical_depth_cache": ""}
    ls._note_write_result(
        session, "cancel_appointment",
        {"status": "unavailable_degraded_mode", "message": "x"},
    )
    claim = "That's all done — your appointment has been cancelled."
    assert th.sanitise_response(claim, session) != claim


def test_the_fallback_reply_still_passes_through_gate_5():
    """Layer 3 only works because this path sanitises. If that call is ever
    removed, the arming above buys nothing."""
    src = inspect.getsource(ls.LLMStream._gpt_fallback)
    assert "sanitise_response(reply_text, session)" in src


# ── Steering: the caller should not have to hit the wall to learn ─────────
def test_the_model_is_told_it_cannot_write():
    low = ls._GPT_CONSTRAINT_PREFIX.lower()
    assert "cannot make, move or cancel" in low
    assert "never say anything has been booked" in low
    assert "call straight back" in low or "put them through" in low
