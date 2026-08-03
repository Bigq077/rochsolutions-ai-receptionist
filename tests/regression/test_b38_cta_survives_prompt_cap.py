# tests/regression/test_b38_cta_survives_prompt_cap.py
"""
B-38 — the confirmation question can be truncated out of `last_bot_prompt`.

`last_bot_prompt` is capped at 200 characters (`run_turn`). Every write gate read
it directly, so a read-back long enough to push the CTA past the cut made the
gate blind to a question the caller had just been asked.

**Reproduced 3 Aug 2026**, offline, on entirely ordinary wording — service,
practitioner, site, from-time and to-time:

    reschedule read-back + CTA  = 251 chars   -> CTA lost
    cancel read-back + CTA      = 207 chars   -> CTA lost
    the read-back observed live = 148 chars   -> survives, with ~50 to spare

Seven characters over, on the cancel. This is not an edge case; it is one clause
of detail away from the calls already dialled.

**Three things broke together when it fired:**

  1. the write is BLOCKED — B-36 cause 1, arriving by truncation rather than by
     the model rewording;
  2. the caller's "go ahead" is DROPPED by the slot guard — B-37, by a different
     route;
  3. Gate 5f arms and the caller hears a re-steer.

One truncation re-opens two defects that were already fixed and verified.

`last_question` holds exactly the question sentence and is stored **uncapped**,
so it survives the cut. B-31 (`c69eb61`) established this fallback for the
clinical layer; the write gates never received it.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams import connection as c
from app.media_streams import llm_stream as ls


_RESCHEDULE_LONG = (
    "Just to confirm — I'm moving your Initial Assessment with Marcus at our "
    "Bolton clinic from Tuesday the 4th of August at half past six in the "
    "evening to Monday the 10th of August at quarter past six in the evening. "
    "Shall I go ahead and move it for you?"
)
_CANCEL_LONG = (
    "I can see your Initial Assessment with Marcus at the Bolton clinic on "
    "Wednesday the 5th of August at quarter past seven in the evening. Would "
    "you like to reschedule this appointment, or cancel it altogether?"
)
_BOOKING_LONG = (
    "So that's Quentin Rock, an Initial Assessment with Marcus at our Bolton "
    "clinic on Monday the 10th of August at quarter past six in the evening, "
    "and I've got your number as oh seven five oh two. Shall I go ahead and "
    "book that in?"
)


def _session_from(full_reply: str) -> dict:
    """Exactly what run_turn stores: the prompt capped, the question not."""
    return {
        "last_bot_prompt": full_reply[:200],
        "last_question": ls._question_from_response(full_reply),
    }


# ── The reproduction ──────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "full,predicate",
    [
        (_RESCHEDULE_LONG, ls._move_confirmation_asked),
        (_CANCEL_LONG, ls._cancel_retention_asked),
        (_BOOKING_LONG, ls._booking_confirmation_asked),
    ],
)
def test_the_cap_really_does_lose_the_cta(full, predicate):
    """Fixture guard. If this ever fails the read-backs got shorter and the
    tests below stopped testing anything."""
    assert len(full) > 200, "fixture drift: this read-back no longer overruns"
    assert predicate(full[:200]) is False, (
        "fixture drift: the CTA now survives the cap on its own"
    )


@pytest.mark.parametrize(
    "full,predicate",
    [
        (_RESCHEDULE_LONG, ls._move_confirmation_asked),
        (_CANCEL_LONG, ls._cancel_retention_asked),
        (_BOOKING_LONG, ls._booking_confirmation_asked),
    ],
)
def test_the_gate_still_sees_the_cta_through_last_question(full, predicate):
    assert ls._cta_asked(_session_from(full), predicate) is True


def test_the_b37_bypass_also_survives_the_cap():
    """Otherwise the caller's "go ahead" is dropped by the slot guard — B-37
    returning by a different route."""
    assert c._write_cta_outstanding(_session_from(_RESCHEDULE_LONG)) is True
    assert c._write_cta_outstanding(_session_from(_CANCEL_LONG)) is True


def test_the_short_readback_that_was_observed_live_still_works():
    """CA80f2d410 ran 148 chars. The fix must not depend on overrunning."""
    short = (
        "Just to confirm — I'm moving your appointment to Monday the 10th of "
        "August at quarter past six in the evening. Shall I go ahead and move "
        "it for you?"
    )
    assert len(short) < 200
    assert ls._cta_asked(_session_from(short), ls._move_confirmation_asked) is True


# ── It must not open on turns where nothing was asked ─────────────────────
@pytest.mark.parametrize(
    "prompt,question",
    [
        ("We're open until six.", "Would you like me to check Tuesday?"),
        ("Any of those work?", "Any of those work?"),
        ("", ""),
        ("I can see an appointment on Wednesday.", ""),
    ],
)
def test_no_cta_means_no_gate(prompt, question):
    s = {"last_bot_prompt": prompt, "last_question": question}
    for predicate in (
        ls._booking_confirmation_asked,
        ls._move_confirmation_asked,
        ls._cancel_retention_asked,
    ):
        assert ls._cta_asked(s, predicate) is False
    assert c._write_cta_outstanding(s) is False


def test_the_two_sources_are_judged_whole_never_concatenated():
    """The hazard the design avoids. Joining the two fields can span a match
    that neither field contains — here a prompt ending "I'll book that" beside a
    question "in June?" reads as the booking CTA "book that in" and would open
    the gate on a sentence nobody said."""
    lbp, lq = "Right, I'll book that", "in June?"
    assert ls._booking_confirmation_asked(f"{lbp} {lq}") is True, (
        "fixture drift: the join no longer produces the false match"
    )
    assert ls._cta_asked(
        {"last_bot_prompt": lbp, "last_question": lq},
        ls._booking_confirmation_asked,
    ) is False


# ── Wiring and lifetime ───────────────────────────────────────────────────
def test_no_gate_reads_the_capped_prompt_directly_any_more():
    """A fallback applied to three of four predicates and forgotten on the
    fourth is the failure this test exists to prevent."""
    src = inspect.getsource(ls.LLMStream._execute_tools)
    for predicate in (
        "_booking_confirmation_asked",
        "_move_confirmation_asked",
        "_cancel_retention_asked",
    ):
        assert f'{predicate}(session.get("last_bot_prompt"))' not in src, (
            f"{predicate} still reads the 200-char capped prompt directly"
        )
    assert src.count("_cta_asked(session,") >= 4


def test_last_question_is_assigned_unconditionally_so_it_cannot_go_stale():
    """The one way this fallback could become unsafe: a turn that asks nothing
    leaving an older CTA standing, so a later "yes" satisfies a gate for a
    question the caller was never asked on that turn."""
    src = inspect.getsource(ls.LLMStream.run_turn)
    assert "session[F_LAST_QUESTION] = _question_from_response(_display_reply)" in src
    assert "if " not in src.split("session[F_LAST_QUESTION]")[1].split("\n")[0]


def test_a_turn_that_asked_nothing_clears_the_question():
    """The behavioural half of the staleness check."""
    assert ls._question_from_response("That's all done — you're booked in.") == ""
