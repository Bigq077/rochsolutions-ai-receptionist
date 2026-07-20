"""Tests for app.obs.digest — the once-a-day review summary."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app import config
from app.obs import digest, emailer


def _smtp_configured(monkeypatch):
    monkeypatch.setattr(config, "OBS_DIGEST_EMAIL_TO", "owner@example.com")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_PORT", 587)
    monkeypatch.setattr(config, "SMTP_USER", "susie@example.com")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(config, "SMTP_FROM", "susie@example.com")


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
    monkeypatch.setattr(config, "OBS_DIGEST_EMAIL_TO", "")
    assert emailer.is_configured() is False
    _smtp_configured(monkeypatch)
    assert emailer.is_configured() is True


def test_emailer_send_uses_smtp(monkeypatch):
    _smtp_configured(monkeypatch)
    fake_server = MagicMock()
    smtp_ctx = MagicMock()
    smtp_ctx.__enter__.return_value = fake_server
    with patch("app.obs.emailer.smtplib.SMTP", return_value=smtp_ctx) as smtp_cls:
        ok = emailer.send_email("subj", "body")
    assert ok is True
    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=20)
    fake_server.starttls.assert_called_once()
    fake_server.login.assert_called_once_with("susie@example.com", "app-password")
    fake_server.send_message.assert_called_once()


def test_emailer_noop_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "OBS_DIGEST_EMAIL_TO", "")
    monkeypatch.setattr(config, "SMTP_HOST", "")
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
