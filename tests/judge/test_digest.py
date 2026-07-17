"""Tests for app.obs.digest — the once-a-day review summary."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app import config
from app.obs import digest, emailer


def _smtp_configured(monkeypatch):
    """Configure the digest recipient AND the shared sender's SMTP env.

    On this branch app/obs/emailer.py delegates to app/notifications/email.py, which
    reads SMTP_* from the environment (note: SMTP_USERNAME, not upstream's SMTP_USER).
    """
    monkeypatch.setattr(config, "OBS_DIGEST_EMAIL_TO", "owner@example.com")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "susie@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setenv("SMTP_FROM", "susie@example.com")


def _smtp_unconfigured(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)


def test_build_summary_counts_review_calls():
    calls = [
        {"failure_tags": ["loop"]},
        {"failure_tags": ["loop", "booking_error"]},
    ]
    s = digest.build_summary(calls, 24)
    assert "2 call(s)" in s
    assert "loop×2" in s


def test_build_summary_empty_is_none():
    assert digest.build_summary([], 24) is None


async def test_digest_sends_one_text_for_review_calls(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from app import config
    from app.obs import store
    db = tmp_path / "d.db"
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    monkeypatch.setattr(config, "OBS_ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "OBS_ALERT_SMS_TO", "+440000000000")
    monkeypatch.setattr(config, "OBS_SLACK_WEBHOOK", "")
    store.reset_engine(); store.init_db()

    def seed(sid, action, tags):
        now = datetime.now(timezone.utc).isoformat()
        rec = {"call_sid": sid, "clinic_id": "theorem", "start_utc": now, "end_utc": now,
               "duration_s": 120, "success": False, "reason": "x", "caller_number": "+440000000000",
               "dialled_number": "", "final_state": "x", "collected": {}, "booking_confirmed": False,
               "acuity_booking_id": None, "transfer_attempted": False, "graceful_exit": False,
               "total_retries": 0, "slot_retry_counts": {}, "turn_count": 4, "tone": "neutral"}
        store.capture_call(rec, [{"role": "user", "text": "hi"}])
        store.save_judgement(sid, {"outcome": "no_booking", "quality_score": 2,
                                   "intent_resolved": False, "failure_tags": tags,
                                   "action_needed": action, "evidence": "x", "rubric_version": "v2"})
    try:
        seed("CArev1", "review", ["loop"])
        seed("CArev2", "review", ["booking_error"])
        seed("CAcb1", "callback", ["dead_end"])   # must NOT be in the review digest
        with patch("app.obs.alerts.send_sms", new=AsyncMock(return_value="SM1")) as mock_sms:
            rc = await digest._run(24)
        assert rc == 0
        mock_sms.assert_awaited_once()
        msg = mock_sms.await_args.kwargs["message"]
        assert "2 call(s)" in msg  # the 2 review calls only, not the callback
    finally:
        store.reset_engine()


async def test_digest_no_review_calls_sends_nothing(tmp_path, monkeypatch):
    from app import config
    from app.obs import store
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 'e.db'}")
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    monkeypatch.setattr(config, "OBS_ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "OBS_ALERT_SMS_TO", "+440000000000")
    store.reset_engine(); store.init_db()
    try:
        with patch("app.obs.alerts.send_sms", new=AsyncMock(return_value="SM1")) as mock_sms:
            rc = await digest._run(24)
        assert rc == 0
        mock_sms.assert_not_awaited()
    finally:
        store.reset_engine()


# ---------------------------------------------------------------------------
# Email digest
# ---------------------------------------------------------------------------

def test_emailer_is_configured(monkeypatch):
    """Needs BOTH a recipient and the shared sender's SMTP creds."""
    _smtp_configured(monkeypatch)
    monkeypatch.setattr(config, "OBS_DIGEST_EMAIL_TO", "")
    assert emailer.is_configured() is False        # recipient missing

    monkeypatch.setattr(config, "OBS_DIGEST_EMAIL_TO", "owner@example.com")
    assert emailer.is_configured() is True

    _smtp_unconfigured(monkeypatch)
    assert emailer.is_configured() is False        # transport missing


def test_emailer_delegates_to_the_shared_notifications_sender(monkeypatch):
    """Must NOT re-implement SMTP: it routes through app/notifications/email.py, so
    this service has exactly one set of SMTP credentials."""
    _smtp_configured(monkeypatch)
    with patch("app.notifications.email.send_email", new=AsyncMock(return_value=True)) as mock_send:
        ok = emailer.send_email("subj", "body")
    assert ok is True
    mock_send.assert_awaited_once()
    kwargs = mock_send.await_args.kwargs
    assert kwargs["to"] == "owner@example.com"
    assert kwargs["subject"] == "subj"
    assert kwargs["text"] == "body"


def test_emailer_noop_when_no_recipient(monkeypatch):
    monkeypatch.setattr(config, "OBS_DIGEST_EMAIL_TO", "")
    assert emailer.send_email("s", "b") is False


def test_emailer_swallows_sender_failure(monkeypatch):
    """A failed send must never raise — a scheduled run cannot be allowed to crash."""
    _smtp_configured(monkeypatch)
    with patch("app.notifications.email.send_email",
               new=AsyncMock(side_effect=RuntimeError("smtp down"))):
        assert emailer.send_email("s", "b") is False


def test_build_email_lists_calls_worst_first():
    calls = [
        {"call_sid": "CAa", "quality_score": 3, "failure_tags": ["loop"], "evidence": "e1"},
        {"call_sid": "CAb", "quality_score": 1, "failure_tags": ["dead_end"], "evidence": "e2"},
    ]
    subject, body = digest.build_email(calls, 24)
    assert "2 call(s)" in subject
    # worst (score 1) listed before score 3
    assert body.index("CAb") < body.index("CAa")
    assert "python -m app.obs.replay" in body


async def test_digest_prefers_email_when_configured(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from app.obs import store
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 'm.db'}")
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    monkeypatch.setattr(config, "OBS_ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "OBS_ALERT_SMS_TO", "+440000000000")
    _smtp_configured(monkeypatch)
    store.reset_engine(); store.init_db()
    now = datetime.now(timezone.utc).isoformat()
    rec = {"call_sid": "CArev", "clinic_id": "theorem", "start_utc": now, "end_utc": now,
           "duration_s": 60, "success": False, "reason": "x", "caller_number": "+440000000000",
           "dialled_number": "", "final_state": "x", "collected": {}, "booking_confirmed": False,
           "acuity_booking_id": None, "transfer_attempted": False, "graceful_exit": False,
           "total_retries": 0, "slot_retry_counts": {}, "turn_count": 4, "tone": "neutral"}
    store.capture_call(rec, [{"role": "user", "text": "hi"}])
    store.save_judgement("CArev", {"outcome": "no_booking", "quality_score": 2,
                                   "intent_resolved": False, "failure_tags": ["loop"],
                                   "action_needed": "review", "evidence": "x", "rubric_version": "v2"})
    try:
        with patch("app.obs.emailer.send_email", return_value=True) as mock_email, \
             patch("app.obs.alerts.send_sms", new=AsyncMock(return_value="SM1")) as mock_sms:
            rc = await digest._run(24)
        assert rc == 0
        mock_email.assert_called_once()      # emailed
        mock_sms.assert_not_awaited()        # NOT texted
    finally:
        store.reset_engine()


# ---------------------------------------------------------------------------
# Whole-system daily email: totals over every call, not just the review ones
# ---------------------------------------------------------------------------

def _judged(sid, score, action, booked=False, tags=None):
    return {"call_sid": sid, "clinic_id": "theorem", "quality_score": score,
            "action_needed": action, "booking_confirmed": booked,
            "failure_tags": tags or [], "evidence": "", "outcome": "x"}


def test_build_email_totals_cover_every_call_not_just_review():
    calls = [
        _judged("CAgood1", 5, "none", booked=True),
        _judged("CAgood2", 4, "none", booked=True),
        _judged("CAbad", 1, "review", tags=["loop"]),
    ]
    subject, body = digest.build_email(calls, 24)
    # Subject counts the whole window, and flags how many need attention.
    assert "3 call(s)" in subject
    assert "1 to review" in subject
    # Totals header reports volume/bookings/mean across all three.
    assert "Calls:" in body and "3" in body
    assert "Booked:" in body
    # Every call is listed, not only the review one.
    for sid in ("CAgood1", "CAgood2", "CAbad"):
        assert sid in body


def test_build_email_still_breaks_out_review_calls():
    calls = [_judged("CAgood", 5, "none", booked=True),
             _judged("CAbad", 1, "review", tags=["loop"])]
    _, body = digest.build_email(calls, 24)
    assert "Calls to review" in body
    # the review call appears in its own section above the full listing
    assert body.index("Calls to review") < body.index("All calls")


def test_build_email_empty_window_still_renders():
    """A zero-call day must still produce an email — silence should mean broken."""
    subject, body = digest.build_email([], 24)
    assert "0 call(s)" in subject
    assert "Calls:" in body


def test_callback_calls_are_never_omitted_from_the_daily_report():
    """A callback call is NOT a 'review' call (judge.needs_review excludes it), so it
    must be surfaced explicitly — a report that dropped the day's worst call while
    saying nothing needed a human would be actively misleading."""
    calls = [_judged("CAgood", 5, "none", booked=True),
             _judged("CAurgent", 1, "callback", tags=["missed_escalation"])]
    subject, body = digest.build_email(calls, 24)
    assert "1 callback(s)" in subject
    assert "NEEDED A CALLBACK" in body
    assert "CAurgent" in body
    assert "<-- CALLBACK" in body
    # and it must NOT claim the window was clean
    assert "Nothing needed a human this window." not in body


def test_clean_window_says_so():
    calls = [_judged("CAgood", 5, "none", booked=True)]
    _, body = digest.build_email(calls, 24)
    assert "Nothing needed a human this window." in body
    assert "NEEDED A CALLBACK" not in body


async def test_digest_emails_even_when_nothing_to_review(tmp_path, monkeypatch):
    """A clean day still sends the daily email (unlike the SMS fallback)."""
    from datetime import datetime, timezone
    from app.obs import store
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 'clean.db'}")
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    _smtp_configured(monkeypatch)
    store.reset_engine(); store.init_db()
    now = datetime.now(timezone.utc).isoformat()
    rec = {"call_sid": "CAok", "clinic_id": "theorem", "start_utc": now, "end_utc": now,
           "duration_s": 90, "success": True, "reason": "", "caller_number": "+440000000000",
           "dialled_number": "", "final_state": "done", "collected": {}, "booking_confirmed": True,
           "acuity_booking_id": "1", "transfer_attempted": False, "graceful_exit": True,
           "total_retries": 0, "slot_retry_counts": {}, "turn_count": 8, "tone": "neutral"}
    store.capture_call(rec, [{"role": "user", "text": "hi"}])
    store.save_judgement("CAok", {"outcome": "booked", "quality_score": 5,
                                  "intent_resolved": True, "failure_tags": [],
                                  "action_needed": "none", "evidence": "", "rubric_version": "v2"})
    try:
        with patch("app.obs.emailer.send_email", return_value=True) as mock_email:
            rc = await digest._run(24)
        assert rc == 0
        mock_email.assert_called_once()
        subject = mock_email.call_args.args[0]
        assert "1 call(s)" in subject
    finally:
        store.reset_engine()


async def test_digest_sms_fallback_stays_silent_with_nothing_to_review(tmp_path, monkeypatch):
    """The SMS channel must NOT gain a daily heartbeat — only email always sends."""
    from datetime import datetime, timezone
    from app.obs import store
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 'q.db'}")
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    monkeypatch.setattr(config, "OBS_ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "OBS_ALERT_SMS_TO", "+440000000000")
    monkeypatch.setattr(config, "OBS_DIGEST_EMAIL_TO", "")  # force the SMS fallback
    _smtp_unconfigured(monkeypatch)
    store.reset_engine(); store.init_db()
    now = datetime.now(timezone.utc).isoformat()
    rec = {"call_sid": "CAfine", "clinic_id": "theorem", "start_utc": now, "end_utc": now,
           "duration_s": 90, "success": True, "reason": "", "caller_number": "+440000000000",
           "dialled_number": "", "final_state": "done", "collected": {}, "booking_confirmed": True,
           "acuity_booking_id": "1", "transfer_attempted": False, "graceful_exit": True,
           "total_retries": 0, "slot_retry_counts": {}, "turn_count": 8, "tone": "neutral"}
    store.capture_call(rec, [{"role": "user", "text": "hi"}])
    store.save_judgement("CAfine", {"outcome": "booked", "quality_score": 5,
                                    "intent_resolved": True, "failure_tags": [],
                                    "action_needed": "none", "evidence": "", "rubric_version": "v2"})
    try:
        with patch("app.obs.alerts.send_sms", new=AsyncMock(return_value="SM1")) as mock_sms:
            rc = await digest._run(24)
        assert rc == 0
        mock_sms.assert_not_awaited()
    finally:
        store.reset_engine()


# ---------------------------------------------------------------------------
# Inlined redacted transcripts (OBS_DIGEST_INCLUDE_TRANSCRIPTS)
# ---------------------------------------------------------------------------

def _call_with_transcript():
    return {
        "call_sid": "CAx", "quality_score": 1, "failure_tags": ["wrong_info"],
        "evidence": "quoted the wrong price", "collected": {"name": "Quentin Rock"},
        "transcript": [
            {"role": "user", "text": "Hi it's Quentin Rock, my number's 07700 900123"},
            {"role": "assistant", "text": "Thanks Quentin, an assessment is forty five pounds."},
        ],
    }


def test_build_email_omits_transcript_by_default(monkeypatch):
    monkeypatch.setattr(config, "OBS_DIGEST_INCLUDE_TRANSCRIPTS", False)
    _, body = digest.build_email([_call_with_transcript()], 24)
    assert "transcript (redacted)" not in body


def test_build_email_includes_redacted_transcript_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "OBS_DIGEST_INCLUDE_TRANSCRIPTS", True)
    _, body = digest.build_email([_call_with_transcript()], 24)
    # the transcript is present...
    assert "transcript (redacted)" in body
    assert "an assessment is forty five pounds" in body
    # ...but the phone and known name are gone, replaced by placeholders.
    assert "07700 900123" not in body and "07700900123" not in body
    assert "[PHONE]" in body
    assert "Quentin" not in body and "Rock" not in body
    assert "[NAME]" in body


def test_transcript_lines_withholds_on_pii_leak(monkeypatch):
    # If redaction somehow leaves a phone/email, the transcript is withheld, not emitted.
    monkeypatch.setattr(digest.redact, "redact_transcript",
                        lambda turns, names: [{"role": "user", "text": "call 07700 900123"}])
    lines = digest._transcript_lines({"transcript": [{"role": "user", "text": "x"}]})
    assert lines == ["      (transcript withheld — redaction check failed)"]
