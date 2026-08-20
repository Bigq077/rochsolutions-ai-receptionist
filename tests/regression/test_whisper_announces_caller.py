# tests/regression/test_whisper_announces_caller.py
"""The overflow whisper tells the practitioner WHO is calling.

<Dial callerId> presents a number we own — the clinic's own Susie line, because
Twilio will not present the caller's number — so in front-desk mode the
practitioner's phone rings showing their own clinic number and they answer with
no idea who is on the other end.

These tests pin three things, in descending order of how much damage getting
them wrong would do:

  1. The press-1 contract is untouched. It is the only thing stopping the
     caller's voicemail from swallowing the call, and everything here runs on
     the leg that has just been answered.
  2. Announcing degrades to silence, never to an exception and never to a wrong
     number. Withheld, non-UK, no Redis, no key, a raising Redis: all of them
     must produce exactly today's caller-less whisper.
  3. The number is paced the same way Susie paces it back to the caller.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.media_streams.router import (
    _cached_caller_number,
    _whisper_caller_phrase,
    ms_screen,
)

SPOKEN = "oh seven five oh two, two one one, two oh seven"


# ---------------------------------------------------------------------------
# 1. Phrasing — what we will and will not say out loud
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["+447502211207", "07502211207", "07502 211 207"])
def test_uk_numbers_are_spoken_in_the_familiar_zero_form(raw):
    """+44 is spoken back as 0..., never as "plus four four"."""
    assert _whisper_caller_phrase(raw) == SPOKEN


def test_pacing_matches_what_susie_reads_back_to_the_caller():
    """One owner for what a spoken phone number sounds like in this system."""
    from app.media_streams.tts_stream import _pace_digit_groups
    assert _whisper_caller_phrase("+447502211207") == _pace_digit_groups(
        ["07502", "211", "207"]
    )


@pytest.mark.parametrize(
    "raw",
    ["anonymous", "Anonymous", "withheld", "restricted", "unavailable",
     "unknown", "private", "blocked", "+266696687", "266696687"],
)
def test_withheld_numbers_announce_nothing(raw):
    """A withheld CLI is a truthy string. Announcing it would say nonsense."""
    assert _whisper_caller_phrase(raw) == ""


@pytest.mark.parametrize("raw", ["", "   ", None, "not a number", "+12125551234"])
def test_unsayable_input_announces_nothing(raw):
    """Empty, junk, and non-UK all fall back to the caller-less whisper.

    Non-UK is deliberate, not an oversight: 5/3/3 grouping is a UK shape, and a
    number read out in a shape nobody can check is worse than no number.
    """
    assert _whisper_caller_phrase(raw) == ""


def test_phrasing_never_raises_even_if_the_pacer_explodes():
    with patch(
        "app.media_streams.tts_stream._pace_digit_groups",
        side_effect=RuntimeError("boom"),
    ):
        assert _whisper_caller_phrase("+447502211207") == ""


# ---------------------------------------------------------------------------
# 2. The cache read — every failure path is ""
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cached_caller_number_decodes_bytes():
    redis = AsyncMock()
    redis.get.return_value = b"+447502211207"
    with patch("app.media_streams.session._get_redis", return_value=redis):
        assert await _cached_caller_number("CA1") == "+447502211207"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, b"", "   "])
async def test_cached_caller_number_missing_is_empty(value):
    redis = AsyncMock()
    redis.get.return_value = value
    with patch("app.media_streams.session._get_redis", return_value=redis):
        assert await _cached_caller_number("CA1") == ""


@pytest.mark.asyncio
async def test_cached_caller_number_survives_no_redis_and_a_raising_redis():
    with patch("app.media_streams.session._get_redis", return_value=None):
        assert await _cached_caller_number("CA1") == ""
    redis = AsyncMock()
    redis.get.side_effect = RuntimeError("redis down")
    with patch("app.media_streams.session._get_redis", return_value=redis):
        assert await _cached_caller_number("CA1") == ""
    assert await _cached_caller_number("") == ""


# ---------------------------------------------------------------------------
# 3. The TwiML the practitioner's leg actually receives
# ---------------------------------------------------------------------------

class _Req:
    def __init__(self, parent="CA1", form=None):
        self.query_params = {"parent": parent}
        self.headers = {"host": "example.test"}
        self._form = form or {"From": "+447367002651"}

    async def form(self):
        return self._form


_OVERFLOW = {
    "whisper_text": "Business call from your Susie line. Press 1 to take it.",
    "whisper_text_with_caller": "Business call, from {caller}. Press 1 to take it.",
}


async def _twiml(caller: str, overflow: dict) -> str:
    with patch(
        "app.media_streams.router._cached_caller_number",
        AsyncMock(return_value=caller),
    ), patch(
        "app.clinic_config.get_clinic", return_value={"call_overflow": overflow},
    ), patch(
        "app.clinic_config.clinic_id_from_twilio_to", return_value="jv_v1",
    ):
        resp = await ms_screen(_Req())
    return resp.body.decode()


@pytest.mark.asyncio
async def test_press_one_contract_is_unchanged_in_both_shapes():
    """The voicemail defence. If this breaks, voicemail can accept a call."""
    for caller in ("+447502211207", ""):
        root = ET.fromstring(await _twiml(caller, _OVERFLOW))
        gather = root.find("Gather")
        assert gather is not None, "no <Gather> — nothing can press 1"
        assert gather.get("numDigits") == "1"
        assert root.find("Hangup") is not None, (
            "no <Hangup/> after <Gather> — a silent leg would stay up instead "
            "of screening through to Susie"
        )


@pytest.mark.asyncio
async def test_a_known_caller_is_announced():
    body = await _twiml("+447502211207", _OVERFLOW)
    assert SPOKEN in ET.fromstring(body).find("Gather/Say").text


@pytest.mark.asyncio
async def test_an_unknown_caller_gets_todays_whisper_verbatim():
    body = await _twiml("", _OVERFLOW)
    assert ET.fromstring(body).find("Gather/Say").text == _OVERFLOW["whisper_text"]


@pytest.mark.asyncio
async def test_timeout_is_trimmed_only_when_the_prompt_got_longer():
    """Digits are accepted DURING the prompt, so the practitioner gains time.
    The shorter timeout only stops the CALLER waiting longer on ringback."""
    with_caller = ET.fromstring(await _twiml("+447502211207", _OVERFLOW))
    without = ET.fromstring(await _twiml("", _OVERFLOW))
    assert with_caller.find("Gather").get("timeout") == "5"
    assert without.find("Gather").get("timeout") == "8"


@pytest.mark.asyncio
async def test_a_template_without_the_placeholder_switches_announcing_off():
    """Documented way to disable this per clinic without a deploy."""
    body = await _twiml("+447502211207", {
        "whisper_text": "Plain.",
        "whisper_text_with_caller": "No placeholder here.",
    })
    assert ET.fromstring(body).find("Gather/Say").text == "Plain."


@pytest.mark.asyncio
async def test_an_ampersand_in_config_does_not_produce_malformed_twiml():
    """Pre-existing hazard: whisper_text is operator-edited and interpolated
    into XML. Malformed TwiML here drops a call that was just answered."""
    body = await _twiml("", {"whisper_text": "Marcus & Co. Press 1 <now>."})
    assert ET.fromstring(body).find("Gather/Say").text == "Marcus & Co. Press 1 <now>."


@pytest.mark.asyncio
async def test_whisper_survives_a_clinic_lookup_that_raises():
    """Config failure must not drop the call — fall back to the built-in text."""
    with patch(
        "app.media_streams.router._cached_caller_number", AsyncMock(return_value=""),
    ), patch("app.clinic_config.get_clinic", side_effect=RuntimeError("no config")):
        resp = await ms_screen(_Req())
    say = ET.fromstring(resp.body.decode()).find("Gather/Say").text
    assert "Press 1" in say
