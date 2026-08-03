# tests/regression/test_b46_readback_waits_for_confirmed_phone.py
"""
B-46 — the booking read-back fired before any slot had been offered.

The post-collect guard in llm_stream blocks check_availability once the caller's
details are settled, and forces the booking read-back instead. It tested:

    _col.get("phone") and (_col.get("name") or _col.get("full_name"))

`collected["phone"]` is pre-loaded from the Twilio caller-ID at CONNECT —
connection.py, verbatim: "Populate collected.phone from Twilio caller-ID so Susie
never asks for it." It is set unconditionally on every inbound call that carries
a number, before the caller has said a word.

So the first arm was always true and the condition collapsed to "a name has been
collected". Under name-first the first name is stored at turn 1, which meant the
guard fired BEFORE any slot existed: check_availability was blocked, and the
model was told to read back a booking for a slot nobody had offered — skipping
the surname and phone-confirmation steps entirely.

THE FIX is main's: gate on session["phone_confirmed"], which is set only where
the caller actively confirms a number (the keypad commit and the two
verbal-confirm sites in connection.py). book_appointment's A1 gate already
requires that flag, so a booking cannot complete without it — "the caller has
confirmed their number" and "the slot is already agreed" are the same moment,
and it is the moment this guard was always meant to fire at.

WHAT THIS FILE PROTECTS IN BOTH DIRECTIONS

The premature read-back is only half the risk. The guard exists because the
model spuriously re-runs availability after collection and dead-ends (BUG-14),
and releasing it too far brings that back. So the invariants below matter as
much as the fails-before cases, and most of them pass on the parent commit —
which is what a preserved invariant should do.

Two protections here are latency-eval-only and main does not have them: the
_caller_requests_new_day_or_time escape (Bug B, CAc6b971ad — the caller asked
for Wednesday seven times behind this guard and hung up unbooked) and the BUG-14
name/location injection. Both must survive.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams import llm_stream
from app.media_streams.llm_stream import _post_collect_readback_due


CALLER_ID = "+447502211207"


def _msgs(text: str = ""):
    return [{"role": "user", "content": text}] if text else []


def _session_at_connect(**overrides):
    """The session exactly as connection.py leaves it after the Twilio start
    event: collected["phone"] pre-filled from caller-ID, nothing confirmed."""
    session = {
        "collected": {"phone": CALLER_ID},
        "phone_from_twilio": True,
    }
    session.update(overrides)
    return session


# ── FAILS BEFORE THE FIX ────────────────────────────────────────────────────
# Each of these is a turn where the caller has given a name and has NOT
# confirmed a number. The old condition fired on every one of them.

def test_the_live_defect_first_name_at_turn_one_must_not_force_a_readback():
    """The exact reported shape: name-first, caller-ID pre-filled, no slot yet."""
    session = _session_at_connect(collected={"phone": CALLER_ID, "name": "Quentin"})
    assert _post_collect_readback_due("check_availability", session, _msgs()) is False, (
        "the read-back was forced before any slot was offered — caller-ID "
        "pre-fills collected['phone'], so this guard collapsed to 'a name has "
        "been collected'"
    )


@pytest.mark.parametrize("collected_extra", [
    {"name": "Quentin"},
    {"full_name": "Quentin Roch"},
    {"name": "Quentin", "full_name": "Quentin Roch"},
])
def test_no_name_shape_releases_the_guard_without_a_confirmed_phone(collected_extra):
    session = _session_at_connect(collected={"phone": CALLER_ID, **collected_extra})
    assert _post_collect_readback_due("check_availability", session, _msgs()) is False


@pytest.mark.parametrize("utterance", [
    "",
    "i'd like to book an appointment",
    "my ankle's been sore",
    "quentin",
    "quentin roch",
    "tuesday would be good",
])
def test_the_guard_stays_down_through_the_whole_pre_phone_conversation(utterance):
    """Nothing the caller says before confirming a number should arm it."""
    session = _session_at_connect(collected={"phone": CALLER_ID, "name": "Quentin"})
    assert _post_collect_readback_due(
        "check_availability", session, _msgs(utterance)
    ) is False


def test_an_explicitly_false_phone_confirmed_is_not_a_confirmation():
    session = _session_at_connect(
        collected={"phone": CALLER_ID, "name": "Quentin"},
        phone_confirmed=False,
    )
    assert _post_collect_readback_due("check_availability", session, _msgs()) is False


# ── INVARIANTS — these pass on the parent and must keep passing ─────────────
# The guard's real purpose: once the caller HAS confirmed a number, stop
# re-running availability and read the booking back (BUG-14).

def test_a_confirmed_phone_plus_a_name_still_arms_the_guard():
    session = _session_at_connect(
        collected={"phone": CALLER_ID, "name": "Quentin"},
        phone_confirmed=True,
    )
    assert _post_collect_readback_due("check_availability", session, _msgs()) is True


def test_a_keypad_typed_number_arms_it_too():
    """The keypad commit sets phone_confirmed alongside a normalised number."""
    session = {
        "collected": {"phone": "07502211207", "full_name": "Quentin Roch"},
        "phone_confirmed": True,
        "phone_entered_by_keypad": True,
    }
    assert _post_collect_readback_due("check_availability", session, _msgs()) is True


def test_a_confirmed_phone_with_no_name_does_not_arm_it():
    session = _session_at_connect(phone_confirmed=True)
    assert _post_collect_readback_due("check_availability", session, _msgs()) is False


@pytest.mark.parametrize("tool_name", [
    "book_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "lookup_patient",
    "get_clinic_info",
])
def test_only_check_availability_is_guarded(tool_name):
    """book_appointment must never be blocked by this — it is the write the
    read-back exists to reach."""
    session = _session_at_connect(
        collected={"phone": CALLER_ID, "name": "Quentin"},
        phone_confirmed=True,
    )
    assert _post_collect_readback_due(tool_name, session, _msgs()) is False


# ── Bug B escape — latency-eval only, main does not have it ─────────────────
# CAc6b971ad: the caller asked for Wednesday seven times behind this guard,
# was re-read Tuesday every time, and hung up unbooked.

@pytest.mark.parametrize("utterance", [
    "you have a different slot open for example on wednesday",
    "anything on wednesday by any chance",
    "i asked for wednesday",
    "can i do thursday instead",
    "what about tomorrow",
    "anything later please",
    "do you have anything in the morning",
])
def test_a_caller_changing_the_slot_releases_the_guard(utterance):
    session = _session_at_connect(
        collected={"phone": CALLER_ID, "name": "Quentin"},
        phone_confirmed=True,
    )
    assert _post_collect_readback_due(
        "check_availability", session, _msgs(utterance)
    ) is False, (
        "Bug B: the caller is still choosing when to come in, so the guard must "
        "stand down or they are re-read the old day until they hang up"
    )


@pytest.mark.parametrize("utterance", [
    "yes it is",
    "yes please",
    "that's right",
    "tom green",
    "no it's 07596 897492",
    "0 7 5 9 6 8 9 7 4 9 2",
    "yes that's the one",
])
def test_ordinary_collection_turns_do_not_release_the_guard(utterance):
    """BUG-14: releasing on these brings back the spurious re-search. Note the
    digit cases — _caller_wants_new_slot WOULD fire on them, which is why the
    guard uses the purpose-built predicate instead."""
    session = _session_at_connect(
        collected={"phone": CALLER_ID, "name": "Quentin"},
        phone_confirmed=True,
    )
    assert _post_collect_readback_due(
        "check_availability", session, _msgs(utterance)
    ) is True


# ── the fix must not be quietly reverted ───────────────────────────────────

def test_the_predicate_does_not_read_collected_phone():
    """The whole defect was one dictionary key. Source-pinned so a later edit
    cannot reintroduce it while the behavioural tests still pass."""
    src = inspect.getsource(_post_collect_readback_due)
    body = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    )
    # The docstring names collected["phone"] on purpose; strip it before looking.
    doc = _post_collect_readback_due.__doc__ or ""
    body = body.replace(doc, "")
    assert '_col.get("phone")' not in body and "_col.get('phone')" not in body, (
        "B-46: collected['phone'] is pre-filled from the caller-ID at connect "
        "and must never gate this gate — use session['phone_confirmed']"
    )
    assert "phone_confirmed" in body


def test_the_call_site_reads_through_the_predicate():
    """A fallback applied to the predicate and forgotten at the call site is
    the B-38 failure mode. Assert the inline condition is gone."""
    src = inspect.getsource(llm_stream)
    assert "_post_collect_readback_due(tool_name, session, messages)" in src
    assert 'and _col.get("phone")' not in src, (
        "the inline post-collect condition is back — it must route through "
        "_post_collect_readback_due"
    )


def test_empty_and_missing_session_shapes_do_not_raise():
    for session in ({}, {"collected": None}, {"collected": {}}):
        assert _post_collect_readback_due("check_availability", session, None) is False
