# tests/regression/test_confirm_phone_bare_yes.py
"""
CONFIRM_PHONE re-asked forever when the caller answered "yes".

Reproduced 2026-07-26 on `latency-eval`. The deterministic gate asks a plain
yes/no question — *"Just to check — is that 0 7 7 0 0, 9 0 0, 4 5 6?"* — and then
did not accept a plain answer:

    HARD GATE CONFIRM_PHONE: ambiguous 'yes' — tight re-ask

`_HG_YES` required a phrase ("that's right", "correct", "use this number").
A bare "yes" / "yeah" / "yep" fell to the ambiguous branch, which re-emits the
identical question with no retry counter, no escalation and no transfer — so the
caller was asked the same thing for the rest of the call, `phone_confirmed` stayed
`False`, and the flow never reached CONFIRM_BOOKING. `\bno\b` *was* matched, so
the gate accepted a bare no and refused a bare yes on a yes/no question.

History: `5c7ea4e` (24 Apr 2026) replaced yes/no phone confirmation with explicit
phrase commands and removed the bare affirmatives. `3bbe4f0` (10 Jun 2026)
reversed that on the LLM path — `connection.py._PHONE_CONFIRM_AFFIRMATIVES`
re-admits bare "yes" because verbal confirmations were "falling through, leaving
phone_confirmed unset". The deterministic gate was never brought along, and it is
the surviving cause of FIX_QUEUE_PRE_DEMO A1's magic-phrase friction (Jules rows
17/19/21: 150-261 s, no booking).

The fix accepts word-bounded bare affirmatives, but *only* behind
`phone_confirm_armed` — the turn-boundary guard that the April change was really
defending against (a split-turn surname remnant landing on this gate). Weak
tokens ("ok", "right", "fine") deliberately still do not confirm a number.

Covered here: bare yes accepted; negatives still route to correction; weak tokens
still re-ask; "yesterday" cannot confirm; an unarmed gate still refuses.
"""
from __future__ import annotations

import copy

import pytest

from app.media_streams.flow import FlowEngine, BOOKING_FLOW, _COLLECT_PHONE_INDEX
from app.media_streams.session import DEFAULT_MS_SESSION


class _FakeTTS:
    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)

    def empty(self):
        return not self.items

    def get_nowait(self):
        return self.items.pop(0)


async def _noop_llm(*_a, **_k):
    return ""


async def _engine_at_confirm_phone():
    """Drive the booking flow to CONFIRM_PHONE with a number read back."""
    session = copy.deepcopy(DEFAULT_MS_SESSION)
    session.update({
        "full_name":           "Tom Brown",
        "state":               "COLLECT_PHONE",
        "flow_state":          "COLLECT_PHONE",
        "flow_step":           _COLLECT_PHONE_INDEX,
        "phone_from_twilio":   True,
        "phone_confirmed":     False,
        "phone_confirm_armed": False,
        "phone_dtmf_buffer":   "",
        "phone_digits_buffer": "",
        "phone_awaiting_dtmf": True,
        "selected_location":   "alcester",
    })
    tts = _FakeTTS()
    engine = FlowEngine(session, tts, _noop_llm)
    engine._active_flow = BOOKING_FLOW
    engine._intent_detected = True

    await engine.handle_transcript("07700900456")
    assert session.get("state") == "CONFIRM_PHONE"
    assert session.get("phone_confirm_armed") is True
    tts.items.clear()
    return engine, session, tts


# ── The defect ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("answer", ["yes", "yeah", "yep", "yup", "yes thanks"])
async def test_bare_affirmative_confirms_the_number(answer):
    engine, session, _tts = await _engine_at_confirm_phone()
    await engine.handle_transcript(answer)
    assert session.get("phone_confirmed") is True, (
        f"{answer!r} answers the yes/no question the gate just asked"
    )
    assert session.get("state") != "CONFIRM_PHONE", "must not re-ask"
    assert session.get("phone_confirm_armed") is False, "gate must disarm once consumed"


async def test_bare_yes_does_not_loop():
    """The ambiguous branch has no counter and no escalation, so a rejected bare
    yes was unbounded — the caller never leaves this state."""
    engine, session, tts = await _engine_at_confirm_phone()
    first_question = session.get("last_question")
    await engine.handle_transcript("yes")
    assert session.get("last_question") != first_question or session.get("phone_confirmed") is True


# ── Existing accepts preserved ────────────────────────────────────────────
@pytest.mark.parametrize("answer", [
    "that's right", "yes that's right", "that's correct", "correct",
    "use this number",
])
async def test_phrase_commands_still_confirm(answer):
    engine, session, _tts = await _engine_at_confirm_phone()
    await engine.handle_transcript(answer)
    assert session.get("phone_confirmed") is True


# ── Negatives and weak tokens unchanged ───────────────────────────────────
@pytest.mark.parametrize("answer", [
    "no",
    "nope",
    "no use a different number",
    "yeah use a different number",   # affirmative token, negative intent
    "yes but that's the wrong number",
])
async def test_negatives_never_confirm(answer):
    engine, session, _tts = await _engine_at_confirm_phone()
    await engine.handle_transcript(answer)
    assert session.get("phone_confirmed") is not True, (
        f"{answer!r} rejects the number — it must never confirm it"
    )


@pytest.mark.parametrize("answer", ["ok", "okay", "right", "fine"])
async def test_weak_tokens_still_re_ask(answer):
    """These are the split-turn remnants the April change defended against; they
    stay out of the accept list, so they must not confirm a number."""
    engine, session, _tts = await _engine_at_confirm_phone()
    await engine.handle_transcript(answer)
    assert session.get("phone_confirmed") is not True


async def test_word_boundary_yesterday_does_not_confirm():
    engine, session, _tts = await _engine_at_confirm_phone()
    await engine.handle_transcript("i called yesterday about this")
    assert session.get("phone_confirmed") is not True, (
        "'yesterday' contains 'yes' — the match must be word-bounded"
    )


async def test_unarmed_gate_still_refuses_a_bare_yes():
    """The turn-boundary guard is what makes accepting a bare yes safe: when the
    phone question was NOT the last thing asked, a stray yes must not confirm."""
    engine, session, _tts = await _engine_at_confirm_phone()
    session["phone_confirm_armed"] = False
    await engine.handle_transcript("yes")
    assert session.get("phone_confirmed") is not True
