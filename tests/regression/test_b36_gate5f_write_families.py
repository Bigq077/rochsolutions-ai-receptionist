# tests/regression/test_b36_gate5f_write_families.py
"""
B-36 cause 2a/2b/2c/2e — Gate 5f arms on the REFUSAL, not only on the flow.

`CA23199d089` (3 Aug 2026): reschedule_appointment was BLOCKED and Susie told the
caller their appointment had moved. Gate 5f did not fire — it required
`session["booking_flow_active"]`, which has two assignment sites and neither
fires on a reschedule. Fixing the vocabulary alone would have been a no-op.

The arm is now `refused this turn` OR the original
`booking_flow_active AND NOT booking_write_confirmed`.

Four things this file exists to prevent, in descending order of how badly they
would end:

  1. **R5 — a re-steer unlocking the wrong write gate.** The guard's recovery
     text becomes `last_bot_prompt`, and every write gate in llm_stream reads
     last_bot_prompt to decide whether its confirmation question was asked. The
     booking re-steer contains BOTH booking gate literals. Fire it on a
     reschedule phantom and the caller's next "yes" satisfies the BOOKING gate:
     a phantom reschedule becomes a real booking of a new appointment.
  2. **2c — the over-fire trap.** A SUCCESSFUL reschedule must never be armed,
     or the guard strips a real confirmation. That is not hypothetical: Gate 5c
     did exactly that on 2026-06-12 and abandoned a completed booking.
  3. **R1 — losing coverage that already worked.** The original arm catches a
     phantom the model produced having called no tool at all. OR, not replace.
  4. **R4 — the widened vocabulary false-positiving.** "We've moved to a new
     building" is a plausible clinic FAQ answer.
"""
from __future__ import annotations

import json

import pytest

from app.media_streams import turn_handler as th
from app.media_streams import llm_stream as ls


BOOKING = th.WRITE_FAMILY_BOOKING
RESCHEDULE = th.WRITE_FAMILY_RESCHEDULE
CANCEL = th.WRITE_FAMILY_CANCEL


def _session(**over):
    """A session with no booking flow — the shape a reschedule call actually has."""
    s = {"_clinical_depth_cache": "", "v3_cta_count": 0}
    s.update(over)
    return s


def _refuse(session, tool_name):
    """Drive a real refusal through the real recorder."""
    return ls._note_write_result(
        session, tool_name, {"status": "reschedule_confirmation_required"}
    )


# ══════════════════════════════════════════════════════════════════════════
# R5 — the re-steers must each arm their OWN write gate and no other
# ══════════════════════════════════════════════════════════════════════════
# Asserted against the gates' real predicates, not re-typed literals.
_GATE_PREDICATES = {
    BOOKING:    ls._booking_confirmation_asked,
    RESCHEDULE: ls._move_confirmation_asked,
    CANCEL:     ls._cancel_retention_asked,
}


@pytest.mark.parametrize("family", [BOOKING, RESCHEDULE, CANCEL])
def test_resteer_arms_its_own_gate(family):
    """The recovery must be reachable: re-asking the question has to count."""
    resteer = th._FAMILY_RESTEER[family]
    assert _GATE_PREDICATES[family](resteer) is True, (
        f"the {family} re-steer does not satisfy the {family} gate — the caller "
        f"would answer a question the gate cannot see, and the write would block "
        f"again"
    )


@pytest.mark.parametrize("family", [BOOKING, RESCHEDULE, CANCEL])
def test_resteer_arms_no_other_gate(family):
    """R5. The leak that would have shipped: one shared re-steer string."""
    resteer = th._FAMILY_RESTEER[family]
    for other, predicate in _GATE_PREDICATES.items():
        if other == family:
            continue
        assert predicate(resteer) is False, (
            f"the {family} re-steer also satisfies the {other} gate.\n"
            f"  re-steer: {resteer!r}\n"
            f"A phantom {family} could then be turned into a real {other} write "
            f"by the caller's next 'yes'."
        )


def test_the_booking_resteer_is_the_specific_r5_hazard():
    """Named explicitly so the reason survives a refactor of the loop above."""
    assert ls._booking_confirmation_asked(th._FALSE_CONFIRM_RESTEER) is True
    assert ls._move_confirmation_asked(th._FALSE_CONFIRM_RESTEER) is False
    assert ls._cancel_retention_asked(th._FALSE_CONFIRM_RESTEER) is False


@pytest.mark.parametrize("family", [BOOKING, RESCHEDULE, CANCEL])
def test_no_resteer_is_itself_a_claim(family):
    """A re-steer that tripped the guard would loop or be dropped as a repeat."""
    resteer = th._FAMILY_RESTEER[family]
    for f in (BOOKING, RESCHEDULE, CANCEL):
        assert th._false_write_claim(resteer, f) is False


@pytest.mark.parametrize("family", [BOOKING, RESCHEDULE, CANCEL])
def test_resteer_survives_the_last_bot_prompt_cap(family):
    """B-31: last_bot_prompt is truncated at 200 chars and a lost '?' has
    switched a whole layer off before. Every re-steer must fit with room."""
    assert len(th._FAMILY_RESTEER[family]) < 200


# ══════════════════════════════════════════════════════════════════════════
# 2a — arming
# ══════════════════════════════════════════════════════════════════════════
def test_a_refused_reschedule_arms_the_guard():
    s = _session()
    _refuse(s, "reschedule_appointment")
    assert th._armed_write_families(s) == [RESCHEDULE]


def test_a_refused_cancel_arms_the_guard():
    s = _session()
    ls._note_write_result(s, "cancel_appointment", {"status": "cancellation_confirmation_required"})
    assert th._armed_write_families(s) == [CANCEL]


def test_nothing_refused_and_no_booking_flow_arms_nothing():
    assert th._armed_write_families(_session()) == []


def test_r1_the_original_flow_arm_is_kept_not_replaced():
    """A pure hallucination: the model claims a booking having called no tool.
    There is no refusal to arm on, so only the original arm catches it."""
    s = _session(booking_flow_active=True)
    assert th._armed_write_families(s) == [BOOKING]
    out = th.sanitise_response("You're all booked for Tuesday.", s)
    assert out == th._FALSE_CONFIRM_RESTEER


def test_refused_family_leads_the_flow_arm():
    """Attribution matters: on a turn that is both, the claim belongs to the
    write that was actually refused, so it gets that family's re-steer."""
    s = _session(booking_flow_active=True)
    _refuse(s, "reschedule_appointment")
    assert th._armed_write_families(s) == [RESCHEDULE, BOOKING]


def test_a_corrupt_marker_does_not_raise():
    """The session round-trips through Redis JSON; defend the read."""
    s = _session(**{th.WRITE_REFUSED_KEY: "not-a-dict"})
    assert th._armed_write_families(s) == []


def test_the_marker_is_json_serialisable():
    """A set here would raise TypeError inside redis_store.save_session — an
    unhandled exception in the middle of a live booking."""
    s = _session()
    _refuse(s, "reschedule_appointment")
    ls._note_write_result(s, "cancel_appointment", {"status": "cancellation_confirmation_required"})
    assert json.loads(json.dumps(s))[th.WRITE_REFUSED_KEY] == {
        RESCHEDULE: True, CANCEL: True,
    }


# ══════════════════════════════════════════════════════════════════════════
# 2b — the reschedule phantom, end to end
# ══════════════════════════════════════════════════════════════════════════
def test_the_observed_b36_phantom_is_caught():
    """The shape recorded in the register for CA23199d089."""
    s = _session()
    _refuse(s, "reschedule_appointment")
    out = th.sanitise_response("That's you rescheduled — you're now in for Thursday.", s)
    assert out == th._FALSE_RESCHEDULE_RESTEER
    assert ls._move_confirmation_asked(out) is True
    assert ls._booking_confirmation_asked(out) is False


def test_the_same_phantom_is_untouched_when_nothing_was_refused():
    """2a from the other side: without a refusal there is nothing to guard."""
    s = _session()
    out = th.sanitise_response("That's you rescheduled — you're now in for Thursday.", s)
    assert "rescheduled" in out.lower()


def test_a_second_phantom_chunk_is_dropped_not_repeated():
    s = _session()
    _refuse(s, "reschedule_appointment")
    assert th.sanitise_response("You're all moved to Thursday.", s) == th._FALSE_RESCHEDULE_RESTEER
    assert th.sanitise_response("That's you rescheduled.", s) == ""


def test_a_cancel_phantom_is_caught():
    s = _session()
    ls._note_write_result(s, "cancel_appointment", {"status": "cancellation_confirmation_required"})
    out = th.sanitise_response("That's cancelled for you.", s)
    assert out == th._FALSE_CANCEL_RESTEER
    assert ls._cancel_retention_asked(out) is True


def test_booking_vocabulary_still_counts_on_a_reschedule_turn():
    """"You're all booked in for Thursday" after a REFUSED reschedule is exactly
    as much a phantom as "you're rescheduled" — and gets the reschedule
    re-steer, not a booking CTA."""
    s = _session()
    _refuse(s, "reschedule_appointment")
    out = th.sanitise_response("You're all booked in for Thursday.", s)
    assert out == th._FALSE_RESCHEDULE_RESTEER


# ══════════════════════════════════════════════════════════════════════════
# 2c — the over-fire trap. THE test that makes the design safe.
# ══════════════════════════════════════════════════════════════════════════
def test_a_successful_reschedule_confirmation_is_never_examined():
    s = _session()
    ls._note_write_result(s, "reschedule_appointment", {"success": True})
    assert th._armed_write_families(s) == []
    out = th.sanitise_response("That's you moved to Thursday at ten.", s)
    assert "moved to thursday" in out.lower(), (
        "the guard stripped a REAL reschedule confirmation — this is the "
        "2026-06-12 Gate 5c failure repeating"
    )


def test_a_successful_cancellation_confirmation_is_never_examined():
    s = _session()
    ls._note_write_result(s, "cancel_appointment", {"success": True})
    out = th.sanitise_response("That's cancelled for you.", s)
    assert "cancelled" in out.lower()


def test_r3_refused_then_succeeded_in_one_turn():
    """The observed call ran lookup -> reschedule(refused) -> speech in a single
    turn, and the loop retries writes up to MAX_TOOL_ITERATIONS times. If the
    retry succeeds, the stale marker must not strip the legitimate confirmation."""
    s = _session()
    _refuse(s, "reschedule_appointment")
    assert th._armed_write_families(s) == [RESCHEDULE]
    ls._note_write_result(s, "reschedule_appointment", {"success": True})
    assert th._armed_write_families(s) == []
    out = th.sanitise_response("That's you moved to Thursday at ten.", s)
    assert "moved to thursday" in out.lower()


def test_a_success_clears_only_its_own_family():
    s = _session()
    _refuse(s, "reschedule_appointment")
    ls._note_write_result(s, "cancel_appointment", {"status": "cancellation_confirmation_required"})
    ls._note_write_result(s, "cancel_appointment", {"success": True})
    assert th._armed_write_families(s) == [RESCHEDULE]


def test_a_refused_reschedule_does_not_strip_a_real_booking_confirmation():
    """The mixed turn a single boolean marker would have got wrong."""
    s = _session()
    ls._note_write_result(s, "book_appointment", {"success": True})
    _refuse(s, "reschedule_appointment")
    out = th.sanitise_response("You're all booked for Tuesday at ten.", s)
    # The booking family is disarmed by its own success; the reschedule family
    # is armed, and booking vocabulary on a refused-reschedule turn is a claim.
    # It is re-steered to the RESCHEDULE question — never to a booking CTA.
    assert out == th._FALSE_RESCHEDULE_RESTEER
    assert ls._booking_confirmation_asked(out) is False


# ══════════════════════════════════════════════════════════════════════════
# R4 — the widened vocabulary, measured against both classes
# ══════════════════════════════════════════════════════════════════════════
_RESCHEDULE_PHANTOMS = [
    "that's you rescheduled",
    "you're rescheduled for thursday",
    "that's all moved for you",
    "you're now moved to thursday at ten",
    "it's changed to friday",
    "i've moved that to thursday",
    "i've moved you to thursday",
    "we've rescheduled your appointment",
    "i've switched it to the afternoon",
    "your appointment has been moved to thursday",
    "your appointment is now rescheduled",
    "your booking is moved",
    "you're all set for thursday",
    "you're in for thursday at ten",
    "that's you moved",
    "i've changed that to the 6th",
]

_CANCEL_PHANTOMS = [
    "that's cancelled",
    "your appointment is cancelled",
    "your appointment has been cancelled",
    "i've cancelled that for you",
    "we've cancelled your appointment",
    "it's now cancelled",
    "that's been cancelled",
    "i've cancelled it",
    "your booking is now cancelled",
    "i've taken that off the diary",
    "i've taken it out of the system",
    "the appointment is cancelled",
]


@pytest.mark.parametrize("text", _RESCHEDULE_PHANTOMS)
def test_reschedule_phantoms_are_caught(text):
    assert th._false_write_claim(text, RESCHEDULE) is True


@pytest.mark.parametrize("text", _CANCEL_PHANTOMS)
def test_cancel_phantoms_are_caught(text):
    assert th._false_write_claim(text, CANCEL) is True


# The other half of the measurement. Every one of these is a sentence Susie may
# legitimately say on a turn where the write WAS refused, so each is a real
# false-positive candidate, not padding.
_LEGITIMATE_ON_A_REFUSED_TURN = [
    # The FAQ trap R4 named. This is why the pattern requires an object after
    # the verb: "moved you/that/it to", never a bare "moved to".
    "we've moved to a new building on the high street",
    "the clinic has moved to number forty two",
    "we moved to the new premises last year",
    # Offers, questions and intent — the classes Gate 5c over-fired on.
    "shall i go ahead and move it for you",
    "would you like me to move that to thursday",
    "i can move that for you once i've found the appointment",
    "i'll move that for you now",
    "i'm going to move that to thursday",
    "let me just move that for you",
    "i haven't moved anything yet",
    "i can't move that without your date of birth",
    "to move that i'll need your phone number",
    "before i move it can i take your surname",
    "once that's moved i'll send you a text",
    "do you want me to cancel it altogether",
    "would you like to keep this appointment or cancel it altogether",
    "i'll cancel that for you once you confirm",
    "i haven't cancelled anything",
    "i can't cancel that without finding it first",
    "to cancel that i'll need the date",
    "shall i cancel it altogether",
    # Statements about the caller's own situation, not about a write.
    "you're welcome to move it again nearer the time",
    "you can cancel any time up to twenty four hours before",
    "i'm just finding that appointment for you",
    "let me check what we've got on thursday",
]


@pytest.mark.parametrize("text", _LEGITIMATE_ON_A_REFUSED_TURN)
@pytest.mark.parametrize("family", [BOOKING, RESCHEDULE, CANCEL])
def test_no_false_positive_on_legitimate_lines(text, family):
    assert th._false_write_claim(text, family) is False, (
        f"over-fire on {family}: {text!r} — this strips a legitimate sentence"
    )


def test_the_faq_line_is_specifically_safe_end_to_end():
    """The single line R4 flagged as dangerous, through the real gate."""
    s = _session()
    _refuse(s, "reschedule_appointment")
    out = th.sanitise_response("We've moved to a new building on the high street.", s)
    assert "moved to a new building" in out.lower()


# ══════════════════════════════════════════════════════════════════════════
# R6 — the marker is scoped to write TOOLS, not to falsy result shapes
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "tool_name,result",
    [
        ("lookup_patient",     {"found": False}),
        ("lookup_appointment", {"found": False, "error": "No future appointment found."}),
        ("check_availability", {"available_days": []}),
    ],
)
def test_a_failed_lookup_does_not_arm_the_guard(tool_name, result):
    """"I can't find your appointment" is a legitimate thing to say, and the
    lookup family reports `found`, not `success`."""
    s = _session()
    ls._note_write_result(s, tool_name, result)
    assert th._armed_write_families(s) == []


def test_a_failed_lookup_does_not_strip_the_turns_speech():
    s = _session()
    ls._note_write_result(s, "lookup_patient", {"found": False})
    out = th.sanitise_response(
        "I can't find an appointment under that number, I'm afraid.", s
    )
    assert "can't find" in out.lower() or "cant find" in out.lower()


# ══════════════════════════════════════════════════════════════════════════
# R2 — marker lifetime
# ══════════════════════════════════════════════════════════════════════════
def test_the_marker_is_turn_scoped_and_the_reset_is_wired():
    """Left set, the guard stays armed for the rest of the CALL and strips every
    later confirmation. Pins that the per-turn reset clears this key — the same
    site that resets _false_confirm_resteered."""
    import inspect
    src = inspect.getsource(ls.LLMStream._streaming_tool_loop)
    assert "_false_confirm_resteered" in src, "reset site moved — re-pin this test"
    assert "WRITE_REFUSED_KEY" in src, (
        "the refusal marker is not cleared per turn; it will stay armed for the "
        "rest of the call"
    )


def test_booking_write_confirmed_is_not_cleared_per_turn():
    """The opposite lifetime, deliberately: a completed booking is call-scoped."""
    import inspect
    src = inspect.getsource(ls.LLMStream._streaming_tool_loop)
    assert 'session["booking_write_confirmed"] = ' not in src
    assert 'pop("booking_write_confirmed"' not in src
