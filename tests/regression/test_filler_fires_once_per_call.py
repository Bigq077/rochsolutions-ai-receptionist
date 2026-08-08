"""
Regression (CAd34a122247, Vital Edge, 2026-08-08 08:36:12-08:38:20): the hold
clip fired nine times in a 123-second call, and not once before a slot
presentation.

Owner report: "they fire way too regularly."

`expect_slot_presentation` was four exclusions and nothing else — not the DTMF
grid, not name, not phone, not location. Vital Edge is single-site, so the
location question auto-confirms at second one and for most of the call NONE of
the four were active. Every turn qualified.

Timing every LLM round trip in that call is what settles the rule. Turn duration
is bimodal with nothing between the modes:

    turn                        iterations   duration
    "checkup and deep massage"      1          1.27s
    price FAQ                       1          2.39s
    "90 minutes please"             1          2.31s   (already suppressed)
    phone confirm                   1          3.10s
    "um anytime to be honest"       3          7.05s   <- wanted the clip
    "yeah that'll be quentin rock"  3          8.68s   <- see below
    "yeah monday at 10 works"       1          2.49s
    "yeah 9 in the morning works"   1          2.22s

One model iteration costs ~2.3s all in. What separates the modes is whether a
TOOL runs — two iterations minimum, so ~4.6s before the provider answers. Stage
is a proxy for that, and a bare deny-list is a bad one.

The 8.68s name turn is not a counterexample. It ran check_availability, got
`booking_details_already_complete`, and retried three times; that block sits in
an elif chain BEFORE TOOL_EXECUTORS, so `with_filler` never ran either and the
clip was the only cover. Fixing the retry makes it a one-iteration turn.

Three properties are pinned here. The first two are the rule; the third is what
makes the rule survive being wrong about the other two.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.media_streams.filler_guard import FillerGuard, expect_slot_presentation


_ALL_CLEAR = dict(
    timing_preference_known=True,
    slots_already_presented=False,
    slot_map_active=False,
    name_collection_pending=False,
    phone_collection_active=False,
    location_question_active=False,
)


# ── 1. The allow-list ───────────────────────────────────────────────────────

def test_the_lookup_turn_still_qualifies():
    """"um anytime to be honest" — 7.05s, three iterations. The one turn in the
    call that genuinely wanted cover."""
    assert expect_slot_presentation(**_ALL_CLEAR) is True


def test_no_timing_preference_means_nothing_to_look_up():
    """
    The condition that silences the first half of the call. Before the caller
    has expressed any day or time there is nothing to fetch, so the turn was
    only ever going to be one model call — 1.3-3.1s in the measured call.

    Covers the 1.27s acknowledgement, the 2.39s price FAQ and the 3.10s phone
    confirmation, none of which called a tool.
    """
    assert expect_slot_presentation(**{**_ALL_CLEAR, "timing_preference_known": False}) is False


def test_slots_already_presented_means_a_readback_not_a_presentation():
    """
    "yeah monday at 10 works" / "yeah 9 in the morning works" — 2.49s and 2.22s,
    one iteration each, choosing between options already on the table.

    Distinct from slot_map_active, which is only True for the DTMF grid. On the
    measured call the options were offered conversationally and the grid was
    never armed, so this was the state that mattered and nothing read it.
    """
    assert expect_slot_presentation(**{**_ALL_CLEAR, "slots_already_presented": True}) is False


@pytest.mark.parametrize(
    "stage",
    [
        "slot_map_active",
        "name_collection_pending",
        "phone_collection_active",
        "location_question_active",
    ],
)
def test_the_original_stage_exclusions_still_hold(stage):
    """The allow-list is additive — it did not loosen anything."""
    assert expect_slot_presentation(**{**_ALL_CLEAR, stage: True}) is False


def test_the_predicate_requires_a_positive_reason_to_fire():
    """
    The shape of the old bug, stated as a property: with every exclusion clear
    but nothing asserted, the answer must be No. A deny-list returns True here,
    which is how a single-site clinic made every turn qualify.
    """
    assert expect_slot_presentation(
        timing_preference_known=False,
        slots_already_presented=False,
        slot_map_active=False,
        name_collection_pending=False,
        phone_collection_active=False,
        location_question_active=False,
    ) is False


# ── 2. Once per call ────────────────────────────────────────────────────────

def _guard(tmp_path: Path, sent: list) -> FillerGuard:
    (tmp_path / "filler_checking.ulaw").write_bytes(b"\x01" * 100)
    (tmp_path / "filler_checking_2.ulaw").write_bytes(b"\x02" * 100)

    async def _send(b: bytes) -> None:
        sent.append(b)

    return FillerGuard(clip_path=tmp_path / "filler_checking.ulaw", send_audio=_send)


async def test_the_clip_speaks_at_most_once_per_call(tmp_path):
    """
    The gate that does not depend on reading booking state correctly.

    On the measured call every check_availability was BLOCKED, so none returned
    slots, so `slots_already_presented` never became True and the stage
    exclusions had nothing to bite on — Susie read the options out of her own
    text rather than a slot buffer. A predicate built only from booking state
    inherits every bug in that state. A latch does not.
    """
    sent: list[bytes] = []
    guard = _guard(tmp_path, sent)
    session = {"booking_flow_active": True}

    for _ in range(9):  # the nine turns of CAd34a122247
        await guard.arm(session, delay_ms=10, expect_lookup=True)
        await asyncio.sleep(0.05)
        guard.cancel()

    assert len(sent) == 1, (
        f"clip spoke {len(sent)} times in one call — the whole defect was nine"
    )


async def test_a_turn_that_never_spoke_does_not_spend_the_calls_one_clip(tmp_path):
    """
    The latch is set where audio goes out, not at arm(). A turn whose LLM
    answers inside the 350ms delay is cancelled having said nothing; if that
    burned the latch, the caller would get no clip at all for the whole call.
    """
    sent: list[bytes] = []
    guard = _guard(tmp_path, sent)
    session = {"booking_flow_active": True}

    # Fast turn — cancelled before the clip fires.
    await guard.arm(session, delay_ms=10_000, expect_lookup=True)
    guard.cancel()
    assert not sent
    assert not session.get("_filler_clip_spoke_this_call")

    # The real lookup turn still gets its clip.
    await guard.arm(session, delay_ms=10, expect_lookup=True)
    await asyncio.sleep(0.05)
    guard.cancel()
    assert len(sent) == 1


async def test_the_latch_is_per_call_not_per_process(tmp_path):
    """It lives in `session`, so a new call starts clean. A module- or
    instance-level flag would silence the clip for every caller after the
    first."""
    sent: list[bytes] = []
    guard = _guard(tmp_path, sent)

    for _ in range(3):  # three separate calls
        session = {"booking_flow_active": True}
        await guard.arm(session, delay_ms=10, expect_lookup=True)
        await asyncio.sleep(0.05)
        guard.cancel()

    assert len(sent) == 3


# ── 3. The shared cooldown clock ────────────────────────────────────────────

async def test_the_clip_registers_in_the_filler_cooldown(tmp_path):
    """
    08:37:06.969 the clip said "Let me just check that for you…"; 1.46s later
    llm_stream's ack filler said "Right with you…" on top of it.

    `should_play_filler` exists to stop exactly that and had never been told the
    clip speaks — FillerGuard set `_filler_clip_spoke_this_turn` but never
    called `note_filler_played`. Known and written down in VE_PORT_PLAN.md:166
    before it was heard on a live call.
    """
    from app.filler_phrases import should_play_filler

    sent: list[bytes] = []
    guard = _guard(tmp_path, sent)
    session = {"booking_flow_active": True}

    assert should_play_filler(session) is True, "clean session — nothing spoken yet"

    await guard.arm(session, delay_ms=10, expect_lookup=True)
    await asyncio.sleep(0.05)
    guard.cancel()

    assert sent, "clip should have spoken"
    assert should_play_filler(session) is False, (
        "the clip spoke but the cooldown clock does not know — the ack filler "
        "will queue a second hold phrase on top of it"
    )


async def test_a_silent_turn_does_not_start_the_cooldown(tmp_path):
    """Suppressing the clip must not suppress the TTS filler too. That would
    turn one unnecessary phrase into no phrase at all on a slow turn."""
    from app.filler_phrases import should_play_filler

    sent: list[bytes] = []
    guard = _guard(tmp_path, sent)
    session = {"booking_flow_active": True}

    await guard.arm(session, delay_ms=10, expect_lookup=False)
    await asyncio.sleep(0.05)

    assert not sent
    assert should_play_filler(session) is True
