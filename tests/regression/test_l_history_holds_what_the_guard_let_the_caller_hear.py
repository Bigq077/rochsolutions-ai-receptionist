"""Defect L: the model's history must hold what the guard let the caller hear.

`CAddd98ce0`, northgate demo line, 12 Sep 2026 20:52, build 8461c4bd -- the
call that proved A and B:

    20:52:12,829  slot_guard REPLACED a slot fact: "The nearest I've got to
                  four in the afternoon is twenty past four in the afternoon
                  on Tuesday 15th September. Shall I bo…" -> 'Sorry — let me
                  just double-check that one for you.'
    20:52:12,829  offer RETRACTED by the fact guard -- 1 slot(s) un-recorded
    ...           (no `caller ACCEPTED` anywhere; collected.selected_slot null;
                   slot_offers[0].spoken false)
    20:53:08      model: "so that's Quentin Road, Tuesday the 15th of
                  September at twenty past four in the afternoon — shall I go
                  ahead and book that in?"

The engine's record was clean and the model overrode it from memory:
`_append_history` stores "the post-Gate-5 text", the fact guard runs one seam
later in `_tts_loop`, so `conversation_history` held the retracted offer as
if it had been said. The read-back guard then PASSED the sentence, because
16:20 is in the diary (invariant 4, not invariant 1 -- exactly the D-s note).

The 1 Aug Gate 5f defect (CA7d46c2bc) was this shape one guard earlier:
speech rewritten, history recorded the claim, the model believed itself.

Fix: the guard records every replacement (`note_replacement`);
`_append_history` stores the rewritten text (`rewrite_as_heard`); and the
REPLACED site rewrites an entry already appended
(`apply_replacements_to_history`). Both directions, because the append and
the TTS loop race.
"""
from __future__ import annotations

from app.media_streams.llm_stream import _append_history
from app.tools import slot_fact_guard as guard
from app.tools.slot_fact_guard import (
    apply_replacements_to_history,
    note_replacement,
    rewrite_as_heard,
)

OFFER = (
    "The nearest I've got to four in the afternoon is twenty past four in the "
    "afternoon on Tuesday 15th September. Shall I book that in for you?"
)
NAME_Q = "Before I do that — could I take your first name and surname?"
REPLY = OFFER + " " + NAME_Q
RECOVERY = guard.RECOVERY_SENTENCE


def _assistant_entries(session):
    return [e["content"] for e in session["conversation_history"] if e["role"] == "assistant"]


def test_a_replacement_noted_before_the_append_is_what_history_stores():
    session = {}
    note_replacement(session, OFFER, RECOVERY)
    _append_history(session, "uh yes go for it", REPLY)
    (stored,) = _assistant_entries(session)
    assert "twenty past four" not in stored, stored
    assert stored.startswith(RECOVERY)
    assert NAME_Q in stored


def test_a_replacement_noted_after_the_append_rewrites_the_entry():
    session = {}
    _append_history(session, "uh yes go for it", REPLY)
    assert "twenty past four" in _assistant_entries(session)[0]
    note_replacement(session, OFFER, RECOVERY)
    assert apply_replacements_to_history(session) == 1
    (stored,) = _assistant_entries(session)
    assert "twenty past four" not in stored, stored
    assert stored.startswith(RECOVERY)


def test_the_guard_sees_the_tts_split_sentence_not_the_whole_reply():
    """`_tts_loop` hands the guard sentences; history holds the joined reply."""
    session = {}
    _append_history(session, "uh yes go for it", REPLY)
    first_sentence = OFFER.split(". ")[0] + "."
    note_replacement(session, first_sentence, RECOVERY)
    note_replacement(session, "Shall I book that in for you?", "")  # dropped tail
    assert apply_replacements_to_history(session) == 1
    (stored,) = _assistant_entries(session)
    assert "twenty past four" not in stored
    assert "Shall I book" not in stored
    assert NAME_Q in stored


def test_a_dropped_watchdog_reask_leaves_no_trace():
    session = {}
    reask = "Sorry, I didn't catch that. Before I do that — could I take your first name and surname?"
    note_replacement(session, reask, "")
    assert rewrite_as_heard(session, reask) == ""


def test_identity_when_nothing_was_replaced():
    session = {}
    assert rewrite_as_heard(session, REPLY) == REPLY
    _append_history(session, "hi", REPLY)
    assert _assistant_entries(session) == [REPLY]
    assert apply_replacements_to_history(session) == 0


def test_never_raises_on_a_bad_session():
    assert rewrite_as_heard(None, "x") == "x"  # type: ignore[arg-type]
    note_replacement(None, "a", "b")  # type: ignore[arg-type]
    assert apply_replacements_to_history({"conversation_history": "nope"}) == 0
