# tests/regression/test_b75c_reschedule_outcome_backstop.py
"""
B-75c — a landed reschedule had no unspoken-outcome backstop; booking did.

JV `CA9262659c67e03b73b5ff2992f72bc832`, 21 Aug 2026:

    19:59:30.487  reschedule_appointment -> success, Friday 28 August at 17:15
    19:59:32.356  barge-in on the partial 'have'
    19:59:32.899  [ms_conn] tts_inhibit: discarding stale chunk
                  "That's you rescheduled - you're now in for Friday the 28th o"
    19:59:34.909  [ms_llm] turn produced no audible speech
    19:59:50.956  caller: "...have you rescheduled it then"

The move was in the diary. The sentence announcing it was generated and then
thrown away as a stale chunk, the turn ended having emitted nothing, and there
was no backstop to say the true thing instead.

Booking has had exactly this backstop since CAd8868396 (Vital Edge, 11 Aug),
where a written booking went unannounced and the caller hung up believing
nothing had happened. Reschedule never got one for the same reason B-75 itself
happened one function away: the latch that feeds it was written only under
`family == WRITE_FAMILY_BOOKING`.

This does NOT fix the barge-in that discarded the chunk — that is held
deliberately, because the recorded anchors say the obvious fix there makes
turn-taking worse. It makes the discard survivable: the caller hears the
outcome either way.

PROVISIONAL IS NOT CONFIRMED. On a provisional clinic the move is a REQUEST,
so `_exec_reschedule_appointment` now reports which path ran and the sentence
changes accordingly. Telling a Vital Edge caller they are moved would be the
false confirmation the whole write-guard family exists to prevent.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams import llm_stream as ls
from app.media_streams import turn_handler as th


SLOT = "Friday 28 August at 17:15"
KEY = "_booking_outcome_unspoken"


def _moved(**over):
    r = {
        "success": True,
        "rescheduled_to": SLOT,
        "attempted_slot_iso": "2026-08-28T17:15:00",
    }
    r.update(over)
    return r


# ══════════════════════════════════════════════════════════════════════════
# 1 — the sentence exists and is built from the TOOL RESULT
# ══════════════════════════════════════════════════════════════════════════
def test_a_landed_move_leaves_a_sentence_for_the_caller():
    s = {}
    ls._note_write_result(s, "reschedule_appointment", _moved())
    assert SLOT in s.get(KEY, ""), "a written move left the caller nothing to hear"


def test_the_confirmed_sentence_says_moved():
    line = ls._reschedule_outcome_line(_moved())
    low = line.lower()
    assert "rescheduled" in low
    assert SLOT in line


def test_the_provisional_sentence_never_claims_it_is_done():
    """Vital Edge: the practitioner has not accepted yet."""
    line = ls._reschedule_outcome_line(_moved(provisional=True))
    low = line.lower()
    assert "not confirmed just yet" in low
    assert "rescheduled" not in low, "a provisional move announced as done"
    assert SLOT in line


def test_neither_sentence_promises_a_text():
    """SMS is env-gated per service; a promise made here cannot check it.

    Same rule the booking twin states.
    """
    for r in (_moved(), _moved(provisional=True)):
        low = ls._reschedule_outcome_line(r).lower()
        assert "text" not in low and "sms" not in low


# ══════════════════════════════════════════════════════════════════════════
# 2 — it must never speak when there is nothing true to say
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("result", [
    {"status": "reschedule_confirmation_required"},   # refused at the gate
    {"success": False, "error": "Calendar not connected."},
    {"success": True},                                 # no slot to speak
    {"success": True, "rescheduled_to": ""},
    {"success": True, "rescheduled_to": "   "},
    None,
    "not-a-dict",
])
def test_nothing_is_armed_without_a_real_written_slot(result):
    assert ls._reschedule_outcome_line(result) == ""


def test_a_refused_move_arms_no_backstop():
    s = {}
    ls._note_write_result(
        s, "reschedule_appointment", {"status": "reschedule_confirmation_required"},
    )
    assert not s.get(KEY)


# ══════════════════════════════════════════════════════════════════════════
# 3 — the integration point that actually matters
# ══════════════════════════════════════════════════════════════════════════
def test_the_backstop_sentence_survives_gate_5():
    """It is spoken through the deferred fallback, so it passes sanitise_response.

    A sentence the guard strips is worse than no backstop: the turn would emit
    nothing at all, which is the state this exists to escape. This is the whole
    reason B-75 had to land first — before it, Gate 5f was armed on the booking
    family for the rest of any reschedule call.
    """
    s = {"_clinical_depth_cache": "", "v3_cta_count": 0, "booking_flow_active": True}
    ls._note_write_result(s, "reschedule_appointment", _moved())
    line = s[KEY]
    assert th.sanitise_response(line, s).strip() == line.strip(), (
        "Gate 5 rewrote the backstop sentence"
    )


def test_the_provisional_backstop_also_survives_gate_5():
    s = {"_clinical_depth_cache": "", "v3_cta_count": 0, "booking_flow_active": True}
    ls._note_write_result(s, "reschedule_appointment", _moved(provisional=True))
    line = s[KEY]
    assert th.sanitise_response(line, s).strip() == line.strip()


# ══════════════════════════════════════════════════════════════════════════
# 4 — lifetime: the same one booking already has
# ══════════════════════════════════════════════════════════════════════════
def test_the_backstop_is_consumed_once_and_cleared_per_turn():
    """Shares booking's key, so it must share booking's lifetime exactly.

    Popped when spoken, and cleared at the top of every turn so a later empty
    turn can never re-announce a stale move.
    """
    src = inspect.getsource(ls.LLMStream._streaming_tool_loop)
    assert 'session.pop("_booking_outcome_unspoken", None)' in src, (
        "the per-turn clear moved - a stale move could be re-announced"
    )
    # the pop-when-spoken lives in the deferred-fallback path, not this method
    mod_src = inspect.getsource(ls)
    assert mod_src.count('session.pop("_booking_outcome_unspoken"') >= 2, (
        "the consume-once pop moved"
    )


def test_the_booking_backstop_is_unchanged():
    """The twin must keep working exactly as it did."""
    s = {}
    ls._note_write_result(
        s, "book_appointment",
        {"success": True, "booked_slot": "Tuesday 18 August at 12:00"},
    )
    assert "Tuesday 18 August at 12:00" in s.get(KEY, "")
    assert "booked in" in s[KEY].lower()


# ══════════════════════════════════════════════════════════════════════════
# 5 — the executor really reports which path it took
# ══════════════════════════════════════════════════════════════════════════
def test_the_reschedule_executor_reports_provisional():
    """Without this the backstop cannot tell a move from a request.

    Asserted against the source: driving the executor needs Google credentials.
    """
    from app.tools import receptionist_tools as rt
    src = inspect.getsource(rt._exec_reschedule_appointment)
    assert '"provisional"' in src, (
        "the reschedule executor no longer reports provisional - the backstop "
        "will announce a Vital Edge request as a completed move"
    )
    assert '"rescheduled_to"' in src
