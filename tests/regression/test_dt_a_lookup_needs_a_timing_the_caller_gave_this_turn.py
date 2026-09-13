"""D-t, enforced in the engine: check_availability runs only on a timing the
caller gave THIS turn, or confirmed to Susie's own question.

`CAdd1bdd17`, northgate demo line, 13 Sep 2026 12:31, build bef29667 -- the
first call on the reworded prompt:

    caller (symptom turn): "... i'm free tuesdays and thursdays but only
                            after 4 i also need to know about parking"
    Susie : "... would you like to get booked in so Priya can take a
             proper look at both?"
    caller: "uh yes please"
    Susie : "Tuesdays and Thursdays after four -- just a moment while I
             check"                       <- the echo, WITHOUT the wait
    tool  : check_availability(date_hint="Tuesdays and Thursdays after 4pm")

Same shape as CA5c69c585 and CAddd98ce0 the night before, on a prompt that
now says in three places to ask first. The prompt does not hold the rule;
`_timing_unearned_this_turn` does, in the same place the re-query guard and
the slot-locked guard already refuse a spurious lookup.

Owner's carve-outs (D-t, confirmed twice on 12 Sep): timing in the same
breath as the booking request, urgency wherever it was said, and a yes to
Susie's own timing / echo question all go straight to the lookup.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import _timing_unearned_this_turn
from app.tools import slot_fact_guard as guard


def _msgs(*turns):
    out = []
    for role, text in turns:
        out.append({"role": role, "content": text})
    return out


def _session(history):
    return {"conversation_history": [dict(m) for m in history]}


SYMPTOM = ("uh right so i've got the knee thing kind of shoulder thing and i'm in "
           "shoreditch i'm free tuesdays and thursdays but only after 4 i also "
           "need to know about parking")
BOOK_Q = ("And for the knee and shoulder — would you like to get booked in so "
          "Priya can take a proper look at both?")
HINT = {"date_hint": "Tuesdays and Thursdays after 4pm"}


def test_a_yes_to_the_booking_offer_with_a_remembered_timing_is_blocked():
    msgs = _msgs(("user", SYMPTOM), ("assistant", BOOK_Q), ("user", "uh yes please"))
    ask = _timing_unearned_this_turn(_session(msgs), msgs, HINT)
    assert ask, "the lookup ran on the model's memory of the symptom turn"
    assert "You mentioned" in ask
    assert "only after 4" in ask          # the caller's own words, for the echo
    assert "Do not call check_availability in this turn" in ask


def test_no_timing_anywhere_asks_the_plain_question():
    msgs = _msgs(("user", "i've hurt my knee"), ("assistant", BOOK_Q), ("user", "yes please"))
    ask = _timing_unearned_this_turn(_session(msgs), msgs, {"date_hint": "next week"})
    assert ask and "preference for when" in ask


@pytest.mark.parametrize("utterance", [
    "yes after four on a tuesday",
    "tuesday please",
    "any evening",
    "as soon as possible",
    "i'd like to book an appointment as soon as possible please",   # same breath
    "next week",
    "the 15th",
    "half past three",
    "i'm flexible",
    "doesn't matter",
    "book me in on thursday morning",
])
def test_a_timing_in_this_turns_words_is_allowed(utterance):
    msgs = _msgs(("assistant", BOOK_Q), ("user", utterance))
    assert _timing_unearned_this_turn(_session(msgs), msgs, {"date_hint": utterance}) is None


def test_urgency_said_earlier_is_allowed_with_an_urgency_hint():
    """CA3e342642 (JV Bolton, 24 Jul): ASAP is a complete answer wherever said."""
    msgs = _msgs(("user", "i need to be seen as soon as possible"),
                 ("assistant", "Right — what's the problem?"),
                 ("user", "my back"), ("assistant", BOOK_Q), ("user", "yes"))
    assert _timing_unearned_this_turn(_session(msgs), msgs, {"date_hint": "as soon as possible"}) is None


def test_an_urgency_hint_the_caller_never_said_is_blocked():
    msgs = _msgs(("user", "my back"), ("assistant", BOOK_Q), ("user", "yes"))
    assert _timing_unearned_this_turn(_session(msgs), msgs, {"date_hint": "as soon as possible"})


def test_a_yes_to_the_echo_question_is_allowed_and_folds_the_words_into_the_guard():
    echo = ("You mentioned Tuesdays and Thursdays after four — shall I look at "
            "those, or is there another time that suits?")
    msgs = _msgs(("user", SYMPTOM), ("assistant", BOOK_Q), ("user", "yes please"),
                 ("assistant", echo), ("user", "yeah those"))
    s = _session(msgs)
    assert _timing_unearned_this_turn(s, msgs, HINT) is None
    # Defect D: the builder's "nearest to four" and the guard's asked-set now
    # come from the same words -- the echo the caller just confirmed.
    asked = {str(t)[:5] for t in (s.get(guard._ASKED) or [])}
    assert "16:00" in asked


def test_a_yes_to_the_plain_timing_question_is_allowed():
    msgs = _msgs(("assistant", "Do you have a preference for when you'd like to come in?"),
                 ("user", "no not really"))
    assert _timing_unearned_this_turn(_session(msgs), msgs, {"date_hint": ""}) is None


def test_a_re_query_with_an_offer_on_the_table_is_not_this_gates_business():
    msgs = _msgs(("assistant", BOOK_Q), ("user", "what else have you got"))
    s = _session(msgs)
    s["last_offered_slots"] = [{"start": "2026-09-15T16:20:00+01:00"}]
    assert _timing_unearned_this_turn(s, msgs, HINT) is None


def test_never_raises():
    assert _timing_unearned_this_turn(None, None, None) is None  # type: ignore[arg-type]


# ── CAdb28a1a6, 13 Sep 12:58 (5d28332e): a same-turn BOUND, retracted anyway ──
#
#   caller: "um yes after 4 on a tuesday"          -> requested_clock_times: []
#   tool  : date_hint="Tuesday after 4pm"           -> 16:00
#   gate5 : "The nearest I've got to four ... is twenty past four"
#   guard : '4 in the afternoon' reads as 16:00 ... REPLACED
#
# The parser reads a bound as no clock time by design; the guard therefore
# never learns the hour the caller said, while the builder learns it from the
# model's paraphrase. When the caller's own words carry a bound WITH an hour,
# the hint is folded into the guard's asked-set on the allow path.

def test_a_same_turn_bound_folds_the_hint_into_the_guard():
    msgs = _msgs(("assistant", "Do you have a preference for when you'd like to come in?"),
                 ("user", "um yes after 4 on a tuesday"))
    s = _session(msgs)
    assert _timing_unearned_this_turn(s, msgs, {"date_hint": "Tuesday after 4pm"}) is None
    asked = {str(t)[:5] for t in (s.get(guard._ASKED) or [])}
    assert "16:00" in asked, "the guard would retract 'the nearest I've got to four'"


def test_a_same_turn_point_or_day_does_not_fold_the_hint():
    """Only a bound the parser declines earns the fold; a day alone must not
    let a model-invented hour into the guard."""
    for utterance in ["tuesday please", "thursday morning", "yes please"]:
        msgs = _msgs(("assistant", "Do you have a preference for when you'd like to come in?"),
                     ("user", utterance))
        s = _session(msgs)
        _timing_unearned_this_turn(s, msgs, {"date_hint": "Tuesday after 4pm"})
        assert not (s.get(guard._ASKED) or []), utterance
