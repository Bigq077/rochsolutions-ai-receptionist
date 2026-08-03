# tests/regression/test_a3b_booked_name_reaches_every_record.py
"""
A3b — the name that reached the calendar must reach every other record too.

`CA74b20e5dff8d3b4734e5a0be016b537f`, 3 Aug 2026, build `914cda38cf9f` — the
first call on which the A3 read-back worked:

    FINAL → queue: "i'm here to see quentin rook"     <- STT mangled it
    read-back:     "So that's Quentin Rook, Monday the 10th…"
    caller:        "it's not quentin rook it's quentin roch r-o-c-h"
    read-back:     "Thanks for that — so that's Quentin Roch, Monday the 10th…"
    book_appointment  patient_name: "Quentin Roch"  -> calendar  ✓
    📊 Row built —    name=Quentin Rook                          ✗

The caller heard the error and corrected it, the calendar got the right name,
and the call-summary row still said **Rook**.

**Mechanism.** The name lives in two places and the consumers disagree about
which to read:

  * `actionable_summary.py:229` checks `session["patient_name"]` FIRST, then
    falls back to `collected["name"]`.
  * the booking executor updated only `collected["name"]`.
  * `connection.py:10376` had already latched `session["patient_name"]` to
    "Quentin Rook" and — by design — refuses to overwrite a name that already
    carries a surname, so the correction could never reach it.

That read-back ratchet is deliberate ("only ever EXTEND the existing first
name") and is **left alone**; see `A3` in `REGISTER_B_U.md`. The fix instead
aligns session with the name that actually reached the calendar, which closes
the divergence for every cause rather than adding a second guess about which
speaker to believe.

**On testing the booking path.** These tests exercise the pure helper and the
executor's source, never `book_appointment` itself — a test that calls the
booking path can write a real appointment (the 60 accidental Acuity bookings
from `tests/auto`). Same rule as `test_booking_phone_matches_confirmed.py`.

**Which of these fail before the fix:** the call-site tests in section 3. The
helper in section 1 is new code, so its tests cannot fail-before in a
meaningful sense — they pin behaviour, they do not demonstrate the defect.
Section 2 pins the consumer precedence that makes the sync necessary at all.
"""
from __future__ import annotations

import inspect

import pytest

from app.tools import actionable_summary as asum
from app.tools import receptionist_tools as rt


def _sync():
    """Resolve the helper lazily.

    Deliberately NOT a module-level import. Pre-fix the helper does not exist,
    and a top-level import would make every test in this file error with
    ImportError — including the section-3 call-site tests, whose whole job is
    to fail on the defect itself. A test that fails for the wrong reason
    proves nothing.
    """
    fn = getattr(rt, "_sync_booked_patient_name", None)
    if fn is None:
        pytest.skip("_sync_booked_patient_name not present — see section 3")
    return fn


# ─────────────────────────────────────────────────────────────────────────────
# 1. The helper — both records move together
# ─────────────────────────────────────────────────────────────────────────────

def test_the_correction_from_CA74b20e5d_reaches_both_records():
    """The exact session shape from the call, with the corrected name booked."""
    session = {
        "patient_name": "Quentin Rook",              # latched by the read-back
        "collected": {"name": "Quentin Rook"},
    }

    assert _sync()(session, "Quentin Roch") is True

    assert session["patient_name"] == "Quentin Roch", (
        "session['patient_name'] still holds the mangled surname. "
        "actionable_summary reads this key FIRST, so the call-summary row "
        "names the wrong patient even though the calendar is correct."
    )
    assert session["collected"]["name"] == "Quentin Roch"


def test_both_keys_agree_whatever_they_started_as():
    """The two records must never be left disagreeing."""
    for before in (
        {},
        {"collected": {}},
        {"patient_name": "Quentin"},
        {"collected": {"name": "Quentin"}},
        {"patient_name": "Sarah Jenkins", "collected": {"name": "Quentin Rook"}},
    ):
        session = dict(before)
        _sync()(session, "Quentin Roch")
        assert session["patient_name"] == session["collected"]["name"] == "Quentin Roch", (
            f"records disagree after sync, starting from {before!r}"
        )


def test_it_creates_collected_when_absent():
    """The executor calls setdefault itself, but the helper must not assume it."""
    session: dict = {}
    _sync()(session, "Quentin Roch")
    assert session["collected"]["name"] == "Quentin Roch"


# ─────────────────────────────────────────────────────────────────────────────
# 1b. The guard — an empty name must not become a NEW way to lose one
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("empty", ["", None])
def test_an_empty_name_never_clobbers_the_top_level_key(empty):
    """Writing "" to patient_name would blind every reader that prefers it.

    collected["name"] is still assigned unconditionally — that is the
    pre-existing behaviour at both call sites and this fix does not change it.
    The asymmetry is deliberate and documented on the helper.
    """
    session = {"patient_name": "Quentin Roch", "collected": {"name": "Quentin Roch"}}

    assert _sync()(session, empty) is False
    assert session["patient_name"] == "Quentin Roch", (
        "an empty booked name overwrote a good session name — that is a new "
        "way to lose a name, and every reader that prefers patient_name would "
        "then miss it."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. The consumer precedence that makes the sync necessary
# ─────────────────────────────────────────────────────────────────────────────

def test_actionable_summary_still_prefers_the_top_level_key():
    """`build_actionable_summary_row` is async and calls Anthropic, so its
    precedence is asserted from source rather than by invoking it.

    If this ever reorders, re-read the fix: the sync may become unnecessary, or
    a different divergence may have opened.
    """
    src = inspect.getsource(asum.build_actionable_summary_row)

    i_top = src.find('raw_session.get("patient_name")')
    i_col = src.find('collected.get("name")')

    assert i_top != -1 and i_col != -1, (
        "actionable_summary no longer resolves the patient name from these two "
        "keys — the mechanism this regression is about has changed shape."
    )
    assert i_top < i_col, (
        "actionable_summary no longer prefers session['patient_name'] over "
        "collected['name']. That precedence is the reason a stale top-level "
        "key put the wrong name on the summary row for CA74b20e5d."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. The call sites — THIS is what fails before the fix
# ─────────────────────────────────────────────────────────────────────────────

def _executor_src() -> str:
    return inspect.getsource(rt._exec_book_appointment)


def test_the_booking_executor_syncs_both_records():
    """Both branches — the calendar path and the manual-followup path.

    Pre-fix this source contained two bare `session["collected"]["name"] =
    patient_name` assignments and no sync, which is the defect.
    """
    src = _executor_src()
    n = src.count("_sync_booked_patient_name(")

    assert n >= 2, (
        f"_exec_book_appointment calls the name sync {n} time(s); both the "
        "calendar-success branch and the manual-followup branch must use it. "
        "The manual branch matters MORE, not less — no calendar event exists "
        "there, so the summary row is the only record of who was booked."
    )


# There are FOUR booking executors, not one, and every one of them had the same
# bare assignment. Missing the other three would have fixed the defect for the
# Google Calendar clinic and left it live for Acuity and for Vital Edge's
# provisional path — the two the port plans are about.
_BOOKING_EXECUTORS = [
    "_exec_book_appointment",
    "_book_appointment_acuity",
    "_book_appointment_provisional",
]


@pytest.mark.parametrize("fn_name", _BOOKING_EXECUTORS)
def test_no_booking_executor_writes_collected_name_without_syncing(fn_name):
    """A bare assignment is how the two records drifted in the first place."""
    fn = getattr(rt, fn_name, None)
    assert fn is not None, f"{fn_name} no longer exists — re-scope this test"
    src = inspect.getsource(fn)

    assert 'session["collected"]["name"] = patient_name' not in src, (
        f"{fn_name} assigns collected['name'] directly. That leaves "
        "session['patient_name'] stale, and actionable_summary reads the stale "
        "one FIRST — exactly the CA74b20e5d divergence. Route it through "
        "_sync_booked_patient_name instead."
    )


@pytest.mark.parametrize("fn_name", _BOOKING_EXECUTORS)
def test_every_booking_executor_syncs_the_name(fn_name):
    """Each executor must write the booked name to both records."""
    fn = getattr(rt, fn_name, None)
    assert fn is not None, f"{fn_name} no longer exists — re-scope this test"

    assert "_sync_booked_patient_name(" in inspect.getsource(fn), (
        f"{fn_name} does not sync the booked name. Whichever provider or "
        "booking mode this executor serves, its callers end up on the same "
        "summary row and the same SMS as every other clinic."
    )


def test_the_ratchet_is_deliberately_left_alone():
    """Scope guard.

    The read-back upgrade at connection.py refuses to replace a name that
    already has a surname. That is intentional — it stops the model's
    paraphrase overwriting a surname captured from the caller's own clean
    transcript. This fix works downstream of it and must not have relaxed it.
    """
    from app.media_streams import connection as c

    src = inspect.getsource(c)
    assert '" " not in _cur_name' in src, (
        "the read-back name-upgrade ratchet at connection.py has been relaxed. "
        "That is Option A, a different change with a different risk profile "
        "(it lets a model paraphrase overwrite a caller-captured surname). If "
        "it was relaxed deliberately, this fix needs re-reviewing alongside it."
    )
