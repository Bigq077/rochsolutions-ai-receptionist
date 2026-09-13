"""
Regression: Gate 5g deadlocked the name step and the caller hung up.

CA041352eb04a40fcb5ebd13ee37379722 (2026-08-08, build 03929f24bca6) ended
`outcome=abandoned name=None dur=89s`. The caller gave his name three times and
was asked a fourth:

    00:01:27  caller: "um yeah that would be quentin rook"
    00:01:28  Susie:  "Before I do that — could I take your first name and surname?"
    00:01:36  caller: "yeah that'll be quentin rook"
    00:01:38  Susie:  (identical sentence)
    00:01:45  caller: "yeah i said that would be quentin rook"
    00:01:47  Susie:  (identical sentence)
              caller hangs up

Every one of those turns logged `[ms_gate5] booking CTA held back — name missing`.

THE LOOP — the gate destroys the evidence that would let it stop firing:

  1. The caller answers with his name.
  2. The model replies acknowledging it AND reaching for the booking CTA:
     "Thanks Quentin — shall I go ahead and book that in?"
  3. Gate 5g tests _name_known(session) → False (storage happens AFTER the reply
     is scanned) and substitutes the CTA sentence with the name question.
  4. _append_history stores the SPOKEN text — post-gate — deliberately, since
     2026-08-02 (CA7d46c2bc: the model used to read back its own ungated claims).
  5. _v3_try_persist_name scans that spoken text for an acknowledgement. The
     gate just deleted it. Nothing is stored.
  6. _name_known is still False → step 3, forever.

⚠️ The FIRST name is only ever learned from Susie's own speech. The caller's
utterance is used solely to recover a surname. So any turn whose acknowledgement
is rewritten loses the name however clearly the caller said it.

Why it did NOT fire on CA1e755281 forty minutes earlier: there the model replied
"Thanks Quentin — is oh, seven, five…" with no CTA, so Gate 5g never ran and the
readback survived. The deadlock needs the model to acknowledge the name and
offer to book in one reply — the better it behaves, the likelier it hangs.

"rook" vs "rock" is a red herring: the patterns capture the FIRST name, which
was "Quentin" in both calls.

TWO LAYERS SINCE N-6 (CAe3023240, 13 Sep 2026). The loop above had a root:
"storage happens AFTER the reply is scanned" (step 3). Gate 5g now asks the
same reader FIRST, on the chunks spoken so far plus the one in hand, ANCHORED
patterns only — so an acknowledgement in "Thanks <Name>" or "So that's <Name>,"
form is stored before the CTA is judged, and the sentence that replaces it asks
for the PHONE. The one-sentence form still loses its readback (the accepted
trade); it no longer loses the name.

O-18 — the raw-reply recovery after the turn — is the second layer, for an
acknowledgement the anchored reader cannot see: the BARE form ("Quentin — got
it, shall I book that in?") needs the phase signal that lives in connection.py.
That is the fixture this file now uses to pin it.
"""
from __future__ import annotations

from app.media_streams.connection import _v3_try_persist_name
from app.media_streams.turn_handler import sanitise_response


# ⚠️ SENTENCE BOUNDARIES DECIDE WHETHER THE READBACK IS LOST. _BOOKING_CTA_SENTENCE_RE
# matches a whole SENTENCE, so:
#
#   one sentence   "Thanks Quentin, shall I go ahead and book that in?"
#                  → the acknowledgement is inside the sentence being deleted and
#                    goes with it. Since N-6 the NAME is read first, so what is
#                    lost is the words, not the fact.
#
#   two sentences  "Thanks Quentin — that's Monday… Shall I book that in?"
#                  → only the CTA sentence goes; "Thanks Quentin" survives.
#
# The one-sentence form is what CA041352eb hit: the synthesised chunk at
# 00:01:28 was 'Before I do that — could I take your first name and surname?'
# alone, len=60, with no readback in front of it.
RAW_ACK_PLUS_CTA = "Thanks Quentin, shall I go ahead and book that in?"

RAW_ACK_THEN_CTA_TWO_SENTENCES = (
    "Thanks Quentin — that's Monday the 10th of August at three in the "
    "afternoon. Shall I go ahead and book that in?"
)

#: The BARE acknowledgement form. No "Thanks", no "So that's" — the anchored
#: reader Gate 5g uses cannot see a name here, so this is the shape that still
#: needs O-18.
RAW_BARE_ACK_PLUS_CTA = "Quentin — got it, shall I go ahead and book that in?"

CALLER = "um yeah that would be quentin rook"


def test_the_two_sentence_form_was_never_broken():
    """
    Pins the boundary. Here the acknowledgement is its own sentence and
    survives the CTA strip. If this ever starts failing, the CTA pattern has
    widened to eat neighbouring sentences and the blast radius is much larger.
    """
    session = {"booking_flow_active": True}
    spoken = sanitise_response(RAW_ACK_THEN_CTA_TWO_SENTENCES, session)
    assert "thanks quentin" in spoken.lower()
    assert session["patient_name"].split()[0] == "Quentin"


# ── 1. the gate still does its job ──────────────────────────────────────────

def test_the_cta_is_still_held_back_when_a_step_is_missing():
    """Neither layer disarms Gate 5g — the CTA hold-back is correct. With the
    name read out of the reply, the step outstanding is the phone."""
    session = {"booking_flow_active": True}
    spoken = sanitise_response(RAW_ACK_PLUS_CTA, session)
    assert "book that in" not in spoken.lower()
    assert "first name and surname" not in spoken.lower()
    assert "number" in spoken.lower()


def test_the_acknowledgement_words_are_still_lost_on_the_one_sentence_form():
    """The precondition for the old bug — stated so the boundary cannot drift.
    The words go; the name does not (next test)."""
    session = {"booking_flow_active": True}
    spoken = sanitise_response(RAW_ACK_PLUS_CTA, session)
    assert "quentin" not in spoken.lower()


# ── 2. layer one: the name is read before the sentence is deleted (N-6) ─────

def test_the_anchored_ack_is_stored_before_the_cta_is_judged():
    session = {"booking_flow_active": True, "_turn_user_text": CALLER}
    sanitise_response(RAW_ACK_PLUS_CTA, session)
    assert session["patient_name"] == "Quentin Rook"
    assert session["_gate5g_persisted_name"] is True
    assert not session.get("_gate5g_dropped_name_ack"), (
        "nothing was lost, so the recovery must not be armed"
    )


def test_the_name_question_is_never_asked_when_the_reply_carries_the_name():
    """The whole point, restated for layer one. CA041352eb's turn: the caller
    has just said his name and the model acknowledged it."""
    session = {"booking_flow_active": True}
    spoken = sanitise_response(RAW_ACK_PLUS_CTA, session)
    assert "first name and surname" not in spoken.lower(), (
        "the caller was asked for their name again — this is the loop that "
        "abandoned CA041352eb"
    )


# ── 3. layer two: O-18, for the ack the anchored reader cannot see ──────────

def test_the_bare_ack_is_invisible_to_the_gate_and_sets_the_recovery_flag():
    session = {"booking_flow_active": True}
    spoken = sanitise_response(RAW_BARE_ACK_PLUS_CTA, session)
    assert "quentin" not in spoken.lower()
    assert not session.get("patient_name")
    assert session.get("_gate5g_dropped_name_ack") is True
    assert "first name and surname" in spoken.lower()


def test_the_flag_is_not_set_when_the_name_is_already_known():
    """Only the NAME case loses evidence. A phone hold-back must not set it."""
    session = {"booking_flow_active": True, "patient_name": "Quentin Rock"}
    sanitise_response(RAW_BARE_ACK_PLUS_CTA, session)
    assert not session.get("_gate5g_dropped_name_ack")


def test_the_spoken_text_alone_cannot_yield_the_bare_name():
    """This is the O-18 bug, stated directly: after the gate has run, the
    text the persist step sees contains no acknowledgement."""
    session = {"booking_flow_active": True}
    spoken = sanitise_response(RAW_BARE_ACK_PLUS_CTA, session)
    assert _v3_try_persist_name(
        session, spoken, post_slot_pending=True, caller_utterance=CALLER,
    ) is False
    assert not session.get("patient_name")


def test_the_raw_reply_still_carries_the_bare_name():
    """The recovery source. session["turns"] keeps raw_text for exactly this."""
    session = {"booking_flow_active": True}
    assert _v3_try_persist_name(
        session, RAW_BARE_ACK_PLUS_CTA, post_slot_pending=True, caller_utterance=CALLER,
    ) is True
    assert session["patient_name"].split()[0] == "Quentin"


def test_the_name_question_is_not_repeated_once_the_name_is_recovered():
    """Turn N holds the CTA back and O-18 stores the name from the raw reply;
    turn N+1 must therefore NOT ask for the name again."""
    session = {"booking_flow_active": True}
    sanitise_response(RAW_BARE_ACK_PLUS_CTA, session)
    assert session.pop("_gate5g_dropped_name_ack") is True
    _v3_try_persist_name(
        session, RAW_BARE_ACK_PLUS_CTA, post_slot_pending=True, caller_utterance=CALLER,
    )
    spoken = sanitise_response(RAW_BARE_ACK_PLUS_CTA, session)
    assert "first name and surname" not in spoken.lower()


# ── 4. the recovery window stays narrow ─────────────────────────────────────

def test_the_flag_is_consumed_not_left_standing():
    """A sticky True would let a LATER turn read a name out of an unrelated
    raw reply (v3_awaiting_surname once back-filled 'Sara Six' from a slot
    number). The call site pops it; this pins that it is poppable."""
    session = {"booking_flow_active": True}
    sanitise_response(RAW_BARE_ACK_PLUS_CTA, session)
    assert session.pop("_gate5g_dropped_name_ack", False) is True
    assert session.pop("_gate5g_dropped_name_ack", False) is False


def test_a_turn_with_no_cta_does_not_set_the_flag():
    """CA1e755281's shape — acknowledgement, no CTA. Nothing is deleted."""
    session = {"booking_flow_active": True}
    sanitise_response(
        "Thanks Quentin — is oh, seven, five, oh, two the best number for you?",
        session,
    )
    assert not session.get("_gate5g_dropped_name_ack")
