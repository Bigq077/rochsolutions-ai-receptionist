"""
CA166de2a9 (Theorem, 10 Aug 2026) — Susie described an internal block to the
caller as a fact about Mark's calendar.

    15:02:15  "it looks like Wednesday afternoon has filled up"
    15:02:33  "that slot doesn't seem to be available any more"

Neither sentence came from Acuity. check_availability had been BLOCKED — the
post-collect guard refusing to look at the diary at all — and the model, handed
a refusal in the same position a real availability result occupies, explained it
to the caller in the only vocabulary that fits: unavailability.

The caller was left believing a Wednesday afternoon existed and had gone. It had
not. He was later booked on a Thursday he suggested himself.

This is B-58's rule, and the third member of that family:

    a guard must never leave the model able to state world state it was not told.

B-58 was a refusal RULE that asserted "their original appointment still stands".
This is a refusal MESSAGE that implies the diary was read. Same shape, different
surface.

── Why the clause and not stronger obedience wording ───────────────────────
The blocks already say "Do NOT call check_availability" and "Say EXACTLY this,
then stop", and the model still narrated. That is the lesson of
test_blocked_tool_forces_text: the message and the frame it arrives in said
opposite things and the frame won. Here the frame is "a tool that answers
availability questions has returned", and nothing in the payload contradicted
the inference. The clause contradicts it explicitly.

── The deliberate exclusion ────────────────────────────────────────────────
`already_retrieved` returns session["available_days"] — real diary data. A claim
about availability made from THAT is grounded, and gagging it would stop Susie
saying the true thing. Only the refusals that consult nothing are covered.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.media_streams.llm_stream import _NOT_AVAILABILITY_NEWS


_SRC = Path(__file__).resolve().parents[2] / "app" / "media_streams" / "llm_stream.py"


def _window_after(marker: str, size: int = 1400) -> str:
    src = _SRC.read_text(encoding="utf-8")
    idx = src.index(marker)
    return src[idx: idx + size]


# ── 1. The clause says the necessary things ─────────────────────────────────

def test_the_clause_denies_the_inference_rather_than_the_action():
    """
    "Do NOT call check_availability" constrains a tool call. What went wrong was
    a SENTENCE, so the clause has to constrain the sentence.
    """
    low = _NOT_AVAILABILITY_NEWS.lower()
    assert "not a reading of the diary" in low
    assert "nothing has been checked" in low


@pytest.mark.parametrize(
    "word",
    # The vocabulary the model actually reached for, plus its near neighbours.
    ["full", "taken", "gone", "unavailable", "no longer free"],
)
def test_the_clause_names_the_words_it_forbids(word):
    """
    A general "do not speculate" would not have caught "it looks like Wednesday
    afternoon has filled up" — that reads to the model as reporting, not
    speculating. The forbidden claims are named.
    """
    assert word in _NOT_AVAILABILITY_NEWS.lower()


# ── 2. It is attached to the refusals that consult nothing ──────────────────

@pytest.mark.parametrize(
    "marker",
    [
        # The block that fired seven times on the live call.
        '"error": "booking_details_already_complete"',
        # Its neighbour — the "both or neither" pair from 117c56a. Releasing or
        # hardening one and not the other has already cost one call.
        '"status": "slot_already_confirmed"',
    ],
)
def test_the_no_data_refusals_carry_the_clause(marker):
    assert "_NOT_AVAILABILITY_NEWS" in _window_after(marker), (
        f"{marker} refuses without reading the diary, so its message must say so"
    )


def test_the_grounded_result_does_not_carry_the_clause():
    """
    already_retrieved hands back available_days. Pinned as a deliberate
    exclusion so a later sweep does not "finish the job" and gag a true
    statement about availability.
    """
    assert "_NOT_AVAILABILITY_NEWS" not in _window_after('"status": "already_retrieved"')


# ── 3. It is appended, not substituted ──────────────────────────────────────

def test_the_readback_instruction_survives_the_clause():
    """
    The booking_details_already_complete message is load-bearing: it carries the
    injected full name and slot phrase that BUG-14 and DEFECT-3 exist to supply.
    Appending must not have replaced it.
    """
    window = _window_after('"error": "booking_details_already_complete"', size=400)
    assert "_rb_msg + _NOT_AVAILABILITY_NEWS" in window, (
        "the readback message must be extended, never replaced"
    )


def test_the_clause_starts_with_a_separator():
    """Concatenated straight onto a message ending in '"' or '.', so it needs
    its own leading space or it fuses two sentences together."""
    assert _NOT_AVAILABILITY_NEWS.startswith(" ")
