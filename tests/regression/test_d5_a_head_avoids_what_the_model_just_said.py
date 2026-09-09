# tests/regression/test_d5_a_head_avoids_what_the_model_just_said.py
"""
D5 - Susie opened two consecutive turns with the same words.

Demo line, 9 Sep 2026:

    03:35:44   head:   "No problem at all —"
    03:35:58   model:  "No problem at all."

This is NOT the head-echo defect that `66b8c209` and `ff0fa987` fixed. Those
were one turn, where the model repeated the head it had just been handed and the
join stripped it. Here the two are separate turns with a caller utterance
between them, so the dedupe correctly declined - stripping across a turn
boundary would delete a genuine acknowledgement, and a deleted acknowledgement
is how the name deadlock happened.

What is fixable without touching model speech at all is the OTHER half.
`render_intent_head` rotates its pool by `len(session["used_fillers"])`, so it
counts only the heads WE have spoken. The model's own openers are not in that
count, and `Intent.CANCEL_REQ`'s pool holds exactly the phrase the model likes:

    ["No problem at all —", "Yes, no problem —"]

So the rotation cheerfully hands back a head the caller heard fifteen seconds
ago. Given what the assistant last said, it can step past it - the pool is our
own constant, not a phrase lifted out of model speech, so this is not another
instance of a gate matching one literal of what the model happens to write.

Deliberately limited: it only ever CHOOSES a different head. It never edits,
strips or suppresses anything the model said, and when every member of the pool
has been used it returns the rotation's own answer rather than nothing.
"""
from __future__ import annotations

from app.hold_speech import EM_DASH, INTENT_HEADS, Intent, render_intent_head


CANCEL_POOL = INTENT_HEADS[Intent.CANCEL_REQ]


def test_the_pool_still_holds_two_wordings():
    """If this ever drops to one member the avoidance below cannot work, and
    the test should say so rather than passing vacuously."""
    assert len(CANCEL_POOL) >= 2


def test_a_head_the_model_just_used_is_stepped_past():
    said = "No problem at all. Let me pull that up for you."
    got = render_intent_head(Intent.CANCEL_REQ, index=0, avoid=said)
    assert got != f"No problem at all {EM_DASH}"
    assert got in CANCEL_POOL


def test_the_rotation_is_unchanged_when_nothing_clashes():
    """No `avoid`, or an unrelated one, must give exactly what it gave before."""
    for avoid in ("", None, "I've got you booked in for Thursday."):
        assert render_intent_head(Intent.CANCEL_REQ, index=0, avoid=avoid) == \
            CANCEL_POOL[0]
        assert render_intent_head(Intent.CANCEL_REQ, index=1, avoid=avoid) == \
            CANCEL_POOL[1]


def test_it_falls_back_rather_than_falling_silent():
    """When the model has used every wording, a head is still returned - going
    quiet is worse than repeating."""
    said = " ".join(CANCEL_POOL)
    got = render_intent_head(Intent.CANCEL_REQ, index=0, avoid=said)
    assert got == CANCEL_POOL[0]


def test_the_subject_carrying_choice_still_wins():
    """The `{subject}` rule outranks avoidance: naming what the caller asked
    for is the point of a situational head."""
    got = render_intent_head(
        Intent.NAMED_DAY, subject="Saturday", index=0,
        avoid="Let me see what Saturday looks like -",
    )
    assert "Saturday" in got


def test_it_never_raises():
    for avoid in (None, "", 7, object()):
        render_intent_head(Intent.CANCEL_REQ, index=0, avoid=avoid)
