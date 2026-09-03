"""A caller who has just chosen a slot is not waiting, and owes no apology.

CA3dff2f4b (3 Sep 2026, 00:24, build dc58d3b5). The caller cut into the readout
to choose:

    00:24:18,691  FINAL -> queue: 'yeah the monday at 10 past 5 in the evening works'
    00:24:18,699  caller ACCEPTED 2026-09-07T17:10:00+01:00 -- pinned (P6b)
    00:24:21,711  filler phrase triggered: 'Sorry, still with you -'

Three seconds after the engine had deterministically resolved the choice and
pinned it, Susie apologised for a wait. `UNKNOWN_SLOW` is the fallback for a
turn whose work is UNKNOWN; this turn's work was known.

It is the second wrong thing said on this exact turn shape. Before `_hs_picking`
suppressed the diary heads, the same pick produced "Let me see what I've got in
the afternoon -" (2 Sep 09:09) -- a lookup nobody was doing. Suppressing that
left nothing, so the contentless apology took over. Both are the same gap: on a
pick there is something true and specific to say, and Susie said neither.

Not fixed by rewording, deliberately. hold_speech.py records that these phrases
were once bare discourse markers, that they failed live in three separate ways,
and that it "could not have been fixed by rewording". The trigger is the defect.
"""
from __future__ import annotations

import pytest

from app.hold_speech import (
    INTENT_HEADS,
    Intent,
    classify_intent,
    is_hold_head,
    render_intent_head,
    subject_for,
)

# The utterance, verbatim from the call.
PICK = "yeah the monday at 10 past 5 in the evening works"
READOUT = ("Number 3, Wednesday 9th September - eight in the morning, or "
           "ten past five in the evening.")


def _head(utterance, *, slot_selection=True, **kw):
    hits = classify_intent(utterance, READOUT, slot_selection=slot_selection, **kw)
    if not hits:
        return ""
    return render_intent_head(hits[0], subject=subject_for(utterance), index=0)


# ── the defect ──────────────────────────────────────────────────────────────

def test_a_pick_gets_a_head_at_all():
    """The behavioural check, written WITHOUT naming the new intent.

    Every other test here imports `Intent.SLOT_PICKED`, so on dc58d3b5 they
    fail by AttributeError -- which proves nothing about behaviour. This one
    fails on dc58d3b5 the way the caller experienced it: no head, so the turn
    fell through to the UNKNOWN_SLOW fallback and Susie said
    "Sorry, still with you -" to someone who had just chosen a slot.
    """
    assert classify_intent(PICK, READOUT, slot_selection=True) != []
    assert _head(PICK) != ""



def test_the_pick_from_the_call_now_has_something_true_to_say():
    assert classify_intent(PICK, READOUT, slot_selection=True) == [Intent.SLOT_PICKED]
    assert _head(PICK) == "Monday it is \u2014"


def test_the_head_promises_no_lookup():
    """The failure before this one. A pick must never get a diary head."""
    for intent in classify_intent(PICK, READOUT, slot_selection=True):
        head = render_intent_head(intent, subject=subject_for(PICK), index=0)
        for promise in ("Let me see", "Let me check", "Let me have a look",
                        "checking", "Just a moment"):
            assert promise.lower() not in head.lower(), head


@pytest.mark.parametrize("picked", [
    # Every case pinned by test_choosing_a_slot_still_gets_silence (30 Aug) and
    # test_a_resolved_pick_silences_the_lookup_head. All band-only.
    "ten in the morning",
    "can I take two in the afternoon please",
    "yeah ten in the morning",
    "yeah the last day in the afternoon works",
    # And picks by position, which name nothing at all.
    "number two",
    "yeah, that one",
])
def test_a_pick_naming_no_day_still_gets_silence(picked):
    """The 30 Aug decision is left standing, deliberately.

    Those tests chose silence because "a head in front of it would promise" a
    lookup. This head promises none, so their REASON does not cover it and the
    rule could be reopened -- but that is a decision to take on purpose, not a
    side effect of fixing a different phrase.

    A second reason agrees: `subject_for` lower-cases a band, so the
    subject-carrying head would render "afternoon it is -", an opener in lower
    case that nobody writes.
    """
    assert classify_intent(picked, READOUT, slot_selection=True) == []
    assert _head(picked) == ""


def test_the_subject_free_head_is_kept_as_the_documented_fallback():
    """Unreachable while the day gate stands, and kept on purpose: every pool
    using {subject} carries a subject-free member, and `render_intent_head`
    returns "" rather than "  it is -" if that convention is ever broken."""
    assert render_intent_head(Intent.SLOT_PICKED, subject="", index=0) ==         "That one works —"


# ── what must not change ────────────────────────────────────────────────────

def test_a_clinical_screen_still_wins():
    """A screen in play silences every head. Safety outranks warmth."""
    assert classify_intent(PICK, READOUT, slot_selection=True,
                           screen_pending=True) == []


def test_nothing_changes_when_the_caller_is_not_picking():
    """`slot_selection` is the engine's deterministic verdict, and the only
    thing that opens this path."""
    assert Intent.SLOT_PICKED not in classify_intent(
        "what are your opening hours", READOUT, slot_selection=False)
    assert Intent.SLOT_PICKED not in classify_intent(
        "can I come in on Tuesday", "", slot_selection=False)


def test_a_real_question_during_the_window_still_gets_its_own_head():
    """A caller asking something NEW while an offer stands is not picking, and
    must keep the head for what they actually asked."""
    hits = classify_intent("actually how much is it", READOUT, slot_selection=False)
    assert Intent.FAQ_PRICE in hits


# ── the head must behave like a head ────────────────────────────────────────

@pytest.mark.parametrize("head", INTENT_HEADS[Intent.SLOT_PICKED])
def test_the_pool_is_shaped_like_every_other_pool(head):
    """One subject-carrying member, one subject-free fallback, and the TTS layer
    must recognise both so it paces them like a person rather than rushing."""
    rendered = head.replace("{subject}", "Monday")
    assert is_hold_head(rendered), rendered
    assert rendered.endswith("\u2014")


def test_the_pool_has_a_subject_free_fallback():
    pool = INTENT_HEADS[Intent.SLOT_PICKED]
    assert any("{subject}" in h for h in pool)
    assert any("{subject}" not in h for h in pool)


def test_the_head_survives_the_banned_phrase_stripper():
    """A head stripped as a banned opener would leave the caller with silence."""
    from app.media_streams.turn_handler import _BANNED_SENTENCE_RE

    for head in INTENT_HEADS[Intent.SLOT_PICKED]:
        rendered = head.replace("{subject}", "Monday")
        for name, rx in _BANNED_SENTENCE_RE:
            assert not rx.match(rendered), (name, rendered)
