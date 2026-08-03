"""Gate 5f stood down on a `?` ANYWHERE in the chunk — and the prompts mandate
a closing that puts the question right beside the claim.

`_false_write_claim` returned False the moment a "?" appeared. That is correct
for a single sentence, but the unit is a ResponseChunker chunk and the chunker
accumulates until MIN_WORDS (15). A completion claim is shorter than that, so it
is emitted in the SAME chunk as the sentence that follows it — and both prompts
REQUIRE that sentence to be "Is there anything else I can help with?"
(clinic_template_prompt.py:2343, and the same wording in _build_theorem_v3).

Net effect: on a refused write, the guard stood down on the one wording the
prompt guarantees. This is very likely why Gate 5f has never fired live.

The fix drops question SENTENCES rather than standing down on the whole chunk.
The negation check still runs over the full text, which is the conservative
direction — see the docstring on _false_write_claim.
"""
import re

import pytest

from app.media_streams.chunker import ResponseChunker
from app.media_streams.turn_handler import (
    _declarative_part,
    _false_write_claim,
    WRITE_FAMILY_BOOKING,
    WRITE_FAMILY_CANCEL,
    WRITE_FAMILY_RESCHEDULE,
)

FAMILIES = (WRITE_FAMILY_BOOKING, WRITE_FAMILY_RESCHEDULE, WRITE_FAMILY_CANCEL)

# The exact closing both prompts mandate after cancel_appointment.
CANCEL_CLOSING = (
    "That's all done - your appointment has been cancelled. "
    "Is there anything else I can help with?"
)
BOOKING_CLAIM_THEN_QUESTION = (
    "All booked - you're in for Saturday the 15th at ten. "
    "Is there anything else I can help with?"
)


def _chunks(text):
    """What the real chunker actually emits for this text."""
    c = ResponseChunker()
    out = []
    for tok in text.split(" "):
        ch = c.add_token(tok + " ")
        if ch:
            out.append(ch)
    tail = c.flush()
    if tail:
        out.append(tail)
    return out


def _caught_families(text):
    fams = set()
    for ch in _chunks(text):
        for fam in FAMILIES:
            if _false_write_claim(ch, fam):
                fams.add(fam)
    return fams


# ── the defect ─────────────────────────────────────────────────────────────


def test_the_claim_and_the_question_really_do_share_one_chunk():
    """The premise. If the chunker ever split these, the bug would not exist —
    so pin it, because MIN_WORDS is a tuning knob someone may change."""
    assert len(_chunks(CANCEL_CLOSING)) == 1
    assert len(_chunks(BOOKING_CLAIM_THEN_QUESTION)) == 1


def test_cancel_closing_is_caught():
    """Cancel is the destructive family and this is its mandated closing."""
    assert WRITE_FAMILY_CANCEL in _caught_families(CANCEL_CLOSING)


def test_booking_claim_beside_a_question_is_caught():
    """Not cancel-only — any completion claim followed by a question escaped."""
    assert WRITE_FAMILY_BOOKING in _caught_families(BOOKING_CLAIM_THEN_QUESTION)


# ── the over-fire protection, which must be exactly as strong as before ─────


@pytest.mark.parametrize(
    "line",
    [
        "Shall I go ahead and book that in for you?",
        "Would you like me to move it for you?",
        "Would you like to keep this appointment, or cancel it altogether?",
        "I can get you booked in with Mark - would that work?",
        "Let me get that sorted for you.",
        "Let me get that moved for you.",
        "I havent booked that yet. Is there anything else I can help with?",
        "I cannot cancel that without the appointment reference.",
        "Once that's booked I'll text you a confirmation.",
        "I'll get you booked in as soon as you confirm the time.",
    ],
)
def test_legitimate_lines_stay_quiet(line):
    """Offers, questions, in-progress statements and negations must never fire.
    An over-fire here deletes real speech — this gate stripped a real
    confirmation and abandoned a completed booking once (Gate 5c, 2026-06-12)."""
    for fam in FAMILIES:
        assert not _false_write_claim(line, fam), f"{fam} over-fired on {line!r}"


def test_a_chunk_that_is_only_questions_still_stands_down():
    """Unchanged behaviour: nothing declarative to judge."""
    only_questions = "Is that the right one? Shall I go ahead?"
    assert _declarative_part(only_questions) == ""
    for fam in FAMILIES:
        assert not _false_write_claim(only_questions, fam)


def test_negation_in_the_question_half_still_protects_the_whole_chunk():
    """The conservative choice, pinned. The negation check runs over the FULL
    text including question sentences, so every stand-down the guard had before
    is preserved. If someone narrows it to the declarative part only, this
    fails — and that change would need its own measurement against the 27
    legitimate lines in test_false_confirmation_guard.py."""
    line = "Shall I book that in? That's all booked."
    for fam in FAMILIES:
        assert not _false_write_claim(line, fam)


# ── the splitter itself ────────────────────────────────────────────────────


def test_declarative_part_keeps_statements_and_drops_questions():
    got = _declarative_part(
        "All booked. Is there anything else? We'll see you then."
    )
    assert "All booked." in got
    assert "We'll see you then." in got
    assert "anything else" not in got


def test_declarative_part_is_empty_only_when_everything_is_a_question():
    assert _declarative_part("One? Two? Three?") == ""
    assert _declarative_part("") == ""
    assert _declarative_part("A statement.") == "A statement."


def test_only_one_sentence_splitter_is_defined_in_the_module():
    """The regression this change actually caused, pinned.

    The first draft declared its own `_SENTENCE_SPLIT_RE` with `\\s+`. At module
    scope that REBOUND the name for the whole file, so Gate 5g's splitter (which
    needs `\\s*` to handle "...for you now.I need to book...") stopped seeing two
    sentences and a reasoning leak reached the caller. The full suite caught it
    as one new failure — test_reasoning_never_reaches_tts::CA2f0b0707.

    A second definition of this name is always a bug: whichever appears last
    wins for every use in the file, including ones its author never looked at.
    """
    import inspect
    from app.media_streams import turn_handler

    src = inspect.getsource(turn_handler)
    defs = re.findall(r"^_SENTENCE_SPLIT_RE\s*=", src, re.M)
    assert len(defs) == 1, (
        f"expected exactly one _SENTENCE_SPLIT_RE definition, found {len(defs)}"
    )


def test_sentence_splitter_still_handles_the_no_space_form():
    """The property the collision broke: the model runs sentences together with
    no space after the period, and both Gate 5g and this guard depend on that
    splitting correctly."""
    got = _declarative_part("All booked.Is there anything else I can help with?")
    assert "All booked." in got
    assert "anything else" not in got


def test_theorem_objectless_reschedule_is_still_missed():
    """Scope marker, NOT an endorsement. Theorem's prompt teaches
    "I've rescheduled to [date]" with no object, which
    _FALSE_RESCHEDULE_CLAIM_RE deliberately does not match (the object
    requirement is what keeps "we've moved to a new building" out).

    That is a separate, prompt-side gap tracked for the Theorem port, and this
    change does not address it. Pinned so the two are not conflated: if a later
    commit makes this pass, the objectless form was matched somewhere and the
    new-building false positive needs re-checking.
    """
    line = "I've rescheduled to Monday 1st June at three in the afternoon."
    assert not _false_write_claim(line, WRITE_FAMILY_RESCHEDULE)
