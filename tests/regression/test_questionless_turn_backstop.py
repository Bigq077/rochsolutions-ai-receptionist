# tests/regression/test_questionless_turn_backstop.py
"""P1 (2026-07-25) — a turn that asked nothing armed nothing, and the only
recovery threw the booking away.

Incident
--------
jv_v1, 2026-07-25 02:30. Verbatim:

    02:30:32.791  Susie: "...Would you like to book an assessment?"   watchdog armed
    02:30:34.891  caller: 'please'
    02:30:36.050  Susie: "Right —"          <- len=7, the ENTIRE reply
    02:30:36.776  TTS done. NO watchdog armed.
    02:30:47.388  safety net: "Sorry, I can't quite hear you — how can I help today?"
    02:31:04.818  caller hung up.  outcome=abandoned, dur=60s

Inbound audio was perfect throughout (`lost_total=0 inbound_audio=flowing
media_frames=2988`), so nothing was mis-heard. The call died because Susie
spoke without asking anything and no timer was armed to notice.

Why "Right —"
-------------
The prompt MANDATES it: "acknowledge simply: 'Right —' and NOTHING ELSE ...
The system injects the next question and handles everything else
automatically." The LLM obeyed exactly. The system's injection is gated on a
CTA-affirm regex that matches "please do"/"please book" but not bare "please",
so `booking_flow_active` was never set and the timing question never fired.
The LLM was forbidden from covering and its replacement never spoke.

Root cause
----------
Spec W armed the watchdog only when the turn's last sentence contained a
question (`_has_question_w`) or the location ladder was active
(`_loc_active_w`). `_loc_active_w` is THE SAME BUG, patched for the location
path alone — its own comment cites the 2026-06-12 stress test where the
question-only guard skipped arming and "the booking/location context was
lost".

Fix
---
When a turn asks nothing but a question is still outstanding, re-arm the timer
against that outstanding question and mutate nothing else. The whole
detector-gate family now fails OPEN into a normal re-ask instead of fail
SILENT into dead air.
"""

import asyncio
import types

import pytest

from app.media_streams import connection as conn


_REAL_CONTAINS_Q = conn.SilenceHandler._prompt_contains_question


class StubSH:
    """Silence handler exposing exactly what the Spec W block touches.

    `_prompt_contains_question` is the REAL implementation so the test cannot
    drift from production classification.
    """

    _prompt_contains_question = _REAL_CONTAINS_Q
    # Borrowed from production too — a divergent copy here would let the test
    # pass while the real classification changed underneath it.
    _WATCHDOG_QUESTION_SIGNALS = conn.SilenceHandler._WATCHDOG_QUESTION_SIGNALS

    def __init__(self, last_question: str = "") -> None:
        self.last_question = last_question
        self._q_gen = 3
        self._no_input_watchdog_task = None
        self._watchdog_has_retired = False
        self._llm_busy = False
        self.currently_reasking = False
        self._cancelled = False
        self.reask_count = 7            # sentinel — must survive the backstop
        self._no_input_reask_count = 2  # sentinel — the per-q_gen cap
        self._last_question_set_at = 0.0
        self.restart_calls = 0

    def _restart_timer(self) -> None:
        self.restart_calls += 1

    def on_tts_finished(self, text, chunk_started_at=0.0) -> None:
        pass


class StubQueue:
    def empty(self) -> bool:
        return True

    def qsize(self) -> int:
        return 0


def _handler(sh: StubSH, session=None):
    """Minimal handler with the REAL _delayed_tts_finished bound to it."""
    h = types.SimpleNamespace(
        _tts_gen=0,
        _silence_handler=sh,
        _tts_chunks_completed=set(),
        _tts_expected_final_seq=1,
        _current_chunk_seq=0,
        _tts_pending_terminal=0,
        _tts_pending_terminal_text="",
        _tts_pending_terminal_chunk_start_ts=0.0,
        tts_text_queue=StubQueue(),
        session=session if session is not None else {},
        _tts_audio_done_at=0.0,
        _last_audio_or_transcript_ts=0.0,
        _clinical_response_active=True,
    )
    # NOTE: deliberately no `_flow` attribute — the flow-complete guard is
    # `hasattr(self, "_flow")`, so omitting it keeps the method running.
    h._delayed_tts_finished = conn.WebSocketCallHandler._delayed_tts_finished.__get__(h)
    return h


async def _fire(h, text: str):
    await h._delayed_tts_finished(0.0, text, gen=0, chunk_started_at=0.0,
                                  q_gen_at_start=-1, chunk_seq=1)


# ---------------------------------------------------------------------------
# The regression.
# ---------------------------------------------------------------------------
async def test_questionless_turn_rearms_against_outstanding_question():
    """THE INCIDENT: "Right —" after an unanswered booking CTA."""
    sh = StubSH("Would you like to book an assessment so he can take a proper look?")
    await _fire(_handler(sh), "Right —")

    assert sh.restart_calls == 1, (
        "a turn that asked nothing left no timer armed — this is the 10.6s of "
        "dead air that ended the 02:30 call"
    )


async def test_backstop_does_not_overwrite_the_outstanding_question():
    """Context preservation — the reason this beats the safety net.

    The safety net re-anchors to "how can I help today?", discarding the
    booking. Replaying the real outstanding question keeps it.
    """
    cta = "Would you like to book an assessment so he can take a proper look?"
    sh = StubSH(cta)
    await _fire(_handler(sh), "Right —")

    assert sh.last_question == cta, (
        "backstop overwrote last_question with the question-less turn — the "
        "re-ask would replay 'Right —', which is not a question"
    )


async def test_backstop_preserves_the_per_qgen_reask_cap():
    """The backstop must not hand the caller an unbounded re-ask loop.

    `_restart_timer` caps audible re-asks at one per question generation using
    `_no_input_reask_count` and `_q_gen`. Resetting either — as the normal arm
    path does, because it has a genuinely NEW question — would defeat that cap
    for a turn that asked nothing new.
    """
    sh = StubSH("What's your full name?")
    await _fire(_handler(sh), "Right —")

    assert sh._no_input_reask_count == 2, "backstop reset the re-ask counter"
    assert sh.reask_count == 7, "backstop reset reask_count"
    assert sh._q_gen == 3, "backstop advanced q_gen — defeats the per-q_gen cap"


# ---------------------------------------------------------------------------
# It must stay quiet when there is genuinely nothing to re-ask.
# ---------------------------------------------------------------------------
async def test_no_arm_when_no_question_is_outstanding():
    """Nothing outstanding → arming would invent a prompt out of nowhere.

    `_restart_timer` also starts the W1/W2/W3 silence cascade, which has no
    empty-question guard of its own, so this must be gated here.
    """
    sh = StubSH("")
    await _fire(_handler(sh), "It's £75.")
    assert sh.restart_calls == 0


async def test_no_arm_when_outstanding_text_is_not_a_question():
    """An affirmation is not something you can re-ask."""
    sh = StubSH("Of course —")
    await _fire(_handler(sh), "Right —")
    assert sh.restart_calls == 0


# ---------------------------------------------------------------------------
# The pre-existing path must be untouched.
# ---------------------------------------------------------------------------
async def test_turn_with_a_question_still_takes_the_original_path():
    """A real question still overwrites last_question and resets counters."""
    sh = StubSH("Would you like to book an assessment?")
    await _fire(_handler(sh), "Lovely. What's your full name?")

    assert sh.restart_calls == 1
    assert sh.last_question == "What's your full name?", (
        "the normal arm path must still store the NEW question"
    )
    assert sh._no_input_reask_count == 0, "new question must reset the counter"
    assert sh._q_gen == 4, "new question must advance q_gen"


@pytest.mark.parametrize(
    "guard,value",
    [
        ("_llm_busy", True),
        ("currently_reasking", True),
        ("_cancelled", True),
        ("_watchdog_has_retired", True),
    ],
)
async def test_existing_spec_w_guards_still_block_the_backstop(guard, value):
    """The backstop lives INSIDE the Spec W guards, not around them."""
    sh = StubSH("Would you like to book an assessment?")
    setattr(sh, guard, value)
    await _fire(_handler(sh), "Right —")
    assert sh.restart_calls == 0, f"backstop ran despite {guard}={value}"


async def test_live_watchdog_is_not_disturbed():
    """If a watchdog is already running, leave its deadline alone."""
    sh = StubSH("Would you like to book an assessment?")

    class _LiveTask:
        def done(self):
            return False

    sh._no_input_watchdog_task = _LiveTask()
    await _fire(_handler(sh), "Right —")
    assert sh.restart_calls == 0


async def test_location_ladder_arm_is_unchanged():
    """`_loc_active_w` was the narrow precursor of this fix — keep it working."""
    sh = StubSH("")
    h = _handler(sh, session={"v3_location_q_active": True})
    await _fire(h, "We've got two locations, so I want to check the right one.")
    assert sh.restart_calls == 1
    assert sh.last_question == conn._LOC_RUNG1_OPEN


# ---------------------------------------------------------------------------
# T-4 — bare acknowledgement mid-booking with nothing behind it.
#
# CA2087528410, 2026-08-24 00:03:57. Caller opened booking himself, straight
# after a pricing answer: "that's all good can i book an appointment then mate".
# The prompt mandates the bare stub "Right —" on booking start because "the
# system injects the next question" — but the injector is gated on
# `_cta_affirm = _bot_had_cta and _utt_is_affirm`, which requires SUSIE'S
# PREVIOUS TURN to have carried a booking CTA. A caller-initiated booking has no
# prior CTA by construction, so the gate could not match, nothing was injected,
# and the call sat in silence for SEVEN seconds until the caller said "hello".
#
# T-3 correctly declines to help here: "Right —" is 2 words, and its open
# invitation would walk the caller out of the booking (the 02:30 incident).
# The recovery has to be the booking flow's OWN next question.
# ---------------------------------------------------------------------------
async def test_bare_ack_mid_booking_arms_the_flows_next_question():
    sh = StubSH(last_question="")          # nothing outstanding — backstop can't fire
    h = _handler(sh, session={"clinic_id": "jv_v1"})
    h.booking_flow_active = True

    await _fire(h, "Right —")

    assert sh.restart_calls == 1, "T-4 must re-arm the timer, not leave dead air"
    assert sh.last_question, "a question must be armed"
    assert sh._prompt_contains_question(sh.last_question), (
        "what T-4 arms must classify as a question, or _restart_timer's "
        "Spec Z Gate 2 will discard it"
    )
    assert "anything else" not in sh.last_question.lower(), (
        "must NOT be the T-3 open invitation — that walks the caller out of "
        "the booking mid-flow (the 02:30 incident)"
    )


async def test_bare_ack_outside_booking_still_stays_silent():
    """T-4 is scoped to an active booking. Outside one, nothing changes."""
    sh = StubSH(last_question="")
    h = _handler(sh, session={"clinic_id": "jv_v1"})
    h.booking_flow_active = False

    await _fire(h, "Right —")

    assert sh.restart_calls == 0
    assert sh.last_question == ""


async def test_t4_does_not_pre_empt_the_backstop():
    """An outstanding question still wins — T-4 is the last resort, not the first."""
    sh = StubSH(last_question="What's the appointment for?")
    h = _handler(sh, session={"clinic_id": "jv_v1"})
    h.booking_flow_active = True

    await _fire(h, "Right —")

    assert sh.last_question == "What's the appointment for?", (
        "the BACKSTOP replays the REAL outstanding question; T-4 must not "
        "overwrite it"
    )


async def test_t4_does_not_pre_empt_t3_for_a_substantive_answer():
    """A 4+ word answer keeps the T-3 nudge even while booking is active."""
    sh = StubSH(last_question="")
    h = _handler(sh, session={"clinic_id": "jv_v1"})
    h.booking_flow_active = True

    await _fire(h, "A sports massage is forty pounds for thirty minutes.")

    assert "anything else" in sh.last_question.lower()
