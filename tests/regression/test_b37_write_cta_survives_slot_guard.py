# tests/regression/test_b37_write_cta_survives_slot_guard.py
"""
B-37 — the answer to a write confirmation was dropped before the LLM saw it.

`CA8d90deb26327b97d8b6f396e55b63272`, 3 Aug 2026 09:38:53, build `e61c8f805d6e`.
Susie asked *"Shall I go ahead and move it for you?"*. The caller said
**"uh go ahead"**. The log:

    [ms_conn] slot fragment ignored — re-arming: 'uh go ahead'

Three words, none of them in `_COMMUNICATIVE_WORDS`, so
`_is_short_meaningless_fragment` discarded it inside the Spec H slot guard. It
never reached the LLM — no `iteration=1`, no tool call. The watchdog then fired
the wrong re-ask (*"which of those would you like?"* — a SLOT re-ask, when the
outstanding question was the move CTA) and the caller had to repeat themselves.
~18 seconds lost.

**Why booking never hit this and reschedule did.** The Spec J bypass one branch
up is armed by `_NAME_REQUEST_PHRASES`, and the booking flow asks for a name
after slot selection. A reschedule already knows the patient from
`lookup_patient`, so it never asks for a name, `post_slot_confirmation_pending`
was never set, and the slot map stayed live straight through the move CTA.

**What this is NOT.** It is not the affirmation verdict. `_book_reply_verdict`
already handles these replies — L1 settles "go ahead" as yes and returns
'unsure' for "go for it", handing it to the L2 classifier that exists for
precisely that phrase (see `_book_verdict_deterministic`, whose docstring names
"go for it" and the booking it lost on CA7e389a47). Nothing in the yes-patterns
needed changing, and `test_the_shared_yes_patterns_were_not_edited` forbids it.

The bypass is a ROUTING decision, not a safety one: dropping is the dangerous
act, and passing to the LLM is safe because the write gate still adjudicates.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams import connection as c


# The three CTAs, in the wording the gates actually accept.
_BOOKING_CTA = "So that's Tom, Tuesday at ten — shall I go ahead and book that in?"
_MOVE_CTA = (
    "Just to confirm — I'm moving your appointment to Monday the 10th of August "
    "at half past four. Shall I go ahead and move it for you?"
)
_CANCEL_CTA = (
    "Would you like to reschedule this appointment, or cancel it altogether?"
)


@pytest.mark.parametrize("cta", [_BOOKING_CTA, _MOVE_CTA, _CANCEL_CTA])
def test_every_write_cta_is_recognised_as_outstanding(cta):
    assert c._write_cta_outstanding({"last_bot_prompt": cta}) is True


@pytest.mark.parametrize(
    "last_bot_prompt",
    [
        "",
        "Any of those work?",
        "Monday 10th August — Number 1, half past four in the afternoon.",
        "Do you have a preference for when you'd like to reschedule to?",
        "Is that the number the appointment was booked under?",
        "I can see an appointment on Tuesday the 4th of August — is that the right one?",
    ],
)
def test_no_cta_outstanding_leaves_the_guard_alone(last_bot_prompt):
    """The bypass must not be open during ordinary slot selection, or a genuine
    slot fragment would start reaching the LLM on every turn."""
    assert c._write_cta_outstanding({"last_bot_prompt": last_bot_prompt}) is False


def test_a_missing_session_does_not_raise():
    assert c._write_cta_outstanding({}) is False
    assert c._write_cta_outstanding(None) is False


# ── The observed failure ──────────────────────────────────────────────────
_REPLIES_THAT_WERE_DROPPED = [
    "uh go ahead",     # <- CA8d90deb2, verbatim
    "go ahead",
    "go for it",       # <- named in _book_verdict_deterministic's docstring
    "i accept",
    "do it",
    "crack on",
    "go on then",
    "that works",
    "sounds good",
]


@pytest.mark.parametrize("reply", _REPLIES_THAT_WERE_DROPPED)
def test_the_dropped_replies_now_reach_the_llm(reply):
    session = {"last_bot_prompt": _MOVE_CTA}
    assert c._slot_guard_bypass_for_write_cta(session, reply) is True


def test_the_verbatim_call_utterance():
    """The exact string from the log, against the exact prompt from the log."""
    assert c._is_short_meaningless_fragment("uh go ahead") is True, (
        "fixture drift: this test exists because the fragment filter drops it"
    )
    assert c._slot_guard_bypass_for_write_cta(
        {"last_bot_prompt": _MOVE_CTA}, "uh go ahead"
    ) is True


@pytest.mark.parametrize("reply", _REPLIES_THAT_WERE_DROPPED)
def test_a_reply_without_a_cta_outstanding_is_still_guarded(reply):
    """The bypass is condition-gated. With no CTA on record the old behaviour
    stands — otherwise this would widen the guard for every slot turn."""
    session = {"last_bot_prompt": "Any of those work?"}
    assert c._slot_guard_bypass_for_write_cta(session, reply) is False


# ── Disfluency is still dropped ───────────────────────────────────────────
@pytest.mark.parametrize(
    "noise", ["um", "uh", "erm", "hmm", "um uh", "uh, erm", "oh well", "so"]
)
def test_pure_disfluency_is_still_dropped_during_a_cta(noise):
    """Passing "um" to the LLM costs a turn and a re-ask for nothing."""
    assert c._is_pure_filler(noise) is True
    assert c._slot_guard_bypass_for_write_cta(
        {"last_bot_prompt": _MOVE_CTA}, noise
    ) is False


@pytest.mark.parametrize(
    "utterance", ["uh go ahead", "um yes", "erm no", "oh go for it", "well do it"]
)
def test_disfluency_plus_content_is_not_pure_filler(utterance):
    """The observed reply began with "uh". Leading noise must not disqualify."""
    assert c._is_pure_filler(utterance) is False


def test_the_filler_list_enumerates_noises_not_intents():
    """Guards the design property, not the behaviour.

    The repeated failure in this codebase is a hand-maintained vocabulary sitting
    between the caller and what they asked for (B-25, the step-8 reword, the
    timing singles, B-36 cause 1). Those lists all enumerate INTENTS and go stale
    when a caller phrases something a new way. This list enumerates noises, so it
    cannot: anything that is not one of these gets through. If a word of meaning
    ever lands in it, that property is gone.
    """
    meaningful = {
        "yes", "no", "yeah", "ok", "okay", "sure", "go", "ahead", "do", "it",
        "accept", "book", "move", "cancel", "please", "fine", "works", "right",
    }
    assert not (c._PURE_FILLER_TOKENS & meaningful), (
        f"a word of meaning entered the filler list: "
        f"{c._PURE_FILLER_TOKENS & meaningful}"
    )


# ── Ordering inside the guard ─────────────────────────────────────────────
def test_the_bypass_is_checked_before_the_fragment_drop():
    """Load-bearing. If the drop ran first the bypass would be unreachable."""
    src = inspect.getsource(c.WebSocketCallHandler._llm_loop)
    bypass = src.find("_slot_guard_bypass_for_write_cta")
    drop = src.find("_is_short_meaningless_fragment")
    assert bypass != -1, "the bypass arm is no longer in the guard"
    assert drop != -1, "fixture drift: the fragment drop moved"
    assert bypass < drop, (
        "the fragment drop now runs before the write-CTA bypass — the bypass is "
        "dead code and B-37 has regressed"
    )


def test_the_bypass_is_checked_before_the_non_specific_affirmation_clarify():
    """During a CTA, "that works" means yes to the CTA — not "I haven't told you
    which slot". Spec AJ's clarify re-ask would be the wrong question."""
    src = inspect.getsource(c.WebSocketCallHandler._llm_loop)
    bypass = src.find("_slot_guard_bypass_for_write_cta")
    clarify = src.find("_is_non_specific_slot_affirmation")
    assert clarify != -1, "fixture drift: Spec AJ moved"
    assert bypass < clarify
