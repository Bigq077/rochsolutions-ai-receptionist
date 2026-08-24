"""B-85 — pressing the slot number did nothing, on every clinic.

CA3db609f7ecb78e3127b11a1459bf3b34 (24 Aug 2026, jv_v1):

    16:51:37.986  slot map extracted ... DTMF standby: {'1': 'quarter to six
                  in the evening', '2': 'half past six ...', '3': ...}
    16:52:04.731  DTMF raw digit='1' v3_phone_dtmf_active=False
    16:52:04.746  [ms_lost] reason=dtmf_digit_discarded text='1' call_total=1

Susie read out three numbered options, the map was armed and logged as "DTMF
standby", the caller pressed 1 — and the digit was discarded. It was the only
lost input on the call.

`v3_slot_dtmf_active` had exactly ONE writer that set it True, and it required

    "keypad" in last_bot_prompt

alongside `_slot_stage_active`. Susie only says "keypad" when asking for a
phone NUMBER, and during phone collection slot_map_stage is NONE — so
_slot_stage_active is False. The two halves could never hold at once. Every
other reference to the flag in the codebase is a pop.

So slot selection by keypad was dead everywhere, and B-80's superseded-map
guard sat behind a gate that never opened.

The numbered readout is the invitation. `_slot_stage_active` is the guard.
"""

import asyncio
import time
import types

import pytest

from app.media_streams.connection import SlotMapStage


# The real map and readout from the call.
LIVE_MAP = {
    "1": "quarter to six in the evening",
    "2": "half past six in the evening",
    "3": "quarter past seven in the evening",
}
LIVE_PROMPT = (
    "Tuesday 1st September — Number 1, quarter to six in the evening. "
    "Number 2, half past six in the evening. Number 3, quarter past seven "
    "in the evening. Any of those work?"
)


def _conn(session, stage=SlotMapStage.TIME_SELECTION):
    """A MediaStreamConnection stub carrying only what _handle_dtmf touches."""
    from app.media_streams.connection import WebSocketCallHandler

    c = object.__new__(WebSocketCallHandler)
    c.session = session
    c.slot_map_stage = stage
    c.transcript_queue = asyncio.Queue()
    c._silence_handler = None
    c.booking_flow_active = True
    c.lost_utterances = []

    def _note(reason, text=""):
        c.lost_utterances.append((reason, text))

    c._note_utterance_lost = _note
    # Only reached by the phone-collection path (the last test); the slot
    # branch never touches these.
    c._silence_handler = types.SimpleNamespace(
        last_audio_received_at=0.0,
        set_state=lambda *a, **k: None,
        on_question_asked=lambda *a, **k: None,
        _restart_timer=lambda *a, **k: None,
        _rearm_no_input_watchdog=lambda *a, **k: None,
        _no_input_task=None,
        _timer_task=None,
    )
    c._tts_task = None
    c._playback_task = None
    c.websocket = None
    c.stream_sid = "MZtest"
    return c


def _session():
    return {
        "v3_dtmf_slot_map": dict(LIVE_MAP),
        "v3_awaiting_slot_selection": True,
        "last_bot_prompt": LIVE_PROMPT,   # note: contains no "keypad"
        "clinic_id": "jv_v1",
        "state": "GREETING",
    }


async def _press(conn, digit):
    await conn._handle_dtmf({"dtmf": {"digit": digit}})


async def test_pressing_the_slot_number_selects_that_slot():
    """The defect: this used to be discarded."""
    session = _session()
    conn = _conn(session)

    await _press(conn, "1")

    assert not conn.transcript_queue.empty(), (
        "the keypress produced nothing — B-85 is back"
    )
    _ts, label = conn.transcript_queue.get_nowait()
    assert label == "quarter to six in the evening", label
    assert conn.lost_utterances == [], conn.lost_utterances


@pytest.mark.parametrize("digit,expected", list(
    (d, LIVE_MAP[d]) for d in ("1", "2", "3")
))
async def test_every_offered_number_resolves(digit, expected):
    conn = _conn(_session())
    await _press(conn, digit)

    _ts, label = conn.transcript_queue.get_nowait()
    assert label == expected


async def test_the_word_keypad_is_not_required():
    """Pinning the actual rule.

    Susie never says "keypad" while reading slots out — requiring it is what
    made the arm unsatisfiable. If someone reintroduces that condition this
    fails.
    """
    session = _session()
    assert "keypad" not in session["last_bot_prompt"].lower()
    conn = _conn(session)

    await _press(conn, "2")

    assert not conn.transcript_queue.empty()


async def test_a_digit_outside_the_map_is_not_silently_dropped():
    """B-80's guard is now actually reachable.

    A press against a 1-3 list must be counted and must not resolve to
    anything — but it must be counted under its OWN reason, not the generic
    discard, so the two remain countable apart.
    """
    conn = _conn(_session())

    await _press(conn, "7")

    assert conn.transcript_queue.empty()
    assert conn.lost_utterances, "an unmapped digit vanished without a count"
    reason = conn.lost_utterances[0][0]
    assert reason == "dtmf_slot_no_mapping", reason


async def test_a_superseded_map_still_refuses_to_resolve():
    """B-80 must survive: a follow-up has moved the offer on.

    Resolving against the old map books a time the caller heard earlier and is
    no longer being offered — a silent wrong-slot booking, worse than the
    keypress doing nothing.
    """
    session = _session()
    session["v3_slot_map_superseded"] = True
    conn = _conn(session)

    await _press(conn, "1")

    assert conn.transcript_queue.empty()
    assert conn.lost_utterances[0][0] == "dtmf_slot_map_superseded"


async def test_not_armed_when_the_stage_has_moved_past_slot_selection():
    """During name/phone collection a digit is NOT a slot choice.

    This is the guard that makes the "keypad" word redundant — it must keep
    doing its job on its own.
    """
    session = _session()
    conn = _conn(session, stage=SlotMapStage.NONE)

    await _press(conn, "1")

    assert conn.transcript_queue.empty(), (
        "a digit during name/phone collection was read as a slot selection"
    )


async def test_phone_collection_digits_are_left_alone():
    """v3_phone_dtmf_active short-circuits before the slot branch."""
    session = _session()
    session["v3_phone_dtmf_active"] = True
    conn = _conn(session)

    # The whole slot block sits inside `if not v3_phone_dtmf_active`, so the
    # digit should fall straight through to phone accumulation. That machinery
    # is not stubbed and is not what this test is about — what matters is that
    # NOTHING was injected as a slot choice on the way past.
    try:
        await _press(conn, "1")
    except AttributeError:
        pass

    assert conn.transcript_queue.empty(), (
        "a phone digit was swallowed as a slot selection"
    )
