"""
The missed-patient ping must not claim to have been sent when it wasn't (T-6).

Observed on call 2 of the Theorem acceptance sweep, 2026-08-04 21:14:13, two
lines apart:

    [sms] SMS_ENABLED is off — outbound SMS suppressed (not sent)
    [ms_conn] staff notify SMS sent → +447870166861

This is not a logging nicety. The message it claims to have sent is:

    "Hi Mark, a caller just asked to speak to you but didn't get through.
     Their number is ... Give them a call back when you get a chance."

It is the safety net for the case where the AI has already failed a patient. If
it silently does not go out, nobody calls them back — and the one artefact that
would reveal it says everything is fine.

`connection.py` discarded `send_sms`'s return value. The contract is
`Optional[str]`: a SID on success, `None` on every failure — invalid number,
Twilio error, or SMS_ENABLED off.

Three defects were found in that block, not one:

  1. the SID was discarded and success logged unconditionally
  2. `if _staff_phone:` had no `else`, so a clinic with neither a
     transfer_phone nor THEOREM_NOTIFICATION_SMS lost the caller in total
     silence — no SMS and no log
  3. no branch carried the caller's number, so on any failure the one piece of
     information needed to act — who to ring back — was gone

The branch structure is asserted from source: the block sits deep inside the
connection cleanup path and driving it would require a live call object, which
would test the harness rather than the fix. The `send_sms` CONTRACT the fix
depends on is asserted behaviourally below — that is the part that could
silently change underneath it.
"""

import inspect

import pytest

from app.media_streams import connection as conn


@pytest.fixture(scope="module")
def block():
    """The staff-notify block, isolated from the rest of cleanup."""
    src = inspect.getsource(conn)
    start = src.index("# Notify staff if caller asked for a human")
    end = src.index("staff notify SMS FAILED", start)
    return src[start:end + 400]


# ── 1. the SID is captured and branched on ──────────────────────────────────

def test_the_send_result_is_captured(block):
    """THE regression. `await _send_sms(...)` on its own line discards the
    result, which is what produced the false log."""
    assert "_notify_sid = await _send_sms(" in block, (
        "send_sms's return value is being discarded again"
    )


def test_success_is_conditional(block):
    assert "if _notify_sid:" in block


def test_no_unconditional_success_log(block):
    """There must be no path that logs 'sent' without checking the SID."""
    import re

    for m in re.finditer(r'staff notify SMS sent', block):
        window = block[max(0, m.start() - 400):m.start()]
        assert "if _notify_sid:" in window, (
            "a 'staff notify SMS sent' log is reachable without a SID check"
        )


def test_failure_is_logged_at_error(block):
    """warning() is what the old code used for a genuinely exceptional case and
    it got lost in the noise. A lost patient is an error."""
    assert "staff notify SMS NOT SENT" in block
    assert "logger.error(" in block


# ── 2. the silent-skip hole is closed ───────────────────────────────────────

def test_missing_staff_phone_is_no_longer_silent(block):
    """A clinic with no transfer_phone and no THEOREM_NOTIFICATION_SMS used to
    fall through the `if` with no SMS and no log at all — the caller vanished."""
    assert "staff notify SKIPPED" in block, (
        "the no-staff-phone branch is gone; a caller who asked for a human can "
        "disappear without a trace again"
    )


# ── 3. every failure path carries the callback number ───────────────────────

@pytest.mark.parametrize("marker", [
    "staff notify SMS NOT SENT",
    "staff notify SKIPPED",
])
def test_failure_branches_carry_the_caller_number(block, marker):
    """The caller's number is the whole payload. If the text does not arrive,
    the log has to be enough to make the callback from."""
    idx = block.index(marker)
    window = block[idx:idx + 700]
    assert "CALL THEM BACK ON %s" in window, (
        f"the {marker!r} branch does not log the caller's number — the "
        "callback is unrecoverable from logs"
    )


def test_the_exception_path_carries_it_too():
    """The except branch cannot rely on _caller — the exception may have been
    raised before it was bound — so it re-derives the number inline."""
    src = inspect.getsource(conn)
    idx = src.index("staff notify SMS FAILED")
    window = src[idx:idx + 600]
    assert "CALL THEM BACK ON %s" in window
    assert "twilio_from_local" in window, (
        "the exception path references a variable that may be unbound instead "
        "of re-deriving the caller number"
    )


# ── the contract the fix rests on ───────────────────────────────────────────

async def test_send_sms_returns_none_when_suppressed(monkeypatch):
    """Behavioural, and the important one: this is the exact condition that
    produced the false log on call 2. If send_sms ever starts returning a
    truthy value while suppressing, the branch above silently starts lying
    again."""
    import importlib

    monkeypatch.setenv("SMS_ENABLED", "false")
    import app.notifications.sms as sms
    sms = importlib.reload(sms)

    svc = sms.SMSService.__new__(sms.SMSService)
    svc.client = None          # never reached — the gate returns first
    svc.from_number = "+447380841468"

    result = await svc.send_sms(to="+447502211207", message="test")
    assert result is None, (
        "send_sms returned a truthy value while suppressing the send — every "
        "caller of it that branches on the SID is now wrong"
    )
