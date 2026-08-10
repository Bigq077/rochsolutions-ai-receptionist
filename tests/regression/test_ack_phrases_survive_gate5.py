"""
No booking-ack phrase may begin with an opener Gate 5 strips.

`_V3_ACK_PHRASES` in connection.py is matched against `_last_bot`, which since
2 Aug 2026 is the POST-Gate-5 text — what the caller actually HEARD (see
`_record_turn` in llm_stream: "conversation_history stores what the caller
HEARD"). Gate 5's `banned_opener` rule deletes a leading "Of course, " /
"Of course — ", so any entry beginning with one can never match anything.

Five of the eight entries on canonical began with exactly that, and were dead:

    "of course —"
    "of course — let me get that sorted for you"
    "of course — i'd be happy to sort that"
    "of course, let's get that moved"
    "of course — let's get that sorted"

This is not cosmetic. The tuple sets `booking_flow_active`, and
`clinic_template_prompt` mandates "Of course, let's get that moved" at the
reschedule ack. Gate 5 reduced that to "let's get that moved", which matched
nothing — so on Vital Edge and JV a reschedule ack went undetected. Theorem hit
the same defect through the same door (T-18, seven seconds of dead air) and was
fixed on 26 Apr; canonical was not, and carried it until 2026-08-10.

The obvious test — assert the tuple equals a literal list — would not have
caught the original bug, because the original list WAS the intended list. The
property that matters is the relationship between the tuple and the gate, so
that is what is asserted here. See also the standing rule recorded against
`write-gates-match-one-literal`.
"""

import inspect
import re

import pytest

from app.media_streams import connection as c
from app.media_streams.turn_handler import _BANNED_SENTENCE_RE


def _banned_opener_re() -> re.Pattern:
    for name, pattern in _BANNED_SENTENCE_RE:
        if name == "banned_opener":
            return pattern
    raise AssertionError(
        "_BANNED_SENTENCE_RE no longer has a 'banned_opener' rule — if it was "
        "renamed or removed, this whole test needs re-aiming, not deleting"
    )


def _ack_phrases() -> tuple:
    """Read the tuple out of the source.

    It is a local inside the LLM loop, so it cannot be imported. Parsing the
    literal is deliberate: it pins the phrases as WRITTEN, which is where the
    defect lived.
    """
    src = inspect.getsource(c).splitlines()
    start = next(
        i for i, line in enumerate(src) if "_V3_ACK_PHRASES = (" in line
    )
    phrases = []
    for line in src[start + 1:]:
        # Trailing comments carry parentheses of their own ("(short ack)"),
        # so the close must be found on a comment-stripped line — indexing to
        # the first ")" in the raw source finds the one inside that comment
        # and silently returns a one-entry tuple.
        code = line.split("#", 1)[0]
        if code.strip() == ")":
            break
        phrases.extend(re.findall(r'"([^"]+)"', code))
    return tuple(phrases)


def test_the_tuple_is_still_found():
    """If this ever fails the rest of the file is silently vacuous."""
    phrases = _ack_phrases()
    assert len(phrases) >= 4, phrases


@pytest.mark.parametrize("phrase", _ack_phrases())
def test_no_ack_phrase_is_deleted_by_gate5(phrase):
    """
    The core property. A phrase Gate 5 rewrites can never match the text the
    caller heard, so it is dead code that reads as live configuration.
    """
    survived = _banned_opener_re().sub("", phrase)
    assert survived == phrase, (
        f"{phrase!r} starts with an opener Gate 5 strips — it would be "
        f"rewritten to {survived!r} before this tuple ever sees it, so it can "
        f"never match. Anchor the entry on the part Gate 5 leaves alone."
    )


def test_the_reschedule_ack_the_prompt_mandates_is_detectable():
    """
    The end-to-end claim, and the one that was false on Vital Edge: the exact
    sentence the template prompt tells the model to say must, after Gate 5 has
    run on it, match something in the tuple.
    """
    spoken = "Of course, let's get that moved for you."
    heard = _banned_opener_re().sub("", spoken)
    assert any(p in heard.lower() for p in _ack_phrases()), (
        f"the mandated reschedule ack becomes {heard!r} after Gate 5 and "
        f"matches no entry in {_ack_phrases()} — booking_flow_active will not "
        f"be set and the reschedule flow stalls"
    )


def test_the_cancel_ack_is_detectable():
    """Same claim for the cancel side, which uses a different phrase."""
    spoken = "No problem at all — let's get that cancelled."
    heard = _banned_opener_re().sub("", spoken)
    assert any(p in heard.lower() for p in _ack_phrases()), (
        f"the cancel ack becomes {heard!r} after Gate 5 and matches nothing"
    )
