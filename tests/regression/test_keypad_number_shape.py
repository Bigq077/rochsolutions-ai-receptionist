"""C3 — a keypad buffer that is not a UK mobile is never queued to the model.

Regression for CA3590527b (1 Aug 2026), the upstream half of the defect whose
downstream half is covered by test_booking_phone_matches_confirmed.py.

    caller types  0 7 9 8 7 1 2 4 7                        <- nine digits
    +5.0s         near-complete finalize -> complete = buf  <- accepted raw
                  _commit_dtmf_phone_for_booking refused    <- required 10
                  phone_confirmed never set
                  "079871247" queued as a synthetic transcript
    model         books 07987124700                         <- padded with "00"
                  two SMS reminders scheduled to it

The three finalize paths each carried their own idea of "complete":

    path                 armed at   accepted
    immediate            >= 11      buf[:11]
    idle (3.5s)          >= 10      "0" + buf, unconditionally
    near-complete (5.0s) >=  9      buf, as typed
    _commit_dtmf...      --         >= 10 digits

Any threshold a finalize path accepts and the commit refuses is a number that
reaches the model with no record of it in the session — which is exactly the
shape that lets the model invent the rest. These tests pin the four to one
predicate, and pin the property that actually protects the caller: the digits
are not queued at all.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

import app.media_streams.connection as conn


# ── The predicate ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("07700900456",    "07700900456"),   # already canonical
    ("7700900456",     "07700900456"),   # leading zero omitted — same number
    ("447700900456",   "07700900456"),   # country code dialled in
    ("+44 7700 900456", "07700900456"),  # punctuation and spaces
    ("0770090045",     "0770090045"),    # 10 digits starting 0 — NOT padded
])
def test_normalisation(raw, expected):
    assert conn._normalise_keypad_number(raw) == expected


def test_a_dropped_digit_is_not_padded_into_a_different_number():
    """The idle path used to do `"0" + buf` for any 10-digit buffer. For a
    caller who omitted the leading zero that is a repair; for an 11-digit entry
    that dropped a digit it fabricates. It cannot be both, so it must only be
    the first — and 0770090045 must fail rather than become 00770090045."""
    assert conn._normalise_keypad_number("0770090045") == "0770090045"
    assert conn._is_valid_uk_mobile("0770090045") is False


@pytest.mark.parametrize("good", [
    "07700900456", "7700900456", "447700900456", "+447700900456",
    "07700 900456", "07987124700",
])
def test_valid_uk_mobiles_are_accepted(good):
    assert conn._is_valid_uk_mobile(good) is True


@pytest.mark.parametrize("bad", [
    "079871247",      # the CA3590527b buffer — nine digits
    "0798712470",     # ten, leading zero, one short
    "",
    None,
    "0770",
    "01527123456",    # landline: 11 digits, wrong prefix
    "02012345678",    # London landline
    "447700900",      # country code but short
    "077009004567",   # twelve digits
])
def test_non_mobiles_are_rejected(bad):
    assert conn._is_valid_uk_mobile(bad) is False


def test_landlines_are_rejected_deliberately():
    """Not an oversight: the booking confirmation and both reminders are SMS,
    so a landline on a booking is a patient who is never told about their
    appointment. If the clinic ever takes landlines this test is the thing that
    should be made to fail first."""
    assert conn._is_valid_uk_mobile("01527123456") is False


# ── The commit agrees with the finalize paths ────────────────────────────────

class _Sess(dict):
    pass


class _Harness:
    def __init__(self, session):
        self.session = session

    _commit_dtmf_phone_for_booking = conn.WebSocketCallHandler._commit_dtmf_phone_for_booking


def _booking_session(**over):
    s = _Sess({"v3_caller_intent": "booking", "twilio_from_local": "07502211207"})
    s.update(over)
    return s


def test_the_nine_digit_buffer_is_not_committed():
    s = _booking_session()
    _Harness(s)._commit_dtmf_phone_for_booking("079871247")
    assert s.get("phone_confirmed") is not True
    assert "phone" not in s.get("collected", {})


def test_the_commit_stores_the_normalised_form():
    """A3 (`_reconcile_booking_phone`) compares the model's argument against
    collected["phone"]. If the commit stored "7700900456" while every readback
    said "07700900456", the two halves of the same fix would disagree."""
    s = _booking_session()
    _Harness(s)._commit_dtmf_phone_for_booking("7700900456")
    assert s["collected"]["phone"] == "07700900456"
    assert s["phone_number"] == "07700900456"


def test_commit_and_finalize_share_one_predicate():
    """The defect was a threshold disagreement, so assert there is nothing left
    to disagree about: the commit must not carry its own length test."""
    src = inspect.getsource(conn.WebSocketCallHandler._commit_dtmf_phone_for_booking)
    assert "_is_valid_uk_mobile" in src
    # Comments stripped: the docstring and the change note both quote the old
    # rule on purpose, and matching those would make this pass for the wrong
    # reason (or fail for one).
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#")
    )
    assert "len(_digits)" not in code, (
        "the commit has grown a private length rule again — that is the "
        "CA3590527b shape"
    )


# ── The deterministic-flow exclusion must cover all four states ──────────────

@pytest.mark.parametrize("state", [
    "COLLECT_PHONE", "COLLECT_PHONE_RETURNING",
    "COLLECT_PHONE_RESCHEDULE", "RETURNING_PLAN_COLLECT_PHONE",
])
def test_flowengine_phone_states_keep_ownership_of_the_flag(state):
    """Each of these has its own readback and CONFIRM_PHONE gate, plus ~10 sites
    that reset phone_confirmed when the caller rejects the readback. Setting it
    early from here would make those resets fight a flag they did not set.

    COLLECT_PHONE_RESCHEDULE was the one missing from the list, and the
    v3_caller_intent guard does not cover it: that key is only set on the
    v3/LLM path, so on a deterministic reschedule it is unset and the
    "reschedule" branch never fires. Hence intent is left unset here."""
    s = _booking_session(state=state)
    s.pop("v3_caller_intent")
    _Harness(s)._commit_dtmf_phone_for_booking("07700900456")
    assert s.get("phone_confirmed") is not True, state
    assert s.get("phone_entered_by_keypad") is not True, state


def test_the_v3_path_is_still_committed_without_an_intent():
    """The exclusion must be about the state, not about intent being absent —
    otherwise it would swallow the v3 path this whole fix exists for."""
    s = _booking_session(state="GREETING")
    s.pop("v3_caller_intent")
    _Harness(s)._commit_dtmf_phone_for_booking("07700900456")
    assert s["phone_confirmed"] is True


# ── The property that protects the caller ────────────────────────────────────

class _Queue:
    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)


class _ReaskHarness:
    """Enough of the handler for the re-ask to run. Nothing here reaches TTS,
    Redis or Twilio — the queues are lists."""

    def __init__(self, session):
        self.session = session
        self.call_sid = "CAtest"
        self.tts_text_queue = _Queue()
        self.transcript_queue = _Queue()

    _reask_invalid_keypad_number = conn.WebSocketCallHandler._reask_invalid_keypad_number


@pytest.fixture
def no_redis(monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(conn, "save_session", _noop)


def test_the_digits_are_never_queued_to_the_model(no_redis):
    """The whole fix. The model padded a fragment it could see; a fragment it
    cannot see cannot be padded."""
    h = _ReaskHarness(_booking_session())
    asyncio.run(h._reask_invalid_keypad_number("079871247"))
    assert h.transcript_queue.items == []
    assert "079871247" not in " ".join(h.tts_text_queue.items)


def test_the_buffer_is_cleared_so_a_retype_starts_fresh(no_redis):
    h = _ReaskHarness(_booking_session(phone_dtmf_buffer="079871247"))
    asyncio.run(h._reask_invalid_keypad_number("079871247"))
    assert h.session["phone_dtmf_buffer"] == ""


def test_the_first_reask_keeps_the_keypad_armed(no_redis):
    """They are mid-entry with their phone at their ear; disarming would mean a
    retype went nowhere."""
    h = _ReaskHarness(_booking_session())
    asyncio.run(h._reask_invalid_keypad_number("079871247"))
    assert h.session["v3_phone_dtmf_active"] is True
    assert h.session["phone_awaiting_dtmf"] is True


def test_the_reask_does_not_tell_them_to_do_what_they_just_did(no_redis):
    """susie_system_prompt marks "Could you type that number on your keypad?"
    as WRONG on this path, and "Sorry, I didn't catch that" is G2-banned."""
    h = _ReaskHarness(_booking_session())
    asyncio.run(h._reask_invalid_keypad_number("079871247"))
    said = h.tts_text_queue.items[0].lower()
    assert "didn't catch that" not in said
    assert "double-check" in said


def test_the_reask_ladder_terminates(no_redis):
    """An unbounded "that's not right, try again" is a worse failure than the
    bug it replaces. By attempt 3 the keypad is disarmed and the caller is asked
    to speak, so there is no fourth identical prompt."""
    h = _ReaskHarness(_booking_session())
    for _ in range(3):
        asyncio.run(h._reask_invalid_keypad_number("079871247"))
    assert h.session["phone_dtmf_reask_count"] == 3
    assert h.session["v3_phone_dtmf_active"] is False
    assert h.session["phone_awaiting_dtmf"] is False
    assert "read it out" in h.tts_text_queue.items[-1].lower()


def test_the_second_attempt_offers_the_caller_id_as_an_escape(no_redis):
    """Reuses `_is_use_this_number`, which is already wired at three sites and
    stores the caller ID itself — so the escape needs no new branch."""
    h = _ReaskHarness(_booking_session())
    asyncio.run(h._reask_invalid_keypad_number("079871247"))
    asyncio.run(h._reask_invalid_keypad_number("079871247"))
    said = h.tts_text_queue.items[1]
    assert "use this number" in said.lower()
    assert conn._is_use_this_number("use this number") is True


def test_no_caller_id_skips_straight_to_speech(no_redis):
    """Offering "the number you're calling from" when there isn't one would be
    a dead end — a withheld caller ID must not strand the caller."""
    h = _ReaskHarness(_booking_session(twilio_from_local="", twilio_from=""))
    asyncio.run(h._reask_invalid_keypad_number("079871247"))
    asyncio.run(h._reask_invalid_keypad_number("079871247"))
    assert "read it out" in h.tts_text_queue.items[1].lower()


def test_a_good_entry_resets_the_ladder(no_redis):
    """Otherwise a caller who corrects themselves, then later has to change the
    number, lands on the terminal rung for their first mistake."""
    src = inspect.getsource(conn.WebSocketCallHandler)
    assert src.count('self.session["phone_dtmf_reask_count"] = 0') == 3, (
        "every finalize path must clear the re-ask ladder on success"
    )


# ── All three paths route through the predicate ──────────────────────────────

def test_every_finalize_path_validates_before_queueing():
    """The structural guarantee. A fourth finalize path, or one that loses its
    check, reopens CA3590527b silently — this is the test that would catch it."""
    src = inspect.getsource(conn.WebSocketCallHandler)
    assert src.count("if not _is_valid_uk_mobile(complete):") == 3, (
        "a DTMF finalize path can queue a number it has not validated"
    )
    assert src.count("_reask_invalid_keypad_number(buf)") == 3


def test_the_paths_still_all_commit():
    """C1's invariant must survive C3's edits — the number is committed on the
    accept path, not just validated."""
    src = inspect.getsource(conn.WebSocketCallHandler)
    assert src.count("_commit_dtmf_phone_for_booking(complete)") == 3
    assert src.count("_inject_phone_context_for_lookup(complete)") == 3


def test_the_idle_path_does_not_double_reask():
    """Both the 3.5 s idle task and the 5.0 s near-complete task are armed for a
    10-digit buffer. The idle one fires first and clears the buffer, so the
    near-complete task's `buf != expected_buf` guard makes it a no-op — but only
    because the re-ask clears the buffer. Pin that."""
    src = inspect.getsource(conn.WebSocketCallHandler._reask_invalid_keypad_number)
    assert 'self.session["phone_dtmf_buffer"] = ""' in src
