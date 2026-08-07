"""
Regression (O-3): a spoken transfer request must not end in dead air.

`cc385a8` fixed this for the DTMF path — "press 1 to speak to Mark" now resolves
the dial target BEFORE announcing, and keeps the caller with Susie when there
isn't one. The spoken path was left alone, and it is the one a caller actually
uses: the model streams "let me put you straight through" and only THEN calls
`transfer_to_human`. By the time `_on_transfer_request` runs, the promise has
been made and cannot be retracted. `_handle_transfer` then returns silently when
TRANSFER_DISABLED is set or no target is configured, and the caller waits.

Two things follow from that, both fixed here:

  * `_on_transfer_request` resolves the target itself and, when there is none,
    says the next true thing and arms the watchdog instead of returning into
    silence.
  * `_exec_transfer_to_human` no longer texts the practitioner "call coming
    through now" when no leg will be placed. That message is a false alarm that
    buries the real one — they wait for a ring instead of ringing back.

Note on the register's O-3: its evidence was a caller asking "can I book the
session with Mark?", read as booking intent. That is booking intent — the caller
wants an appointment with a named practitioner, not to be put through to him —
so it is not evidence of a broken transfer. Its cited anchor,
`susie_system_prompt.py:1512`, is inside `get_system_prompt()`, which theorem_v3
never calls; `test_the_cited_guardrail_does_not_reach_this_clinic` pins that.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams.connection import WebSocketCallHandler
from app.prompts.susie_system_prompt import build_system_prompt_parts
from app.tools.receptionist_tools import _exec_transfer_to_human


def _on_transfer_src() -> str:
    """
    Code only. The comment explaining this fix necessarily names both
    `_handle_transfer` and `resolve_transfer_target`, and the ordering
    assertion below would then be measuring prose.
    """
    src = inspect.getsource(WebSocketCallHandler._on_transfer_request)
    return "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )


def test_the_spoken_path_resolves_the_target_before_dialling():
    src = _on_transfer_src()
    assert "resolve_transfer_target" in src, (
        "_on_transfer_request dispatches to _handle_transfer without checking "
        "whether a leg can be placed — a caller already told they are being put "
        "through gets silence"
    )
    assert src.index("resolve_transfer_target") < src.index("_handle_transfer"), (
        "the target is resolved after the dial is attempted, which is too late"
    )


def test_the_no_target_branch_speaks_rather_than_returning():
    src = _on_transfer_src()
    head, _, tail = src.partition("resolve_transfer_target")
    assert "tts_text_queue.put" in tail, "the no-target path emits no speech"
    assert "on_question_asked" in tail, (
        "the watchdog is not armed for the recovery question. The tool path can "
        "reach here with no transcript event, so an unarmed watchdog reproduces "
        "the dead air this guard exists to stop — the same mistake cc385a8 "
        "called out on the DTMF branch"
    )
    assert "conversation_history" in tail, (
        "the recovery question is not written to history, so the next turn "
        "re-opens as though it was never asked"
    )


def test_the_recovery_offers_something_real():
    """
    'I can't put you through' on its own is a dead end. The caller must be left
    with a next step, and it has to be one the system can actually deliver —
    add_to_waitlist texts the practitioner a CALLBACK NEEDED.
    """
    src = _on_transfer_src()
    lowered = src.lower()
    assert "take a message" in lowered or "call you" in lowered, (
        "the no-target recovery does not offer the caller anything"
    )


@pytest.mark.asyncio
async def test_no_dial_target_means_no_call_coming_through_text(monkeypatch):
    """
    The practitioner must not be told a call is inbound when none is.
    """
    sent: list = []

    async def _fake_send(to, message):
        sent.append((to, message))

    monkeypatch.setattr("app.notifications.sms.send_sms", _fake_send)
    monkeypatch.setattr("app.routes.realtime.resolve_transfer_target",
                        lambda session: None)
    monkeypatch.setattr("app.tools.handoff.send_to_sheet",
                        lambda *a, **k: None)

    session = {
        "clinic_id": "theorem_v3",
        "collected": {"name": "John Smith", "phone": "+447700900456"},
        "twilio_from": "+447700900456",
        "call_sid": "CAtest",
    }
    await _exec_transfer_to_human({"reason": "caller asked for a person"}, session)

    # Let the fire-and-forget tasks run.
    import asyncio
    await asyncio.sleep(0)

    bodies = " ".join(m for _, m in sent)
    assert "coming through now" not in bodies, (
        "the practitioner was told a call is coming through, but no dial target "
        "exists so no leg will be placed"
    )
    assert "CALLBACK NEEDED" in bodies, (
        "no dial target and no callback text either — the caller asked for a "
        "human and no human hears about it"
    )


@pytest.mark.asyncio
async def test_the_sweep_kill_switch_stays_completely_silent(monkeypatch):
    """
    TRANSFER_DISABLED exists so a test sweep never texts a real staff number.
    The recovery branch above must not become a hole in it.
    """
    sent: list = []

    async def _fake_send(to, message):
        sent.append((to, message))

    monkeypatch.setattr("app.notifications.sms.send_sms", _fake_send)
    monkeypatch.setattr("app.config.TRANSFER_DISABLED", True)
    monkeypatch.setattr("app.tools.handoff.send_to_sheet", lambda *a, **k: None)

    session = {
        "clinic_id": "theorem_v3",
        "collected": {"name": "John Smith", "phone": "+447700900456"},
        "call_sid": "CAtest",
    }
    await _exec_transfer_to_human({"reason": "sweep"}, session)

    import asyncio
    await asyncio.sleep(0)

    assert sent == [], f"TRANSFER_DISABLED leaked an SMS: {sent}"


def test_a_working_target_still_gets_the_normal_heads_up():
    """The fix must not cost the ordinary case its heads-up."""
    src = inspect.getsource(_exec_transfer_to_human)
    assert "coming through now" in src, (
        "the transfer heads-up SMS is gone — the practitioner now gets no "
        "warning at all before the phone rings"
    )


def test_the_cited_guardrail_does_not_reach_this_clinic():
    """
    O-3 blamed a prompt block at susie_system_prompt.py:1512 ("ONLY in these
    exact situations" plus four NEVERs). theorem_v3 renders via
    _build_theorem_v3; that block lives in get_system_prompt() and is never
    sent. Blaming it would have produced a fix nobody's model ever reads.
    """
    static, dynamic = build_system_prompt_parts({"clinic_id": "theorem_v3", "collected": {}})
    prompt = f"{static}\n{dynamic}"
    assert "ONLY in these exact situations" not in prompt
    assert "transfer_to_human" in prompt, (
        "the live prompt no longer mentions transfer_to_human at all — the "
        "escape hatch is now genuinely unreachable"
    )
