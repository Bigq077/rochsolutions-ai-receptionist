"""
B-62 (P1) — a caller was told quarter past six; the diary said half past five.

JV CA6bd7fb424a72246a38671d4690913850, 19 Aug 2026, build 85302fd. Calendar
checked after the call: 17:30.

    12:46:35  reschedule_appointment success=true -> Wednesday 19 August 17:30
              (the only confirmation SMS on the call)
       ...    the caller is confused by B-61 and restarts the reschedule ...
    12:48:34  reschedule_appointment {"new_slot_iso": "2026-08-19T18:15:00+01:00"}
    12:48:34  BLOCKED - the move confirmation question was never asked
    12:48:34  ... the reschedule family already completed this call -
              guard NOT armed, duplicate-write rule attached instead
    12:48:36  "That's you rescheduled - you're now in for Wednesday the 19th..."
              "Confirmation text on its way."

The second move was REFUSED. Susie announced it as done, at the NEW time, and
promised a text that was never sent. `CLAUDE.md` 6.1 - "every booking the caller
believes was made exists" - failing outright.

Cause: Layer 2b in `_note_write_result` asks only whether the FAMILY has
succeeded this call:

    if succeeded.get(family):
        ... attach _WRITE_ALREADY_DONE_RULE, do NOT arm the guard, return

It never compares WHICH slot was refused against WHICH succeeded. 17:30 -> 18:15
is not a duplicate of anything; "already done" is a lie about the thing the
caller just asked for, and the guard that would have stripped the claim is
switched off by the same branch.

The coarseness is deliberate and documented (llm_stream.py ~:954): the CANCEL
executor's success payload carries no appointment id, so an id-keyed latch would
need executor changes. That reasoning is sound for cancel and for a genuine
duplicate. It is wrong the moment the second write names a different target.

    Scope note: these tests cover RESCHEDULE only. It is the family that can be
    fixed without touching an executor, because _exec_reschedule_appointment
    already returns `rescheduled_to` (receptionist_tools.py ~:6563). Cancel
    needs separate thought and must not block this.

HOW THE TARGET REACHES THE DECISION
-----------------------------------
`_note_write_result(session, tool_name, result)` never receives the tool ARGS,
and neither reschedule executor returns an ISO, so before this fix the refused
target was not visible to the decision at all. The funnel call site now attaches
`attempted_slot_iso` from the args to BOTH the success and the refusal — one
place, symmetric, no signature change. The contract these tests pin is "a
different-target refusal arms the guard"; the transport is an implementation
detail and may move.
"""

import pytest

from app.media_streams.llm_stream import (
    _WRITE_ALREADY_DONE_RULE,
    _note_write_result,
    WRITE_SUCCEEDED_KEY,
)
from app.media_streams.turn_handler import (
    WRITE_FAMILY_RESCHEDULE,
    WRITE_REFUSED_KEY,
)


TOOL = "reschedule_appointment"

SLOT_A_ISO = "2026-08-19T17:30:00+01:00"          # the move that succeeded
SLOT_A_SPOKEN = "Wednesday 19 August at 17:30"
SLOT_B_ISO = "2026-08-19T18:15:00+01:00"          # the move that was refused


def _the_move_that_succeeded() -> dict:
    """_exec_reschedule_appointment's real success payload, as the funnel passes it.

    `attempted_slot_iso` is not the executor's — neither reschedule executor
    returns an ISO — it is attached at the `_note_write_result` call site from
    the tool args, so both the success and the refusal arrive saying which slot
    they concern. Without it neither can be told from the other.
    """
    return {
        "success": True,
        "rescheduled_to": SLOT_A_SPOKEN,
        "attempted_slot_iso": SLOT_A_ISO,
    }


def _a_refusal(attempted_slot_iso: str | None) -> dict:
    """The move gate's real refusal, plus the target it refused.

    `status` with no `success` key is the shape every gate refusal has
    (llm_stream.py ~:4047). `attempted_slot_iso` is attached by the funnel call
    site, not by the gate — pass None here to model a refusal whose target
    cannot be identified.
    """
    refusal = {
        "status": "reschedule_confirmation_required",
        "message": (
            "reschedule_appointment cannot fire yet. Ask 'Shall I go ahead and "
            "move it for you?' and wait for a clear yes before calling "
            "reschedule_appointment."
        ),
    }
    if attempted_slot_iso is not None:
        refusal["attempted_slot_iso"] = attempted_slot_iso
    return refusal


def _armed(session: dict) -> bool:
    return bool((session.get(WRITE_REFUSED_KEY) or {}).get(WRITE_FAMILY_RESCHEDULE))


def _session_after_a_successful_move() -> dict:
    """Run the real success path so WRITE_SUCCEEDED_KEY is set the way it is live."""
    session: dict = {}
    _note_write_result(session, TOOL, _the_move_that_succeeded())
    assert (session.get(WRITE_SUCCEEDED_KEY) or {}).get(WRITE_FAMILY_RESCHEDULE), (
        "precondition failed: the successful move did not register"
    )
    assert not _armed(session), "a success must never arm the guard"
    return session


# -- the regression --------------------------------------------------------

def test_a_refused_move_to_a_different_slot_arms_the_guard():
    """
    The whole of B-62 in one assertion. 17:30 succeeded; 18:15 was refused.
    Nothing about that is a duplicate, so the false-confirmation guard has to be
    armed - it is the only thing standing between the model and "you're now in
    for quarter past six", which is what the caller acted on.
    """
    session = _session_after_a_successful_move()

    _note_write_result(session, TOOL, _a_refusal(SLOT_B_ISO))

    assert _armed(session), (
        "a refused move to a DIFFERENT slot was treated as a duplicate of the "
        "one that succeeded, so the false-confirmation guard was never armed. "
        "On CA6bd7fb424a72246a38671d4690913850 the caller was then told he was "
        "in for quarter past six; the diary said half past five."
    )


def test_the_model_is_not_told_the_different_slot_was_already_done():
    """
    The other half, and the one that actually produced the sentence: on the
    duplicate path the model is handed _WRITE_ALREADY_DONE_RULE ("a reschedule
    already completed successfully earlier on this call"). Against a DIFFERENT
    slot that rule is false, and it is what invited the claim.
    """
    session = _session_after_a_successful_move()

    result = _note_write_result(session, TOOL, _a_refusal(SLOT_B_ISO))

    assert result.get("caller_message_rule") != _WRITE_ALREADY_DONE_RULE[
        WRITE_FAMILY_RESCHEDULE
    ], (
        "the model was told the refused move was 'already done' - but it was a "
        "different time from the one that succeeded, so nothing about it was "
        "done"
    )


def test_the_refusal_must_carry_what_it_refused():
    """
    The fail-safe, and the reason this defect was reachable at all.

    Before the fix nothing told the funnel which slot a refusal concerned, so
    18:15 and 17:30 were indistinguishable and every refusal after a success
    read as a duplicate. The target is plumbed now — but if it is ever missing
    again (a malformed tool call, a new refusal site that forgets it), the
    answer must be "not a duplicate", not "probably fine".
    """
    session = _session_after_a_successful_move()

    _note_write_result(session, TOOL, _a_refusal(None))

    assert _armed(session), (
        "a refusal that names no target is indistinguishable from a duplicate, "
        "and this one was let through as one. Whatever the transport, the fix "
        "must FAIL SAFE: when the decision cannot tell which slot was refused, "
        "arm the guard. Silence is recoverable; 'you're now in for quarter past "
        "six' when the diary says half past five is not."
    )


# -- what must NOT change --------------------------------------------------

def test_a_genuine_duplicate_is_still_not_armed():
    """
    CA0f9a12, the reason Layer 2b exists. The SAME move refused twice is a
    duplicate: arming would strip the turn's speech over an attempt that changed
    nothing, and the no-claim rule would have the model say something about the
    caller's calendar that this code has no basis for.

    This must keep passing. If a fix for B-62 breaks it, the fix is wrong.
    """
    session = _session_after_a_successful_move()

    _note_write_result(session, TOOL, _a_refusal(SLOT_A_ISO))

    assert not _armed(session), (
        "a repeat of the move that already succeeded was treated as a failure; "
        "CA0f9a12 is what that looks like on a call"
    )


def test_a_genuine_duplicate_still_gets_the_already_done_rule():
    """The companion half — the model is steered to say goodbye, not apologise."""
    session = _session_after_a_successful_move()

    result = _note_write_result(session, TOOL, _a_refusal(SLOT_A_ISO))

    assert result.get("caller_message_rule") == _WRITE_ALREADY_DONE_RULE[
        WRITE_FAMILY_RESCHEDULE
    ], "the genuine-duplicate steer was lost"


def test_a_refusal_with_no_prior_success_still_arms():
    """
    The ordinary path, untouched: a refused move with nothing successful behind
    it arms the guard, as it always has. Pinned so a B-62 fix cannot narrow the
    arming condition by accident.
    """
    session: dict = {}

    _note_write_result(session, TOOL, _a_refusal(SLOT_B_ISO))

    assert _armed(session), (
        "a refused move with no earlier success must arm — this is the original "
        "B-36 cause 2 behaviour and nothing here should touch it"
    )
