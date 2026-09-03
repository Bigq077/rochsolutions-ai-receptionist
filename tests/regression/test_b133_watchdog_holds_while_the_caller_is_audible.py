"""
Regression: the re-ask fired while the caller was drawing breath to answer.

B-133 — CA51bb75fe9cfa8560bd107a2adee0639f, Theorem, 3 September 2026,
build 4eda31f3c8c9.

    17:24:18.162  "Is this for our Awlstuh or Redditch clinic?"  (playout ends)
    17:24:24.180  WATCHDOG_FIRE q_gen=4 attempt=#1
                  voice_gap=0.0s voiced_since_prompt=True
    17:24:25.328  barge-in: partial='um'          <- the caller's answer
    17:24:26.127  ack 'Sorry — go ahead.'
    17:24:30.528  FINAL: 'uh the alcester clinic'

The fire line logged its own defect. `voice_gap=0.0s` means the caller was
audible AT THAT INSTANT and `voiced_since_prompt=True` means it began after
Susie stopped talking. The re-ask landed on top of his answer, cost a barge-in
teardown and an apology, and he named the clinic ~6s later than he had tried
to. Reported as "why did it make me repeat Alcester".

── WHY THE EXISTING GUARDS ALL MISSED ─────────────────────────────────────────
Phase 3 already holds for speech, and does it well:

  * WATCHDOG_ENGAGEMENT_HOLD   — last_engagement_at within 2.0s
  * prompt_speech_detected     — suppress, with a 4.0s cap and re-arm

Every one of them keys on an STT EVENT. At 17:24:24.180 no partial had arrived
— the first was 1.1s later — so `prompt_speech_detected` was False and
`last_engagement_at` still pointed at the previous turn. The gap is exactly one
window: **voice energy present, nothing transcribed yet.** Everything after an
STT event was already covered, which is why this fix is one guard and not a
redesign.

── WHY THIS IS NOT THE "RECOVERY OVER TRIGGER" MISTAKE ────────────────────────
That rule comes from the barge-in family, where a stricter trigger makes Susie
UN-INTERRUPTIBLE — she talks over someone who wants the floor. This is the
other axis: she is silent and deciding whether to start. Holding makes her more
patient, not less interruptible. It is the missing sibling of
WATCHDOG_DTMF_HOLD, one modality over.

── THE CAP IS THE SAFETY ARGUMENT ─────────────────────────────────────────────
`voice_gap` is raw inbound frame energy, so a car, a television or a busy
waiting room reads as voiced indefinitely. An unbounded hold would suppress the
watchdog for that caller entirely — a dead call, strictly worse than the defect
being fixed. DTMF may hold uncapped because a keypress is unambiguous; energy
is not.

The cap has a meaning rather than a tuned value: the hold only has to bridge
STT's own latency, because once a partial lands the transcript path cancels the
watchdog anyway. On this call that gap was 1.2s. If 1.5s of "voice" produces no
transcript, it was not speech — so the cap FIRES rather than extends.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import (
    _VOICE_HOLD_CAP_S,
    _VOICE_HOLD_GAP_S,
    _VOICE_HOLD_STEP_S,
    _voice_hold_verdict,
)


# The live call's own numbers.
LIVE_VOICE_GAP = 0.0
STT_PARTIAL_LAG_S = 1.2      # 17:24:24.18 fire -> 17:24:25.33 first partial


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_caller_mid_answer_is_not_talked_over():
    assert _voice_hold_verdict(True, LIVE_VOICE_GAP, 0.0) == "hold"


def test_the_hold_outlasts_stt_partial_latency():
    """The cap must cover the real gap between energy and the first partial,
    or the fix does not reach the call it was written for."""
    # Bounded, deliberately. An unbounded `while ... == "hold"` HANGS when the
    # cap regresses instead of failing, and a hanging test is worse than a red
    # one -- it stalls the suite and reads as an environment problem. Found by
    # neutering the cap, which is the whole point of neutering.
    held = 0.0
    for _ in range(200):
        if _voice_hold_verdict(True, LIVE_VOICE_GAP, held) != "hold":
            break
        held += _VOICE_HOLD_STEP_S
    else:
        pytest.fail("the hold never expires — the cap has regressed")
    assert held >= STT_PARTIAL_LAG_S, (
        "the budget expires before STT could plausibly have reported the "
        "utterance — %.2fs held vs %.2fs observed" % (held, STT_PARTIAL_LAG_S)
    )


# ---------------------------------------------------------------------------
# The guards. Every one of these is a way to turn the fix into a worse defect.
# ---------------------------------------------------------------------------
def test_true_silence_still_fires():
    """THE guard. The watchdog exists to rescue a silent call; a hold that
    swallowed genuine silence would be a far worse defect than the one fixed."""
    assert _voice_hold_verdict(False, float("inf"), 0.0) == "fire"


def test_a_noisy_line_is_bounded_and_fires():
    """A car, a TV, a waiting room: energy never stops. The watchdog must
    still arrive. This is the failure mode the fix INTRODUCES, so it is the
    one that has to be pinned."""
    held = 0.0
    for _ in range(200):
        verdict = _voice_hold_verdict(True, 0.0, held)
        if verdict != "hold":
            break
        held += _VOICE_HOLD_STEP_S
    else:
        pytest.fail("a permanently voiced line held forever — the watchdog is dead")
    assert verdict == "cap"
    assert held <= _VOICE_HOLD_CAP_S + _VOICE_HOLD_STEP_S


def test_the_total_hold_is_small_against_the_grace():
    """Worst case must stay a pause, not a wait. The shortest grace in play on
    the live call was 6.0s."""
    assert _VOICE_HOLD_CAP_S <= 2.0
    assert _VOICE_HOLD_STEP_S < _VOICE_HOLD_CAP_S


def test_echo_during_her_own_turn_does_not_hold():
    """The 2026-07-25 lesson, enforced rather than re-litigated. A speakerphone
    returns Susie's voice into the caller's mic; `voiced_since_prompt` is False
    for it because it compares against _tts_audio_done_at + _ECHO_TAIL_SEC. If
    this ever returns "hold", the echo defect has been reopened through the
    back door."""
    assert _voice_hold_verdict(False, 0.0, 0.0) == "fire"


def test_a_pause_between_words_does_not_hold_forever():
    """The hold tracks CURRENT audibility, not "spoke at some point". A caller
    who has stopped gets the re-ask at the normal time."""
    assert _voice_hold_verdict(True, _VOICE_HOLD_GAP_S + 0.01, 0.0) == "fire"
    assert _voice_hold_verdict(True, 2.0, 0.0) == "fire"


def test_an_unusable_probe_reading_fires():
    """A probe that cannot answer must never be able to silence the watchdog.
    inf and NaN both have to fall through to firing — NaN especially, because
    every comparison against it is False and the naive `if gap > X: fire`
    spelling would hold instead."""
    assert _voice_hold_verdict(True, float("inf"), 0.0) == "fire"
    assert _voice_hold_verdict(True, float("nan"), 0.0) == "fire"


def test_the_guard_is_actually_wired_into_the_watchdog():
    """The verdict is pure and easy to test; the risk is that it is never
    called. Pins the call site so a passing suite cannot hide a dead fix."""
    import inspect

    from app.media_streams import connection

    src = inspect.getsource(connection)
    assert "_voice_hold_verdict(" in src.replace("def _voice_hold_verdict(", "")
    assert "WATCHDOG_VOICE_HOLD" in src
    assert "WATCHDOG_VOICE_HOLD_CAP" in src


def test_the_cap_is_reported_loudly():
    """The counter this fix ships with. Repeated caps mean a noise floor
    problem, not a slow caller, and that has to be visible in the log without
    a code change."""
    import inspect

    from app.media_streams import connection

    src = inspect.getsource(connection)
    cap_at = src.index("WATCHDOG_VOICE_HOLD_CAP")
    window = src[max(0, cap_at - 400):cap_at]
    assert "logger.warning" in window, "the cap must warn, not inform"
