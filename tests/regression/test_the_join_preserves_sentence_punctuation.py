r"""The seam repair in join_after_head must INSERT a space, not eat one.

`join_after_head` exists to make the model's reply read as the completion of
the hold phrase. Part of that is repairing the run-on the model produces when
it welds its own opener to the payload:

    "...what's available.The available slots for Tuesday"

which ElevenLabs reads without a breath. The repair was:

    re.sub(r"([\.!?])([A-Z])", r" ", body)

The replacement is a bare space, so both capture groups are DISCARDED: the full
stop and the capital letter after it are deleted along with nothing else.

    "available.The available slots"  ->  "available he available slots"

Verified against the shipped function on 2026-08-29. It is reachable only where
`hold_speech` is enabled -- one clinic today, every clinic after the fold -- and
it corrupts the exact seam the whole hold-speech design depends on.

The fix is the backreferences, `r"\1 \2"`.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import join_after_head

EM = "\u2014"


@pytest.mark.parametrize(
    "payload, must_contain, must_not_contain",
    [
        # The shape of the 106 stored run-on fragments. Payloads here must not
        # begin with an interim opener ("just a moment", anything containing
        # "check"): `_strip_interim_opener` removes those before the seam
        # repair ever runs, which would make this test pass for the wrong
        # reason.
        ("The slot is available.The available slots for Tuesday 28th July "
         "are as follows.",
         "available. The available", "available he available"),
        ("That's done.We'll see you then.", "done. We'll", "done e'll"),
        ("No problem.I've got you booked in.", "problem. I've", "problem 've"),
    ],
)
def test_a_sentence_boundary_keeps_its_full_stop(payload, must_contain,
                                                 must_not_contain):
    joined = join_after_head(payload, f"Let me see {EM}")
    assert must_contain in joined, joined
    assert must_not_contain not in joined, joined


def test_no_character_is_lost_across_the_repair():
    """The blunt check that catches any future eating replacement.

    The repair may only ADD a space at a boundary, so every letter either side
    of it must survive. Compared case-insensitively because the join also
    decapitalises the first word to continue the head's open clause -- and note
    that join_after_head returns the BODY alone; the head is prepended by the
    caller.
    """
    payload = "That works.Tuesday suits us.Friday does too."
    body = join_after_head(payload, f"Let me see {EM}")
    assert sorted(body.replace(" ", "").lower()) ==         sorted(payload.replace(" ", "").lower()), (
            f"characters were lost or added: {body!r} from {payload!r}"
        )


def test_an_empty_head_leaves_the_payload_untouched():
    """No hold phrase played, so there is no seam to repair and nothing to join."""
    payload = "Just a moment.The available slots for Tuesday."
    assert join_after_head(payload, "") == payload


# ── The opener strip must not leave a fragment behind ───────────────────────

def test_stripping_an_opener_does_not_leave_a_dangling_clause():
    """"Let me check what's available for Saturday" loses its verb when the
    opener goes, leaving "What's available for Saturday." -- which reads as a
    statement, is a fragment, and was spoken.

    `_ORPHAN_LEAD` was added for exactly this, after six "While I look that up."
    fragments reached callers on 21-22 Aug. It listed the adverbial words
    (while / whilst / as I / so I / until) and stopped one word short of the
    complement ones, so the commonest opener in the whole corpus -- "Let me
    check what's..." -- fell straight through it.

    Situational heads make this path far more common, because a head is now
    spoken on turns that previously had none, and the model's own opener is
    stripped every time one is.
    """
    payload = ("Let me check what's available for morning sports massage slots."
               "I'm afraid the available slots on Saturday are all 30-minute.")
    body = join_after_head(payload, f"Let me see where a sixty-minute fits {EM}")
    assert not body.lower().startswith("what"), (
        f"a dangling complement clause survived the strip: {body!r}"
    )
    assert body.startswith("I'm afraid"), body


def test_a_legitimate_reply_is_not_eaten_by_the_orphan_guard():
    """The guard only runs when an opener was actually stripped, so a reply that
    simply begins with one of those words is untouched."""
    payload = "What is it you're looking to come in for?"
    assert join_after_head(payload, f"Let's get you booked in {EM}") == (
        "what is it you're looking to come in for?"
    )
