# tests/regression/test_phone_step_markers_agree.py
"""
The phone step is recognised by whatever wording Step 8 actually speaks.

CA3145c15f (30 Jul 2026). The caller gave a reason, a slot, a name and a number,
confirmed the number twice, and hung up unbooked after Susie asked for the number
a third time. Nothing in the conversation was wrong — the number was right and she
had it. `phone_confirmed` was simply never set, so `book_appointment`'s A1 gate
refused every write and answered each refusal with "read the number back and ask
again", which is the loop the caller heard.

The cause is drift between three copies of the same "is this the phone step?"
literal set. On 2026-07-26 Step 8's wording changed to

    "I've got you on … — is that the best number for the booking?"

`clinic_template_prompt._PHONE_STEP_MARKERS` and `llm_stream._PHONE_STEP_MARKERS`
were both updated. A third, private copy inside connection.py's booking verbal
phone-confirm branch was not — it still carried the 2026-06-23 literals
("use this number", "number you're calling on", "number you booked"), none of
which occur in the new sentence. That branch is the only thing that sets
phone_confirmed on the LLM path, so from 26 Jul it could not fire at all.

connection.py no longer keeps its own copy. These tests pin the two that remain
in agreement, and pin both against the sentence Step 8 really speaks — so a
future reword that misses a copy fails here instead of on a live call.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import _PHONE_STEP_MARKERS as LLM_MARKERS
from app.prompts.clinic_template_prompt import _PHONE_STEP_MARKERS as PROMPT_MARKERS


# The exact sentence Step 8 instructs the model to speak, and the form it came
# out as on the call that lost the booking.
STEP_8_SPOKEN = (
    "I've got you on oh seven five oh two, two one one, two oh seven "
    "— is that the best number for the booking?"
)
STEP_8_AS_HEARD = "I've got you on 07502, 211, 207 - is that the best number for the booking?"


def _is_phone_step(last_question: str, markers) -> bool:
    """The test performed on the last spoken question, in every copy."""
    return any(mk in (last_question or "").lower() for mk in markers)


def test_the_two_marker_copies_are_identical():
    """Drift between them is the defect — not a difference of purpose."""
    assert set(LLM_MARKERS) == set(PROMPT_MARKERS), (
        "phone-step markers have drifted between llm_stream and "
        "clinic_template_prompt; a reword updated one copy and not the other, "
        "which is how CA3145c15f lost a booking"
    )


@pytest.mark.parametrize("markers,name", [(LLM_MARKERS, "llm_stream"),
                                          (PROMPT_MARKERS, "clinic_template_prompt")])
@pytest.mark.parametrize("spoken", [STEP_8_SPOKEN, STEP_8_AS_HEARD])
def test_every_copy_recognises_the_sentence_step_8_speaks(markers, name, spoken):
    assert _is_phone_step(spoken, markers) is True, (
        f"{name} does not recognise Step 8's own wording as the phone step"
    )


@pytest.mark.parametrize("markers", [LLM_MARKERS, PROMPT_MARKERS])
@pytest.mark.parametrize("spoken", [
    "So that's Sara, Wednesday the 5th of August at quarter past six in the "
    "evening — shall I go ahead and book that in?",
    "Tuesday 4th August — Number 1, quarter to six in the evening. "
    "Number 2, half past six in the evening. Any of those work?",
    "Thanks Sara — and your surname?",
    "Right — what's the appointment for?",
])
def test_other_questions_are_not_mistaken_for_the_phone_step(markers, spoken):
    """The booking read-back must never count as the phone question: treating it
    as one would let a "yes" meaning "yes, book it" silently confirm a number."""
    assert _is_phone_step(spoken, markers) is False, (
        f"{spoken[:50]!r} must not register as the phone step"
    )


def test_connection_keeps_no_private_copy_of_the_markers():
    """The fix is that connection.py reuses the shared set. If a future edit
    reintroduces a local literal list, this catches it at the source."""
    from pathlib import Path

    import app.media_streams.connection as conn

    src = Path(conn.__file__).read_text(encoding="utf-8", errors="ignore")
    assert "_PHONE_STEP_MARKERS" in src, (
        "connection.py's booking phone-confirm branch must test the phone step "
        "with the shared marker set, not its own literals"
    )
    assert '"number you booked" in _bk_lastq' not in src, (
        "the 2026-06-23 private literal set is back in connection.py — that is "
        "the exact drift that lost CA3145c15f"
    )
