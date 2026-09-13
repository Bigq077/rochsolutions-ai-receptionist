"""One slot on the table, Susie has just asked about it: any yes is a yes to it.

`CAafb7f031`, northgate demo line, 13 Sep 2026 13:15, build 86b77625 -- an
ordinary booking:

    Susie : "Number 1, Monday 14th — ten to nine, or ten past five.
             Number 2, Tuesday the 15th — eight, or ten past five.
             Number 3, Wednesday the 16th — eight in the morning, or ten past
             five in the evening. Any of those work?"
    caller: "yeah the wednesday at 8 works"      -> caller ACCEPTED 08:00
    Susie : "Yes — eight in the morning on Wednesday 16th September is free.
             Shall I book that in for you?"       (one slot on the table)
    caller: "uh yeah go for it"
            -> slot_accepted_by_caller: None
    model : check_availability(date_hint="8am")   (spurious)
    engine: the re-query guard asks the resolver, gets None, stands down;
            the dedup branch reads Wednesday's OTHER times (eight left out)
    caller: "i was booking an 8 o'clock slot ... why'd you take it away"

WHY NONE. Steps 2-3 of the resolver decide WHICH of the day's heard times
was meant: Wednesday had two heard (08:00 from the list, 17:10 too), the
words named no label, no band, no position -- decline. Right for a numbered
list; wrong when the offer holds ONE slot and the caller has just been asked
"shall I book that in?". There is no "which".

The owner's objection to a phrase-list fix ("if somebody confirms in a
non-traditional way he is stuck") is the rule here: acceptance is decided by
STATE -- one slot on the table, Susie's last turn presented it, the words
accept (step 0) and refuse nothing -- not by a vocabulary of yeses.

Guarded on the moment: a yes to a parking answer while the offer sits on
the table pins nothing (the A2 hole, deliberately closed here).
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    apply_resolved_time_to_session,
    record_spoken_slots,
    slot_accepted_by_caller,
)

WED = "2026-09-16"
GRID = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10", "13:00",
        "13:50", "14:40", "15:30", "16:20", "17:10"]
SPOKEN = ["eight in the morning", "ten to nine in the morning",
          "twenty to ten in the morning", "half past ten in the morning",
          "twenty past eleven in the morning", "ten past twelve in the afternoon",
          "one in the afternoon", "ten to two in the afternoon",
          "twenty to three in the afternoon", "half past three in the afternoon",
          "twenty past four in the afternoon", "ten past five in the evening"]
FOLLOW_UP = ("Yes — eight in the morning on Wednesday 16th September is free. "
             "Shall I book that in for you?")


def _day(date, label):
    return {"date": date, "day_label": label, "slot_times": GRID,
            "slot_times_spoken": SPOKEN, "times_not_shown": 0}


def _after_the_follow_up(last_susie_turn=FOLLOW_UP):
    """The session as the call left it: multi-day list heard, then the
    one-slot follow-up applied, with `last_susie_turn` in history."""
    s = {"available_days": [_day("2026-09-14", "Monday 14th September"),
                            _day("2026-09-15", "Tuesday 15th September"),
                            _day(WED, "Wednesday 16th September")],
         "conversation_history": []}
    # What the multi-day readout recorded as heard: two per day.
    record_spoken_slots(s, [
        {"start": "2026-09-14T08:50:00+01:00"}, {"start": "2026-09-14T17:10:00+01:00"},
        {"start": "2026-09-15T08:00:00+01:00"}, {"start": "2026-09-15T17:10:00+01:00"},
        {"start": f"{WED}T08:00:00+01:00"}, {"start": f"{WED}T17:10:00+01:00"},
    ])
    slot = {"start": f"{WED}T08:00:00+01:00", "end": f"{WED}T08:45:00+01:00",
            "date": WED, "time": "08:00", "spoken": "eight in the morning",
            "day_label": "Wednesday 16th September"}
    apply_resolved_time_to_session(s, slot, asked=["08:00"])
    s["conversation_history"] += [
        {"role": "user", "content": "yeah the wednesday at 8 works"},
        {"role": "assistant", "content": last_susie_turn},
    ]
    return s


@pytest.mark.parametrize("utterance", [
    "uh yeah go for it",
    "brilliant, do that",
    "yeah sounds good to me mate",
    "go on then",
    "that'd be lovely",
    "yes",
    "um yes please",
    "eight's fine",
    "wednesday's fine",
])
def test_any_yes_to_the_one_slot_offer_accepts_it(utterance):
    s = _after_the_follow_up()
    got = slot_accepted_by_caller(s, utterance)
    assert (got or "")[:19] == f"{WED}T08:00:00", (
        f"{utterance!r} did not accept the one slot on the table -- the re-query "
        "guard stands down and the caller is read the day's other times"
    )


@pytest.mark.parametrize("utterance", [
    "no",
    "uh no not that one",
    "what about thursday",
    "have you got anything later",
    "hello you still there",
    "what else have you got",
    "quentin rock",
])
def test_a_non_acceptance_still_declines(utterance):
    s = _after_the_follow_up()
    assert slot_accepted_by_caller(s, utterance) is None, utterance


def test_naming_another_heard_time_is_not_a_yes_to_this_one():
    """1b declines; the ladder below then resolves the time they named (a
    heard Wednesday slot), exactly as before -- never the one on the table."""
    s = _after_the_follow_up()
    got = slot_accepted_by_caller(s, "actually can you do ten past five")
    assert (got or "")[:19] != f"{WED}T08:00:00"
    assert (got or "")[:19] == f"{WED}T17:10:00"


def test_a_yes_to_a_later_question_does_not_pin():
    """The offer is still on the table but Susie's last turn was about
    parking: a yes there is not a slot acceptance."""
    s = _after_the_follow_up(
        last_susie_turn="There's two hours free in the Barlow Moor Road car park. "
                        "Anything else I can help with?")
    assert slot_accepted_by_caller(s, "uh yeah go for it") is None
    assert slot_accepted_by_caller(s, "yes") is None


def test_a_single_heard_time_still_resolves_without_history():
    """The pre-existing path (one heard time on the day) is untouched."""
    s = _after_the_follow_up()
    s["conversation_history"] = []
    # Only 08:00 heard on Wednesday -> step 3's single-heard return.
    from app.tools import slot_followup as sf
    s[sf._SPOKEN_KEY] = [x for x in s[sf._SPOKEN_KEY] if not x.startswith(f"{WED}T17:10")]
    assert (slot_accepted_by_caller(s, "yes") or "")[:19] == f"{WED}T08:00:00"


def test_a_yes_to_the_name_question_does_not_re_pin():
    """CAef461542 (13 Sep 14:29): Susie's read-back-plus-name-question carried
    the slot label; "yeah um it'll be elektra" re-pinned the slot. The turn
    must be ASKING about the slot."""
    s = _after_the_follow_up(
        last_susie_turn="so that's Wednesday the 16th of September at eight in the "
                        "morning — could I take your first name and surname?")
    assert slot_accepted_by_caller(s, "yeah um it'll be elektra um giacometti") is None
    assert slot_accepted_by_caller(s, "yeah") is None
