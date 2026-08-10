"""
Regression (CA166de2a99b97322f3d7ead3645b97d86, Theorem, 2026-08-10 15:00-15:02):
after Acuity refused the slot, the engine spent four minutes refusing to look at
the diary.

    15:00:11  book_appointment 2026-08-12T16:00 → Acuity 400, "not an available
              time slot"
    15:00:51  check_availability BLOCKED — name collected + phone CONFIRMED
    15:01:06  check_availability BLOCKED   (…seven times in total, every one of
    15:01:13  check_availability BLOCKED    them AFTER the failure above)
    …
    15:02:50  caller: "can you do thursday the 13th at all"  ← he fixed it himself

Each block handed the model the same instruction — "Do NOT call
check_availability and do NOT ask for the day or time again" — and told it to
re-read the booking summary for a slot the calendar had just rejected. Three
more bookings were attempted on slots that did not exist, each one an owner
manual-followup SMS.

`_post_collect_readback_due` reads "the caller's details are settled and nobody
is trying to change the slot". That premise dies the moment the provider rejects
the slot, and nothing in the predicate noticed.

── Why both guards, in one commit ──────────────────────────────────────────
Releasing the post-collect guard alone would have changed nothing on this call.
The `elif` immediately after it — the slot-locked guard — was satisfied on every
one of those turns too: `v3_confirmed_slot_phrase` set, `last_offered_slots`
empty, and "yes please" as the utterance, which carries neither a digit nor a
new-slot word. It would have taken over the blocking and the call would have run
exactly as it did.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import (
    BOOKING_WRITE_FAILED_KEY,
    WRITE_SUCCEEDED_KEY,
    _caller_wants_new_slot,
    _note_write_result,
    _post_collect_readback_due,
)

# The session as it stood at 15:00:51 — name in, phone confirmed, slot "agreed",
# and one Acuity refusal already behind it.
def _collected_session(**extra) -> dict:
    session = {
        "phone_confirmed": True,
        "collected": {"name": "Jack Told", "full_name": "Jack Told", "phone": "07554997278"},
        "v3_confirmed_slot_phrase": (
            "Four in the afternoon — lovely. So that's Wednesday the 12th of "
            "August at four in the afternoon"
        ),
    }
    session.update(extra)
    return session


YES_PLEASE = [{"role": "user", "content": "yes please"}]


# ── 1. The defect ───────────────────────────────────────────────────────────

def test_a_failed_booking_releases_the_post_collect_block():
    session = _collected_session()
    assert _post_collect_readback_due("check_availability", session, YES_PLEASE) is True
    session[BOOKING_WRITE_FAILED_KEY] = True
    assert _post_collect_readback_due("check_availability", session, YES_PLEASE) is False


def test_the_utterance_that_kept_the_slot_locked_guard_alive():
    """Proof of the "both or neither" argument in the module docstring: on those
    turns the caller said "yes please", which `_caller_wants_new_slot` does not
    recognise — no digit, no new-slot word. So the second guard's escape hatch
    was shut and it would have blocked in the first one's place."""
    assert _caller_wants_new_slot(YES_PLEASE) is False


# ── 2. What must NOT release it ─────────────────────────────────────────────

def test_a_gate_refusal_does_not_release_the_block():
    """The gate refusals carry {"status": "..._required"} and no success key —
    the write was never attempted because details are still missing, which is
    exactly when the block is doing its job. Releasing there would reopen
    BUG-14, the spurious re-search during name collection."""
    session = {}
    _note_write_result(session, "book_appointment", {"status": "name_required", "message": "..."})
    assert session.get(BOOKING_WRITE_FAILED_KEY) is None


def test_a_real_executor_failure_does_release_it():
    """What Acuity actually returned, four times."""
    session = {}
    _note_write_result(session, "book_appointment", {
        "success": False,
        "error": 'Acuity request error (400): The time "2026-08-12T16:00:00+01:00" '
                 "is not an available time slot.",
    })
    assert session[BOOKING_WRITE_FAILED_KEY] is True


@pytest.mark.parametrize("tool", ["reschedule_appointment", "cancel_appointment"])
def test_only_the_booking_family_arms_it(tool):
    """The flag answers one question — is the slot on record still worth
    defending — and only a booking puts a slot on record."""
    session = {}
    _note_write_result(session, tool, {"success": False, "error": "nope"})
    assert session.get(BOOKING_WRITE_FAILED_KEY) is None


def test_a_failure_after_a_success_does_not_release_it():
    """CA0f9a12's duplicate-write path: the family already completed, so this
    attempt changed nothing and must not reopen the diary."""
    session = {WRITE_SUCCEEDED_KEY: {"booking": True}}
    _note_write_result(session, "book_appointment", {"success": False, "error": "duplicate"})
    assert session.get(BOOKING_WRITE_FAILED_KEY) is None


# ── 3. Re-arming once a booking really exists ───────────────────────────────

def test_a_later_success_clears_the_flag():
    """Once a real booking exists the caller is not choosing a time any more, so
    the blocks are right again — and a farewell turn should not spend a round
    trip re-reading the diary."""
    session = {BOOKING_WRITE_FAILED_KEY: True}
    _note_write_result(session, "book_appointment", {"success": True, "acuity_booking_id": "1"})
    assert session.get(BOOKING_WRITE_FAILED_KEY) is None
    assert session["booking_write_confirmed"] is True


def test_the_block_is_back_on_after_that_success():
    session = _collected_session(**{BOOKING_WRITE_FAILED_KEY: True})
    _note_write_result(session, "book_appointment", {"success": True, "acuity_booking_id": "1"})
    assert _post_collect_readback_due("check_availability", session, YES_PLEASE) is True


# ── 4. Unrelated behaviour, pinned ──────────────────────────────────────────

def test_the_block_still_ignores_other_tools():
    session = _collected_session()
    assert _post_collect_readback_due("book_appointment", session, YES_PLEASE) is False


def test_an_unconfirmed_phone_still_releases_the_block():
    """B-46: the predicate reads phone_confirmed, never collected["phone"],
    which is pre-loaded from caller-ID and always truthy."""
    session = _collected_session()
    session["phone_confirmed"] = False
    assert _post_collect_readback_due("check_availability", session, YES_PLEASE) is False
