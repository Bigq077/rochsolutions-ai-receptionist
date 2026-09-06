"""B-145b — a caller who has just chosen is never told "let me look".

`CAa0389cae74d3ba76e220ab0280972101`, northgate, 2026-09-05 23:10:24:

    caller: 'um 10 past 5 in the evening suits'
    Susie:  situational head (time_band): "Let me see what I've got in the
            evening —"
    Susie:  "so that's Monday the 7th of September at ten past five in the
            evening"

She promised a lookup and then confirmed. Fourth instance of the promised-work
defect landing on a pick, and the one the owner reported by ear.

WHY THE EXISTING READERS DID NOT CATCH IT. All three inputs to `slot_selection`
RESOLVE — `utterance_is_slot_selection` is containment against the spoken
labels, `slot_accepted_by_caller` needs the slot, `day_accepted_by_caller`
needs the day. Each can decline for a reason that has nothing to do with
whether the caller picked, and here all three did: the offer still held three
dates, so `slot_accepted_by_caller`'s lone-date branch — added on 3 Sep for
this exact sentence — could not fire.

B-145 stops that state arising. This stops the NEXT decline, whatever produces
it, from promising a lookup again. Resolving is the right way to answer "which
slot"; it is the wrong way to answer "should I promise work", because the cost
of a decline is a false promise.

The predicate is therefore about the QUESTION, not any one answer to it, and
it is deny-by-default in the direction that matters: a BAND alone is excluded,
because "mornings work better" is a preference whose lookup really happens.
"""
from __future__ import annotations

import pytest

from app.hold_speech import Intent, classify_intent, utterance_accepts_an_offer

READOUT = "Any of those work?"


# ── The live sentence, and the shapes around it ─────────────────────────────

@pytest.mark.parametrize("utterance", [
    "um 10 past 5 in the evening suits",          # the live one
    "10 past 5 in the evening works",
    "oh yeah thursday at half past 6 works",
    "eight in the morning works",
    "quarter past eight in the evening suits me",
    "5 o clock works",
    "i'll take the 8:30",
    "yeah monday works",
    "tuesday's perfect",
])
def test_an_acceptance_is_recognised(utterance):
    assert utterance_accepts_an_offer(utterance), utterance


def test_the_lookup_head_is_suppressed_on_the_live_sentence():
    """The defect, in the two calls that differ only by this flag."""
    said = "um 10 past 5 in the evening suits"
    assert classify_intent(said, READOUT) == [Intent.TIME_BAND], (
        "fixture drift: this used to produce the TIME_BAND head"
    )
    assert classify_intent(said, READOUT, slot_selection=True) == []


# ── What must NOT be suppressed ─────────────────────────────────────────────

@pytest.mark.parametrize("utterance", [
    "mornings work better",
    "the afternoon works better for me",
    "evenings are good",
])
def test_a_band_preference_keeps_its_head(utterance):
    """A band with no day and no clock time is a PREFERENCE, and the lookup it
    asks for really does happen. Suppressing "Let me see what I've got in the
    morning —" there would delete a head that is telling the truth — the
    direction this family must not fail in."""
    assert not utterance_accepts_an_offer(utterance), utterance
    assert classify_intent(utterance, READOUT) == [Intent.TIME_BAND], utterance


@pytest.mark.parametrize("utterance", [
    "monday doesn't work",
    "10 past 5 doesn't work",
    "no, tuesday's not great",
    "eight in the morning won't work for me",
    "i can't do half past six",
])
def test_a_refusal_is_never_an_acceptance(utterance):
    """Every word in the acceptance set can be carrying a refusal. A negated
    acceptance is never clean enough to act on, so any negator declines."""
    assert not utterance_accepts_an_offer(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "what else have you got",
    "have you got anything on tuesday",
    "what about monday",
    "",
    "that works",          # no day, no clock time — the 30 Aug silence decision
    "number two",
])
def test_deny_by_default(utterance):
    assert not utterance_accepts_an_offer(utterance), utterance


def test_the_predicate_is_pure():
    """No session, no clock, no I/O — so it can be asked before anything has
    been resolved, which is the whole point of it."""
    said = "um 10 past 5 in the evening suits"
    assert utterance_accepts_an_offer(said) is utterance_accepts_an_offer(said)


def test_the_reader_is_gated_on_the_slot_map():
    """`llm_stream` asks this only while numbered options are on the table.

    Guarding on the MAP rather than on `v3_awaiting_slot_selection` is the rule
    `connection.py` states in its own comment, and the flag is derived from the
    map every turn — so a guard on the flag alone never survives.
    """
    import inspect

    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    marker = "_hs_picking = _accepts_an_offer(_hs_utterance)"
    assert marker in src, "the backstop is not wired into the head machinery"
    before = src.split(marker)[0]
    guard = before.rsplit("if not _hs_picking", 1)[-1]
    assert "v3_dtmf_slot_map" in guard, (
        "the backstop must only be asked while an offer is on the table"
    )
