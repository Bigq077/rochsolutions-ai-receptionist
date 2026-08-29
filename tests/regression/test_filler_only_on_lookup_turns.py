"""
Regression: the hold clip fired on every booking turn, not just slot lookups.

FillerGuard's only gate was session["booking_flow_active"], which goes True the
moment the caller says "I'd like to book" and stays True until the call ends. So
the clip fired on every turn of the booking, including turns that call no tool
at all.

Measured on CA1e7552819091949f02c08a39f5203d36 (2026-08-07, build cf0f35df0b49):

    23:42:46  clip   ← "anytime next week"          → SLOTS READ OUT     ✅
    23:42:48  clip 2 ← (same turn)                                       ✅
    23:43:02  clip   ← "the 10th at 5 in the evening" → readback + name   ❌
    23:43:05  clip 2 ← (same turn)                                       ❌
    23:43:21  clip   ← "that would be Quentin Rock"  → NO TOOL           ❌
    23:43:35  clip   ← "use this number"             → NO TOOL           ❌

Owner decision 2026-08-08: the clip belongs at exactly ONE moment — the caller
has given a day/time preference and Susie is about to fetch and read out the
options. That is the only turn with a cold Acuity round trip and nothing to say
meanwhile.

Note "a tool will run" is NOT the rule. The 23:43:02 turn DID call
check_availability, but it ended in a readback and a name request rather than a
slot presentation, so the clip does not belong there either. Gating on "is a
tool coming?" would have kept it.

Suppression is not silence: with the clip quiet `_filler_clip_spoke_this_turn`
stays False, so with_filler speaks its own TTS phrase at tool invocation. A
re-lookup still gets a voice — a spoken one instead of a recorded one.

The fix is an `expect_lookup` gate, decided by the caller from STRUCTURAL stage
state rather than by sniffing what Susie last said. The sibling check in the
watchdog (connection.py ~4116) does the phrase-matching thing —
`"particular day or time" in last_bot_prompt` — and that is both the failure
mode this codebase has hit three times and a read of a 200-char-truncated field.

Note the flag is read as an ATTRIBUTE (`self.post_slot_confirmation_pending`).
`session["post_slot_confirmation_pending"]` is never assigned anywhere in the
repo, so gating on the session key would have silently read None forever and
the guard would have looked fixed while changing nothing.
"""
from __future__ import annotations

import asyncio

import pytest

from app.media_streams.filler_guard import FillerGuard, expect_slot_presentation


# ── the rule itself, one case per firing in the call above ──────────────────

def _stage(**over) -> bool:
    """Default = the pre-slot moment; override one stage at a time.

    The two positive conditions were added 2026-08-08 (CAd34a122247) when the
    predicate became an allow-list — the defaults here assert the moment this
    file was always describing, so every case below still reads as it did.
    """
    kw = dict(
        timing_preference_known=True,
        slots_already_presented=False,
        slot_map_active=False,
        name_collection_pending=False,
        phone_collection_active=False,
        location_question_active=False,
    )
    kw.update(over)
    return expect_slot_presentation(**kw)


def test_rule_fires_when_slots_are_about_to_be_read_out():
    """23:42:46 — 'anytime next week'. The one moment it belongs to."""
    assert _stage() is True


def test_rule_is_silent_once_options_are_on_the_table():
    """
    23:43:02 — 'the 10th at 5 in the evening'. This turn DOES call
    check_availability, so any gate keyed on "is a tool coming?" keeps it. The
    rule is slot PRESENTATION, and this turn ends in a readback plus a name
    request, so the clip does not belong.
    """
    assert _stage(slot_map_active=True) is False


def test_rule_is_silent_during_name_collection():
    """23:43:21 — 'that would be Quentin Rock'. No tool at all."""
    assert _stage(name_collection_pending=True) is False


def test_rule_is_silent_during_phone_collection():
    """23:43:35 — 'use this number'. No tool at all."""
    assert _stage(phone_collection_active=True) is False


def test_rule_is_silent_while_still_picking_a_clinic():
    assert _stage(location_question_active=True) is False


def test_any_single_stage_is_enough_to_silence_it():
    """
    Deny-by-default: the stages are ORed, so no combination re-opens the clip.
    This repo has a standing defect class where a guard has several arms and
    fixing one leaves the others live.
    """
    import itertools

    names = [
        "slot_map_active",
        "name_collection_pending",
        "phone_collection_active",
        "location_question_active",
    ]
    for n in range(1, len(names) + 1):
        for combo in itertools.combinations(names, n):
            assert _stage(**{k: True for k in combo}) is False, combo


def _guard(sent: list[bytes]) -> FillerGuard:
    """A guard with a real clip loaded, capturing what it would send."""
    async def _send(b: bytes) -> None:
        sent.append(b)

    from app.media_streams.connection import _AUDIO_CLIPS_DIR

    return FillerGuard(
        clip_path=_AUDIO_CLIPS_DIR / "filler_checking.ulaw",
        send_audio=_send,
    )


async def _run(guard: FillerGuard, session: dict, expect_lookup: bool) -> None:
    """arm(), then wait past the 350ms primary delay without cancelling."""
    await guard.arm(session, expect_lookup=expect_lookup)
    await asyncio.sleep(0.6)
    guard.cancel()


# ── the two firings that should stop ────────────────────────────────────────

async def test_no_clip_on_the_name_turn():
    """23:43:21 — post_slot_confirmation_pending is True; no tool runs."""
    sent: list[bytes] = []
    guard = _guard(sent)
    await _run(guard, {"booking_flow_active": True}, expect_lookup=False)
    assert sent == [], "clip fired on the name turn"
    assert guard.has_played is False


async def test_no_clip_on_the_phone_turn():
    """23:43:35 — v3_phone_dtmf_active is True; no tool runs."""
    sent: list[bytes] = []
    guard = _guard(sent)
    await _run(guard, {"booking_flow_active": True}, expect_lookup=False)
    assert sent == [], "clip fired on the phone turn"


# ── the firings that must survive ───────────────────────────────────────────

async def test_clip_still_fires_on_a_lookup_turn():
    """23:42:46 — the whole reason the clip exists. Must not regress."""
    sent: list[bytes] = []
    guard = _guard(sent)
    await _run(guard, {"booking_flow_active": True}, expect_lookup=True)
    assert len(sent) == 1, "the availability-lookup clip stopped firing"
    assert guard.has_played is True


async def test_expect_lookup_defaults_to_true():
    """The four non-v3 arm sites pass no kwarg and must keep working."""
    sent: list[bytes] = []
    guard = _guard(sent)
    await guard.arm({"booking_flow_active": True})
    await asyncio.sleep(0.6)
    guard.cancel()
    assert len(sent) == 1


# ── the gate must not resurrect the outer gate ──────────────────────────────

async def test_expect_lookup_does_not_override_booking_flow_active():
    """Outside the booking flow the clip stays silent regardless."""
    sent: list[bytes] = []
    guard = _guard(sent)
    await _run(guard, {"booking_flow_active": False}, expect_lookup=True)
    assert sent == []


# ── the per-turn flag with_filler reads must still be reset ─────────────────

async def test_suppressed_turn_still_clears_the_with_filler_flag():
    """
    with_filler reads _filler_clip_spoke_this_turn to decide whether its own
    opening phrase would be a second way of saying the same thing. A suppressed
    turn must clear it, or a stale True from the previous lookup turn leaks
    forward and silences the TTS filler on a turn where nothing else speaks —
    turning an over-firing bug into a dead-air bug.
    """
    sent: list[bytes] = []
    guard = _guard(sent)
    session = {"booking_flow_active": True}

    # Turn 1: a real lookup — the clip speaks and sets the flag.
    await _run(guard, session, expect_lookup=True)
    assert session["_filler_clip_spoke_this_turn"] is True

    # Turn 2: the name turn — suppressed, and the flag must go back to False.
    await _run(guard, session, expect_lookup=False)
    assert session["_filler_clip_spoke_this_turn"] is False, (
        "stale True leaked into a suppressed turn — with_filler would now "
        "suppress its phrase too and the caller hears nothing"
    )


async def test_flag_is_cleared_before_the_gate_not_after():
    """Same hazard on the outer gate: leaving the flow must not strand True."""
    sent: list[bytes] = []
    guard = _guard(sent)
    session = {"booking_flow_active": True}
    await _run(guard, session, expect_lookup=True)
    assert session["_filler_clip_spoke_this_turn"] is True

    session["booking_flow_active"] = False
    await guard.arm(session)
    assert session["_filler_clip_spoke_this_turn"] is False
