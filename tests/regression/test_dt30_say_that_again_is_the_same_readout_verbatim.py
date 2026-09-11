"""
Regression: "say that again" is answered with the words Susie last said.

Spec rows DT-30 and DT-31; owner decision D-o (2026-09-11).

Two demo calls on the morning of 11 Sep 2026, build a590abaa:

    CA778651b7  08:44:36  [ms_conn] non-slot utterance during slot selection
                          -- passing to LLM: 'say that again'
                08:44:38  [LAT] turn_seq=6 path=llm content_ttfa_ms=2042
    CA34942aee  08:52:04  ... passing to LLM: 'say that again'
                08:52:07  [LAT] turn_seq=8 path=llm content_ttfa_ms=2415

Both times the model read the right three times in the right order -- from
its own context, in 2.0-2.4 s against 0.13-0.14 s for every producer turn on
the same calls, and as the author of three slot facts, which D-n forbids. On
10 Sep (N3) the same act was dropped as a fragment and cost 19 s.

D-o says: re-speak the last SPOKEN list verbatim. Never re-run the selector --
it applies novelty against the spoken record and returns three different
times, which is how a repeat once became a new readout. Never re-query.

These tests drive the real dispatcher (`try_unspoken_followup_speech`) with
the caller's words, after a real producer has recorded a real readout through
the one writer (`apply_offer_to_session`).
"""
from __future__ import annotations

import pytest

from app.tools import slot_followup as sf
from app.tools.receptionist_tools import _spoken_slot_time
from app.tools.slot_offer import (
    apply_offer_to_session,
    build_slot_offer,
    offer_as_record,
)

MON, TUE, WED, THU = "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"
GRID = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10", "13:00",
        "13:50", "14:40", "15:30", "16:20", "17:10"]


def _day(date, label, times):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t) for t in times],
        "slots": [{"start": f"{date}T{t}:00+01:00"} for t in times],
    }


def _days():
    return [
        _day(MON, "Monday 14th September", GRID),
        _day(TUE, "Tuesday 15th September", GRID),
        _day(WED, "Wednesday 16th September", GRID),
        _day(THU, "Thursday 17th September", GRID),
    ]


def _session_with_a_spoken_readout():
    """A real multi_day readout, recorded the way every producer records one."""
    days = _days()
    s = {"clinic_id": "northgate", "available_days": days}
    offer = build_slot_offer(days[:3], pretrimmed=False)
    assert offer is not None and offer.mode == "multi_day"
    apply_offer_to_session(s, offer_as_record(offer), offer.chunks)
    # Written by the tool executor on the live path; the named-day producer
    # gates on it (its step 1).
    s["_slot_presentation_mode"] = "multi_day"
    return s, offer


# ── the act ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("said", [
    "say that again",
    "sorry, say that again",
    "can you say those again",
    "could you read them again please",
    "run that by me again",
    "repeat that",
    "sorry could you repeat those",
    "pardon",
    "sorry",
    "what",
    "come again",
    "i didn't catch that",
    "i didn't quite hear that",
    "missed that",
    "what were they again",
    "what were the times",
    "what was the second one",
    "one more time",
])
def test_these_are_repeat_requests(said):
    assert sf.utterance_requests_repeat(said), said


@pytest.mark.parametrize("said", [
    # ASK_OPTIONS, every one of them -- the rows the producers below own.
    "what else have you got",
    "what else have you got on monday",
    "anything later",
    "what about tuesday",
    "have you got anything around 12",
    "any other days",
    "something different",
    # a request to LOOK again is a lookup, not a repeat
    "can you check again",
    "have another look",
    # a refusal that happens to start with sorry
    "sorry monday doesn't work",
    # a pick
    "number two",
    "the first one",
    "ten past twelve works",
    # nothing
    "",
])
def test_these_are_not_repeat_requests(said):
    assert not sf.utterance_requests_repeat(said), said


# ── DT-30: the offer is on the table ───────────────────────────────────────

def test_a_repeat_is_the_last_readout_verbatim():
    s, offer = _session_with_a_spoken_readout()
    keypad = dict(s["v3_dtmf_slot_map"])
    heard = list(s[sf._SPOKEN_KEY])

    again = sf.try_unspoken_followup_speech(s, "sorry, say that again")

    assert again == offer.text
    assert s["v3_dtmf_slot_map"] == keypad, "inv 10: the keypad is unchanged"
    assert s[sf._SPOKEN_KEY] == heard, "inv 16: nothing new recorded as heard"


def test_a_repeat_does_not_select():
    """The times a selector would choose next differ from the ones spoken --
    that is what novelty means. A repeat must not consult it."""
    s, offer = _session_with_a_spoken_readout()
    # What "what else" would say next: different days, by D-g.
    more = sf.more_days_speech(dict(s))
    assert more and more != offer.text, "the shape cannot discriminate"
    again = sf.try_unspoken_followup_speech(s, "say that again")
    assert again == offer.text
    assert again != more


def test_a_single_time_readout_can_be_repeated_too():
    days = [_day(MON, "Monday 14th September", ["10:30"])]
    s = {"clinic_id": "northgate", "available_days": days}
    offer = build_slot_offer(days, pretrimmed=False)
    assert offer is not None
    apply_offer_to_session(s, offer_as_record(offer), offer.chunks)
    assert "v3_dtmf_slot_map" not in s, "one option arms no keypad"
    assert sf.try_unspoken_followup_speech(s, "say that again") == offer.text


def test_a_repeat_after_a_named_day_readout_is_that_readout():
    """The second readout on both demo calls: 'tell me about Monday', then
    'say that again' must be Monday's three, not the week's six."""
    s, week = _session_with_a_spoken_readout()
    monday = sf.try_unspoken_followup_speech(s, "tell me about monday")
    assert monday and monday != week.text
    again = sf.try_unspoken_followup_speech(s, "say that again")
    assert again == monday


# ── DT-31: the offer has been cleared ──────────────────────────────────────

def test_a_repeat_survives_the_offer_being_cleared():
    """`last_offered_slots` is wiped by several turn types (B-78/B-80). The
    words are not."""
    s, offer = _session_with_a_spoken_readout()
    s.pop("last_offered_slots", None)
    assert sf.try_unspoken_followup_speech(s, "what were those again") == offer.text


def test_a_repeat_survives_a_watchdog_reask():
    """A watchdog 'any of those work?' pops B-120's copy (it is a later chunk)
    and B-132's (it is not an ordinary answer). A repeat after it is still
    about the options."""
    s, offer = _session_with_a_spoken_readout()
    s.pop("_slot_readout_chunks", None)
    s.pop("_content_turn_chunks", None)
    assert sf.try_unspoken_followup_speech(s, "say that again") == offer.text


# ── when it is NOT ours ────────────────────────────────────────────────────

def test_a_repeat_after_an_ordinary_answer_is_about_that_answer():
    """'How much is it?' -- an answer plays -- 'say that again'. The caller
    means the price. B-132's record is present and B-120's has been popped by
    that answer's first chunk; the producer stands aside."""
    s, offer = _session_with_a_spoken_readout()
    s.pop("_slot_readout_chunks", None)
    s["_content_turn_chunks"] = ["An initial assessment is fifty-five pounds."]
    assert sf.try_unspoken_followup_speech(s, "say that again") is None


def test_nothing_spoken_means_nothing_to_repeat():
    """D-o's last clause: with no spoken record the honest answer is to check
    again, and that sentence is the model's."""
    s = {"clinic_id": "northgate", "available_days": _days(),
         "last_offered_slots": [{"start": f"{MON}T08:00:00+01:00"}]}
    assert sf.try_unspoken_followup_speech(s, "say that again") is None


def test_after_a_slot_is_locked_a_repeat_is_not_a_readout():
    s, _ = _session_with_a_spoken_readout()
    s["v3_confirmed_slot_phrase"] = "Monday at eight in the morning"
    assert sf.try_unspoken_followup_speech(s, "say that again") is None


# ── the row is discriminating ──────────────────────────────────────────────

def test_without_the_producer_the_act_reaches_nothing(monkeypatch):
    """Proof the DT-30 scorer row measures the act and not the formatter:
    remove the producer and the dispatcher hands 'say that again' to the
    model, which is the 11 Sep morning defect."""
    s, offer = _session_with_a_spoken_readout()
    monkeypatch.setattr(sf, "repeat_speech", lambda *_a, **_k: None)
    assert sf.try_unspoken_followup_speech(s, "say that again") is None


def test_the_one_writer_records_the_words():
    """`apply_offer_to_session` is the one place every producer records an
    offer. The durable copy lives there so no producer can speak without
    leaving something to repeat."""
    s, offer = _session_with_a_spoken_readout()
    assert s[sf.LAST_READOUT_KEY]["chunks"] == list(offer.chunks)
    assert s[sf.LAST_READOUT_KEY]["mode"] == "multi_day"
