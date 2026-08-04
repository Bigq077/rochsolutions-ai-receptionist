"""
The Alcester/Redditch location ladder, and its keypad fallback.

Characterisation test. The ladder was already built and already correct when
this was written (2026-08-04) — it had simply never been pinned by anything, so
nothing would have caught it silently regressing. Verified during the Theorem
acceptance sweep, on owner request.

The contract, in the order the caller experiences it:

    rung 1  open question   "Is this for our Awlstuh or Redditch clinic?"
    rung 2  biased confirm  "did you say the Awlstuh clinic? … 'use this clinic'"
    rung 3  KEYPAD          "press 1 for Awlstuh, or 2 for Redditch"

Rung 3 is the SECOND fallback — that positioning is the requirement, and it is
what these tests hold. Voice must keep working throughout; the keypad is an
escape hatch for when speech recognition has already failed twice, never a
replacement for saying the clinic out loud.

It must also cover every context that asks the question — booking, cancel,
reschedule, and a clinic-specific FAQ — not booking alone.

"Awlstuh" is the deliberate phonetic spelling of Alcester for ElevenLabs. Seeing
it here is correct.
"""

import inspect
import re

import pytest
from unittest.mock import MagicMock

from app.media_streams import connection as conn


# ── the ladder, as constants ────────────────────────────────────────────────

def test_three_rungs_exist_and_are_distinct():
    rungs = (conn._LOC_RUNG1_OPEN, conn._LOC_RUNG2_CONFIRM, conn._LOC_RUNG3_DTMF)
    assert len(set(rungs)) == 3, "two ladder rungs speak the same words"


def test_rung1_names_both_clinics_and_asks():
    r1 = conn._LOC_RUNG1_OPEN.lower()
    assert "awlstuh" in r1 and "redditch" in r1
    assert r1.rstrip().endswith("?"), "the opening rung must be a question"


def test_rung2_is_a_biased_confirm_not_a_keypad_prompt():
    """Rung 2 must stay a spoken confirm. If the keypad prompt migrates up to
    rung 2, callers get sent to their keypad before voice has had a fair
    second attempt."""
    r2 = conn._LOC_RUNG2_CONFIRM.lower()
    assert "use this clinic" in r2, "rung 2 lost its verbal trigger phrase"
    assert "press 1" not in r2 and "keypad" not in r2


def test_rung3_is_the_keypad_and_maps_1_alcester_2_redditch():
    r3 = conn._LOC_RUNG3_DTMF.lower()
    assert "keypad" in r3
    # 1 must be named before 2, and bound to Alcester.
    one = re.search(r"press 1 for (\w+)", r3)
    two = re.search(r"2 for (\w+)", r3)
    assert one and one.group(1) == "awlstuh", "digit 1 is no longer Alcester"
    assert two and two.group(1) == "redditch", "digit 2 is no longer Redditch"


def test_rung2_biasing_is_parametrised_per_clinic():
    """The booking-ack path may bias Redditch; watchdog and silence always bias
    Alcester. Both must speak the same sentence shape."""
    assert "Redditch clinic" in conn._loc_rung2_confirm("Redditch")
    assert "Awlstuh clinic" in conn._loc_rung2_confirm("Awlstuh")


# ── the keypad handler ──────────────────────────────────────────────────────

class _BareHandler(conn.WebSocketCallHandler):
    """A handler with no __init__ run.

    _handle_dtmf reaches into a lot of live-call state that is irrelevant to
    which clinic a keypress selects — task handles, playout clocks, Twilio
    stream ids. Returning None for anything not explicitly set keeps these
    tests about the location ladder instead of about the connection's full
    attribute surface, and every one of those reads is a falsy guard anyway.
    """

    def __getattr__(self, name):
        return None


def _handler(session):
    import asyncio

    h = _BareHandler.__new__(_BareHandler)
    h.session = session
    h.call_sid = "CA_test"
    h.tts_text_queue = asyncio.Queue()
    h.audio_out_queue = asyncio.Queue()
    # The watchdog is incidental here: _handle_dtmf cancels its timers, stamps
    # arrival time and re-arms questions on it.
    h._silence_handler = MagicMock()
    return h


def _spoken(handler):
    out = []
    while not handler.tts_text_queue.empty():
        out.append(handler.tts_text_queue.get_nowait())
    return " ".join(str(x) for x in out)


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    async def _noop(call_sid, session):
        return None
    monkeypatch.setattr(conn, "save_session", _noop, raising=False)


@pytest.mark.parametrize("digit,expected", [("1", "alcester"), ("2", "redditch")])
async def test_digit_resolves_the_clinic(digit, expected):
    session = {"v3_awaiting_location_dtmf": True, "v3_caller_intent": "booking"}
    h = _handler(session)
    await h._handle_dtmf({"dtmf": {"digit": digit}})

    assert session["selected_location"] == expected
    assert session["v3_location_confirmed"] is True
    assert session["v3_awaiting_location_dtmf"] is False, "keypad stayed armed"
    assert session["v3_location_q_active"] is False, "the question stayed open"


async def test_invalid_digit_reprompts_and_keeps_the_keypad_armed():
    """A caller who fat-fingers 5 must not be stranded with the gate closed and
    no clinic chosen."""
    session = {"v3_awaiting_location_dtmf": True, "v3_caller_intent": "booking"}
    h = _handler(session)
    await h._handle_dtmf({"dtmf": {"digit": "5"}})

    assert "selected_location" not in session, "an invalid key chose a clinic"
    assert session["v3_awaiting_location_dtmf"] is True, "keypad disarmed on a bad key"
    spoken = _spoken(h).lower()
    assert "press 1" in spoken and "2 for redditch" in spoken


async def test_phone_keypad_mode_wins_over_location_keypad():
    """While collecting a phone number every digit belongs to the number. If the
    location branch ran first, a '1' in a phone number would silently rebind the
    clinic mid-booking."""
    session = {
        "v3_phone_dtmf_active": True,
        "v3_awaiting_location_dtmf": True,
        "phone_dtmf_buffer": "",
    }
    h = _handler(session)
    await h._handle_dtmf({"dtmf": {"digit": "1"}})

    assert "selected_location" not in session, (
        "a phone-number digit was consumed as a clinic choice"
    )


async def test_cancel_and_reschedule_continue_their_own_flow():
    """After the clinic is known, cancel/reschedule must go to phone lookup —
    not be dropped into the booking timing question."""
    for intent in ("cancel", "reschedule"):
        session = {"v3_awaiting_location_dtmf": True, "v3_caller_intent": intent}
        h = _handler(session)
        await h._handle_dtmf({"dtmf": {"digit": "1"}})

        assert session.get("v3_awaiting_phone_confirm") is True, (
            f"{intent} did not advance to the phone step after the keypad choice"
        )
        assert "use this number" in _spoken(h).lower()


# ── coverage of every context that asks the question ────────────────────────

def test_question_is_armed_for_booking_cancel_reschedule_and_faq():
    """Four distinct sites arm v3_location_q_active. The ladder keys off that
    flag alone, so losing an arming site silently removes the keypad fallback
    from that whole flow while booking keeps working."""
    src = inspect.getsource(conn)
    arms = src.count('["v3_location_q_active"] = True')
    assert arms >= 3, (
        f"only {arms} site(s) arm the location question — a flow lost its "
        "fallback ladder"
    )
    # cancel/reschedule get their own past-tense wording
    assert "Was your original appointment at" in src


def test_voice_still_resolves_without_touching_the_keypad():
    """The keypad is an escape hatch, never a replacement. Both spoken clinic
    names must still be recognised — proven live on the 2026-08-04 sweep, where
    'book at your redditch clinic' resolved on the first turn."""
    src = inspect.getsource(conn)
    assert "_detect_location_alias_inline" in src, "the spoken-alias fast path is gone"
    assert "inline alias" in src
    for spoken in ("alcester", "redditch"):
        assert spoken in src.lower()
