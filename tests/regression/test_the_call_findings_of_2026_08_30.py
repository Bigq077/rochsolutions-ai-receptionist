"""Three findings from the demo call of 2026-08-30, build 9e8d22bcc8fa.

That call confirmed all four fixes from the night before and was clean on the
call sheet's own criteria. These are what it surfaced that was new. None is a
safety defect; all three are the kind of thing a caller notices.

  1. After a numbered readout, no diary head could ever fire again.
  2. The same sentence spoken twice, six seconds apart.
  3. The session length asked twice, two minutes after it was answered.

Finding 4 -- the hold latch reading text Gate 5 may delete -- has its own file,
`test_the_hold_latch_survives_gate_5.py`, because it needs the streaming path
rather than a pure function.
"""
from __future__ import annotations

import pytest

from app.hold_speech import _CONFIRM_Q, Intent, classify_intent
from app.media_streams.connection import (
    offered_slot_labels,
    utterance_is_slot_selection,
)
from app.tools.slot_followup import (
    exhaustion_offer_signature,
    exhaustion_sentence_already_said,
    note_exhaustion_sentence_said,
)


# ═══ 1. After a numbered readout, no diary head can ever fire ═══════════════
#
# `_CONFIRM_Q` listed `\bnumber \d\b` to catch a confirm question. A slot
# readout always says "Number 1, ... Number 2, ...", so from the first offer
# onwards every turn read as answering one and every diary intent was dropped.
# 244 suppressions in the corpus, 186 from that token alone.
#
# It is NOT simply deleted: most of the 186 are genuine selections and should
# stay silent. The engine's own B-90 verdict -- is this utterance one of the
# labels just offered? -- replaces the proxy.

READOUT = "Number 1, ten in the morning. Number 2, two in the afternoon."
OFFER = {"slot_labels": ["ten in the morning", "two in the afternoon"]}


def test_the_readout_token_is_gone_from_the_confirm_pattern():
    """State the deletion out loud. Putting it back re-breaks every later head."""
    assert not _CONFIRM_Q.search(READOUT), (
        "a slot READOUT still matches _CONFIRM_Q — every diary head for the "
        "rest of the call is suppressed again"
    )


@pytest.mark.parametrize("utterance,intent", [
    ("um what do you have next tuesday", Intent.NAMED_DAY),
    ("actually what's the soonest you've got", Intent.EARLIEST),
])
def test_a_new_request_after_a_readout_gets_its_head(utterance, intent):
    """The two turns from the call that got silence where they wanted a head."""
    hits = classify_intent(utterance, READOUT, slot_selection=False)
    assert intent in hits, (
        f"{utterance!r} got {hits} — a caller asking something new during the "
        f"slot window is still waiting for a lookup"
    )


@pytest.mark.parametrize("picked", [
    "ten in the morning",
    "can I take two in the afternoon please",
    "yeah ten in the morning",
])
def test_choosing_a_slot_still_gets_silence(picked):
    """The 186 the pattern was reaching for. A selection is an ANSWER; there is
    no lookup behind it and a head in front of it would promise one."""
    assert utterance_is_slot_selection(picked, OFFER), picked
    assert classify_intent(picked, READOUT, slot_selection=True) == []


def test_a_genuine_confirm_question_still_suppresses():
    """Removing one token must not disarm the rest of the pattern."""
    for q in (
        "Just to confirm, is that right?",
        "Which one of those works best?",
        "Shall I go ahead and book that?",
    ):
        assert _CONFIRM_Q.search(q), q


def test_the_selection_verdict_has_exactly_one_definition():
    """It had three: inline at the B-90 capture site, a hand-written mirror in
    the B-90 test, and the `number \\d` proxy in _CONFIRM_Q that was a worse
    version of the same question asked of the wrong sentence.

    This pins the shared one so the next reader does not write a fourth.
    """
    assert offered_slot_labels(OFFER) == {"10 in morning", "2 in afternoon"}
    assert not utterance_is_slot_selection("nine in the morning", OFFER)
    assert not utterance_is_slot_selection("mornings please", OFFER)
    # Unreadable input reports False rather than raising — this sits on the
    # live turn path and a head is a nicety.
    for junk in (None, "", 123, []):
        assert utterance_is_slot_selection(junk, OFFER) is False
    assert offered_slot_labels(None) == set()


def test_the_keypad_map_counts_as_an_offer_too():
    """A keypress injects the map value, not the spoken label list."""
    session = {"v3_dtmf_slot_map": {"1": "ten in the morning"}}
    assert utterance_is_slot_selection("ten in the morning", session)


# ═══ 2. The same sentence twice, six seconds apart ═════════════════════════
#
#   23:59:46  "I don't have any further times on that day — would you like me
#              to look at a different day?"
#   23:59:53  ... the identical sentence, after the caller said something new.

SLOTS = [{"start": "2026-09-05T10:05:00"}, {"start": "2026-09-05T11:10:00"}]


def test_the_exhaustion_sentence_is_earned_once_per_offer():
    session = {"last_offered_slots": list(SLOTS)}
    assert not exhaustion_sentence_already_said(session)
    note_exhaustion_sentence_said(session)
    assert exhaustion_sentence_already_said(session), (
        "the second ask would get the identical sentence again"
    )


def test_a_fresh_offer_earns_it_again():
    """A real lookup that puts new times on the table is a NEW fact. Latching
    on the day alone would swallow a correct answer."""
    session = {"last_offered_slots": list(SLOTS)}
    note_exhaustion_sentence_said(session)
    session["last_offered_slots"] = [{"start": "2026-09-05T12:15:00"}]
    assert not exhaustion_sentence_already_said(session)


def test_the_signature_is_order_independent():
    """The same two slots in the other order are the same offer."""
    a = {"last_offered_slots": list(SLOTS)}
    b = {"last_offered_slots": list(reversed(SLOTS))}
    assert exhaustion_offer_signature(a) == exhaustion_offer_signature(b)


def test_an_unreadable_session_never_suppresses():
    """Fails OPEN, and the direction is deliberate: the sentence is TRUE by the
    time it is reached (exhaustion_claim_is_supported has already passed), so a
    wrong False costs one repetition while a wrong True swallows a correct
    answer the first time the caller asks."""
    for bad in ({}, {"last_offered_slots": []}, {"last_offered_slots": "nope"}):
        assert exhaustion_sentence_already_said(bad) is False
        assert exhaustion_offer_signature(bad) == ""


def test_the_follow_up_path_declines_the_second_time():
    """End to end through the real dispatcher, not just the helper."""
    from app.tools import slot_followup as sf

    session = {
        "last_offered_slots": list(SLOTS),
        "available_days": [{"date": "2026-09-05", "times_not_shown": 0}],
        "collected": {},
    }
    first = sf.try_unspoken_followup_speech(session, "have you got anything else")
    assert first and "further times on that day" in first, first
    second = sf.try_unspoken_followup_speech(session, "anything else at all")
    assert second is None, (
        f"the identical sentence came back a second time: {second!r}"
    )


# ═══ 3. The session length was asked twice ════════════════════════════════
#
#   23:59:07  caller: "uh i care for a 60-minute one please"
#             engine: session length captured: 60 minutes
#   00:01:27  Susie:  "would you like a 30-minute session at thirty-eight
#                      pounds or a 60-minute at sixty-two?"
#
# The capture was right and the booking was written at a real 60 minutes. The
# model simply had no way to see that the question was settled.

def _call_state(session: dict) -> str:
    from app.clinic_config import get_clinic
    from app.prompts.clinic_template_prompt import build_clinic_prompt

    static, dynamic = build_clinic_prompt(session, get_clinic("northgate"))
    for blob in (static, dynamic):
        for line in blob.split("\n"):
            if "already known (do NOT re-ask)" in line:
                return line
    return ""


def test_a_captured_session_length_reaches_the_model():
    line = _call_state({
        "clinic_id": "northgate",
        "turn_count": 3,
        "collected": {},
        "_service_duration_choice": 60,
    })
    assert "session_length=60 minutes" in line, (
        "the captured length is not in CALL STATE, so the model cannot know "
        "the question was answered and will ask it again — A4"
    )


def test_no_length_no_line():
    """It must not assert a length the caller never chose."""
    line = _call_state({
        "clinic_id": "northgate",
        "turn_count": 3,
        "collected": {"name": "Quentin"},
    })
    assert "session_length" not in line, line


def test_it_rides_in_the_do_not_re_ask_list_rather_than_a_new_rule():
    """Deliberately NOT a suppression rule.

    "Suppression cannot beat an instruction" — three times in this codebase a
    question was deleted from the output while the schema or a prompt line
    still asked for it, and the model simply re-asked in different words. The
    value being visibly KNOWN is what closes the question.
    """
    line = _call_state({
        "clinic_id": "northgate",
        "turn_count": 3,
        "collected": {"name": "Quentin"},
        "_service_duration_choice": 90,
    })
    assert line.startswith("CALL STATE:") or "already known" in line
    assert "session_length=90 minutes" in line
    assert "name=Quentin" in line, "it must not displace the existing facts"
