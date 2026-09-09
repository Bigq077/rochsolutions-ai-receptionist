"""The caller asked to cancel and was asked which day suits them.

theorem_v3 `CAffb37870`, 9 Sep 2026 02:50, on the live Theorem line, build
`ff0fa987c92b`:

    02:50:31.8  caller: 'um would you like to cancel my appointment'
    02:50:32.5  [ms_llm] situational head (cancel_req)   <- classified CORRECTLY
    02:50:34.9  Susie:  "was the appointment you'd like to cancel at our
                         Awlstuh or Redditch clinic?"
    02:50:42.1  caller: 'uh your ooster clinic'
    02:50:42.8  [ms_conn v3] Haiku resolved location: alcester, intent=BOOKING
    02:50:43.0  Susie:  'Is there a particular day or time that works best
                         for you?'

`v3_caller_intent` has EIGHT readers in connection.py and every one defaults to
"booking" when unset. It had TWO writers and neither can see the ordinary shape
of a cancel:

  * the intent-pivot block lives under `elif _v3_loc_answering:`, so it only
    sees a caller who says "cancel" WHILE ANSWERING the location question. On
    turn 1 that branch does not run; on turn 2 the caller said only a place
    name.
  * the booking-ack block infers the intent by matching "no problem at all"
    against SUSIE'S OWN reply -- [[write-gates-match-one-literal]] again, and
    a literal the hold-speech dedupe is now designed to remove from that reply.

NOT A NEW CLASSIFIER. `classify_intent` already had this right 600ms into the
call. This records what it decided rather than asking a third time in a third
way, and it is strictly stronger than the pivot block's bare substring set:
CANCEL_REQ requires a corroborator, so a bare "cancel" does not match.

WHY THE DEMO LINE COULD NOT CATCH THIS. Northgate is single-site and
auto-confirms its location at call start ("two-clinic location gate suppressed
on all paths"), so the Haiku resolver that read the default never runs there.
The code is byte-identical on both lines -- `production` and `latency-eval` are
the same commit AND the same tree hash -- so this is a config-shaped blind
spot, not a branch difference.
"""
from __future__ import annotations

import pytest

from app.hold_speech import classify_intent
from app.media_streams.connection import (
    _V3_INTENT_FROM_HOLD,
    _v3_record_caller_intent,
)

# The two utterances from the call, in order. Note the STT mangling: "I'd like"
# came through as "would you like", which reads as a question -- the same
# mangling that made the engine skip soft-context extraction on this turn.
CANCEL_UTTERANCE = "um would you like to cancel my appointment"
LOCATION_ANSWER = "uh your ooster clinic"


def _session(**kw):
    s = {"conversation_history": []}
    s.update(kw)
    return s


# ── Premise ─────────────────────────────────────────────────────────────────

def test_the_classifier_already_had_this_right():
    """If this stops holding, the fix has no source of truth to record."""
    assert [h.value for h in classify_intent(CANCEL_UTTERANCE, "")] == [
        "cancel_req"
    ]


def test_the_location_answer_carries_no_intent():
    """It must not clobber the cancel recorded on the previous turn."""
    assert classify_intent(LOCATION_ANSWER, "") == []


# ── The defect ──────────────────────────────────────────────────────────────

def test_the_live_call_ends_on_cancel_not_booking():
    s = _session()
    for utterance in (CANCEL_UTTERANCE, LOCATION_ANSWER):
        _v3_record_caller_intent(s, utterance)
    assert s.get("v3_caller_intent") == "cancel", (
        "the Haiku resolver reads this and asks a caller who is cancelling "
        "'Is there a particular day or time that works best for you?'"
    )


def test_the_intent_is_recorded_on_the_very_first_turn():
    """Turn 1 is the whole point: both existing writers run later than this."""
    s = _session()
    _v3_record_caller_intent(s, CANCEL_UTTERANCE)
    assert s["v3_caller_intent"] == "cancel"


@pytest.mark.parametrize("utterance,expected", [
    ("uh yeah i'd like to cancel my appointment", "cancel"),
    ("i need to cancel it", "cancel"),
    ("can i move my appointment", "reschedule"),
    ("i'd like to reschedule my booking", "reschedule"),
    ("id like to book an appointment", "booking"),
])
def test_each_intent_family_is_recorded(utterance, expected):
    s = _session()
    _v3_record_caller_intent(s, utterance)
    assert s.get("v3_caller_intent") == expected


# ── The guards ──────────────────────────────────────────────────────────────

def test_booking_never_downgrades_a_cancel():
    """A caller cancelling one appointment often books another in the same
    breath. The readers use this to decide whether a phone number is a LOOKUP
    KEY or a contact detail, and getting that backwards deletes the wrong
    appointment -- see [[duplicate-write-rule-is-family-not-slot]]."""
    s = _session(v3_caller_intent="cancel")
    _v3_record_caller_intent(s, "id like to book an appointment")
    assert s["v3_caller_intent"] == "cancel"


@pytest.mark.parametrize("utterance", [
    "friday at ten please",
    "uh your ooster clinic",
    "how much does it cost",
    "my shoulder really hurts",
    "yes",
    "",
])
def test_an_utterance_that_is_not_an_intent_leaves_the_record_alone(utterance):
    """Anything the classifier returns that is not one of the three families
    is not an answer to 'what does this caller want'."""
    s = _session(v3_caller_intent="cancel")
    _v3_record_caller_intent(s, utterance)
    assert s["v3_caller_intent"] == "cancel"


def test_nothing_is_recorded_when_nothing_is_claimed():
    s = _session()
    _v3_record_caller_intent(s, "uh hello")
    assert "v3_caller_intent" not in s


def test_the_map_covers_exactly_the_three_readers_branch_on():
    """The readers branch on cancel / reschedule / anything-else. A fourth
    value here would reach eight `.get(..., "booking")` sites unreviewed."""
    assert set(_V3_INTENT_FROM_HOLD.values()) == {
        "cancel", "reschedule", "booking"
    }


def test_it_never_raises():
    """A head must never break a call, and neither must this."""
    for bad in (None, 123, object()):
        _v3_record_caller_intent(_session(), bad)  # type: ignore[arg-type]
    _v3_record_caller_intent({}, "cancel my appointment")


# ── The second live call: a THIRD copy of the post-location decision ────────
#
# `CA6b71544d`, 9 Sep 03:12, on build 6f277d414bcb -- the build that recorded
# the intent correctly:
#
#     03:12:28.8  [ms_conn v3] caller intent = cancel      <- the fix FIRED
#     03:12:39.0  [ms_conn v3] location answer intercepted — ack-only, no run_turn
#     03:12:39.2  Susie: 'Is there a particular day or time that works best
#                         for you?'
#
# Recording the intent was necessary and not sufficient. THREE sites decide
# what to ask once the clinic is known -- the keypad path, the Haiku resolver,
# and the deterministic verbal intercept. The first two already branched on the
# intent; the third did not, and clean STT ("uh at your alcester clinic") is
# what routes a call to it rather than to Haiku.

import ast
from pathlib import Path

from app.media_streams.connection import _V3_PHONE_CONFIRM_Q


def test_the_phone_confirm_question_has_exactly_one_owner():
    """The clinic-question keywords drifted into three copies and the one that
    was never updated is what let Susie ask twice (f89a4c7e). The same shape
    here let the third site ask a cancelling caller for a day and time.

    Counts STRING LITERALS in the module, not references: three call sites may
    reference the constant, but none may spell the sentence out again.
    """
    src = Path("app/media_streams/connection.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    spelled = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "associated with your booking" in n.value
    ]
    assert len(spelled) == 1, (
        f"{len(spelled)} copies of the phone-confirm wording — it must be "
        f"spelled once and referenced by every site "
        f"(lines {[n.lineno for n in spelled]})"
    )


def test_the_two_follow_up_questions_are_different_questions():
    """A cancelling caller's number is a LOOKUP KEY, not a contact detail."""
    assert "day or time" not in _V3_PHONE_CONFIRM_Q.lower()
    assert "booking" in _V3_PHONE_CONFIRM_Q.lower()


def test_every_post_location_site_branches_on_the_intent():
    """All three sites must consult `v3_caller_intent`. A fourth site added
    without it is the defect this file exists for, twice over.

    Asserted structurally: each place that can speak the preference question
    must sit inside a function that also reads the intent.
    """
    src = Path("app/media_streams/connection.py").read_text(encoding="utf-8")
    # The deterministic intercept is the site that lacked the branch. Its new
    # guard must sit immediately before the treatment-bypass fallback.
    assert (
        '"v3_caller_intent", "booking"\n'
        '                                            ) in ("reschedule", "cancel"):'
        in src.replace("\r\n", "\n")
    ), (
        "the deterministic verbal location intercept no longer branches on the "
        "caller's intent — a cancelling caller will be asked to pick a slot"
    )
