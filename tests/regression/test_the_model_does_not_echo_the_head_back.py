"""The head, and then the model saying the very same thing again.

Reported by the owner from the live Theorem line, `CAd16d6e36`, 8 Sep 2026.
The caller asked to cancel an appointment:

    16:58:46.65  head:  'No problem at all —'
    16:58:51.24  model: 'no problem at all.'          <- 4.6s later, again
    16:58:51.36  model: "Was the appointment you'd like to cancel at our
                        Awlstuh or Redditch clinic?"

The clinic question being asked twice is a different defect and is fixed in
`test_the_clinic_question_is_asked_once.py` (f89a4c7e). This file is the other
half of what the caller heard: the opener said twice.

AND THEN IT HAPPENED AGAIN, on the build that fixed it. `CAce958696`, 8 Sep
17:46, demo line, build `66b8c209f261`:

    17:46:59.39  head:  'No problem at all -'
    17:47:05.57  model: "no problem at all. I've got you on oh seven five oh
                        two, two one one, two oh seven - is that the number
                        the appointment was booked under?"

Same head, same echo. The only difference is that the chunker handed the echo
and the payload over WELDED TOGETHER rather than 600ms apart, and `66b8c209`
matched on full equality, so it could not see a chunk that continues.

That is why the strip is now a prefix strip -- but only where the echo ENDS A
CLAUSE, which is the entire safety of it and is pinned by
`test_a_head_word_that_continues_a_phrase_is_never_cut` below.

WHY THE EXISTING MACHINERY DID NOT CATCH IT. `join_after_head` had two dedupe
paths and neither can see this sentence:

  * `_APOLOGY_HEAD_RE` / `_APOLOGY_OPENER_RE` — conditional on the head being
    an apology. "No problem at all" is not one.
  * `_strip_interim_opener` — built from the 95 stored LOOKUP duplicates
    ("Let me see", "Let me check"). It leaves this sentence untouched, so
    `body` came back non-empty and the pure-duplicate branch was never reached.

Both of those are asserted below as premises, because if either ever starts
covering this sentence the rest of the file is testing a defect that cannot
occur.

NOT A THIRD FAMILY REGEX. That would have fixed this call and left the next.
`INTENT_HEADS` has 21 families and 46 wordings and the model can echo any of
them, so the discriminator is not which family the head belongs to — it is
whether the chunk carries anything the caller has not already heard. That is
what `_echoes_head` asks, and the sweep below asks it of every head in the
pool rather than of the one that happened to be in last night's log.

THE SUPPRESSION IS STILL OPT-IN. `suppress_pure_duplicate` remains the single
owner of "may this caller end up with silence" — answering that a second time
inside the new branch is how B-121 happened, and the default-contract tests
below pin that the new branch defers to it in both directions.
"""
from __future__ import annotations

import pytest

from app.hold_speech import INTENT_HEADS
from app.media_streams.llm_stream import (
    _APOLOGY_HEAD_RE,
    _echoes_head,
    _head_echo_remainder,
    _may_suppress_pure_dupe,
    _strip_interim_opener,
    join_after_head,
)
from app.media_streams.turn_handler import sanitise_response

# The exact pair from the call.
HEAD = "No problem at all \u2014"
ECHO = "no problem at all."

# The same head carrying the turn's real payload. It is the control in every
# test below: nothing here may touch a chunk that has something to say.
WITH_PAYLOAD = (
    "No problem at all - was your original appointment at our "
    "Awlstuh or Redditch clinic?"
)


# ── Premise ─────────────────────────────────────────────────────────────────

def test_neither_existing_dedupe_path_can_see_this_sentence():
    """If either starts covering it, this file is vacuous."""
    assert not _APOLOGY_HEAD_RE.match(HEAD), (
        "the cancel head now reads as an apology, so the apology stripper "
        "would already have handled this"
    )
    assert _strip_interim_opener(ECHO) == ECHO, (
        "the opener stripper now reduces this sentence, so the pre-existing "
        "pure-duplicate branch would already have handled this"
    )


def test_gate_5_does_not_delete_the_echo():
    """The caller could hear it, which is why this was reportable at all.

    A defect that Gate 5 removes appears and disappears with the model's
    wording — see [[obs-transcripts-are-raw-not-spoken]].
    """
    assert sanitise_response(ECHO, {"clinic_id": "theorem_v3"}), (
        "Gate 5 now deletes this sentence and the caller would hear one "
        "opener, not two"
    )


def test_the_call_would_have_permitted_suppression():
    """`_may_suppress_pure_dupe` is upstream of the fix and gates it.

    The head was real and pre-dated the model's text, so the fix reaches this
    call. If it did not, suppressing here would change nothing on the line.
    """
    assert _may_suppress_pure_dupe({"_hold_head_spoken": True}, HEAD, False)


# ── The defect ──────────────────────────────────────────────────────────────

def test_the_echo_is_suppressed_when_the_caller_can_afford_it():
    assert join_after_head(ECHO, HEAD, suppress_pure_duplicate=True) == "", (
        "the caller hears 'No problem at all' and then the model's own "
        "version of the same sentence seconds later"
    )


def test_the_default_contract_is_unchanged():
    """Suppression stays opt-in — only a caller sitting above the empty-turn
    rescue can promise the turn still produces audio."""
    assert join_after_head(ECHO, HEAD) == ECHO


# ── The generality: every head in the pool, not just this one ───────────────

def _rendered_heads():
    for intent, pool in INTENT_HEADS.items():
        for head in pool:
            yield intent, head.replace("{subject}", "Friday")


@pytest.mark.parametrize(
    "head",
    [h for _i, h in _rendered_heads()],
    ids=[f"{i.name}:{n}" for n, (i, _h) in enumerate(_rendered_heads())],
)
def test_any_head_echoed_back_is_suppressed(head):
    """The model can echo any of the 46 wordings, so all of them are asked.

    Echoed in the shape the live one arrived in: lower-cased, and closing on a
    full stop where the head closed on a dash. A comparison that keeps casing
    or punctuation misses the very case this exists for.
    """
    echo = head.rstrip().rstrip("\u2014-,\u2026 ").strip().lower() + "."
    assert join_after_head(echo, head, suppress_pure_duplicate=True) == "", (
        f"an echo of {head!r} still reaches the caller"
    )


# ── The guards: nothing with a payload may be touched ───────────────────────

def test_a_chunk_that_merely_opens_with_the_head_keeps_its_payload():
    """Full equality, not a prefix strip. Removing an opener from a sentence
    that continues is a judgement about content, and that belongs to the two
    family-specific strippers, each conditional on a head it recognises."""
    out = join_after_head(WITH_PAYLOAD, HEAD, suppress_pure_duplicate=True)
    assert "awlstuh or redditch clinic?" in out.lower(), (
        "the turn's only question was deleted along with its opener"
    )


@pytest.mark.parametrize("chunk", [
    "Was your original appointment at our Awlstuh or Redditch clinic?",
    "I'm sorry to hear that, shoulder pain can be really limiting.",
    "Friday the fourteenth at ten is free.",
])
def test_an_unrelated_chunk_is_never_suppressed(chunk):
    assert join_after_head(chunk, HEAD, suppress_pure_duplicate=True) != ""


@pytest.mark.parametrize("head", ["", "   "])
def test_an_empty_head_is_never_an_echo(head):
    """A placeholder head is evidence that we do not know what the caller
    heard, not evidence that they heard anything. Suppressing against one
    would delete a sentence to make room for nothing."""
    assert not _echoes_head(ECHO, head)
    assert join_after_head(ECHO, head, suppress_pure_duplicate=True) == ECHO


def test_an_empty_chunk_is_not_an_echo_of_everything():
    assert not _echoes_head("", HEAD)
    assert not _echoes_head("   ...   ", HEAD)


# ── The second call: the echo welded to the payload ─────────────────────────

# `CAce958696`, 8 Sep 17:46, on build 66b8c209f261 -- the build that fixed the
# first one. Trimmed to the first chunk the log shows (len=84).
WELDED = (
    "no problem at all. I've got you on oh seven five oh two, "
    "two one one, two oh seven"
)


def test_full_equality_could_not_see_the_welded_chunk():
    """The premise of the second fix, and the reason the first was not enough."""
    assert not _echoes_head(WELDED, HEAD), (
        "this chunk is now a pure echo, so it never needed the prefix strip "
        "and this section is testing a defect that cannot occur"
    )


def test_the_welded_echo_loses_only_the_repeated_part():
    out = join_after_head(WELDED, HEAD, suppress_pure_duplicate=True)
    assert "no problem at all" not in out.lower(), (
        "the caller hears 'No problem at all' and then the model opening its "
        "very next sentence with it again"
    )
    assert "oh seven five oh two" in out, (
        "the phone read-back was deleted along with the echo -- the turn's "
        "entire payload"
    )


def test_the_welded_echo_loses_the_repetition_even_without_the_opt_in():
    """`suppress_pure_duplicate` owns one question -- may this caller end up
    with SILENCE -- and a chunk carrying a payload never can. So the repetition
    goes either way, exactly as `_APOLOGY_OPENER_RE` already strips
    unconditionally and defers to the opt-in only when nothing is left.

    The default contract that must not move is the one below it: a chunk that
    is NOTHING BUT the echo is still spoken unless a caller opts in."""
    assert join_after_head(WELDED, HEAD) != WELDED
    assert "oh seven five oh two" in join_after_head(WELDED, HEAD)
    assert join_after_head(ECHO, HEAD) == ECHO


# ── The guard that makes the prefix strip safe ──────────────────────────────

@pytest.mark.parametrize("chunk,head", [
    # The head's last word opens a noun phrase in the model's sentence. Cutting
    # here would leave the caller with "Spaces, we have six."
    ("As for parking spaces, we have six.", "As for parking —"),
    ("In terms of pricing structure, it depends.", "In terms of pricing —"),
    # A lookup head continuing into its own object. `_strip_interim_opener`
    # owns this one and reduces it to "" on its own terms.
    ("Let me see what Tuesday looks like.", "Let me see —"),
])
def test_a_head_word_that_continues_a_phrase_is_never_cut(chunk, head):
    """The echo must END A CLAUSE. Without this the strip is a guess about
    meaning rather than a fact about the text -- see [[write-gates-match-one-literal]]."""
    assert _head_echo_remainder(chunk, head) is None, (
        f"{chunk!r} would be cut mid-phrase"
    )


@pytest.mark.parametrize("chunk,head,expected", [
    ("Let me see, Friday at ten is free.", "Let me see —",
     "Friday at ten is free."),
    ("Friday it is - ten in the morning.", "Friday it is —",
     "ten in the morning."),
])
def test_an_echo_that_ends_a_clause_is_cut_at_the_boundary(chunk, head, expected):
    assert _head_echo_remainder(chunk, head) == expected


def test_the_echo_must_be_at_the_very_start():
    """A repetition later in the sentence is doing different work, and this
    must never reach into the middle of a reply."""
    assert _head_echo_remainder("Yes. No problem at all.", HEAD) is None


@pytest.mark.parametrize("head", ["", "   ", "…"])
def test_a_placeholder_head_yields_no_remainder(head):
    assert _head_echo_remainder(ECHO, head) is None
