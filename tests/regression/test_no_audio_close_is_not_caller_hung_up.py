"""
Regression: a call SUSIE ended must not be recorded as the caller hanging up.

Call CAe2120b (theorem_v3, 2026-08-06 23:17). The caller reached the booking
readback — "So that's John Smith, Monday the 10th of August at three in the
afternoon… shall I go ahead and book that in?" — and then went silent. The
watchdog re-asked, the dead-air safety net re-asked twice more and played its
sign-off ("I'm not able to hear you at the moment — feel free to call back and
we'll get that sorted for you."), and SUSIE closed the call.

The teardown ladder in connection._cleanup ended at `caller_hung_up` for
anything that was not booked, transferred or a graceful exit, so the durable
record said the caller hung up. That string is not just a log label: it is
interpolated verbatim into the obs judge prompt ("flow-reported reason: {reason}",
app/obs/judge.py), and the judge's free-text `evidence` becomes the body of the
operator CALL BACK SMS. The operator was texted "…and the caller hung up,
leaving booking_confirmed False" about a call the caller never hung up on.

The safety net already sets session["no_audio_close"] on its second fire. The
fix is to read it in the ladder rather than let it fall through.
"""
import pytest

from app.media_streams.connection import derive_call_outcome


def test_the_safety_net_close_is_not_reported_as_a_hangup():
    """The CAe2120b session shape: silence after the booking offer, safety net
    closed the call. Nothing was booked and no transfer was attempted."""
    success, reason = derive_call_outcome({
        "booking_confirmed": None,
        "confirmation_sms_sent": None,
        "transfer_attempted": None,
        "no_audio_close": True,
    })
    assert reason != "caller_hung_up", (
        "Susie ended this call after the dead-air safety net gave up; recording "
        "it as caller_hung_up is what made the obs judge tell the operator the "
        "caller hung up"
    )
    assert reason == "no_audio_close"
    assert success is False


def test_a_real_hangup_is_still_a_hangup():
    """The guard must not swallow the genuine case: no safety-net close flag,
    nothing achieved — that IS the caller hanging up."""
    success, reason = derive_call_outcome({
        "booking_confirmed": None,
        "no_audio_close": None,
    })
    assert (success, reason) == (False, "caller_hung_up")


@pytest.mark.parametrize(
    "session, expected",
    [
        # A booking that also tripped the dead-air net still reports as booked:
        # what happened to the caller outranks how the line ended.
        ({"booking_confirmed": True, "no_audio_close": True}, "booked"),
        ({"transfer_attempted": True, "no_audio_close": True}, "transferred"),
        ({"graceful_exit": True, "no_audio_close": True}, "graceful_exit"),
    ],
)
def test_no_audio_close_never_outranks_a_real_outcome(session, expected):
    _, reason = derive_call_outcome(session)
    assert reason == expected


def test_the_reason_reaches_the_judge_prompt():
    """The whole point of the label. If build_prompt stops carrying `reason`,
    this fix is inert and the judge is guessing again."""
    from app.obs.judge import build_prompt

    _, reason = derive_call_outcome({"no_audio_close": True})
    prompt = build_prompt({
        "call_sid": "CAe2120b",
        "clinic_id": "theorem_v3",
        "reason": reason,
        "booking_confirmed": None,
        "transfer_attempted": None,
        "duration_s": 123,
        "turn_count": 10,
        "transcript": [{"role": "user", "text": "yeah john smith"}],
    })
    assert "no_audio_close" in prompt
    assert "caller_hung_up" not in prompt
