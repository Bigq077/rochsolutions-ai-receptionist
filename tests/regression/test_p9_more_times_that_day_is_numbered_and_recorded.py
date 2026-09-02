"""
P9: "the others on that day" must be numbered, and the keypad must follow.

CA665dc0309da186874a37f30034196e33, 2026-09-02 10:33, northgate, build
`ce193d95` — the call that deliberately exercised the single-day branch left
untouched by `1c972167`.

    Susie : "Number 1, ten to nine in the morning. Number 2, twenty past four
             in the afternoon. Number 3, ten past five in the evening.
             And I've a few others that day if none of those suit."
    caller: "uh um yeah what else have you got that day"
    Susie : "On Tuesday 8th September I also have twenty to ten in the morning,
             half past ten in the morning, twenty past eleven in the morning, …"
             — eight times, 306 characters, 15 seconds, no numbers

THE RECORD SPLIT, which is the defect:

    last_offered_slots  08:50, 16:20, 17:10  ->  09:40, 10:30, 11:20, 12:10
    v3_dtmf_slot_map    unchanged — still the original three

    "the second one"  ->  10:30   (correct)
    pressing 2        ->  16:20   (a real free slot he never heard)

One utterance, two appointments, decided by whether the caller spoke or
pressed. The wrong one books silently, because 16:20 is genuinely available and
nothing downstream can tell it was not meant.

What the sentence SAID was right: the caller said "that day", Susie really had
promised "a few others", and scoping to Tuesday is B-103 working. It spoke
without writing the record — the same disease as the 09:43 regression, in the
path that was left alone.

OWNER DECISION, 2026-09-02, superseding the 24 Aug rule in
`all_remaining_on_next_day` ("an explicit 'tell me the others' gets ALL of
them"): **three numbered, then "a few more after those"** — what the primary
readout already does with the same problem. The old rule was written against a
two-at-a-time batch that made a caller ask three times to walk one Tuesday;
eight in one breath overshot the correction.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import record_spoken_slots, try_unspoken_followup_speech

_TIMES = ["08:50", "09:40", "10:30", "11:20", "12:10", "16:20", "17:10"]
_SPOKEN = [
    "ten to nine in the morning",
    "twenty to ten in the morning",
    "half past ten in the morning",
    "twenty past eleven in the morning",
    "ten past twelve in the afternoon",
    "twenty past four in the afternoon",
    "ten past five in the evening",
]

TUESDAY = {
    "date": "2026-09-08",
    "day_label": "Tuesday 8th September",
    "slot_times": list(_TIMES),
    "slot_times_spoken": list(_SPOKEN),
    "times_not_shown": 0,
    "slots": [
        {"start": "2026-09-08T{}:00+01:00".format(t), "end": ""} for t in _TIMES
    ],
}

# The three she read out first: 08:50, 16:20, 17:10.
_HEARD = ("08:50", "16:20", "17:10")


def _after_single_day_offer():
    session = {
        "available_days": [TUESDAY],
        "_slot_presentation_mode": "single_day",
        "last_offered_slots": [
            {"start": "2026-09-08T{}:00+01:00".format(t), "end": ""}
            for t in _HEARD
        ],
        "slot_labels": [_SPOKEN[0], _SPOKEN[5], _SPOKEN[6]],
        "v3_dtmf_slot_map": {
            "1": _SPOKEN[0], "2": _SPOKEN[5], "3": _SPOKEN[6],
        },
    }
    record_spoken_slots(session, [
        {"start": "2026-09-08T{}:00+01:00".format(t), "date": "2026-09-08"}
        for t in _HEARD
    ])
    return session


@pytest.mark.asyncio
async def test_speaking_and_pressing_mean_the_same_slot():
    """THE defect. Fails before the fix — the keypad stayed on the old three."""
    session = _after_single_day_offer()

    spoken = try_unspoken_followup_speech(
        session, "uh um yeah what else have you got that day"
    )
    assert spoken, "the follow-up stopped answering 'what else that day'"

    keypad = list((session.get("v3_dtmf_slot_map") or {}).values())
    assert keypad == session["slot_labels"], (
        "the keypad and the spoken record disagree, so 'the second one' and "
        "pressing 2 book different appointments: keypad={} labels={}".format(
            keypad, session["slot_labels"]
        )
    )
    # And specifically: nothing he heard in the FIRST readout may still be
    # addressable by a digit, because those are not what she just offered.
    assert _SPOKEN[5] not in keypad, (
        "pressing a key still books 'twenty past four', which was in the "
        "previous offer and not in this one"
    )


@pytest.mark.asyncio
async def test_three_numbered_then_a_tail():
    """The owner rule of 2026-09-02, replacing 'all of them in one breath'."""
    session = _after_single_day_offer()
    spoken = try_unspoken_followup_speech(session, "what else have you got that day")

    assert "Number 1" in spoken and "Number 2" in spoken and "Number 3" in spoken, (
        "the options are not numbered, so there is nothing for the caller to "
        "press: {!r}".format(spoken)
    )
    assert "Number 4" not in spoken, (
        "more than three read out in one turn: {!r}".format(spoken)
    )
    assert len(session["last_offered_slots"]) == 3
    # Four were unspoken and three were offered, so the rest must be admitted.
    assert "few others" in spoken or "few more" in spoken, (
        "times were withheld with no tail saying so — that is the silent "
        "withholding the 24 Aug rule exists to prevent: {!r}".format(spoken)
    )


@pytest.mark.asyncio
async def test_only_times_he_has_not_heard_are_offered():
    """The whole point of the question. B-116 at the follow-up."""
    session = _after_single_day_offer()
    spoken = try_unspoken_followup_speech(session, "what else have you got that day")

    for heard in (_SPOKEN[0], _SPOKEN[5], _SPOKEN[6]):
        assert heard not in spoken, "re-read a time he had already heard: {}".format(heard)
    assert all(
        s["start"][11:16] not in _HEARD for s in session["last_offered_slots"]
    ), "a time he had already heard is back in the offer record"


@pytest.mark.asyncio
async def test_the_day_is_still_the_one_he_named():
    """B-103 unchanged: 'that day' scopes, and the answer stays on Tuesday."""
    session = _after_single_day_offer()
    spoken = try_unspoken_followup_speech(session, "what else have you got that day")

    assert "Tuesday 8th September" in spoken
    assert all(s["start"][:10] == "2026-09-08" for s in session["last_offered_slots"])
