"""Tests for app.obs.digest — the once-a-day review summary."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.obs import digest


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
