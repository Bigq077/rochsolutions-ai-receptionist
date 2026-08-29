"""A hold head is synthesised at conversational pace, not rushed.

WHY THIS EXISTS
---------------
Live call CAe9eba9192c50c500c95b7e7a2a187729 (2026-08-29, northgate, the first
call ever to hear a situational head). The words were right -- "On price -" then
the price, "On insurance -" then the answer -- and the owner's report was that
it "spoke too quickly compared to how Susie speaks".

A head is a ten-to-forty-character fragment synthesised on its own, seconds
before the reply it belongs to. ElevenLabs flash gets no sentence around it, so
at the call's default rate it comes out faster than the rest of the call. The
log line from that call shows it plainly: `len=10 speed=default` for "On price -"
and `len=132 speed=default` for the reply -- same rate, very different amount of
context to pace against.

Slower here costs nothing that matters: the head fills a wait that is already
happening. On that call's first turn `content_ttfa_ms` was 5,380, so the head
covered nearly five seconds of silence.
"""
from __future__ import annotations

import pytest

from app.hold_speech import HEADS, INTENT_HEADS, is_hold_head, render_intent_head
from app.media_streams.config import (
    ELEVENLABS_HEAD_SPEED,
    ELEVENLABS_PHONE_SPEED,
    ELEVENLABS_SPEED,
)

EM = "—"


def test_a_head_is_slower_than_ordinary_speech():
    assert ELEVENLABS_HEAD_SPEED < ELEVENLABS_SPEED, (
        "the head is synthesised alone with no sentence to pace it against, so "
        "at the call default it is audibly faster than the rest of Susie"
    )


def test_a_head_is_not_as_slow_as_a_phone_number():
    """The phone rate is careful articulation the caller checks digit by digit.
    A head is ordinary conversation and should sound like it."""
    assert ELEVENLABS_HEAD_SPEED > ELEVENLABS_PHONE_SPEED


@pytest.mark.parametrize("intent", list(INTENT_HEADS))
def test_every_intent_head_is_recognised_as_a_head(intent):
    """The pacing is worthless if the predicate does not match what is spoken.
    Built from the pools at import, so a reworded head cannot fall out of it."""
    assert is_hold_head(render_intent_head(intent, subject="Tuesday"))
    assert is_hold_head(render_intent_head(intent, subject=""))


@pytest.mark.parametrize("kind", list(HEADS))
def test_every_work_head_is_recognised_as_a_head(kind):
    from app.hold_speech import render_head

    assert is_hold_head(render_head(kind, practitioner="Priya"))


@pytest.mark.parametrize("speech", [
    # Real model output from the same call. None of this may be slowed: doing so
    # would change the cadence of the whole call, which is the thing the owner
    # was comparing the head against.
    "Yes — we accept private health insurance referrals.",
    "An initial assessment is fifty-eight pounds for forty-five minutes.",
    "or sixty-two pounds for an hour.",
    "I can get you booked in now and Priya will take your insurer —",
    "Hi there, I'm Susie, Northgate Physiotherapy's AI receptionist — how can I help?",
])
def test_ordinary_speech_is_not_mistaken_for_a_head(speech):
    assert not is_hold_head(speech)


def test_a_short_dash_terminated_fragment_is_not_enough():
    """The predicate matches the head POOLS, not a shape.

    "short and ends in a dash" would have caught the chunker's own fragments --
    it splits on " — " -- and slowed arbitrary pieces of model speech.
    """
    assert not is_hold_head(f"or sixty-two pounds {EM}")
    assert not is_hold_head(f"and Priya will take your insurer {EM}")
