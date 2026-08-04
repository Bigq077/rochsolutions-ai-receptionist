"""
The caller-ID number must be SPOKEN before the caller is asked to confirm it.

Observed on the 2026-08-04 acceptance call: Susie said "if you'd like me to use
the number you're calling from, just say use this number" — the digits were
never said aloud. The caller confirmed a number they had never heard, and it
went onto the booking.

Caller ID is not reliably the caller's own number: diverted lines, office
switchboards and carrier-substituted numbers all arrive looking normal. A blind
yes writes a stranger's number to the booking, and the confirmation text and
the reminders follow it there.

These assertions run against the RENDERED prompt, never the source file.
theorem_v3 has no prompt_engine key, so large parts of susie_system_prompt.py
are dead text for this clinic — three of the sites edited while fixing this
turned out never to render. Asserting on source would have passed while the
live model saw nothing.
"""

import pytest

from app.prompts.susie_system_prompt import _build_theorem_v3

CALLER_E164 = "+447502211207"
SPOKEN = "0 7 5 0 2 2 1 1 2 0 7"          # the live caller's number, spoken form
EXAMPLE = "0 7 7 0 0 9 0 0 1 2 3"         # Ofcom reserved drama range, used in worked examples


def _render(session_extra=None):
    session = {
        "clinic_id": "theorem",
        "twilio_from": CALLER_E164,
        "collected": {"name": "Quentin"},
    }
    session.update(session_extra or {})
    out = _build_theorem_v3(session)
    parts = out if isinstance(out, tuple) else (out,)
    return "\n".join(x for x in parts if isinstance(x, str))


@pytest.fixture(scope="module")
def prompt():
    return _render()


def test_prompt_actually_renders(prompt):
    """Guard the guard: a builder that returns near-empty makes every other
    assertion below vacuous."""
    assert len(prompt) > 50_000, f"theorem_v3 prompt rendered only {len(prompt)} chars"


def test_digits_are_spoken_in_the_offer(prompt):
    """The model must see the digit-by-digit form inside a spoken example."""
    assert EXAMPLE in prompt, "the caller-ID offer no longer shows the digits being spoken"


def test_no_instruction_to_skip_the_readback(prompt):
    """Two separate instructions used to tell the model NOT to say the number.
    Either one returning silently re-breaks this."""
    for banned in (
        "no readback needed",
        "do NOT read it back aloud",
    ):
        assert banned not in prompt, f"prompt still instructs the model: {banned!r}"


def test_blind_confirm_wording_is_prohibited(prompt):
    """The exact failing phrasing must be called out as wrong, not merely absent —
    absent leaves the model free to reinvent it."""
    assert "NEVER offer the calling number without speaking" in prompt


def test_the_offer_example_is_not_numberless(prompt):
    """Worked examples steer harder than rules. None of them may show the
    number-less form, or the model copies it."""
    numberless = "if you'd like me to use the number you're calling from"
    assert numberless not in prompt, (
        "a worked example still shows the caller-ID offer without the digits"
    )


def test_keypad_entered_numbers_still_read_back():
    """The keypad path had its own read-back (U-03 reversed, owner decision
    2026-08-03) covering booking AND cancel/reschedule lookups. This fix must
    not have disturbed it."""
    import inspect
    from app.media_streams import connection

    src = inspect.getsource(connection)
    assert "keypad number read back for confirmation" in src
    assert "U-03 REVERSED" in src, (
        "the cancel/reschedule lookup read-back decision marker is gone"
    )


def test_examples_never_use_a_real_number():
    """Worked examples render on EVERY call regardless of caller ID. A real
    number there is a number the model can speak onto a booking — the first
    draft of this fix baked in the tester's own mobile. Examples must stay
    inside Ofcom's reserved drama range (07700 900000-900999)."""
    prompt = _render()
    assert SPOKEN not in prompt, (
        "a real phone number is hardcoded in the prompt examples"
    )
    assert EXAMPLE in prompt


def test_no_offer_when_caller_id_is_withheld():
    """Withheld number → nothing to offer. The prompt must not invite
    'use this number' when there is no number to use."""
    prompt = _render({"twilio_from": ""})
    assert "there is no number to use" in prompt
