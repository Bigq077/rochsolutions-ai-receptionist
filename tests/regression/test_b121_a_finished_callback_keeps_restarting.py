"""
Regression: an errand already done, announced four times.

B-121 — CA8522b3e23fc64293, vital_edge, build cda304a31350, 1 September 2026.

Alice rang for Jonathan. The callback was taken correctly on turn 7. Then
`request_callback` fired three more times:

    turn 10  caller: "great then susie"
             assi:   "Right with you…" / "Still with you —"
             assi:   "Just to let you know — I've passed your details on to
                      Jonathan and he'll be in touch as soon as he can."
    turn 14  caller: "okay then is that all"
             assi:   "That's all from my end — the message is with Jonathan
                      and he'll be in touch. Take care!"
             assi:   "Let me get that message over for you…"
             assi:   "Jonathan's been notified and will be in touch on that
                      number. Take care, Alice!"
    turn 18  caller: "so jonathan has been notified and i can just wait now
                      for him to give me a quick callback right"
             assi:   "Just getting that for you…"
             assi:   "That's right — he's been notified…"

Four writes, three of them fired by a plain acknowledgement or a goodbye.

── WHAT THIS IS NOT ───────────────────────────────────────────────────────────
It was first read as a filler outliving its turn, because the hold phrase
appears AFTER the sign-off. It is not. The model streamed its farewell text and
a tool call in the same turn — text first, then tool — so the filler is
correctly announcing a genuine, fresh call to `request_callback`. Reading the
obs transcript as audio is the trap P2 of the 1 Sep defect note describes.

Nor did it damage anything: `_queue_owner_callback_sms` dedups on
`_waitlist_pinged` and the record uses `setdefault`, so Jonathan was texted
once. What repeated was the hold phrase and the claim.

── WHAT IT IS ─────────────────────────────────────────────────────────────────
The farewell-turn re-fire that `_WRITE_TOOL_FAMILIES` already guards for
booking, reschedule and cancel. Its own comment records the cancel case,
CA0f9a12, in almost these words: "the model fired cancel_appointment one more
time on the farewell turn". `request_callback` and `add_to_waitlist` were never
members of that family.

Clinic-wide, not a Vital Edge quirk: `build_tool_schemas` hands both tools to
every live clinic (checked in this file), and the executor has no clinic gate.
VE is simply the only clinic with callback traffic so far.

── WHERE THE GATE HAS TO SIT ──────────────────────────────────────────────────
Above the executor, in the gate-refusal chain — NOT as a latch inside
`_exec_request_callback`. The hold phrase is queued by `with_filler` in the
`else` branch BEFORE the executor returns, so an executor-level guard would
still leave the caller hearing "Let me get that message over for you…" over an
errand already finished. `test_the_hold_phrase_is_silent_on_a_repeat` is what
pins that, and it is the caller-audible half of this defect.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.media_streams.llm_stream import (
    LLMStream,
    _CALLBACK_ALREADY_SENT_RULE,
    _same_callback_lead,
)
from app.tools.receptionist_tools import build_tool_schemas


BLOCKED = "callback_already_sent"

# Alice, as the call actually carried her.
ALICE = {
    "patient_name": "Alice",
    "phone": "+447383262949",
    "notes": "enquiry about the Be Sport app — wants a word with Jonathan",
}


def _sent_session(**over) -> dict:
    """A session in which the callback has already gone to the clinic.

    Exactly the two keys `_queue_owner_callback_sms` sets on its success path —
    written here rather than by running the executor so the gate is tested
    against the latch, not against the SMS stack.
    """
    s = {
        "clinic_id": "vital_edge",
        "callback_write_confirmed": True,
        "callback_lead": {
            "patient_name": ALICE["patient_name"],
            "phone": ALICE["phone"],
        },
    }
    s.update(over)
    return s


async def _run_tool(tool_name, args, session, tts_queue=None):
    """Drive _execute_tools with one tool_use, as test_cancel_reschedule_gate does."""
    stream = object.__new__(LLMStream)
    tool_uses = [{"name": tool_name, "input": args, "id": "t1"}]
    mock_exec = AsyncMock(return_value={"success": True, "message": "Clinic notified"})
    with patch.dict(
        "app.tools.receptionist_tools.TOOL_EXECUTORS", {tool_name: mock_exec}
    ):
        blocks = await stream._execute_tools(
            tool_uses, session, "CA8522b3e2",
            tts_text_queue=tts_queue,
            messages=[{"role": "user", "content": "okay then is that all"}],
        )
    return mock_exec, json.loads(blocks[0]["content"])


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_repeat_callback_does_not_reach_the_executor():
    """Turn 14 of the call: "okay then is that all" must not send it again."""
    m, r = await _run_tool("request_callback", ALICE, _sent_session())

    m.assert_not_awaited()
    assert r.get("status") == BLOCKED, r


@pytest.mark.asyncio
async def test_the_hold_phrase_is_silent_on_a_repeat():
    """The caller-audible half. "Let me get that message over for you…" is
    queued by `with_filler` inside the executor branch, so a gate that refused
    any later than this would still play it over a finished errand."""
    q = asyncio.Queue()

    m, r = await _run_tool("request_callback", ALICE, _sent_session(), tts_queue=q)

    assert m.await_count == 0
    assert q.empty(), (
        f"a hold phrase was spoken over an already-sent callback: {q.get_nowait()!r}"
    )


@pytest.mark.asyncio
async def test_the_waitlist_repeat_is_gated_too():
    """Both tools latch through the same `_queue_owner_callback_sms`, so both
    can re-fire on a goodbye and both produce the identical caller experience."""
    m, r = await _run_tool("add_to_waitlist", ALICE, _sent_session())

    m.assert_not_awaited()
    assert r.get("status") == BLOCKED, r


@pytest.mark.asyncio
async def test_the_first_callback_still_runs():
    """The gate must be invisible until something has actually been sent —
    a callback nobody was told about is the defect this whole tool exists for
    (CAc36368cbeb, Dylan Wilson)."""
    m, r = await _run_tool("request_callback", ALICE, {"clinic_id": "vital_edge"})

    m.assert_awaited_once()
    assert r.get("status") != BLOCKED


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_different_lead_is_not_a_repeat():
    """B-62's lesson in a new place. A caller may legitimately ask for someone
    else to be rung back, and matching on the latch alone would suppress a real
    write because an earlier, unrelated one succeeded."""
    other = {**ALICE, "patient_name": "Ray", "phone": "+447700900123"}

    m, r = await _run_tool("request_callback", other, _sent_session())

    m.assert_awaited_once()
    assert r.get("status") != BLOCKED


def test_the_same_number_written_two_ways_is_one_number():
    """STT and caller-ID disagree about form constantly; a repeat that slipped
    through on formatting would be the defect surviving in the common case."""
    assert _same_callback_lead(
        _sent_session(), {"patient_name": "alice", "phone": "07383 262949"}
    )
    assert _same_callback_lead(
        _sent_session(), {"patient_name": "  Alice  ", "phone": "+44 7383 262949"}
    )


def test_an_unknown_lead_is_not_a_repeat():
    """Fails in the safe direction: with nothing to compare, let the write run.
    A duplicate costs a repeated sentence; a suppressed first write costs a
    caller nobody rings back."""
    assert not _same_callback_lead(_sent_session(), {})
    assert not _same_callback_lead(
        {"clinic_id": "vital_edge", "callback_write_confirmed": True}, ALICE
    )
    assert not _same_callback_lead({"clinic_id": "vital_edge"}, ALICE)


def test_the_refusal_reads_as_already_done_not_as_a_failure():
    """B-65: a refusal `message` that contradicted the already-done rule had
    Susie apologising for a cancellation that had in fact succeeded.

    Asserted as DIRECTIVES, not as banned substrings. The first version of this
    test scanned for "apolog" and failed on the rule's own "Do not apologise" —
    a matcher that cannot tell a prohibition from the thing it prohibits, which
    is the mistake `write-gates-match-one-literal` records three times over.
    The prohibitions are carried verbatim from the sibling family so the two can
    never drift into saying different things about the same situation.
    """
    from app.media_streams.llm_stream import (
        WRITE_FAMILY_CANCEL,
        _WRITE_ALREADY_DONE_RULE,
    )

    rule = _CALLBACK_ALREADY_SENT_RULE
    sibling = _WRITE_ALREADY_DONE_RULE[WRITE_FAMILY_CANCEL]

    assert "already been notified" in rule, "must say the errand is done"
    for directive in (
        "Do not apologise",
        "do not tell the caller anything failed",
        "If they are saying goodbye, simply say goodbye",
    ):
        assert directive in rule, f"missing {directive!r}"
        assert directive in sibling, (
            f"{directive!r} left the sibling family — the two rules have "
            f"drifted and this test is now pinning nothing"
        )
    # The one claim it may make is about THIS attempt, never about the world.
    assert "This further attempt did not go through" in rule


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "clinic_id", ["vital_edge", "jv_v1", "theorem_v3", "northgate"]
)
def test_both_tools_reach_every_live_clinic(clinic_id):
    """Why this is not a Vital Edge fix. `build_tool_schemas` IS clinic-aware —
    it rewrites location and service per clinic — so the flat master list is not
    proof on its own. It hands both tools to all four regardless, and the corpus
    is silent on the other three only because they have no callback traffic yet.
    """
    names = {t["name"] for t in build_tool_schemas(clinic_id)}
    assert "request_callback" in names
    assert "add_to_waitlist" in names


def test_the_lead_is_recorded_where_both_tools_latch():
    """The gate needs to know WHICH lead was sent. Recording it anywhere but
    `_queue_owner_callback_sms` would cover one tool and not the other."""
    import inspect

    from app.tools import receptionist_tools as rt

    src = inspect.getsource(rt._queue_owner_callback_sms)
    assert 'session["callback_lead"]' in src
    assert 'session["callback_write_confirmed"] = True' in src
