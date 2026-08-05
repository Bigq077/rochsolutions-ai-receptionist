"""Eval calls must not text the real practitioner.

latency-eval's test line (+447366263180) maps to clinic_id "jv_v1", so an eval
call loads Marcus's real config — including his mobile as transfer_phone and
owner_notification_sms. Turning SMS_ENABLED on for an end-to-end check of the
*patient* messages would also fire every owner alert, waitlist ping and staff
notify at him, once per call, for a whole latency run.

EVAL_STAFF_SMS_TO redirects anything addressed to a configured staff number.
The two properties that matter, and that this file pins:

  1. staff SMS are diverted, and
  2. patient SMS are NOT — the booking confirmation still has to reach the
     caller, or the check the override exists to enable is worthless.

Unset, the whole mechanism is inert. That is the production state.
"""

import pytest

from app.notifications import sms as sms_mod


MARCUS = "+447586605462"      # jv_v1 transfer_phone / owner_notification_sms
JONATHAN = "+447545862307"    # vital_edge
TESTER = "+447700900123"      # stand-in for the engineer's own mobile
CALLER = "+447476952176"      # a patient who rang in


@pytest.fixture(autouse=True)
def _clear_staff_cache(monkeypatch):
    """The staff set is process-cached; rebuild it per test."""
    monkeypatch.setattr(sms_mod, "_STAFF_NUMBERS_CACHE", None)
    monkeypatch.delenv("EVAL_STAFF_SMS_TO", raising=False)
    yield
    sms_mod._STAFF_NUMBERS_CACHE = None


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------
def test_unset_is_a_no_op():
    assert sms_mod.redirect_staff_sms(MARCUS) == MARCUS
    assert sms_mod.redirect_staff_sms(CALLER) == CALLER


# ---------------------------------------------------------------------------
# Staff numbers are diverted
# ---------------------------------------------------------------------------
def test_the_practitioner_is_not_texted_during_an_eval_run(monkeypatch):
    monkeypatch.setenv("EVAL_STAFF_SMS_TO", TESTER)

    assert sms_mod.redirect_staff_sms(MARCUS) == TESTER, (
        "an eval call loads jv_v1, so Marcus's mobile is the default "
        "destination for every owner alert — that is the number this override "
        "exists to protect"
    )


def test_every_configured_clinic_is_covered(monkeypatch):
    """Not just the clinic latency-eval happens to map to today."""
    monkeypatch.setenv("EVAL_STAFF_SMS_TO", TESTER)

    assert sms_mod.redirect_staff_sms(JONATHAN) == TESTER


def test_local_format_is_matched_too(monkeypatch):
    """Call sites pass whatever the config holds; 07… and +447… are one number."""
    monkeypatch.setenv("EVAL_STAFF_SMS_TO", TESTER)

    assert sms_mod.redirect_staff_sms("07586605462") == TESTER


# ---------------------------------------------------------------------------
# Patient numbers are not
# ---------------------------------------------------------------------------
def test_the_patient_still_gets_their_booking_confirmation(monkeypatch):
    monkeypatch.setenv("EVAL_STAFF_SMS_TO", TESTER)

    assert sms_mod.redirect_staff_sms(CALLER) == CALLER, (
        "redirecting patient SMS would defeat the purpose — the point of "
        "enabling SMS on eval is to watch the caller-facing messages land"
    )


# ---------------------------------------------------------------------------
# The choke point
# ---------------------------------------------------------------------------
def test_redirect_sits_on_the_single_send_path():
    """Every surface funnels through SMSService.send_sms; the redirect must be
    there rather than at the dozen staff-directed call sites, or a new call
    site added later silently escapes it."""
    import inspect

    src = inspect.getsource(sms_mod.SMSService.send_sms)
    assert "redirect_staff_sms" in src


def test_a_broken_scan_fails_safe(monkeypatch):
    """If the clinic registry cannot be read, send to the original number
    rather than guessing — a missed redirect is recoverable, a misrouted
    patient message is not."""
    monkeypatch.setenv("EVAL_STAFF_SMS_TO", TESTER)
    monkeypatch.setattr(sms_mod, "_STAFF_NUMBERS_CACHE", frozenset())

    assert sms_mod.redirect_staff_sms(MARCUS) == MARCUS
