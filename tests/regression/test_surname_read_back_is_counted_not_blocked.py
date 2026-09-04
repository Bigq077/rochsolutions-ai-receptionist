"""
The surname read-back exposure is COUNTED. Deliberately not blocked.

§2.5 of OPEN_DEFECTS_2026-09-03.md. The phone has a hard gate — A1,
`receptionist_tools.py`, `phone_confirmed is not True` → `[book] BLOCKED`. The
surname has only a prompt instruction, and that instruction records that
"speech-to-text has written the wrong surname to a real calendar twice".
`_unconfirmed_callback_number` names the shape in one line: "The read-back was
decorative."

── WHY WARN AND NOT BLOCK, WITH THE NUMBER ────────────────────────────────────
Measured over the obs corpus, 851 calls, 25 Jul – 3 Sep 2026. Of the calls that
ACTUALLY BOOKED with a two-part name on record (n=130):

    surname spoken back   85  (65.4%)
    never spoken          45  (34.6%)   <- a block would have refused these

**A block would have refused roughly one booking in three.** That is not a rate
anything can ship as a gate, so this counts the exposure instead and says so in
the log. Promoting it is a separate decision that wants the rate under about 5%
first — and now it has a number to watch rather than an instinct.

Expect a burst of these warnings at first. That is the measurement working.

── SHAPE ──────────────────────────────────────────────────────────────────────
Three-valued on purpose. None means "cannot tell" — a one-word name, or no
assistant turns on record — and must never be reported as a failure, or the
count is inflated by calls the check simply could not read.

It compares a stored VALUE against what was said, never a phrase matcher, which
is the shape `accepted_slot_is_named_in` uses and the rule
`write-gates-match-one-literal` records. `conversation_history` is the
whole-call record of Susie's turns; `_spoken_chunks` is per-turn and popped, so
it is empty by the time a booking fires.
"""
from __future__ import annotations

import inspect

import pytest

from app.tools.receptionist_tools import _surname_was_spoken_back


def _said(*turns):
    return {
        "conversation_history": [
            {"role": "assistant", "content": c} for c in turns
        ]
    }


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------
def test_a_surname_read_back_is_seen():
    assert _surname_was_spoken_back(
        _said("So that's Quentin Rook, Monday the 7th at ten past twelve."),
        "Quentin Rook",
    ) is True


def test_a_surname_never_spoken_is_reported():
    """The live shape: the booking read back a date and a time, never the name."""
    assert _surname_was_spoken_back(
        _said(
            "Could I take your first name and surname?",
            "So that's Monday the 7th of September at ten past twelve.",
        ),
        "Quentin Rook",
    ) is False


def test_the_match_ignores_case():
    assert _surname_was_spoken_back(_said("so that is QUENTIN ROOK"), "Quentin Rook") is True


# ---------------------------------------------------------------------------
# "Cannot tell" is never a failure
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "session,name",
    [
        ({}, "Quentin Rook"),                                   # no history
        ({"conversation_history": []}, "Quentin Rook"),         # empty history
        ({"conversation_history": [                             # caller only
            {"role": "user", "content": "quentin rook"}]}, "Quentin Rook"),
        (_said("anything"), "Quentin"),                         # one-word name
        (_said("anything"), ""),                                # no name
        (_said("anything"), None),                              # no name at all
        (_said("anything"), "Q R"),                             # too short to match on
    ],
)
def test_unreadable_cases_return_none(session, name):
    """None, never False. Reporting these as failures would inflate the very
    rate the decision to block will be taken on."""
    assert _surname_was_spoken_back(session, name) is None


def test_a_user_turn_naming_the_surname_does_not_count():
    """The caller SAYING their name is the thing being checked against, not
    evidence that Susie repeated it — that is the whole point of the check."""
    session = {
        "conversation_history": [
            {"role": "user", "content": "yeah that'll be Quentin Rook"},
            {"role": "assistant", "content": "Thanks — and your number?"},
        ]
    }
    assert _surname_was_spoken_back(session, "Quentin Rook") is False


# ---------------------------------------------------------------------------
# It must not become a gate by accident
# ---------------------------------------------------------------------------
def test_the_counter_cannot_block_a_booking():
    """THE guard. This ships as a WARNING and nothing may read it. If a future
    edit turns it into a gate, that is a decision to take deliberately, with
    the rate in front of you — not something to inherit from a counter."""
    from app.tools import receptionist_tools

    src = inspect.getsource(receptionist_tools)
    start = src.index("SURNAME NOT READ BACK")
    window = src[start - 600:start + 400]

    assert "logger.warning" in window
    assert "_sur_seen is False" in window
    # No early return, no success:False, and no mutation of the booking args.
    assert "return {" not in window
    assert 'args["name"]' not in window


def test_the_counter_is_wired_above_the_executor_branch():
    """Placed with A3, above the backend split, so all four executors are
    covered by one site rather than three that drift."""
    from app.tools import receptionist_tools

    src = inspect.getsource(receptionist_tools)
    warn_at = src.index("SURNAME NOT READ BACK")
    dispatch_at = src.index('if _resolve_clinic_id(session) in ("theorem"', warn_at)
    assert warn_at < dispatch_at
