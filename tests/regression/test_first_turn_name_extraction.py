"""
A junk first name must not reach a booking (T-7).

Observed on call 3 of the Theorem acceptance sweep, 2026-08-04 21:16:06:

    Caller: "and just a shockwave on its own"
    log:    [ms_conn v3] first-turn name extracted: Own

A pricing question produced a first name. `soft_context["name"]` is the slot the
booking read-back reads, so a caller who asks about prices and then books can
carry a junk name onto a real Acuity appointment. Speech-to-text has already put
a wrong surname on Mark's calendar twice; this one needs no mishearing at all.

Two independent defects were found, and the regex one was wrong in BOTH
directions:

    "a shockwave on its own"  -> "Own"    possessive "its" matched the
    "is it worth its cost"    -> "Cost"   contraction, because the apostrophe
                                          in `it[‘’]?s` was OPTIONAL
    "it's quentin"            -> no match the class held only CURLY quotes and
                                          AssemblyAI emits ASCII ones, so the
                                          phrasing the pattern exists to catch
                                          was the one it could not see

The apostrophe is now required and all three forms are accepted.

The second defect is the cluster one: nothing stopped an answer-extractor
running on a turn the caller framed as a question. That gate now covers the
time-preference extractor too (T-11: "what if I rearrange the morning of" was
stored as a preference for mornings).

The `_NOT_NAMES` denylist is left in place as a second line for statement turns,
but it is the wrong shape for this job and these tests do not rely on it — a
denylist can only ever hold junk somebody already thought of, and "own" is a
word ordinary English produces constantly.
"""

import re

import pytest

from app.media_streams import connection as conn


# The pattern as it appears in _handle_transcript's first-turn name block.
# Kept in sync deliberately: if the source pattern changes, this literal must
# change with it, and the source-parity test below is what forces that.
NAME_PATTERN = (
    r"\b(?:it[‘’']s|this is|i[‘’']?m|"
    r"hello[,\s]+(?:it[‘’']s)?)\s+"
    r"([A-Za-z][a-z]{1,20})\b"
)


def _extract(utterance):
    m = re.search(NAME_PATTERN, utterance, re.I)
    return m.group(1) if m else None


# ── the live regression ─────────────────────────────────────────────────────

def test_the_live_regression():
    """The exact call-3 utterance must yield no name."""
    assert _extract("and just a shockwave on its own") is None, (
        "'its own' is producing a first name again"
    )


@pytest.mark.parametrize("utterance", [
    "and just a shockwave on its own",
    "is it worth its cost",
    "the clinic and its parking",
    "does it have its own entrance",
])
def test_possessive_its_never_yields_a_name(utterance):
    """The possessive must not match the contraction. This is the false
    positive that put 'Own' into a booking slot."""
    assert _extract(utterance) is None, f"{utterance!r} produced a name"


# ── the other direction, which was also broken ──────────────────────────────

@pytest.mark.parametrize("utterance,expected", [
    ("it's quentin", "quentin"),        # ASCII apostrophe — STT's actual output
    ("it’s quentin", "quentin"),        # curly, as typed by a human
    ("i'm quentin", "quentin"),
    ("im quentin", "quentin"),          # STT frequently drops the apostrophe
    ("this is quentin", "quentin"),
    ("hello it's quentin", "quentin"),
])
def test_real_introductions_are_caught(utterance, expected):
    """Before this fix "it's quentin" matched NOTHING, because the pattern
    accepted only curly apostrophes and AssemblyAI emits ASCII. The most
    natural way to say your name was invisible."""
    assert _extract(utterance) == expected, (
        f"{utterance!r} no longer yields a name — the pattern regressed the "
        "direction it exists to serve"
    )


def test_pattern_matches_the_source():
    """These tests assert against a literal copy of the production pattern.
    If the source changes and this does not, the tests silently stop testing
    anything real."""
    src = conn.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert r"it[‘’']s" in text, (
        "the required-apostrophe class is gone from connection.py — "
        "possessive 'its' can match the contraction again"
    )


# ── the cluster gate ────────────────────────────────────────────────────────

def test_question_gate_is_wired_to_both_extractors():
    """The gate must cover the name AND the time-preference extractor — T-7 and
    T-11 are the same defect on two sites."""
    import inspect

    src = inspect.getsource(conn)
    assert "_extractable_turn = not _transcript_is_question(" in src
    # name extractor
    assert "if _name_found and _extractable_turn:" in src
    # time-preference extractor
    assert (
        'if _extractable_turn and not self.session.get("v3_location_confirmed"):'
        in src
    )


@pytest.mark.parametrize("question", [
    "what if i rearrange the morning of",       # T-11, live
    "should i take ibuprofen",
    "can i come in on monday morning",
    "do i need to book tuesday",
])
def test_question_turns_are_not_extractable(question):
    """Every one of these mentions a name-shaped or time-shaped token while
    asking something. None is the caller stating a preference about themselves."""
    assert conn._transcript_is_question(question), (
        f"{question!r} is not recognised as a question, so answer-extractors "
        "will still mine it"
    )


def test_the_live_t7_utterance_is_stopped_by_the_REGEX_not_the_gate():
    """Records a real limit, so nobody assumes the gate covers everything.

    "and just a shockwave on its own" is a question in context but not in
    syntax — no question word, no auxiliary inversion, just an elliptical
    follow-up to the previous turn. _transcript_is_question does not recognise
    it and should not be stretched to, since that would start swallowing
    genuine statements.

    So the two fixes cover different ground and both are load-bearing:

        regex   stops THIS utterance, and every other possessive "its"
        gate    stops T-11 / T-14 — turns that ARE syntactically questions

    If the regex is ever reverted, the gate will not save it.
    """
    live = "and just a shockwave on its own"
    assert not conn._transcript_is_question(live), (
        "if this now parses as a question the gate has been widened — check it "
        "has not started swallowing genuine statements too"
    )
    assert _extract(live) is None, (
        "the regex is the only thing stopping the live T-7 case and it has "
        "regressed"
    )


@pytest.mark.parametrize("statement", [
    "it's quentin",
    "my name is quentin",
    "tuesday morning works",
    "next week please",
    "afternoons",
])
def test_statement_turns_remain_extractable(statement):
    """The gate must not swallow the real thing. A caller stating a name or a
    preference has to keep reaching soft_context, or the booking flow starts
    re-asking questions it already had answers to."""
    assert not conn._transcript_is_question(statement), (
        f"{statement!r} now reads as a question — soft-context extraction is "
        "being skipped on genuine answers"
    )
