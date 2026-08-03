"""FM-23 · the reschedule gate matched one literal, so a reworded question blocked a real move.

`CA23199d08907234dddb7d2167fb23753c`, 3 Aug 2026, 01:04. The caller confirmed
with "uh yeah go for it". The log:

    tool: reschedule_appointment … new_slot_iso 2026-08-06T18:45
    WARNING reschedule_appointment BLOCKED — no clear caller yes after the move
            confirmation (last_user_text='uh yeah go for it')
    → "That's you rescheduled — you're now in for Thursday the 6th…"

Nothing moved. `outcome='abandoned'`. The caller was told otherwise.

The gate read:

    "move it for you" in last_bot_prompt AND await _book_reply_verdict(...)

and the model had asked "Shall I go ahead and **move your appointment to
Thursday the 6th of August at quarter to seven in the evening**?" — the
confirmation question, in other words, because the caller had just said "I
think you got cut off" so it re-asked with full detail. The substring missed,
`and` short-circuited, and **the caller's affirmative was never evaluated**.

The booking gate two branches up accepts `"shall i go ahead" OR "book that in"`,
which is why rewording never broke it. This brings reschedule into line.

The safety property is unchanged and is asserted below: the CTA test only makes
the gate *reachable*; `_book_reply_verdict` still has to pass independently, and
a booking CTA still cannot satisfy the reschedule gate.
"""

import pytest

from app.media_streams.llm_stream import _move_confirmation_asked


# ── the phrasings that must be recognised ───────────────────────────────────

@pytest.mark.parametrize(
    "prompt",
    [
        # The canned template CTA. Must never stop matching.
        "Shall I go ahead and move it for you?",
        # The exact line from CA23199d08 that was rejected.
        "Shall I go ahead and move your appointment to Thursday the 6th of "
        "August at quarter to seven in the evening?",
        # Other shapes the model reaches for once it starts elaborating.
        "Shall I go ahead and move that to Thursday?",
        "Shall I reschedule that for you?",
        "Would you like me to move it to Thursday the 6th?",
        "Want me to move your appointment to Saturday?",
        "Happy for me to move it to quarter to seven?",
        "Is that okay to move to Thursday?",
    ],
)
def test_the_move_confirmation_question_is_recognised(prompt):
    assert _move_confirmation_asked(prompt) is True, (
        f"the gate would not recognise {prompt!r} as the move confirmation "
        f"question — the reschedule blocks and the caller is told it happened"
    )


# ── what must NOT count as having asked ─────────────────────────────────────

def test_a_booking_cta_does_not_satisfy_the_reschedule_gate():
    """Both arms are required. "Shall I go ahead and book that in?" has the ask
    shape and no move verb, so it must not open the reschedule gate."""
    assert _move_confirmation_asked("Shall I go ahead and book that in?") is False


def test_the_readback_statement_is_not_the_question():
    """This sentence is spoken on the turn BEFORE the question. It contains
    "moving" but asks nothing, so it must not count — otherwise the gate opens
    a turn early, before the caller has been given anything to say yes to."""
    assert _move_confirmation_asked(
        "Just to confirm — I'm moving your appointment to Thursday the 6th of "
        "August at quarter to seven in the evening."
    ) is False


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        None,
        "Do you have a preference for when you'd like to reschedule to?",
        "I can see an appointment on Tuesday the 4th of August — is that the right one?",
        "Here's what we've got coming up — Number 1, Monday 3rd August.",
        "Shall I go ahead and cancel it altogether?",
    ],
)
def test_unrelated_turns_do_not_open_the_gate(prompt):
    """Including the two that are closest: the timing question (contains
    "reschedule" but is not an ask-to-move) and the cancel CTA (has the ask
    shape but is a different, destructive action)."""
    assert _move_confirmation_asked(prompt) is False


# ── the regression, stated as the call ──────────────────────────────────────

def test_the_exact_call_that_produced_a_phantom_reschedule():
    """One test naming the failure so it cannot be reintroduced quietly."""
    spoken = (
        "Shall I go ahead and move your appointment to Thursday the 6th of "
        "August at quarter to seven in the evening?"
    )
    assert _move_confirmation_asked(spoken) is True
    # And the literal the old gate required is genuinely absent — i.e. this
    # test would have failed before the fix for the right reason.
    assert "move it for you" not in spoken.lower()
