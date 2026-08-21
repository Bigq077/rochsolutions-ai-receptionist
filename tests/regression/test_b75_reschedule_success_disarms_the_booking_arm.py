# tests/regression/test_b75_reschedule_success_disarms_the_booking_arm.py
"""
B-75 — Gate 5f destroyed a TRUE reschedule confirmation and then blocked the retry.

JV `CA9262659c67e03b73b5ff2992f72bc832`, 21 Aug 2026, demo line.

    19:59:30  reschedule_appointment -> {"success": true,
                                         "rescheduled_to": "Friday 28 August at 17:15"}
    19:59:50  caller: "okay that's all good see you bye-bye have you rescheduled it then"
    19:59:52  ERROR [ms_gate5f] false booking confirmation with no successful write
              (armed=booking) - re-steering: "Yes, all sorted - you're booked in for
              Friday the 28th of August at quarter past"
    20:00:09  WARNING reschedule_appointment BLOCKED - the move confirmation question
              was never asked (last_bot_prompt='Sorry - before I confirm anything,
              shall I go ahead and book that in for you?')

The write had landed 22 seconds earlier. The sentence the gate destroyed was true.

Cause. `_armed_write_families`'s legacy R1 arm is
`booking_flow_active AND NOT booking_write_confirmed`. Both halves hold for the
whole of every reschedule call:

  * `booking_flow_active` is set by the booking-ack path in connection.py, which
    fires for `intent=reschedule` too - the log says so in as many words at
    19:58:26 ("booking ack - location known (bolton), intent=reschedule").
  * `booking_write_confirmed` is set ONLY under `family == WRITE_FAMILY_BOOKING`
    in `_note_write_result`. A reschedule latches `WRITE_SUCCEEDED_KEY`
    ["reschedule"] and never touches it.

So the booking family was armed from the first turn to the last, and the first
sentence the model phrased as "you're booked in" was re-steered - a true
statement replaced with a question contradicting it.

Then the second-order failure, which is risk R5 in the header of
test_b36_gate5f_write_families.py reached from a SUCCESS rather than a phantom:
the re-steer becomes `last_bot_prompt`, every write gate reads `last_bot_prompt`
to decide whether its confirmation question was asked, and the booking CTA does
not satisfy the MOVE gate. Had the first reschedule not already landed, this
would have blocked a legitimate one - a silent non-booking.

Why the existing suite missed it. `test_a_successful_reschedule_confirmation_is_never_examined`
is this test without `booking_flow_active`, and its `_session()` helper is
documented as "A session with no booking flow - the shape a reschedule call
actually has". That is the false assumption. The live log shows a reschedule call
has exactly that flag set.

Fix: a successful reschedule stands the R1 booking arm down, as a successful
booking already does. NOT any success - see the cancel test below.
"""
from __future__ import annotations

from app.media_streams import turn_handler as th
from app.media_streams import llm_stream as ls


BOOKING = th.WRITE_FAMILY_BOOKING
RESCHEDULE = th.WRITE_FAMILY_RESCHEDULE
CANCEL = th.WRITE_FAMILY_CANCEL

# What the model actually said at 19:59:52, with the apostrophe normalised the
# way the gate sees it after Gate 5's punctuation pass.
TRUE_CONFIRMATION = (
    "Yes, all sorted - you are booked in for Friday the 28th of August "
    "at quarter past five"
)

NEW_SLOT_ISO = "2026-08-28T17:15:00"


def _rescheduling_session(**over):
    """The shape a reschedule call REALLY has: booking_flow_active is set.

    This is the single fact test_b36's `_session()` gets wrong.
    """
    s = {
        "_clinical_depth_cache": "",
        "v3_cta_count": 0,
        "booking_flow_active": True,
    }
    s.update(over)
    return s


def _succeed(session, tool_name, **extra):
    """Drive a real success through the real recorder - never hand-set the latch."""
    result = {"success": True}
    result.update(extra)
    return ls._note_write_result(session, tool_name, result)


# ══════════════════════════════════════════════════════════════════════════
# 1 — the defect
# ══════════════════════════════════════════════════════════════════════════
def test_a_landed_reschedule_stands_the_booking_arm_down():
    s = _rescheduling_session()
    _succeed(s, "reschedule_appointment", attempted_slot_iso=NEW_SLOT_ISO)
    assert th._armed_write_families(s) == [], (
        "the booking family is armed on a call where the reschedule already "
        "landed - every true confirmation from here is re-steered"
    )


def test_the_true_confirmation_reaches_the_caller():
    """The sentence the live call destroyed."""
    s = _rescheduling_session()
    _succeed(s, "reschedule_appointment", attempted_slot_iso=NEW_SLOT_ISO)
    out = th.sanitise_response(TRUE_CONFIRMATION, s)
    assert "booked in for friday the 28th" in out.lower(), (
        "Gate 5f stripped a confirmation of a write that had already succeeded"
    )
    assert out != th._FALSE_CONFIRM_RESTEER


def test_the_resteer_no_longer_disarms_the_move_gate():
    """Risk R5, the reason this mattered twice.

    The re-steer becomes last_bot_prompt. The BOOKING re-steer does not satisfy
    the MOVE gate, so a retry of the reschedule is blocked - which is exactly
    what happened at 20:00:09. Asserted against the gate's own predicate.
    """
    s = _rescheduling_session()
    _succeed(s, "reschedule_appointment", attempted_slot_iso=NEW_SLOT_ISO)
    spoken = th.sanitise_response(TRUE_CONFIRMATION, s)
    assert not ls._booking_confirmation_asked(spoken), (
        "the caller's next 'yes' would satisfy the BOOKING gate on a "
        "reschedule call"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2 — the hole this fix must NOT open
# ══════════════════════════════════════════════════════════════════════════
def test_a_landed_cancel_does_not_stand_the_booking_arm_down():
    """Reschedule only, never 'any success'.

    After a cancel NO appointment exists, so "you're booked in for Friday" is
    still a phantom and must still be caught. If someone ever widens this to
    `any(succeeded.values())`, this is what breaks.
    """
    s = _rescheduling_session()
    _succeed(s, "cancel_appointment")
    assert th._armed_write_families(s) == [BOOKING]
    assert th.sanitise_response(TRUE_CONFIRMATION, s) == th._FALSE_CONFIRM_RESTEER


def test_r1_still_catches_a_pure_phantom():
    """No tool called at all - the only thing the legacy arm ever caught."""
    s = _rescheduling_session()
    assert th._armed_write_families(s) == [BOOKING]
    assert th.sanitise_response(
        "You're all booked for Tuesday.", s
    ) == th._FALSE_CONFIRM_RESTEER


def test_a_refused_reschedule_still_leads_the_flow_arm():
    """Attribution is unchanged: a REFUSED move still arms, and still leads."""
    s = _rescheduling_session()
    ls._note_write_result(
        s, "reschedule_appointment",
        {"status": "reschedule_confirmation_required"},
    )
    assert th._armed_write_families(s) == [RESCHEDULE, BOOKING]


def test_a_refusal_after_the_success_does_not_rearm_booking():
    """The live call's own shape at 20:00:09: succeeded, then refused again."""
    s = _rescheduling_session()
    _succeed(s, "reschedule_appointment", attempted_slot_iso=NEW_SLOT_ISO)
    ls._note_write_result(
        s, "reschedule_appointment",
        {"status": "reschedule_confirmation_required"},
    )
    assert BOOKING not in th._armed_write_families(s)


# ══════════════════════════════════════════════════════════════════════════
# 3 — lifetime. The write was three turns before the misfire.
# ══════════════════════════════════════════════════════════════════════════
def test_the_success_latch_is_not_cleared_per_turn():
    """Same technique as test_booking_write_confirmed_is_not_cleared_per_turn.

    The write landed on turn 19 and the gate misfired on turn 22. If this latch
    were turn-scoped the fix would not survive one turn boundary.
    """
    import inspect
    src = inspect.getsource(ls.LLMStream._streaming_tool_loop)
    assert "_false_confirm_resteered" in src, "reset site moved - re-pin this test"
    assert "pop(WRITE_SUCCEEDED_KEY" not in src
    assert "WRITE_SUCCEEDED_KEY, None" not in src


def test_the_arm_stays_down_across_a_turn_boundary():
    """Apply the documented per-turn clears and re-check."""
    s = _rescheduling_session()
    _succeed(s, "reschedule_appointment", attempted_slot_iso=NEW_SLOT_ISO)
    for _ in range(3):
        # exactly what _streaming_tool_loop resets at the top of every turn
        s["_false_confirm_resteered"] = False
        s.pop(th.WRITE_REFUSED_KEY, None)
        s.pop("_spoken_this_turn", None)
        assert th._armed_write_families(s) == []


# ══════════════════════════════════════════════════════════════════════════
# 4 — the latch survives a Redis round trip
# ══════════════════════════════════════════════════════════════════════════
def test_the_latch_is_json_serialisable_and_survives_the_round_trip():
    """The session is persisted with json.dumps; a set would raise there."""
    import json
    s = _rescheduling_session()
    _succeed(s, "reschedule_appointment", attempted_slot_iso=NEW_SLOT_ISO)
    revived = json.loads(json.dumps(s))
    assert th._armed_write_families(revived) == []


def test_a_corrupt_success_latch_does_not_raise():
    """Defend the read the same way the refusal marker is defended."""
    s = _rescheduling_session(**{th.WRITE_SUCCEEDED_KEY: "not-a-dict"})
    assert th._armed_write_families(s) == [BOOKING]


# ══════════════════════════════════════════════════════════════════════════
# 5 — the key has one definition
# ══════════════════════════════════════════════════════════════════════════
def test_the_latch_key_is_defined_once_and_shared():
    """turn_handler owns the write vocabulary; llm_stream imports it.

    A second literal would drift, and the two readers would silently stop
    agreeing about what a completed write looks like.
    """
    assert ls.WRITE_SUCCEEDED_KEY is th.WRITE_SUCCEEDED_KEY
    import inspect
    ls_src = inspect.getsource(ls)
    assert ls_src.count('WRITE_SUCCEEDED_KEY = "') == 0, (
        "llm_stream re-defines the key instead of importing it"
    )
