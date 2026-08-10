# tests/regression/test_a3_spelled_surname_correction_overwrites.py
"""
A3 — the caller spelled his surname out and the calendar kept the wrong one.

CA166de2a9 (Theorem, 10 Aug 2026). STT heard "jack told me…" and the capture
pipeline stored the complete-looking name 'Jack Told'. The caller corrected it
in the clearest words the language offers:

    "my surname is jack thompson t-h-o-m-p-s-o-n"

The correction never persisted. Acuity has 'Jack Told'; so does the confirmation
SMS that went to his phone.

The mechanism is first-write-wins with no correction path. _v3_try_persist_name
returned at the very first branch —

    if " " in _existing:
        return False  # a surname is already present — nothing to add

— because 'Jack Told' contains a space and therefore *looked* complete. The
model used "Jack Thompson" for two turns from its own context, then the forced
readback re-injected the stored name (llm_stream.py) and it reverted. That is
why the wrong name is not merely stored but SPOKEN back, which is what makes it
survive to the write.

── Why the gate is stricter than the empty-surname one ─────────────────────
Filling an EMPTY surname risks a missing word. OVERWRITING risks destroying a
correct one, so the correction path requires a strictly stronger cue: the caller
must have said "surname"/"last name"/"family name"/"second name".

Specifically, the spelled-run branch is not allowed to fire on its own here.
backfill_surname strips non-letters before reading a run of single characters,
so a postcode said aloud — "B97 5AB" — becomes the letters b, a, b and spells
the surname 'Bab'. Harmless when the surname slot is empty and about to be
read back; not harmless when it silently replaces a surname the caller gave.

The bare-straggler branch is disabled outright on this path (awaiting_surname is
forced False): it accepts ANY single word, which is the one thing that must
never unseat a name already collected. See CA6dce36c8 ('Sara Six') for what that
branch does when it is wrong.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import _v3_try_persist_name
from app.name_capture import backfill_surname, has_surname_marker


# The utterance is quoted from the call, including the STT's dashes.
LIVE_CORRECTION = "my surname is jack thompson t-h-o-m-p-s-o-n"


def _session(name: str = "Jack Told", **extra) -> dict:
    s = {"patient_name": name, "collected": {"name": name}}
    s.update(extra)
    return s


# ── 1. the live call ────────────────────────────────────────────────────────

def test_the_live_correction_now_persists():
    """The whole defect, end to end: 'Jack Told' must become 'Jack Thompson'."""
    session = _session()
    assert _v3_try_persist_name(
        session, "Thanks Jack.", caller_utterance=LIVE_CORRECTION
    ) is True
    assert session["patient_name"] == "Jack Thompson"
    assert session["collected"]["name"] == "Jack Thompson"


def test_full_name_is_refreshed_when_it_exists():
    """
    full_name OUTRANKS name at the forced readback and the booking summary, so a
    stale one left behind would let the wrong surname win regardless of what
    `name` says — the readback is exactly how 'Jack Told' came back.
    """
    session = _session()
    session["collected"]["full_name"] = "Jack Told"
    session["full_name"] = "Jack Told"

    assert _v3_try_persist_name(
        session, "Thanks Jack.", caller_utterance=LIVE_CORRECTION
    ) is True
    assert session["collected"]["full_name"] == "Jack Thompson"
    assert session["full_name"] == "Jack Thompson"


def test_full_name_is_not_created_when_absent():
    """
    Only refreshed where it already exists. Creating the key would flip which
    branch every `full_name or name` reader takes, which is a much larger change
    than this fix is entitled to make.
    """
    session = _session()
    _v3_try_persist_name(session, "Thanks Jack.", caller_utterance=LIVE_CORRECTION)
    assert "full_name" not in session["collected"]
    assert "full_name" not in session


# ── 2. the correction cue is required ───────────────────────────────────────

@pytest.mark.parametrize(
    "utterance",
    [
        # A postcode read aloud. Digits are stripped before the run is read, so
        # the letters spell 'Bab'. This is the case that makes the spelled-run
        # branch unsafe on its own.
        "it's b nine seven five a b",
        # Ordinary spelling of something that is not a name.
        "the referral code is a b c d",
        # A bare word — the straggler branch, disabled on this path.
        "thompson",
        # An answer to a different question that happens to be one word.
        "yes",
    ],
)
def test_a_correct_surname_is_not_overwritten_without_the_cue(utterance):
    session = _session("Sarah Jenkins")
    assert _v3_try_persist_name(
        session, "Thanks Sarah.", caller_utterance=utterance
    ) is False
    assert session["patient_name"] == "Sarah Jenkins"


def test_the_straggler_branch_cannot_fire_even_with_the_cue():
    """
    The marker opens the path; it does not license the bare-word branch. With a
    marker present but no extractable surname and no spelled run, nothing lands.
    """
    session = _session("Sarah Jenkins", v3_awaiting_surname=True)
    assert _v3_try_persist_name(
        session, "And your surname?", caller_utterance="that's my surname"
    ) is False
    assert session["patient_name"] == "Sarah Jenkins"


def test_marker_without_spelling_still_corrects():
    """The common correction is spoken, not spelled."""
    session = _session()
    assert _v3_try_persist_name(
        session, "Thanks Jack.", caller_utterance="no, my surname is thompson"
    ) is True
    assert session["patient_name"] == "Jack Thompson"


def test_marker_with_only_a_spelled_run_still_corrects():
    """
    "my surname, t-h-o-m-p-s-o-n" — the marker gates the path, backfill_surname
    resolves the value through its spelling branch. Pins that the gate does not
    accidentally require an extractable TOKEN as well as the cue.
    """
    assert has_surname_marker("my surname, t h o m p s o n")
    assert backfill_surname("my surname, t h o m p s o n", "Jack") == "Thompson"

    session = _session()
    assert _v3_try_persist_name(
        session, "Thanks Jack.", caller_utterance="my surname, t h o m p s o n"
    ) is True
    assert session["patient_name"] == "Jack Thompson"


# ── 3. no-op cases ──────────────────────────────────────────────────────────

def test_restating_the_same_surname_is_not_a_change():
    """Avoids a misleading 'CORRECTED' log line for a confirmation."""
    session = _session("Jack Thompson")
    assert _v3_try_persist_name(
        session, "Thanks Jack.", caller_utterance="yes, my surname is thompson"
    ) is False
    assert session["patient_name"] == "Jack Thompson"


def test_a_written_booking_is_not_rewritten():
    """
    Once booking_confirmed is set the appointment exists in the provider under
    the stored name; changing session state alone would only split the two.
    Unchanged from the empty-surname path — pinned so it stays deliberate.
    """
    session = _session(booking_confirmed=True)
    assert _v3_try_persist_name(
        session, "Thanks Jack.", caller_utterance=LIVE_CORRECTION
    ) is False
    assert session["patient_name"] == "Jack Told"


# ── 4. the empty-surname path is untouched ──────────────────────────────────

def test_the_backfill_path_still_works():
    """The original CA6dce36c8-bounded back-fill must be unaffected."""
    session = {"patient_name": "Quentin", "collected": {"name": "Quentin"}}
    assert _v3_try_persist_name(
        session, "And your surname?", caller_utterance="roch"
    ) is True
    assert session["patient_name"] == "Quentin Roch"


def test_the_straggler_is_still_suppressed_during_slot_selection():
    """CA6dce36c8 — 'Sara Six'. Pinned because this fix touches _awaiting."""
    session = {
        "patient_name": "Sara",
        "collected": {"name": "Sara"},
        "v3_awaiting_surname": True,
        "v3_awaiting_slot_selection": True,
    }
    assert _v3_try_persist_name(
        session, "Which suits you?", caller_utterance="six"
    ) is False
    assert session["patient_name"] == "Sara"
