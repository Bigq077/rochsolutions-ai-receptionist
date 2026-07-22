"""
tests/regression/test_cancel_reschedule_gate.py
-----------------------------------------------
FM-23 — reschedule_appointment and cancel_appointment are exposed to the LLM on
the live clinics with NO deterministic consent gate (only book_appointment was
gated, by FM-01). A model misfire can move or DELETE a real patient's
appointment. This gate is the deterministic backstop.

Confirm flow (template_v1 clinics — jv_v1, vital_edge, via clinic_template_prompt):
  * Reschedule CTA is enforced verbatim: "Shall I go ahead and move it for you?"
    → last_bot_prompt contains "move it for you"; caller answers "yes".
  * Cancel has NO "shall I cancel" CTA. The confirm is the retention question
    "Would you like to reschedule this appointment, or cancel it altogether?" and
    the caller consents by SAYING "cancel". So the cancel gate CANNOT reuse
    _book_reply_is_affirmative ("cancel" is in _NO_PATTERNS), and a bare "yes" is
    ambiguous against the OR-question and must NOT cancel.

Gate spec:
  reschedule → block unless last_bot_prompt has "move it for you" AND a clear yes.
  cancel     → block unless BOTH last_bot_prompt has the retention question
               ("cancel it altogether"/"altogether") AND the reply contains an
               explicit "cancel" token (not a bare yes; not reschedule words;
               not "keep/leave it"; not "don't cancel"; not a bare "no").
Bias hard toward NOT cancelling.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.media_streams.llm_stream import LLMStream

RETENTION_Q = "Would you like to reschedule this appointment, or cancel it altogether?"
RESCHEDULE_CTA = (
    "Just to confirm — I'm moving your appointment to Friday the 24th of July at "
    "three in the afternoon. Shall I go ahead and move it for you?"
)
UNRELATED = "How can I help you today?"

_CANCEL_ARGS = {"patient_name": "Quentin Rock", "phone": "07502211207", "location": "bolton"}
_RESCHED_ARGS = {**_CANCEL_ARGS, "new_slot_iso": "2026-07-24T15:00:00+01:00", "duration_minutes": 40}


async def _run_tool(tool_name, args, last_bot_prompt, last_user_text):
    """Drive _execute_tools with one tool_use; caller reply threaded via messages."""
    stream = object.__new__(LLMStream)
    session = {"last_bot_prompt": last_bot_prompt}
    tool_uses = [{"name": tool_name, "input": args, "id": "t1"}]
    messages = [{"role": "user", "content": last_user_text}]
    mock_exec = AsyncMock(return_value={"status": "ok"})
    with patch.dict("app.tools.receptionist_tools.TOOL_EXECUTORS", {tool_name: mock_exec}):
        blocks = await stream._execute_tools(
            tool_uses, session, "CAtest", tts_text_queue=None, messages=messages,
        )
    return mock_exec, json.loads(blocks[0]["content"])

_CANCEL_BLOCKED = "cancellation_confirmation_required"
_RESCHED_BLOCKED = "reschedule_confirmation_required"


# ── cancel: must BLOCK (destructive) ────────────────────────────────────────
@pytest.mark.asyncio
async def test_cancel_blocks_on_no():
    m, r = await _run_tool("cancel_appointment", _CANCEL_ARGS, RETENTION_Q, "no")
    m.assert_not_awaited(); assert r.get("status") == _CANCEL_BLOCKED, r


@pytest.mark.asyncio
async def test_cancel_blocks_on_ambiguous():
    m, r = await _run_tool("cancel_appointment", _CANCEL_ARGS, RETENTION_Q, "um, I'm not sure")
    m.assert_not_awaited(); assert r.get("status") == _CANCEL_BLOCKED, r


@pytest.mark.asyncio
async def test_cancel_blocks_on_absent_reply():
    m, r = await _run_tool("cancel_appointment", _CANCEL_ARGS, RETENTION_Q, "")
    m.assert_not_awaited(); assert r.get("status") == _CANCEL_BLOCKED, r


@pytest.mark.asyncio
async def test_cancel_blocks_on_reschedule_word():
    m, r = await _run_tool("cancel_appointment", _CANCEL_ARGS, RETENTION_Q, "actually, reschedule it instead")
    m.assert_not_awaited(); assert r.get("status") == _CANCEL_BLOCKED, r


@pytest.mark.asyncio
async def test_cancel_blocks_when_retention_never_asked():
    # A stray "cancel it" with no retention question in last_bot_prompt must not cancel.
    m, r = await _run_tool("cancel_appointment", _CANCEL_ARGS, UNRELATED, "cancel it")
    m.assert_not_awaited(); assert r.get("status") == _CANCEL_BLOCKED, r


@pytest.mark.asyncio
async def test_cancel_blocks_on_bare_yes():
    # THE ambiguity guard: a bare "yes" to "reschedule, or cancel?" is ambiguous.
    m, r = await _run_tool("cancel_appointment", _CANCEL_ARGS, RETENTION_Q, "yes")
    m.assert_not_awaited(); assert r.get("status") == _CANCEL_BLOCKED, r


@pytest.mark.asyncio
async def test_cancel_blocks_on_dont_cancel():
    m, r = await _run_tool("cancel_appointment", _CANCEL_ARGS, RETENTION_Q, "no, don't cancel it")
    m.assert_not_awaited(); assert r.get("status") == _CANCEL_BLOCKED, r


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "I don't want to cancel it",
    "no I don't want to cancel",
    "no cancellation",
    "not cancel it",
    "leave the cancellation",
])
async def test_cancel_blocks_on_negated_cancel(reply):
    """Review finding: a reply that negates cancelling but still contains a 'cancel'
    token must NOT cancel. A caller saying 'I don't want to cancel' must never be
    cancelled — the destructive-write false-allow the negation guard closes."""
    m, r = await _run_tool("cancel_appointment", _CANCEL_ARGS, RETENTION_Q, reply)
    m.assert_not_awaited(); assert r.get("status") == _CANCEL_BLOCKED, r


# ── cancel: must ALLOW (explicit cancel token + retention asked) ─────────────
@pytest.mark.asyncio
async def test_cancel_allows_explicit_cancel():
    m, r = await _run_tool("cancel_appointment", _CANCEL_ARGS, RETENTION_Q, "cancel it altogether")
    m.assert_awaited_once(); assert r.get("status") == "ok", r


@pytest.mark.asyncio
async def test_cancel_allows_yes_cancel_it():
    m, r = await _run_tool("cancel_appointment", _CANCEL_ARGS, RETENTION_Q, "yes, cancel it")
    m.assert_awaited_once(); assert r.get("status") == "ok", r


# ── reschedule: mirrors FM-01 ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_reschedule_blocks_on_no():
    m, r = await _run_tool("reschedule_appointment", _RESCHED_ARGS, RESCHEDULE_CTA, "no")
    m.assert_not_awaited(); assert r.get("status") == _RESCHED_BLOCKED, r


@pytest.mark.asyncio
async def test_reschedule_blocks_on_ambiguous():
    m, r = await _run_tool("reschedule_appointment", _RESCHED_ARGS, RESCHEDULE_CTA, "hmm, not sure")
    m.assert_not_awaited(); assert r.get("status") == _RESCHED_BLOCKED, r


@pytest.mark.asyncio
async def test_reschedule_blocks_when_cta_never_asked():
    m, r = await _run_tool("reschedule_appointment", _RESCHED_ARGS, UNRELATED, "yes")
    m.assert_not_awaited(); assert r.get("status") == _RESCHED_BLOCKED, r


@pytest.mark.asyncio
async def test_reschedule_allows_yes_after_cta():
    m, r = await _run_tool("reschedule_appointment", _RESCHED_ARGS, RESCHEDULE_CTA, "yes please")
    m.assert_awaited_once(); assert r.get("status") == "ok", r
