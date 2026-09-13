"""N-6 — Gate 5g reads the name out of this turn's reply before asking for it.

CAe3023240 (northgate demo line, 13 Sep 2026, build 0d5c8556), turn 7, three
log lines 100 ms apart:

    16:47:03,764  [ms_gate5] booking CTA held back — name missing; asked for
                  it instead: 'Before I do that — could I take your first
                  name and surname?'
    16:47:03,804  [ms_conn v3] name recovered from the raw reply — Gate 5g
                  had deleted the acknowledgement: 'Lecture'
    16:47:03,868  [ms_conn v3] name persisted (normal path): 'Lecture'

The model had accepted a corrected name silently at turn 6 (no
acknowledgement, so nothing for _v3_try_persist_name to read). At turn 7 it
wrote the readback — "So that's Lecture, Tuesday the 15th at eight — shall I
go ahead?" — the first text on the call carrying the name. Gate 5g, per chunk,
saw no name, deleted that sentence and asked for the name a THIRD time. The
O-18 recovery then read the name out of the raw reply it had just thrown
away. One turn late by construction.

Now the gate asks the same reader first, with the chunks spoken so far plus
the one in hand, ANCHORED patterns only. The side effects (save_session, DTMF
arming) stay in connection.py, which consumes the marker the gate leaves.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams.turn_handler import sanitise_response
from app.media_streams.llm_stream import _record_spoken

CLI = "07502211207"
T7 = ("So that's Lecture, Tuesday the 15th of September at eight in the "
      "morning — shall I go ahead and book that in?")
NAME_Q = "could I take your first name and surname?"


def _session(**over):
    s = {
        "clinic_id": "northgate",
        "twilio_from_local": CLI,
        "booking_flow_active": True,
        "slots_presented": True,
        "phone_confirmed": True,
        # The keypad slot map from the readout stays latched on a live call.
        "v3_dtmf_slot_map": {"1": "eight in the morning"},
        "_turn_user_text": "uh yeah that is",
    }
    s.update(over)
    return s


# ── The call ────────────────────────────────────────────────────────────────

def test_turn_7_keeps_its_readback_and_its_cta():
    s = _session()
    out = sanitise_response(T7, s)
    assert out == T7, out
    assert s["patient_name"] == "Lecture"
    assert (s.get("collected") or {}).get("name") == "Lecture"
    assert s["_gate5g_persisted_name"] is True
    assert not s.get("_gate5g_dropped_name_ack"), "nothing was dropped"


def test_the_third_ask_never_happens():
    out = sanitise_response(T7, _session())
    assert NAME_Q not in out


def test_the_ack_and_the_cta_can_be_in_different_chunks():
    s = _session()
    first = sanitise_response(
        "So that's Lecture, Tuesday the 15th of September at eight in the morning.", s
    )
    _record_spoken(s, first)
    second = sanitise_response("Shall I go ahead and book that in?", s)
    assert second == "Shall I go ahead and book that in?"
    assert s["patient_name"] == "Lecture"


def test_thanks_form_reads_the_surname_from_the_caller():
    s = _session(_turn_user_text="quentin rook")
    out = sanitise_response("Thanks Quentin — shall I go ahead and book that in?", s)
    assert "shall I go ahead" in out
    assert s["patient_name"] == "Quentin Rook"


def test_phone_still_outstanding_asks_the_phone_not_the_name():
    s = _session(phone_confirmed=False)
    out = sanitise_response(T7, s)
    assert s["patient_name"] == "Lecture"
    assert "best number" in out
    assert "first name" not in out


# ── What must NOT change ───────────────────────────────────────────────────

def test_a_cta_with_no_name_in_it_still_asks_for_the_name():
    s = _session()
    out = sanitise_response("Lovely — shall I go ahead and book that in?", s)
    assert NAME_Q in out
    assert s.get("patient_name") is None
    assert s.get("_gate5g_dropped_name_ack") is True, "O-18 still armed"


@pytest.mark.parametrize("line", [
    # C1 twin of P1: a time-first readback is not a name.
    "So that's quarter to twelve, Tuesday — shall I go ahead and book that in?",
    # B-81: an adjective is not a name.
    "So it's good you're getting it looked at — shall I go ahead and book that in?",
    # BARE stays gated: a title-case opener is not a name without the phase signal.
    "Rehab — shall I go ahead and book that in?",
])
def test_the_readers_false_positive_guards_still_hold(line):
    s = _session()
    sanitise_response(line, s)
    assert s.get("patient_name") is None, line


def test_a_known_name_is_never_overwritten_here():
    s = _session(patient_name="Sarah Jones")
    sanitise_response(T7, s)
    assert s["patient_name"] == "Sarah Jones"
    assert not s.get("_gate5g_persisted_name")


def test_outside_a_booking_the_gate_stays_dormant():
    s = _session(booking_flow_active=False, slots_presented=False)
    out = sanitise_response(T7, s)
    assert out == T7
    assert s.get("patient_name") is None


# ── The seams ──────────────────────────────────────────────────────────────

def test_connection_consumes_the_marker():
    from app.media_streams import connection

    src = inspect.getsource(connection)
    assert 'self.session.pop("_gate5g_persisted_name", False)' in src
    assert "_name_persisted = True" in src


def test_llm_stream_resets_the_marker_per_turn():
    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    assert 'session["_gate5g_persisted_name"] = False' in src
