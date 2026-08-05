# tests/regression/test_stall_phrases_on_cancel_and_reschedule.py
"""
The stall a caller hears while cancelling or moving an appointment.

Owner instruction, 2026-08-05: the waits on cancellation, rescheduling and
confirmation should sound understanding rather than worrying. Two things were
wrong, and the first was a genuine inconsistency rather than a matter of taste:

* **`LOOKUP_FILLERS` contained "Bear with me just a moment…"** — while
  `config.SILENCE_RULE` bans that exact phrase and `turn_handler`'s strip rules
  delete it from model speech. Deterministic fillers go straight onto the TTS
  queue, so this list was the one remaining path by which a caller could hear
  the phrase the rest of the engine forbids.

* **`cancel_appointment` and `reschedule_appointment` had no filler at all.**
  `_FILLER_TOOLS` covered availability, booking and lookup, so the calendar
  round-trip after the caller's go-ahead was silence. `B-40` measured 11.1 s of
  it on a live cancel turn.

The safety property being pinned is that none of these lines can be heard as a
COMPLETED action. They describe work in progress, so a caller who is told "I'm
taking care of that for you now" and then hears a failure message has not been
lied to. They are also checked against Gate 5f's real claim detector, because a
filler bypasses `sanitise_response` and nothing else would catch it.
"""
from __future__ import annotations

import pytest

from app.filler_phrases import (
    BOOKING_WRITE_FILLERS,
    CANCEL_WRITE_FILLERS,
    LOOKUP_FILLERS,
    RESCHEDULE_WRITE_FILLERS,
    THINKING_FILLERS_PRIMARY,
    THINKING_FILLERS_SECONDARY,
)
from app.media_streams import turn_handler as th
from app.media_streams.config import FILLER_PHRASES
from app.media_streams.turn_handler import (
    WRITE_FAMILY_BOOKING,
    WRITE_FAMILY_CANCEL,
    WRITE_FAMILY_RESCHEDULE,
)

ALL_POOLS = {
    "THINKING_FILLERS_PRIMARY": THINKING_FILLERS_PRIMARY,
    "THINKING_FILLERS_SECONDARY": THINKING_FILLERS_SECONDARY,
    "LOOKUP_FILLERS": LOOKUP_FILLERS,
    "BOOKING_WRITE_FILLERS": BOOKING_WRITE_FILLERS,
    "CANCEL_WRITE_FILLERS": CANCEL_WRITE_FILLERS,
    "RESCHEDULE_WRITE_FILLERS": RESCHEDULE_WRITE_FILLERS,
    "FILLER_PHRASES": FILLER_PHRASES,
}

# From config.SILENCE_RULE — what the model is forbidden to say. A deterministic
# filler is held to the same standard; it is spoken by the same voice.
BANNED = (
    "bear with me", "bare with me", "one moment please", "just a moment",
    "bear with", "still there", "did you hear me", "can you hear me",
    "are you still there",
)


@pytest.mark.parametrize("pool_name", sorted(ALL_POOLS))
def test_no_filler_says_what_the_model_is_banned_from_saying(pool_name):
    for phrase in ALL_POOLS[pool_name]:
        low = phrase.lower()
        for banned in BANNED:
            assert banned not in low, (
                f"{pool_name} contains {banned!r} — SILENCE_RULE bans it and "
                f"turn_handler strips it from model speech, so a filler is the "
                f"only way it can still reach a caller: {phrase!r}"
            )


def test_the_phrase_the_owner_named_is_gone():
    assert "Just a second…" not in FILLER_PHRASES


@pytest.mark.parametrize(
    "pool_name", ["CANCEL_WRITE_FILLERS", "RESCHEDULE_WRITE_FILLERS", "LOOKUP_FILLERS"]
)
def test_the_anxious_flows_get_a_reassuring_opener(pool_name):
    """Not a style assertion for its own sake: these three pools are the only
    speech on the turns where the caller is waiting to hear whether they are
    losing an appointment or being charged for one."""
    # The property is that the line speaks TO the caller — it either softens
    # ("no problem at all") or offers to act on their behalf ("let me…",
    # "…for you"). A bare "One moment." does neither.
    warm = (
        "no problem", "of course", "not to worry", "that's fine",
        "that's absolutely fine", "let me", "for you",
    )
    for phrase in ALL_POOLS[pool_name]:
        low = phrase.lower()
        assert any(w in low for w in warm), (
            f"{pool_name} entry has no reassuring wording: {phrase!r}"
        )


@pytest.mark.parametrize(
    "pool_name,family",
    [
        ("CANCEL_WRITE_FILLERS", WRITE_FAMILY_CANCEL),
        ("RESCHEDULE_WRITE_FILLERS", WRITE_FAMILY_RESCHEDULE),
        ("BOOKING_WRITE_FILLERS", WRITE_FAMILY_BOOKING),
        ("LOOKUP_FILLERS", WRITE_FAMILY_CANCEL),
        ("FILLER_PHRASES", WRITE_FAMILY_CANCEL),
    ],
)
def test_no_filler_reads_as_a_completed_write(pool_name, family):
    """A filler is queued straight to TTS and never passes through
    `sanitise_response`, so Gate 5f cannot catch a claim in one. Each line is put
    through the real detector here instead — this is the only place that check
    can happen for these strings."""
    for phrase in ALL_POOLS[pool_name]:
        assert th._false_write_claim(phrase, family) is False, (
            f"{pool_name} entry reads as a completed {family}: {phrase!r} — a "
            f"caller would be told the write happened while it is still running"
        )


@pytest.mark.parametrize("pool_name", sorted(ALL_POOLS))
def test_every_pool_can_still_rotate(pool_name):
    """`pick_filler` resets and re-picks when a pool is exhausted, and
    `_second_filler_text` must be able to find a non-repeat, so a pool of one
    would make a slow turn say the same line twice — the B-30 shape."""
    pool = ALL_POOLS[pool_name]
    assert len(pool) >= 2, f"{pool_name} cannot rotate"
    assert len(set(pool)) == len(pool), f"{pool_name} has a duplicate"


def test_the_two_new_write_tools_are_wired_to_their_pools():
    """The lists exist to be used. Read from the source of `_execute_tools`,
    because `_FILLER_TOOLS` is a local built inside it — the wiring is what
    B-40's silence was, not the wording."""
    import inspect

    from app.media_streams.llm_stream import LLMStream

    src = inspect.getsource(LLMStream._execute_tools)
    for tool, pool in (
        ("cancel_appointment", "CANCEL_WRITE_FILLERS"),
        ("reschedule_appointment", "RESCHEDULE_WRITE_FILLERS"),
    ):
        assert f'"{tool}"' in src and pool in src, (
            f"{tool} is not wired to {pool} — its write turn is silent again"
        )
