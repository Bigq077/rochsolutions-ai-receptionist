"""Theorem owner alerts — Mark must hear about a failed booking.

Pre-go-live audit, 2026-08-05. Two separate defects, one symptom:

1. `CLINICS["theorem"]` carried no `owner_alerts` block, so
   `owner_alerts_enabled()` returned False for every event and nothing was ever
   sent to Mark — not a booking, not a cancellation, not a failed write.

2. Adding the block was not enough. The `manual_followup`, `cancellation` and
   `reschedule` alerts only ever existed on the **Google Calendar** executors.
   Theorem short-circuits to `_book/_cancel/_reschedule_appointment_acuity` at
   the top of each `_exec_*`, before those call sites are reached, so three of
   the four configured events would have stayed silent while looking enabled.

The safety-critical one is `manual_followup`. CLAUDE.md's bar is "every booking
that fails is escalated to a human within minutes"; with Sheets off
(SHEETS_ENABLED=false) this SMS is the only route a failure has to a human.

These tests never call `_book_appointment_acuity` or friends — that path writes
real appointments (see the 60 accidental Acuity bookings from tests/auto). The
alert helpers are exercised directly, and the wiring into the executors is
pinned by source inspection.
"""

import inspect

import pytest

from app.clinic_config import get_clinic
from app.notifications.owner_alert import owner_alerts_enabled, _resolve_owner_phone
from app.tools import receptionist_tools as rt

MARK = "+447870166861"

# Both live Theorem numbers resolve to one of these ids.
THEOREM_IDS = ("theorem", "theorem_v2", "theorem_v3")


# ── 1. Config: the events are enabled, on Mark's number ──────────────────────

@pytest.mark.parametrize("clinic_id", THEOREM_IDS)
@pytest.mark.parametrize(
    "event", ["manual_followup", "booking", "cancellation", "reschedule"]
)
def test_theorem_enables_the_owner_alert(clinic_id, event):
    assert owner_alerts_enabled(get_clinic(clinic_id), event) is True


@pytest.mark.parametrize("clinic_id", THEOREM_IDS)
def test_alerts_resolve_to_marks_number(clinic_id):
    assert _resolve_owner_phone(get_clinic(clinic_id)) == MARK


def test_the_other_clinics_are_untouched():
    """This was a Theorem-only config change; JV keeps its own number."""
    assert _resolve_owner_phone(get_clinic("jv_v1")) != MARK


# ── 2. A failed Acuity write reaches a human ─────────────────────────────────

@pytest.fixture
def sent(monkeypatch):
    """Capture notify_owner calls. The helpers import it at call time, so
    patching the module attribute is enough — no SMS leaves the process."""
    calls = []

    async def _fake(session, **kw):
        calls.append(kw)
        return True

    monkeypatch.setattr("app.notifications.owner_alert.notify_owner", _fake)
    return calls


async def test_a_failed_write_alerts_the_owner(sent):
    await rt._alert_owner_acuity_book_failed(
        {
            "patient_name": "Jane Doe",
            "phone": "07700900123",
            "service": "Initial Assessment",
            "location": "alcester",
            "slot_iso": "2026-08-07T10:00:00",
        },
        {"clinic_id": "theorem"},
        "connection reset",
    )
    assert len(sent) == 1
    assert sent[0]["event"] == "manual_followup"


async def test_the_alert_carries_the_number_to_call_back(sent):
    """Mark cannot act on "a booking failed" — he needs who, and how to reach
    them. The caller was told we could not book, so the callback is the fix."""
    await rt._alert_owner_acuity_book_failed(
        {"patient_name": "Jane Doe", "phone": "07700900123"},
        {"clinic_id": "theorem"},
        "connection reset",
    )
    note = sent[0]["note"]
    assert "07700900123" in note
    assert "FAILED" in note
    assert sent[0]["patient_name"] == "Jane Doe"


async def test_a_reschedules_failed_booking_still_alerts(sent):
    """The inner book of a reschedule carries _suppress_sms to avoid a double
    patient text. That must never suppress the failure alert — a reschedule
    that silently did not happen is the same lost patient."""
    await rt._alert_owner_acuity_book_failed(
        {"patient_name": "Jane Doe", "phone": "07700900123", "_suppress_sms": True},
        {"clinic_id": "theorem"},
        "connection reset",
    )
    assert len(sent) == 1


# ── 3. Cancellation ──────────────────────────────────────────────────────────

async def test_a_cancellation_alerts_the_owner(sent):
    await rt._alert_owner_acuity_cancelled(
        {"patient_name": "Jane Doe"},
        {"clinic_id": "theorem"},
        "Initial Assessment",
        "2026-08-07T10:00:00",
    )
    assert len(sent) == 1
    assert sent[0]["event"] == "cancellation"
    assert sent[0]["when_label"] == "Friday 07 August at 10:00"


async def test_an_unparseable_time_still_sends(sent):
    """Send the raw string rather than swallow the alert over a format."""
    await rt._alert_owner_acuity_cancelled(
        {"patient_name": "Jane Doe"}, {"clinic_id": "theorem"}, "appointment", "soon"
    )
    assert sent[0]["when_label"] == "soon"


async def test_a_reschedules_inner_cancel_does_not_double_buzz(sent):
    """A reschedule is book + cancel internally. Both legs carry _suppress_sms;
    the reschedule sends its own single alert. Mark gets one buzz, not three."""
    await rt._alert_owner_acuity_cancelled(
        {"patient_name": "Jane Doe", "_suppress_sms": True},
        {"clinic_id": "theorem"},
        "Initial Assessment",
        "2026-08-07T10:00:00",
    )
    assert sent == []


# ── 4. Wiring — the helpers are actually called by the Acuity executors ──────
# Config that enables an event with no call site behind it is the exact trap
# this fix was cleaning up, so the call sites are pinned, not assumed. Source
# inspection rather than execution: calling these functions books real
# appointments.

def test_the_acuity_book_failure_paths_alert():
    src = inspect.getsource(rt._book_appointment_acuity)
    # ProviderAuthError, generic Exception, and no-adapter.
    assert src.count("_alert_owner_acuity_book_failed") == 3


def test_slot_contention_does_not_alert():
    """SlotUnavailable means the slot went between check and write — the model
    offers another time and the call carries on. Alerting on routine contention
    trains the owner to ignore the alert that matters."""
    src = inspect.getsource(rt._book_appointment_acuity)
    slot_branch = src.split("except SlotUnavailable")[1].split("except ProviderAuthError")[0]
    assert "_alert_owner_acuity_book_failed" not in slot_branch


def test_every_acuity_cancel_path_alerts():
    """Three ways out of a successful cancel: RC fast-path, exact-id, and the
    legacy name search. Missing one is a silent gap on a subset of calls."""
    src = inspect.getsource(rt._cancel_appointment_acuity)
    assert src.count("_alert_owner_acuity_cancelled") == 3


def test_the_acuity_reschedule_alerts_once():
    """One reschedule, one buzz — however many call sites there are.

    This used to assert a flat `count(...) == 1`. The half-done reschedule fix
    (2026-08-23) added a SECOND alert, for the case where the new slot is booked
    but the original will not cancel: the caller is in the diary twice and
    somebody has to go and remove one by hand.

    That did not break the invariant, only the proxy. The two sites are mutually
    exclusive — the half-done branch returns before STEP 4 is ever reached — so
    exactly one alert still goes out on any given reschedule. Bumping the
    expected count to 2 would have restored green while quietly dropping the
    guard, because 2 is also what a genuine double-buzz looks like. So pin the
    structure that makes them exclusive instead: the alert inside the half-done
    branch, and a return between it and STEP 4's.

    The runtime half of this is in
    tests/regression/test_reschedule_never_hides_a_failed_cancel.py, which
    asserts exactly one owner alert on the half-done path. Source inspection
    here because calling these functions books real appointments.
    """
    src = inspect.getsource(rt._reschedule_appointment_acuity)
    assert src.count('event="reschedule"') == 2

    before_success, marker, after_success = src.partition(
        'session["calendar_status"] = "rescheduled"'
    )
    assert marker, "the success path marker moved — re-anchor this test"

    half_done = before_success.partition('if not cancel_result.get("success"):')[2]
    assert half_done, "the failed-cancel branch moved — re-anchor this test"

    assert half_done.count('event="reschedule"') == 1, (
        "the failed-cancel branch must escalate the duplicate to a human"
    )
    assert "return {" in half_done, (
        "the half-done branch must RETURN before STEP 4, or one reschedule "
        "sends Mark two alerts and reports success it did not achieve"
    )
    assert after_success.count('event="reschedule"') == 1, (
        "the completed-move heads-up must stay on the success path"
    )
