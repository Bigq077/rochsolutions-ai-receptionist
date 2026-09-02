"""P6 residue: a pick resolved on a multi-day offer and never on a single-day one.

`apply_offer_to_session` writes ONE entry per DAY on multi_day and EVERY SLOT
otherwise, so a named position means different things in the two modes.
`slot_accepted_by_caller` read it as a day in both:

    date = str(offered[pos - 1].get("start"))[:10]

On multi_day that is right. On single_day every entry shares one date, so the
position selected the only day there was, step 3 then demanded a time the
ordinal never names, and the function returned None for "the first one",
"number two" and "the last one" alike. A caller who accepted in words got the
list read again -- the exact P6 symptom, in the branch P9 was also found in.

Naming a time with no day failed there too, though a single-day offer has only
one day it could be.

Found by scripts/replay_slot_decisions.py over the stored corpus, not on a
call: 119 turns gained a resolution, 0 picks changed to a different slot.

The second half of this file guards the over-correction. Once ordinals resolved
on a single-day offer, three stored turns began resolving a REQUEST to a
bookable slot ("can you repeat the last day that you offered the slots") and
one resolved a REJECTION ("not the first one"). Those are the wrong-slot kind:
they book silently. `utterance_is_a_request_not_a_pick` declines both shapes.
"""

import pytest

from app.tools import slot_followup as _sf
from app.tools.slot_followup import slot_accepted_by_caller
from app.tools.slot_offer import apply_offer_to_session

# Imported defensively so this file still COLLECTS on a tree without the fix --
# a collection error would hide these behind an ERROR instead of showing them
# as the assertion failures they are.
utterance_is_a_request_not_a_pick = getattr(
    _sf, "utterance_is_a_request_not_a_pick", lambda _text: False)

_DAY = "2026-09-08"
_TIMES = [("08:50", "ten to nine in the morning"),
          ("16:20", "twenty past four in the afternoon"),
          ("17:10", "ten past five in the evening")]


def _single_day_session():
    """A session holding a one-day, three-option offer, via the real writer."""
    payload = [{
        "date": _DAY,
        "day_label": "Tuesday 8th September",
        "slot_times": [t for t, _ in _TIMES],
        "slot_times_spoken": [s for _, s in _TIMES],
        "slots": [{"start": "%sT%s:00" % (_DAY, t)} for t, _ in _TIMES],
    }]
    slots = [{"start": "%sT%s:00" % (_DAY, t), "date": _DAY,
              "day_label": "Tuesday 8th September", "time": t, "spoken": s}
             for t, s in _TIMES]
    session = {"available_days": payload}
    apply_offer_to_session(
        session,
        {"slots": slots, "dtmf_map": {i + 1: s for i, (_, s) in enumerate(_TIMES)},
         "mode": "single_day"},
        ["readout"],
    )
    session["available_days"] = payload
    return session


# -- the defect ------------------------------------------------------------

@pytest.mark.parametrize("utterance,expected", [
    ("the first one", "08:50"),
    ("First one", "08:50"),
    ("the second one", "16:20"),
    ("number two", "16:20"),
    ("Second one", "16:20"),
    ("the last one", "17:10"),
])
def test_an_ordinal_resolves_on_a_single_day_offer(utterance, expected):
    got = slot_accepted_by_caller(_single_day_session(), utterance)
    assert got == "%sT%s:00" % (_DAY, expected), (
        "%r did not resolve to %s -- the caller accepted in words and would "
        "have been read the list again" % (utterance, expected)
    )


@pytest.mark.parametrize("utterance,expected", [
    ("ten to nine in the morning", "08:50"),
    ("twenty past four please", "16:20"),
])
def test_a_time_with_no_day_resolves_on_a_single_day_offer(utterance, expected):
    got = slot_accepted_by_caller(_single_day_session(), utterance)
    assert got == "%sT%s:00" % (_DAY, expected), utterance


def test_a_position_beyond_the_offer_resolves_nothing():
    assert slot_accepted_by_caller(_single_day_session(), "number nine") is None


# -- the over-correction guard --------------------------------------------

@pytest.mark.parametrize("utterance", [
    "can you repeat the last day that you offered the slots please",
    "um yeah could you repeat the last day that you offered the slots on that day",
    "could you offer me the slots for the first friday that you offered me",
    "could you tell me the slots you have on the tuesday again",
    "um could you tell me the slots you have on the tuesday again um not the "
    "not the first one",
])
def test_a_request_for_slots_is_never_a_pick(utterance):
    assert slot_accepted_by_caller(_single_day_session(), utterance) is None, (
        "a request to HEAR slots resolved to a bookable one -- this books "
        "silently, because the slot it picks is genuinely free"
    )


@pytest.mark.parametrize("utterance", [
    "not the first one",
    "no not the second one",
    "the first one, not the second",
])
def test_a_negated_position_is_never_a_pick(utterance):
    assert slot_accepted_by_caller(_single_day_session(), utterance) is None


def test_the_guard_leaves_a_plain_pick_alone():
    assert not utterance_is_a_request_not_a_pick("the first one")
    assert not utterance_is_a_request_not_a_pick("number two")
    assert not utterance_is_a_request_not_a_pick("ten to nine in the morning")


# -- multi_day must be untouched ------------------------------------------

def test_multi_day_position_still_selects_the_day():
    days = ["2026-09-07", "2026-09-08", "2026-09-09"]
    payload = [{
        "date": d, "day_label": "day %d" % i,
        "slot_times": ["09:00"], "slot_times_spoken": ["nine in the morning"],
        "slots": [{"start": "%sT09:00:00" % d}],
    } for i, d in enumerate(days)]
    slots = [{"start": "%sT09:00:00" % d, "date": d, "day_label": "day %d" % i,
              "time": "09:00", "spoken": "nine in the morning"}
             for i, d in enumerate(days)]
    session = {"available_days": payload}
    apply_offer_to_session(
        session,
        {"slots": slots, "dtmf_map": {1: "a", 2: "b", 3: "c"},
         "mode": "multi_day"},
        ["readout"],
    )
    session["available_days"] = payload
    assert slot_accepted_by_caller(session, "the second one") == \
        "2026-09-08T09:00:00"
    assert slot_accepted_by_caller(session, "the last one") == \
        "2026-09-09T09:00:00"
