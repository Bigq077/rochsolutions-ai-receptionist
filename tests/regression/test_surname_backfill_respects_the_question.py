# tests/regression/test_surname_backfill_respects_the_question.py
"""
The surname back-fill must not consume the caller's answer to a different
question.

CA6dce36c8 (31 Jul 2026). The caller gave a COMPLETE name in one turn — "for me
that'd be sara jenkins" — the preamble defeated extraction so only "Sara" was
stored, and v3_awaiting_surname armed. Six turns later, answering "half past five
or quarter past six?", she said "six". From the Render trace:

    [ms_stt] FINAL → queue: 'six'
    [clinical_screening] junk fragment skipped: 'six'
    [ms_conn v3] slot map active — time_selection:
                 {'1': 'half past five…', '2': 'quarter past six…'}
    [ms_conn v3] surname back-filled onto stored first name: 'Sara Six'
    [ms_conn v3] name persisted (normal path): 'Sara Six'

"Sara Six" would have gone onto a real calendar entry. The name is the only
free-text field with no guard behind it — a wrong day is refused at the write, a
wrong surname looks exactly like a right one — and the A3 detector scored 0 on
this call.

Two independent causes, one fixed in each layer:

  1. v3_awaiting_surname is STICKY. Nothing clears it when the conversation moves
     on, and backfill_surname's branch 3 accepts any bare single token once it is
     set. Now suppressed while a slot selection is pending, because a bare word
     then is an answer to the time question. Explicit cues ("my surname is Rock")
     still work — only the bare-straggler inference is withheld.

  2. The stoplists let clock words through. "one" was present and "six" absent,
     purely by accident. Closed as a second line of defence.

The clinical screener classified the same token as a junk fragment on the same
turn, which is the tell: one subsystem already knew "six" was not a word anyone
had offered as a name.
"""
from __future__ import annotations

import pytest

from app.name_capture import backfill_surname


class TestClockWordsAreNeverSurnames:
    @pytest.mark.parametrize("utterance", [
        "six",          # the exact token from CA6dce36c8
        "five",
        "two",
        "quarter",
        "half",
        "past",
        "evening",
        "morning",
        "tuesday",
        "wednesday",
        "tomorrow",
        "anytime",
    ])
    def test_a_time_answer_is_not_a_surname(self, utterance):
        assert backfill_surname(utterance, "Sara", awaiting_surname=True) == "", (
            f"{utterance!r} is an answer to a scheduling question — storing it "
            "as a surname puts a wrong name on a real calendar entry"
        )


class TestRealSurnamesStillLand:
    """The back-fill exists for a real defect (Call 2, 2026-07-07): STT splits
    "Quentin Rock" across two turns and the surname used to be dropped. Breaking
    that to fix this would be a straight trade, not a fix."""

    @pytest.mark.parametrize("utterance,expected", [
        ("rock", "Rock"),
        ("jenkins", "Jenkins"),
        ("rook", "Rook"),
        ("green", "Green"),
    ])
    def test_a_bare_straggler_surname_is_still_accepted(self, utterance, expected):
        assert backfill_surname(utterance, "Sara", awaiting_surname=True) == expected

    @pytest.mark.parametrize("utterance,expected", [
        ("my surname is rock", "Rock"),
        ("last name jenkins", "Jenkins"),
        ("r o c h", "Roch"),
    ])
    def test_explicit_cues_work_regardless_of_awaiting(self, utterance, expected):
        """An explicit cue is unambiguous whatever else is outstanding, so it is
        deliberately NOT gated on awaiting_surname — nor on the new window."""
        assert backfill_surname(utterance, "Sara", awaiting_surname=False) == expected

    def test_a_surname_that_is_also_a_word_still_lands_with_a_cue(self):
        """Callers really are named Green, Winter, Rich. The stoplist only
        governs the BARE straggler; an explicit cue overrides it."""
        assert backfill_surname("my surname is winter", "Sara") == "Winter"


class TestTheWindowIsBounded:
    """Layer 1 — the call site. Mirrors the guard in connection.py so the rule is
    pinned even though the surrounding method is not directly callable."""

    @staticmethod
    def _awaiting(session, last_bot=""):
        slot_pending = bool(
            session.get("v3_awaiting_slot_selection")
            or session.get("v3_dtmf_slot_map")
        )
        return (not slot_pending) and (
            bool(session.get("v3_awaiting_surname")) or any(
                k in (last_bot or "").lower()
                for k in ("surname", "last name", "family name", "full name")
            )
        )

    def test_suppressed_while_a_slot_selection_is_pending(self):
        """The exact state of CA6dce36c8 when "six" arrived."""
        session = {
            "v3_awaiting_surname": True,
            "v3_awaiting_slot_selection": True,
            "v3_dtmf_slot_map": {"1": "half past five", "2": "quarter past six"},
        }
        assert self._awaiting(session) is False

    def test_active_when_the_surname_is_genuinely_outstanding(self):
        assert self._awaiting({"v3_awaiting_surname": True}) is True

    def test_active_when_the_last_prompt_asked_for_the_surname(self):
        assert self._awaiting({}, "Thanks Sara — and your surname?") is True

    def test_a_surname_ask_during_slot_selection_still_defers(self):
        """Belt and braces: even if both are somehow set, the pending slot
        question wins. A bare word is an answer to the question just asked."""
        session = {"v3_awaiting_surname": True, "v3_awaiting_slot_selection": True}
        assert self._awaiting(session, "and your surname?") is False

    def test_inactive_when_nothing_is_outstanding(self):
        assert self._awaiting({}, "Right — what's the appointment for?") is False
