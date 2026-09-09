"""A caller told "that's not soon enough" must hear WHY, not the same list.

D11. 9 Sep 2026, northgate, build 38709d5fbecb — the call that verified the
"soonest" capture fix. She opened correctly:

    "Starting with the soonest — Number 1, Wednesday 9th September — half past
     three in the afternoon, or ten past five in the evening. ..."

The caller said "um that's not soon enough". It was 13:43, so half three TODAY
was the first slot in the diary and there was genuinely nothing before it. The
reply repeated the same two times with no acknowledgement at all:

    "I've got today — Wednesday the 9th of September — at half past three in the
     afternoon, or ten past five. Do either of those work?"

The CONTENT was right. B-137 had already put the earliest day first and B-142
its earliest time. What was missing is the sentence that makes a repeat honest —
the same defect shape B-137 fixed one turn earlier, and `sparse_rota_note` fixed
for the far-out-rota case it cannot cover here (it needs the earliest day to be
several days away, and a second clinic site).

The turn reached the model because nothing deterministic claimed it:
`utterance_requests_more_slots` matches "later", "else", "other", "another",
"instead" — every one a request to move AWAY, while this caller is asking to
move NEARER. And "is this the whole diary" is not something the model can know.

These tests drive `try_unspoken_followup_speech`, the real pre-LLM dispatcher,
not the producer alone — the producer returning the right string is worth
nothing if the dispatcher never reaches it, and the ordering against the other
producers is the part most likely to rot.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.tools.slot_followup import (
    nothing_sooner_speech,
    record_spoken_slots,
    try_unspoken_followup_speech,
    utterance_asks_for_something_sooner,
    utterance_requests_more_slots,
)

TODAY = dt.date.today()
D1 = TODAY.isoformat()
D2 = (TODAY + dt.timedelta(days=1)).isoformat()
D3 = (TODAY + dt.timedelta(days=2)).isoformat()

# Wednesday's real shape on that call: three bookable times, two of them read.
WED = [("15:30", "half past three in the afternoon"),
       ("16:20", "twenty past four in the afternoon"),
       ("17:10", "ten past five in the evening")]
THU = [("08:00", "eight in the morning"), ("18:50", "ten to seven in the evening")]
FRI = [("08:00", "eight in the morning"), ("15:30", "half past three in the afternoon")]


def _day(iso, label, pairs, hidden=0):
    return {
        "date": iso, "day_label": label,
        "slot_times": [t for t, _ in pairs],
        "slot_times_spoken": [s for _, s in pairs],
        "times_not_shown": hidden,
        "slots": [{"start": "%sT%s:00+01:00" % (iso, t), "end": ""}
                  for t, _ in pairs],
    }


def _iso(day, hhmm):
    return "%sT%s:00+01:00" % (day, hhmm)


def _session(spoken, hidden=0):
    """The call as it stood when the caller pushed back."""
    s = {
        "available_days": [
            _day(D1, "Wednesday 9th September", WED, hidden=hidden),
            _day(D2, "Thursday 10th September", THU),
            _day(D3, "Friday 11th September", FRI),
        ],
        "day_preference": "as soon as possible",
        "_slot_presentation_mode": "multi_day",
    }
    slots = [{"start": _iso(d, t)} for d, t in spoken]
    s["last_offered_slots"] = slots
    record_spoken_slots(s, slots)
    return s


#: Exactly what she read out on the live call.
AS_READ = [(D1, "15:30"), (D1, "17:10"),
           (D2, "08:00"), (D2, "18:50"),
           (D3, "08:00"), (D3, "15:30")]


# ── The live call ────────────────────────────────────────────────────────────

def test_the_live_pushback_is_answered_deterministically():
    said = try_unspoken_followup_speech(_session(AS_READ), "um that's not soon enough")
    assert said, "the push-back fell through to the model again"
    assert "nothing before it" in said
    assert "half past three in the afternoon" in said
    assert "Today" in said


def test_the_sentence_survives_the_last_bot_prompt_cap():
    """B-31: a reply over 200 chars loses its '?' to the cap, which switches
    clinical screening's orphan matching off. This sentence ends in a question
    on purpose, so it must fit."""
    said = try_unspoken_followup_speech(_session(AS_READ), "that's not soon enough")
    assert said.rstrip().endswith("?")
    assert len(said) < 200, len(said)


def test_it_names_the_slot_once():
    """Stage B's lesson: a label said twice in one breath is worse than one not
    said at all."""
    said = try_unspoken_followup_speech(_session(AS_READ), "anything sooner")
    assert said.count("half past three in the afternoon") == 1
    assert said.count("Today") == 1


def test_the_offer_on_the_table_is_left_alone():
    """The caller can still take Thursday or Friday. Re-pointing the keypad at
    the one named slot would take that away from them."""
    s = _session(AS_READ)
    before_offered = list(s["last_offered_slots"])
    before_days = list(s["available_days"])
    try_unspoken_followup_speech(s, "that's not soon enough")
    assert s["last_offered_slots"] == before_offered
    assert s["available_days"] == before_days
    assert "v3_dtmf_slot_map" not in s


# ── The matcher ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("utterance", [
    "um that's not soon enough",            # the live call, verbatim
    "that is not soon enough",
    "no saturday is not soon enough",
    "have you got anything sooner",
    "anything earlier",
    "is there anything sooner",
    "can you do sooner than that",
    "nothing before that",
    "that's too far away",
    "i can't wait that long",
    "i need something sooner",
])
def test_a_pushback_is_recognised(utterance):
    assert utterance_asks_for_something_sooner(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "the earlier one please",     # PICKING from what was read — resolve_requested_time owns it
    "yeah number one works",
    "what else have you got",
    "a different day",
    "monday doesn't work",
    "how much does it cost",
    "tuesday at ten past five",
    "that's fine",
    "",
])
def test_everything_else_is_left_to_the_other_producers(utterance):
    assert not utterance_asks_for_something_sooner(utterance), utterance


def test_the_two_matchers_do_not_overlap_on_the_live_utterance():
    """"Not soon enough" asks to move NEARER; `utterance_requests_more_slots`
    means move AWAY. Neither may answer the other's turn."""
    assert utterance_asks_for_something_sooner("that's not soon enough")
    assert not utterance_requests_more_slots("that's not soon enough")
    assert utterance_requests_more_slots("what else have you got")
    assert not utterance_asks_for_something_sooner("what else have you got")


# ── The four guards ──────────────────────────────────────────────────────────

def test_guard_1_a_first_readout_is_not_apologised_for():
    """Gated on THIS turn's push-back, not on the standing `day_preference` —
    firing on a request nobody objected to would concede for no reason."""
    s = _session(AS_READ)
    assert s["day_preference"] == "as soon as possible"
    assert nothing_sooner_speech(s, "what else have you got") is None


def test_guard_2_a_band_filtered_payload_cannot_claim_the_diary():
    """B-97: where `times_not_shown` is positive, times were removed before the
    session saw them, so this payload's earliest is not the diary's earliest."""
    assert nothing_sooner_speech(_session(AS_READ, hidden=4), "anything sooner") is None


def test_guard_3_something_earlier_unspoken_is_offered_not_denied():
    """She read 16:20 and 17:10; 15:30 is still sitting there. Saying "nothing
    before it" would be false, and the producers below read it out instead."""
    s = _session([(D1, "16:20"), (D1, "17:10")])
    assert nothing_sooner_speech(s, "anything sooner") is None


def test_guard_3_holds_when_no_offer_has_been_made_at_all():
    s = _session([])
    s["last_offered_slots"] = [{"start": _iso(D1, "15:30")}]
    assert nothing_sooner_speech(s, "anything sooner") is None


def test_guard_4_it_is_never_said_twice_about_the_same_slot():
    """A repeated push-back answered with the identical sentence is the
    going-in-circles shape this exists to end."""
    s = _session(AS_READ)
    assert nothing_sooner_speech(s, "that's not soon enough")
    assert nothing_sooner_speech(s, "no really, anything sooner") is None
    assert nothing_sooner_speech(s, "that's not soon enough") is None


def test_a_new_earliest_slot_re_arms_the_sentence():
    """The latch is keyed on the slot, not the call: a later lookup that finds
    something genuinely earlier gets its own answer."""
    s = _session(AS_READ)
    assert nothing_sooner_speech(s, "anything sooner")
    s["available_days"][0]["slot_times"].insert(0, "09:00")
    s["available_days"][0]["slot_times_spoken"].insert(0, "nine in the morning")
    s["available_days"][0]["slots"].insert(0, {"start": _iso(D1, "09:00"), "end": ""})
    record_spoken_slots(s, [{"start": _iso(D1, "09:00")}])
    said = nothing_sooner_speech(s, "anything sooner")
    assert said and "nine in the morning" in said


# ── It must never raise on a live booking turn ───────────────────────────────

@pytest.mark.parametrize("session", [
    {}, {"available_days": None}, {"available_days": []},
    {"available_days": [{"date": None}]},
    {"available_days": [{"date": D1, "slots": [{"start": ""}]}]},
    {"available_days": "not a list"},
])
def test_a_malformed_payload_falls_through_quietly(session):
    assert nothing_sooner_speech(dict(session), "anything sooner") is None


# ── Adversarial phrasings ────────────────────────────────────────────────────
# Found by probing the first cut of the matcher, which fired on both of these.
# The producer's other three guards would have caught most of the damage, but a
# caller who says something warm and gets an apology back has been misheard, and
# `_nothing_sooner_said_for` would then be spent on a turn that never asked.

@pytest.mark.parametrize("utterance", [
    "i can't wait to get this sorted",   # anticipation, not impatience
    "i can't wait to see her",
    "before that i had physio elsewhere",  # narrative, not a push-back
    "sooner or later i'll need it",
    "the earlier slot please",           # a PICK — resolve_requested_time owns it
    "earlier you said thursday",
    "i'd like something before christmas",
    "is it too far to walk",
    "i need to be seen by a physio",
])
def test_warmth_and_narrative_are_not_pushbacks(utterance):
    assert not utterance_asks_for_something_sooner(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "i can't wait that long",            # impatience — must still fire
    "i can't wait a week",
    "anything before then",
    "is there owt sooner",
])
def test_the_tightening_did_not_cost_a_real_pushback(utterance):
    assert utterance_asks_for_something_sooner(utterance), utterance


# ── End to end, through the real presentation chain ──────────────────────────
# The property that makes D11 fire reliably rather than by luck, and it is worth
# stating because it is load-bearing and nobody wrote it down:
#
#   B-137 puts the EARLIEST DAY first when the caller asked for the soonest, and
#   B-142 reads that day from its EARLIEST TIME. Between them, the payload's
#   earliest slot is guaranteed to have been SPOKEN on any soonest-request
#   readout -- which is exactly guard 3's condition.
#
# The inverse holds too, and is the reason guard 3 is safe: on a readout the
# caller did NOT ask to be soonest-ordered, B-116 prefers times they have not
# heard, so the earliest may well be unspoken -- and there the honest answer is
# to offer it, which is what falling through does.

def test_a_soonest_readout_always_leaves_d11_able_to_fire():
    from app.tools.receptionist_tools import _cap_presented_slots
    from app.tools.slot_offer import (
        apply_offer_to_session, build_slot_offer, days_were_held_back,
        offer_as_record,
    )

    session = {"day_preference": "as soon as possible"}
    payload = {
        "available_days": [
            _day(D1, "Wednesday 9th September", WED),
            _day(D2, "Thursday 10th September", THU),
            _day(D3, "Friday 11th September", FRI),
        ],
        "success": True,
    }

    capped = _cap_presented_slots(payload, session=session)
    assert capped["presentation_mode"] == "multi_day"
    assert capped.get("lead_in") == "soonest_first"

    offer = build_slot_offer(
        list(capped["presented_days"]),
        lead_in="soonest_first",
        more_days=days_were_held_back(capped),
    )
    assert offer.chunks[0].startswith("Starting with the soonest —")

    session["available_days"] = capped["available_days"]
    session["_slot_presentation_mode"] = "multi_day"
    apply_offer_to_session(
        session, offer_as_record(offer, day_iso=D1), offer.chunks,
    )

    # The whole point: 15:30 is the payload's earliest AND it has been spoken.
    said = try_unspoken_followup_speech(session, "um that's not soon enough")
    assert said, "D11 could not fire after a real soonest readout"
    assert "half past three in the afternoon" in said
    assert "nothing before it" in said
