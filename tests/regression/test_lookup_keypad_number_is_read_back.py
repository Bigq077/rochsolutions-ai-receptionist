"""U-03 reversed — a reschedule/cancel lookup number is read back too.

Owner decision, 3 Aug 2026, after the reschedule verification call
`CA3f1d124905854919a9b0f3cc554ff80f`.

The old policy excluded cancel/reschedule from the C2 keypad read-back because
the typed number is a *search key*, not a contact field: get it wrong and you
lose a search, not a booking. The reversal reasoning is that the caller cannot
tell those apart — a mistyped key surfaces as "I can't find your appointment",
which sounds like the clinic losing their booking, and the one party who could
catch a digit Twilio mangled never hears the number.

What must hold, and is asserted below:

1. The lookup path reads the number back, in the *same* words as booking.
2. It does **not** acquire the booking commit — `phone_confirmed`,
   `collected["phone"]` and `phone_entered_by_keypad` stay untouched, so a
   lookup key can never satisfy book_appointment's A1 gate.
3. Every way a caller says yes — "yes", "go for it", "that's the number", the
   verbatim "uh go go for it" from the 3 Aug call — reads as a confirmation
   and not a rejection, because the engine treats "not a rejection" as consent.

   **Coverage limit, stated rather than implied:** the step that then queues
   the digits for `lookup_patient` lives inline in `handle_transcript`'s loop
   and is not reachable without standing up the whole connection, so it is
   *not* asserted here. What is asserted is the state it depends on —
   `v3_keypad_readback_is_lookup` set and the digits stashed in
   `v3_keypad_readback_phone`. If that branch is ever deleted the lookup
   silently never runs, and these tests would still pass. That is the one
   failure mode this file cannot catch; it needs a dial.
4. On a rejection the number is torn down, the keypad is re-armed with the
   mandated wording, and the shared re-ask ladder counts it.
5. The deterministic FlowEngine phone states are still excluded — they have
   their own CONFIRM_PHONE readback and would otherwise ask twice.
"""

import time

import pytest

from app.media_streams import connection as conn
from app.media_streams.connection import _is_phone_readback_rejection

PHONE = "07700900456"
BOOKING_READBACK = f"Thanks — I've got {PHONE}. Is that correct?"


class _Conn:
    """The three methods under test, bound to a bare session + queues.

    Deliberately not a full WebSocketCallHandler: this pins the phone
    read-back contract, and building a real connection would drag in the STT,
    TTS and LLM streams that have nothing to do with it.
    """

    def __init__(self, session):
        self.session = session
        self.call_sid = "CAtest"
        self.tts_text_queue = _Q()
        self.transcript_queue = _Q()

    _readback_keypad_number = conn.WebSocketCallHandler._readback_keypad_number
    _reject_keypad_number = conn.WebSocketCallHandler._reject_keypad_number
    _inject_phone_context_for_lookup = (
        conn.WebSocketCallHandler._inject_phone_context_for_lookup
    )
    _commit_dtmf_phone_for_booking = (
        conn.WebSocketCallHandler._commit_dtmf_phone_for_booking
    )


class _Q:
    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)


@pytest.fixture(autouse=True)
def _no_session_writes(monkeypatch):
    async def _save(call_sid, session):
        return None

    monkeypatch.setattr(conn, "save_session", _save)


def _reschedule_session():
    return {"v3_caller_intent": "reschedule", "conversation_history": []}


# ── 1. it reads back, in the booking wording ────────────────────────────────

async def test_lookup_number_is_read_back():
    c = _Conn(_reschedule_session())
    c._commit_dtmf_phone_for_booking(PHONE)
    c._inject_phone_context_for_lookup(PHONE)

    assert await c._readback_keypad_number(PHONE) is True, (
        "a reschedule lookup number was not read back — U-03 was reversed on "
        "3 Aug 2026 and this is the behaviour that reversal buys"
    )
    assert c.tts_text_queue.items == [BOOKING_READBACK], (
        "the lookup read-back must be word-identical to the booking one; the "
        "caller should not be able to tell which path they are on"
    )


async def test_readback_takes_the_turn_so_digits_are_not_also_queued():
    """Returning True is the contract the three DTMF finalize sites rely on:
    `if not await self._readback_keypad_number(...): queue the digits`."""
    c = _Conn(_reschedule_session())
    c._inject_phone_context_for_lookup(PHONE)
    await c._readback_keypad_number(PHONE)

    assert c.transcript_queue.items == [], (
        "digits were queued alongside the read-back — the model would answer "
        "over the top of it"
    )
    assert c.session["v3_keypad_readback_pending"] is True
    assert c.session["v3_keypad_readback_is_lookup"] is True


# ── 2. it must NOT acquire the booking commit ───────────────────────────────

async def test_lookup_readback_does_not_confirm_a_booking_number():
    """The load-bearing separation. A search key that satisfied A1 would let
    book_appointment write against a number nobody offered as contact detail."""
    c = _Conn(_reschedule_session())
    c._commit_dtmf_phone_for_booking(PHONE)
    c._inject_phone_context_for_lookup(PHONE)
    await c._readback_keypad_number(PHONE)

    assert c.session.get("phone_confirmed") is not True
    assert c.session.get("phone_entered_by_keypad") is not True
    assert (c.session.get("collected") or {}).get("phone") is None


@pytest.mark.parametrize("intent", ["cancel", "reschedule"])
async def test_both_lookup_intents_arm_it(intent):
    c = _Conn({"v3_caller_intent": intent, "conversation_history": []})
    c._inject_phone_context_for_lookup(PHONE)
    assert await c._readback_keypad_number(PHONE) is True


async def test_booking_intent_is_unaffected():
    """The booking path must still arm off its own flag alone."""
    c = _Conn({"v3_caller_intent": "booking", "conversation_history": []})
    c._commit_dtmf_phone_for_booking(PHONE)
    c._inject_phone_context_for_lookup(PHONE)

    assert c.session.get("phone_entered_by_keypad") is True
    assert c.session.get("phone_entered_by_keypad_for_lookup") is not True
    assert await c._readback_keypad_number(PHONE) is True
    assert c.session["v3_keypad_readback_is_lookup"] is False


@pytest.mark.parametrize(
    "state",
    [
        "COLLECT_PHONE",
        "COLLECT_PHONE_RETURNING",
        "COLLECT_PHONE_RESCHEDULE",
        "RETURNING_PLAN_COLLECT_PHONE",
    ],
)
async def test_deterministic_flowengine_states_are_still_excluded(state):
    """These have their own CONFIRM_PHONE readback. Arming here would read the
    number back twice — the exact double-ask C3 was opened to remove."""
    c = _Conn({"state": state, "conversation_history": []})
    c._commit_dtmf_phone_for_booking(PHONE)
    c._inject_phone_context_for_lookup(PHONE)

    assert await c._readback_keypad_number(PHONE) is False


# ── 3. confirmation wording — every way a caller says yes ───────────────────

@pytest.mark.parametrize(
    "utterance",
    [
        "yes",
        "yeah",
        "yep that's right",
        "go for it",
        "that's the number",
        "that's it",
        "correct",
        "uh go go for it",          # the exact 3 Aug reschedule call transcript
        "yes that's the one",
        "perfect",
    ],
)
def test_affirmations_are_not_read_as_rejections(utterance):
    """The engine treats "not a rejection" as consent, so this is the whole
    affirmation contract: every one of these must fall through to the confirm
    branch, not tear the number down."""
    assert _is_phone_readback_rejection(utterance) is False, (
        f"{utterance!r} was read as a rejection — the caller confirming their "
        f"number would have it wiped and be asked to type it again"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        "no",
        "no that's wrong",
        "nope",
        "no it's a different one",
        "that's not right",
    ],
)
def test_rejections_are_caught(utterance):
    assert _is_phone_readback_rejection(utterance) is True


def test_a_long_turn_merely_containing_no_is_not_a_rejection():
    """The six-word cap. Known limit, kept explicit: CA6e1024db (2 Aug) is an
    11-word rejection this misses, and the keypad-arming net is the backstop."""
    assert _is_phone_readback_rejection(
        "no rush, but can you also tell me about parking?"
    ) is False


# ── 4. rejection tears everything down and re-arms ──────────────────────────

async def test_rejection_clears_the_lookup_flag_and_the_stashed_number():
    c = _Conn(_reschedule_session())
    c._inject_phone_context_for_lookup(PHONE)
    await c._readback_keypad_number(PHONE)
    c.tts_text_queue.items.clear()

    await c._reject_keypad_number()

    assert c.session.get("phone_entered_by_keypad_for_lookup") is False, (
        "a rejected lookup number left its flag armed — the next read-back "
        "would fire against a number the caller already refused"
    )
    assert "v3_keypad_readback_phone" not in c.session
    assert "v3_keypad_readback_is_lookup" not in c.session


async def test_rejection_rearms_the_keypad_with_the_mandated_wording():
    c = _Conn(_reschedule_session())
    c._inject_phone_context_for_lookup(PHONE)
    await c._readback_keypad_number(PHONE)
    c.tts_text_queue.items.clear()

    await c._reject_keypad_number()

    assert c.session["v3_phone_dtmf_active"] is True
    assert c.session["phone_awaiting_dtmf"] is True
    spoken = c.tts_text_queue.items[0]
    # Spec M's safety net keys off these words; a paraphrase that drops them
    # leaves the caller typing into a closed keypad (CA6e1024db).
    assert "keypad" in spoken.lower()
    assert "star key" in spoken.lower()
    assert conn._is_keypad_arming_line(spoken) is True


async def test_rejection_counts_toward_the_shared_reask_ladder():
    """Not a fresh ladder — a rejected read-back is a failed entry like any
    other, so it cannot loop forever (U-02's bound)."""
    c = _Conn(_reschedule_session())
    c.session["phone_dtmf_reask_count"] = 1
    c._inject_phone_context_for_lookup(PHONE)
    await c._readback_keypad_number(PHONE)

    await c._reject_keypad_number()

    assert c.session["phone_dtmf_reask_count"] == 2
