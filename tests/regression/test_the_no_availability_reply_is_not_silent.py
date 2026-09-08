# tests/regression/test_the_no_availability_reply_is_not_silent.py
"""
LAT-3 - the "nothing free that day" reply arrived after 11.5 seconds of silence.

Theorem CA1736b441372dad9949bc289665e63b1c, 8 Sep 2026, build d4c9a850a2c8.
The caller asked for Thursday; Acuity genuinely had nothing on 2026-09-10, and
Susie's answer was correct. Getting to it sounded like a dropped call:

    10:24:32.1  "Let me see what Thursday looks like -"   <- situational head
    10:24:34.5  head finishes
                ... 11.5 s of nothing ...
    10:24:45.9  "nothing free this Thursday I'm afraid -"

    [LAT] turn_seq=5 content_ttfa_ms=14568 llm_ttft_ms=8254 chunk_gate_ms=6185

Against a production bar of no dead air over 3 s without a filler, that is a
4x breach, and it lands on the most common disappointing answer the system has
to give.

WHY ONLY THIS BRANCH. The happy path never waits like this, and not because it
is luckier: a check that RETURNED slots builds its offer deterministically and
skips the second LLM call altogether --

    [ms_llm] slot LLM call SKIPPED - deterministic offer already built

A zero-slot check deliberately does the opposite. It falls through to Sonnet on
the full prompt so the lack of availability can be explained (the re-arm's own
comment cites C8-5, where the focused Haiku slot prompt met an empty result and
emitted silence). That fall-through is correct and stays. What was missing is
that nothing covers it: with_filler stops when the tool returns, and the
situational head played before iteration 1. So the branch that is structurally
the SLOWEST was the only one with no hold phrase.

C8-5's catch-all at the end of the same loop guarantees the turn is not SILENT.
Nothing guaranteed it was not SLOW. This is that guarantee.

THE TRAP THIS FILE PINS. The wording is reused from THINKING_FILLERS_SECONDARY
rather than written fresh, and the tests below enforce that it survives
sanitise_response untouched. Deterministic filler lists have twice been the one
path by which a caller could hear a phrase the engine forbids everywhere else -
"just a moment" and "bear with me just a moment" both shipped inside these
lists while turn_handler was stripping them out of model speech. A new
hand-written phrase here would be a third.
"""

import ast
import inspect
import time

import pytest

from app.media_streams import llm_stream as ls
from app.media_streams.turn_handler import sanitise_response
from app.filler_phrases import (
    FILLER_COOLDOWN_S,
    THINKING_FILLERS_SECONDARY,
    note_filler_played,
    pick_filler,
    should_play_filler,
)


def _zero_slot_branches():
    """The `if` nodes guarding the no-availability hold phrase.

    Identified by the guard itself - a zero-slot check is exactly
    `_ran_check_av and not _last_check_avail` - rather than by any log text,
    so rewording a message does not silently retire this test.
    """
    src = inspect.cleandoc(inspect.getsource(ls.LLMStream._streaming_tool_loop))
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if {"_ran_check_av", "_last_check_avail"} <= names:
            body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
            if "tts_text_queue" in body:
                hits.append(node)
    return hits


def _branch_body():
    branches = _zero_slot_branches()
    assert branches, (
        "no branch guarded on (_ran_check_av and not _last_check_avail) "
        "queues anything to tts_text_queue -- the no-availability round trip "
        "is uncovered again and the caller hears the LAT-3 silence"
    )
    return ast.dump(ast.Module(body=branches[0].body, type_ignores=[]))


# ---------------------------------------------------------------------------
# The fix itself
# ---------------------------------------------------------------------------
def test_a_zero_slot_check_puts_a_hold_phrase_on_the_tts_queue():
    """The 11.5 s gap had nothing in it. Something must reach the caller."""
    body = _branch_body()
    assert "tts_text_queue" in body and "put" in body


def test_the_hold_phrase_is_drawn_from_the_vetted_list():
    """Not hand-written here. See the trap in the module docstring."""
    assert "THINKING_FILLERS_SECONDARY" in _branch_body(), (
        "the hold phrase must come from THINKING_FILLERS_SECONDARY, which is "
        "already vetted against SILENCE_RULE"
    )


def test_it_reports_to_the_shared_filler_clock():
    """Every producer calls note_filler_played - this one is producer four.

    Skipping it would leave _hold_head_spoken unarmed, so Sonnet's reply would
    be free to open with its own hold phrase and the caller would hear two in a
    row (the CA8cf0aaea defect), and the cooldown would not see this filler at
    all.
    """
    assert "note_filler_played" in _branch_body()


def test_it_is_gated_on_the_cooldown():
    """A filler still in the caller's ear must not get a second on top."""
    assert "should_play_filler" in _branch_body()


def test_the_happy_path_is_not_touched():
    """A check that RETURNED slots must add no phrase.

    That path is already fast and already deterministic; a hold phrase there
    would be pure duplication ahead of the offer. The guard must require the
    NEGATION of _last_check_avail, not merely mention it - dropping the `not`
    is the obvious way to get this backwards and would fire on every offer.
    """
    _branch_body()  # fails with the readable message if the branch is gone
    node = _zero_slot_branches()[0]
    assert any(
        isinstance(n, ast.UnaryOp)
        and isinstance(n.op, ast.Not)
        and any(
            isinstance(x, ast.Name) and x.id == "_last_check_avail"
            for x in ast.walk(n)
        )
        for n in ast.walk(node.test)
    ), "the branch must fire on NO slots, not on slots found"


def test_the_hold_phrase_does_not_satisfy_the_silence_guarantee():
    """A hold phrase is not an answer.

    `_turn_real_tts` is what C8-5's end-of-turn catch-all reads to decide the
    caller got something. Only real content sets it. If this branch set it too,
    a turn where the hold phrase played and Sonnet then produced nothing would
    look answered and the catch-all would stay quiet -- trading 11.5 s of
    silence for a turn that ends on "Nearly there..." and never continues.
    """
    body = _branch_body()
    assert "_turn_real_tts" not in body, (
        "the no-availability hold phrase must not mark the turn as answered"
    )


# ---------------------------------------------------------------------------
# The wording, checked against the gate that has caught this list twice
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phrase", THINKING_FILLERS_SECONDARY)
def test_every_secondary_filler_survives_the_speech_gates(phrase):
    """A phrase the engine strips from model speech must not reach a caller
    through a deterministic list instead."""
    session: dict = {}
    assert sanitise_response(phrase, session).strip() == phrase.strip(), (
        f"{phrase!r} is altered by sanitise_response -- it is banned for "
        f"model speech and must not ship as a deterministic filler"
    )


@pytest.mark.parametrize("phrase", THINKING_FILLERS_SECONDARY)
def test_no_secondary_filler_promises_or_denies_availability(phrase):
    """All that is known at this point is that the requested day was empty -
    not what Sonnet will offer instead. The hold phrase must stay contentless.
    """
    low = phrase.lower()
    for claim in ("available", "free", "nothing", "fully booked", "sorry",
                  "afraid", "booked in", "slot"):
        assert claim not in low, (
            f"{phrase!r} makes a claim about availability before the reply "
            f"that decides it has even been generated"
        )


# ---------------------------------------------------------------------------
# The cooldown, at the timings actually measured on the call
# ---------------------------------------------------------------------------
def test_the_live_gap_would_have_been_covered():
    """On CA1736b441 the head was queued at :32.1 and the zero-slot branch was
    reached at :41.2 - 9.1 s later, well clear of the cooldown. The fix must
    actually speak on the call that motivated it."""
    session: dict = {}
    note_filler_played(session, text="Let me see what Thursday looks like -")
    session["_last_filler_ts"] = time.monotonic() - 9.1
    assert should_play_filler(session) is True


def test_a_head_still_playing_blocks_it():
    """The other half: a fast tool call must not stack a second phrase onto a
    head the caller is still hearing."""
    session: dict = {}
    note_filler_played(session, text="Let me see what Thursday looks like -")
    assert should_play_filler(session) is False


def test_the_cooldown_boundary_is_the_shared_one():
    """Pinned to FILLER_COOLDOWN_S so this branch cannot drift away from the
    other three producers."""
    session: dict = {}
    note_filler_played(session, text="anything")
    session["_last_filler_ts"] = time.monotonic() - (FILLER_COOLDOWN_S + 0.05)
    assert should_play_filler(session) is True


def test_the_phrase_pool_does_not_run_dry_or_repeat_back_to_back():
    """A caller who hits several empty days in one call must not hear the same
    phrase twice running."""
    used: list = []
    seen = [pick_filler(THINKING_FILLERS_SECONDARY, used)
            for _ in range(len(THINKING_FILLERS_SECONDARY))]
    assert len(set(seen)) == len(THINKING_FILLERS_SECONDARY)
    # And it keeps working past exhaustion rather than returning "".
    assert pick_filler(THINKING_FILLERS_SECONDARY, used)
