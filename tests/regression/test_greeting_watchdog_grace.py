# tests/regression/test_greeting_watchdog_grace.py
"""
JV Bolton live call (2026-07-24, CA3e342642f57273e29e618074b87ba181) — Susie
talked over the caller 4.5 s after the opening greeting.

Live-call trace:
    18:03:07.496  [ms_watchdog] greeting_grace=6.0s
    18:03:07.496  [ms_watchdog] phone_confirm_grace=10.0s (flow_step=0)
    18:03:07.496  [ms_watchdog] greeting first-turn — wait set to 4.5s
    18:03:12.001  [ms_watchdog] WATCHDOG_FIRE q_gen=1 attempt=#1 state=GREETING
                  "Sorry, I didn't catch that — how can I help today?"
    ...
    18:03:39.731  caller: "i said i'd like to book an appointment as soon as
                           possible please"          <-- "I SAID"

Root cause: the first-turn override in `_no_input_watchdog` hard-set the wait
to 4.5 s — the SHORTEST window in the whole config, applied at the one moment
the caller has most to process (longest prompt of the call, unfamiliar voice,
before they have spoken at all).  Every other state was more patient:
greeting_grace 6.0, reason_grace 7.5, choice_grace 8.0, slot_selection 10.0.

Measured caller response gaps on that same call, from terminal tts_finished to
first partial: 1.6 / 2.5 / 2.7 / 3.5 / 11.8 s.  4.5 s sits inside that
distribution, so it clipped real callers as a matter of course.  The window is
also measured from ESTIMATED playout end (bytes_sent / 8000 Hz; Twilio mark
events are not used), so Twilio-side buffering eats into it further.

Fix: 4.5 -> 6.0, matching what the greeting_grace block itself already declares
correct for greeting states.  The override remains an explicit set rather than
a max() because its purpose is to beat the 10 s phone_confirm_grace that a
spurious flow_step==0 pins on the greeting turn.

These tests drive the real `_no_input_watchdog` coroutine and read the wait it
computes out of its own WATCHDOG_START log line, so they assert observable
behaviour rather than a source constant.
"""

import asyncio
import logging
import re

import pytest

from app.media_streams.connection import SilenceHandler

WATCHDOG_START_RE = re.compile(r"WATCHDOG_START q_gen=\d+ wait=([\d.]+)s")

# The greeting prompt and both no-input re-asks from the live call.  All three
# contain "how can I help", so all three take the first-turn override path.
GREETING_PROMPTS = [
    "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — "
    "how can I help today?",
    "Sorry, I didn't catch that — how can I help today?",
    "Sorry, I can't quite hear you — how can I help today?",
]


async def _computed_wait(caplog, session) -> float:
    """Arm the real watchdog against `session` and return the wait it chose.

    The task is cancelled as soon as WATCHDOG_START has been logged, so no
    re-ask is ever emitted and no TTS is queued.
    """
    handler = SilenceHandler(
        tts_text_queue=asyncio.Queue(),
        trigger_transfer_fn=lambda *a, **k: None,
        get_session=lambda: session,
    )
    task = asyncio.create_task(handler._no_input_watchdog(asyncio.get_event_loop().time(), 1))
    # _no_input_watchdog takes ownership only if it IS the registered task.
    handler._no_input_watchdog_task = task
    try:
        for _ in range(200):                      # ~2 s ceiling
            await asyncio.sleep(0.01)
            for rec in caplog.records:
                m = WATCHDOG_START_RE.search(rec.getMessage())
                if m:
                    return float(m.group(1))
        raise AssertionError("watchdog never logged WATCHDOG_START")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.fixture
def wd_caplog(caplog):
    caplog.set_level(logging.INFO, logger="app.media_streams.connection")
    return caplog


def _greeting_session(prompt: str) -> dict:
    """The session as it stood on the live call's greeting turn.

    state=GREETING and flow_step=0 are both taken from the real log: the
    greeting turn logged greeting_grace=6.0s AND phone_confirm_grace=10.0s,
    which is exactly the pair the override has to arbitrate between.
    """
    return {"state": "GREETING", "flow_step": 0, "last_bot_prompt": prompt}


@pytest.mark.parametrize("prompt", GREETING_PROMPTS)
async def test_greeting_window_is_at_least_six_seconds(wd_caplog, prompt):
    """The caller gets >= 6 s to answer the greeting, not 4.5 s."""
    wait = await _computed_wait(wd_caplog, _greeting_session(prompt))
    assert wait >= 6.0, (
        f"greeting window regressed to {wait}s for prompt {prompt[:50]!r}. "
        "4.5s was the shortest window in the config and cut the caller off "
        "mid-answer on JV Bolton 2026-07-24 ('I SAID I'd like to book…')."
    )


@pytest.mark.parametrize("prompt", GREETING_PROMPTS)
async def test_greeting_window_still_beats_phone_confirm_grace(wd_caplog, prompt):
    """The override must still win over the spurious 10 s phone_confirm_grace.

    flow_step==0 is a phone-confirm sentinel that also matches at call start on
    template_v1 clinics.  If the override ever became a max() the greeting would
    inherit 10 s and a genuinely silent line would sit unprompted that long.
    """
    wait = await _computed_wait(wd_caplog, _greeting_session(prompt))
    assert wait < 10.0, (
        f"greeting window is {wait}s — the first-turn override no longer beats "
        "phone_confirm_grace, so a silent line waits ~10s before any re-prompt."
    )


async def test_greeting_window_is_not_the_shortest_in_the_config(wd_caplog):
    """Sanity-check the ordering the incident inverted.

    The greeting is the longest prompt of the call and the caller's first
    exposure to the voice; it must not be less patient than a mid-call binary
    choice.  ASK_LOCATION's choice_grace (4.5 s) is the fastest legitimate
    state, so the greeting must be strictly more patient than that.
    """
    greeting = await _computed_wait(
        wd_caplog, _greeting_session(GREETING_PROMPTS[0])
    )
    wd_caplog.clear()
    ask_location = await _computed_wait(
        wd_caplog, {"state": "ASK_LOCATION", "last_bot_prompt": "Alcester or Redditch?"}
    )
    assert greeting > ask_location, (
        f"greeting window ({greeting}s) is no more patient than a mid-call "
        f"binary choice ({ask_location}s) — that inversion is the bug."
    )


async def test_non_greeting_prompt_is_unaffected(wd_caplog):
    """The override must stay scoped to the greeting phase.

    Nothing after the caller speaks contains "how can I help", so a normal
    booking question keeps its own state-derived timing.
    """
    wait = await _computed_wait(
        wd_caplog,
        {
            "state": "COLLECT_REASON",
            "last_bot_prompt": "What's been going on with your back?",
        },
    )
    assert wait == pytest.approx(7.5), (
        f"COLLECT_REASON reason_grace changed to {wait}s — the greeting "
        "override has leaked outside the greeting phase."
    )
