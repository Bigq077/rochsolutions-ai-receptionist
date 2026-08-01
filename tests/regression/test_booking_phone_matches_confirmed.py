"""A3 — the number booked must be the number confirmed.

Regression for CA3590527b (1 Aug 2026). The caller typed nine digits on the
keypad, `_commit_dtmf_phone_for_booking` refused them as too short, and the
model padded them with "00" into a plausible-looking UK mobile — 07987124700.
That number appears in no DTMF frame and in no transcript. The booking
succeeded on it and two SMS reminders were scheduled to it.

The A1 gate could not catch it: A1 asks whether *a* number was confirmed, not
whether the number being booked is that one.

These tests exercise `_reconcile_booking_phone` directly rather than
`book_appointment`, deliberately: a test that calls the booking path can write
a real appointment (see the 60 accidental Acuity bookings from tests/auto).
The helper is pure, so the gate's decision is testable with plain dicts.
"""

import pytest

from app.tools.receptionist_tools import _reconcile_booking_phone


def _session(confirmed=None, phone_number=None):
    s = {"phone_confirmed": True, "collected": {}}
    if confirmed is not None:
        s["collected"]["phone"] = confirmed
    if phone_number is not None:
        s["phone_number"] = phone_number
    return s


# ── The call that caused this ────────────────────────────────────────────────

def test_the_fabricated_number_from_CA3590527b_is_corrected():
    """The model padded 079871247 to 07987124700; the session held the number
    that was actually confirmed. The booking must use the confirmed one."""
    fix, reason = _reconcile_booking_phone(
        {"phone": "07987124700"}, _session(confirmed="07502211207")
    )
    assert reason == "mismatch"
    assert fix == "07502211207"


def test_a_number_invented_wholesale_is_corrected():
    fix, reason = _reconcile_booking_phone(
        {"phone": "07000000000"}, _session(confirmed="07987124700")
    )
    assert reason == "mismatch"
    assert fix == "07987124700"


# ── Formatting must never register as a mismatch ─────────────────────────────
# Every path that sets phone_confirmed writes collected["phone"], but flow.py
# stores E.164 (_to_e164_uk) while connection.py stores the local form. If the
# gate could not fold those together it would "correct" every deterministic-flow
# booking onto a differently formatted copy of the same number and log an error
# on each one.

@pytest.mark.parametrize(
    "confirmed, arg",
    [
        ("+447502211207", "07502211207"),
        ("07502211207", "+447502211207"),
        ("+447502211207", "+44 7502 211207"),
        ("07502211207", "07502 211207"),
        ("447502211207", "07502211207"),
    ],
)
def test_equivalent_formats_are_not_a_mismatch(confirmed, arg):
    fix, reason = _reconcile_booking_phone({"phone": arg}, _session(confirmed=confirmed))
    assert reason == "match"
    assert fix is None


def test_the_same_number_passes_untouched():
    fix, reason = _reconcile_booking_phone(
        {"phone": "07987124700"}, _session(confirmed="07987124700")
    )
    assert reason == "match"
    assert fix is None


# ── Reference resolution ─────────────────────────────────────────────────────

def test_phone_number_is_used_when_collected_is_empty():
    """flow.py sets session["phone_number"] alongside collected["phone"]; if
    only the former survives, the gate must still have a reference."""
    fix, reason = _reconcile_booking_phone(
        {"phone": "07000000000"}, _session(phone_number="07502211207")
    )
    assert reason == "mismatch"
    assert fix == "07502211207"


def test_collected_wins_over_phone_number():
    s = _session(confirmed="07502211207", phone_number="07000000000")
    fix, reason = _reconcile_booking_phone({"phone": "07502211207"}, s)
    assert reason == "match"
    assert fix is None


# ── Fail open, never block ───────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, "", "   ", 12345, {"phone": "x"}, True])
def test_a_junk_reference_fails_open(bad):
    """Session slots are written by many paths and are not always strings. No
    reference means no opinion — the gate must not block a booking it cannot
    judge."""
    fix, reason = _reconcile_booking_phone({"phone": "07502211207"}, _session(confirmed=bad))
    assert reason == "no_reference"
    assert fix is None


def test_no_confirmed_number_at_all_fails_open():
    fix, reason = _reconcile_booking_phone({"phone": "07502211207"}, {"collected": {}})
    assert reason == "no_reference"
    assert fix is None


def test_a_missing_phone_argument_is_filled_from_the_confirmed_number():
    """The model omitting `phone` entirely is a mismatch against a real
    reference, so the confirmed number is supplied rather than booking blank."""
    for args in ({}, {"phone": ""}, {"phone": None}):
        fix, reason = _reconcile_booking_phone(args, _session(confirmed="07502211207"))
        assert reason == "mismatch"
        assert fix == "07502211207"


# ── The helper must not mutate ───────────────────────────────────────────────

def test_the_helper_leaves_args_and_session_alone():
    """The gate's caller decides whether to apply the correction; the helper
    reporting one must not have already applied it."""
    args = {"phone": "07000000000"}
    sess = _session(confirmed="07502211207")
    _reconcile_booking_phone(args, sess)
    assert args == {"phone": "07000000000"}
    assert sess["collected"] == {"phone": "07502211207"}


# ── Placement ────────────────────────────────────────────────────────────────

def test_the_gate_runs_before_the_backend_branch():
    """A3 must sit above the Acuity / Google Calendar / provisional split, or
    it only protects one of the three booking backends."""
    import inspect
    from app.tools import receptionist_tools

    src = inspect.getsource(receptionist_tools._exec_book_appointment)
    assert "_reconcile_booking_phone" in src, "A3 gate is not in _exec_book_appointment"
    assert src.index("_reconcile_booking_phone") < src.index(
        "_book_appointment_acuity"
    ), "A3 gate runs after the backend branch — Acuity bookings are unprotected"


def test_the_gate_sits_after_A1():
    """A3 reads the number A1 guarantees exists; running it first would judge a
    booking that was never going to happen."""
    import inspect
    from app.tools import receptionist_tools

    src = inspect.getsource(receptionist_tools._exec_book_appointment)
    assert src.index("phone not confirmed (A1)") < src.index("_reconcile_booking_phone")
