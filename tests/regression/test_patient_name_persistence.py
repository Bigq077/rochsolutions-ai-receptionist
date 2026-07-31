# tests/regression/test_patient_name_persistence.py
"""
Patient-name persistence — the phase gate, 2026-07-31 (CA8f9c5578).

The recurring failure in this area has one shape: the name IS extracted
correctly, and then never stored, so a later gate refuses a booking that was
already right. On CA8f9c5578 the caller said "uh sarah jenkins" in one breath
and book_appointment was refused with patient_name=None while the tool
arguments carried "Sarah Jenkins" — four extra turns, the surname given twice
and the booking authorised twice.

Root cause: _v3_try_persist_name's phase gate required last_bot to contain a
name-REQUEST phrase (or post_slot_pending). But the readback and the request
never co-occur in one response — by the time Susie says "Thanks Sarah — I've
got you on 07502…" she has already moved on to the phone question. The gate
therefore rejected precisely the turn that proved a name was given.

These tests pin both halves: the readback now persists on its own, and the
false positives that the gate used to mask stay rejected.
"""
import pytest

from app.media_streams.connection import (
    _v3_try_persist_name,
    _V3_NAME_CONFIRM_PATTERNS,
    _V3_NAME_CONFIRM_PATTERNS_ANCHORED,
    _V3_NAME_CONFIRM_PATTERNS_BARE,
)


# ---------------------------------------------------------------------------
# The reported defect
# ---------------------------------------------------------------------------

def test_full_name_in_one_utterance_persists_without_a_phase_signal():
    """CA8f9c5578: the exact turn that was lost live."""
    session = {}
    stored = _v3_try_persist_name(
        session,
        "Thanks Sarah — I've got you on 07502, 211, 207 — is that the best "
        "number to reach you on?",
        post_slot_pending=False,          # as it was on the live call
        caller_utterance="uh sarah jenkins",
    )
    assert stored is True
    assert session["patient_name"] == "Sarah Jenkins"
    assert session["surname_captured"] is True
    # collected[] is the path summaries and Sheets read — both must agree.
    assert session["collected"]["name"] == "Sarah Jenkins"


def test_persisted_full_name_satisfies_the_book_appointment_surname_gate():
    """The gate at llm_stream.py blocks on 'no space in patient_name'.

    Pinning the contract between the two here, since the whole cost of this
    defect was paid at that gate rather than at capture.
    """
    session = {}
    _v3_try_persist_name(
        session,
        "Thanks Sarah — I've got you on 07502 — is that the best number?",
        post_slot_pending=False,
        caller_utterance="uh sarah jenkins",
    )
    assert " " in (session.get("patient_name") or "").strip()
    assert session.get("surname_captured") is True


def test_first_name_only_still_arms_the_surname_backfill():
    """No surname in the utterance => first name locked, awaiting the surname."""
    session = {}
    assert _v3_try_persist_name(
        session,
        "Thanks Sarah — is that the best number?",
        post_slot_pending=False,
        caller_utterance="sarah",
    ) is True
    assert session["patient_name"] == "Sarah"
    assert session.get("surname_captured") is not True
    assert session.get("v3_awaiting_surname") is True


# ---------------------------------------------------------------------------
# False positives the phase gate used to mask
# ---------------------------------------------------------------------------
# Opening the gate makes the anchored patterns reachable on turns they never
# saw before. "Thanks for calling …" captured the patient as 'For' — latent
# before this change, reachable after it, so it is guarded and pinned.

@pytest.mark.parametrize("last_bot", [
    "Thanks for calling Joint Venture Physiotherapy — how can I help?",
    "Thanks — I've got two options for you.",
    "Thanks so much — one moment.",
    "Thanks again, all booked.",
    "Right — what's the appointment for?",
    "Right, afternoons — I've got a few slots.",
    "So that's Wednesday the 5th of August at quarter past six — shall I book?",
    "Of course — let me check that for you.",
    "Just to confirm — that's Wednesday, quarter past six?",
])
@pytest.mark.parametrize("post_slot_pending", [False, True])
def test_non_name_bot_lines_never_store_a_name(last_bot, post_slot_pending):
    session = {}
    assert _v3_try_persist_name(
        session, last_bot,
        post_slot_pending=post_slot_pending,
        caller_utterance="anytime next week",
    ) is False
    assert session.get("patient_name") is None


def test_bare_title_case_pattern_stays_behind_the_phase_gate():
    """"Tuesday, that works" must not become a name when no phase signal says
    we are collecting one. Only the ANCHORED patterns were unGated."""
    session = {}
    assert _v3_try_persist_name(
        session, "Marcus, our physio, has availability then — shall I check?",
        post_slot_pending=False,
        caller_utterance="yes please",
    ) is False
    assert session.get("patient_name") is None


# ---------------------------------------------------------------------------
# Historical regressions in this area — must not be reopened
# ---------------------------------------------------------------------------

def test_late_surname_still_backfills_onto_a_locked_first_name():
    """2026-07-07: "Quentin" locked, "surname is Rook" was dropped."""
    session = {
        "patient_name": "Quentin",
        "collected": {"name": "Quentin"},
        "v3_awaiting_surname": True,
    }
    assert _v3_try_persist_name(
        session, "And your surname?",
        post_slot_pending=False,
        caller_utterance="my surname is rook",
    ) is True
    assert session["patient_name"] == "Quentin Rook"
    assert session["surname_captured"] is True


def test_slot_answer_is_not_eaten_as_a_surname():
    """2026-07-31 (CA6dce36c8): "Sara Six" — the answer to the TIME question
    was back-filled as the surname while a slot selection was pending."""
    session = {
        "patient_name": "Sara",
        "collected": {"name": "Sara"},
        "v3_awaiting_surname": True,
        "v3_awaiting_slot_selection": True,
    }
    assert _v3_try_persist_name(
        session, "Which time suits you?",
        post_slot_pending=False,
        caller_utterance="six",
    ) is False
    assert session["patient_name"] == "Sara"


def test_existing_full_name_is_never_overwritten():
    session = {"patient_name": "Sarah Jenkins", "collected": {"name": "Sarah Jenkins"}}
    assert _v3_try_persist_name(
        session, "Thanks Michael — is that the best number?",
        post_slot_pending=True,
        caller_utterance="michael smith",
    ) is False
    assert session["patient_name"] == "Sarah Jenkins"


# ---------------------------------------------------------------------------
# Pattern-list invariant
# ---------------------------------------------------------------------------

def test_pattern_split_preserves_the_full_ordered_list():
    """Anchored first, so the more specific match still wins when both fire."""
    assert _V3_NAME_CONFIRM_PATTERNS == (
        _V3_NAME_CONFIRM_PATTERNS_ANCHORED + _V3_NAME_CONFIRM_PATTERNS_BARE
    )
    assert len(_V3_NAME_CONFIRM_PATTERNS_BARE) == 1, (
        "the bare title-case pattern is the only ungated-unsafe one; if another "
        "is added it must be classified deliberately, not by default"
    )
