# tests/regression/test_gate5_audit_can_see_stateful_gates.py
"""
The Gate 5 blast-radius audit must be able to see gates that read session state.

On 2026-07-31 that audit reported "5 changed turns of 740, 0 emptied — clean" on
the same day Gate 5 was rewriting the caller's chosen booking day back to one
they had abandoned, on every call where anyone changed their mind. It had been
doing that for three weeks. The clean run was false for two independent reasons:

1. Every turn was replayed with `session = {}`, so any gate reading session state
   never executed in either arm. The booking-readback date enforcement needs
   v3_confirmed_slot_phrase and phone_confirmed; with neither set it was dead
   code in the audit while being very much alive in production.

2. obs stores the SPOKEN text — post-Gate-5 — so replaying it through a REWRITE
   rule re-applies a change already baked in. Output equals input, diff empty.
   Live, the gate turned "Wednesday the 5th" into "Tuesday the 4th"; on replay it
   turns "Tuesday the 4th" into "Tuesday the 4th". No diff can ever show it.

A verification instrument with a hole in it is worse than none, because it buys
false confidence: the population test was run, came back clean, and everyone
believed it. These tests pin both fixes so the instrument cannot quietly go blind
again.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("scripts").resolve()))
import audit_gate5_blast_radius as audit  # noqa: E402


class TestSessionReconstruction:
    """Fix 1 — the audit rebuilds the state the gates actually read."""

    def test_a_slot_batch_records_the_day_on_offer(self):
        s = {}
        audit.advance_session(
            s, "bot",
            "Wednesday 5th August — Number 1, half past five in the evening. "
            "Number 2, quarter past six in the evening. Any of those work?")
        assert s["v3_last_offered_day_iso"] == "2026-08-05"
        assert s["v3_awaiting_slot_selection"] is True
        assert s["last_offered_slots"], "staleness fallback needs these too"

    def test_prose_mentioning_a_date_is_not_a_slot_batch(self):
        """Only a NUMBERED batch is an offer. Treating a confirmation sentence as
        one would make every readback look like a fresh day on the table."""
        s = {}
        audit.advance_session(
            s, "bot",
            "So that's Quinton, Wednesday the 5th of August at half past five "
            "in the evening — shall I go ahead and book that in?")
        assert "v3_last_offered_day_iso" not in s

    def test_the_name_request_captures_the_confirmed_phrase(self):
        s = {}
        audit.advance_session(
            s, "bot",
            "So that's Tuesday the 4th of August at half past six in the "
            "evening — could I take your first name and surname?")
        assert s["v3_confirmed_slot_phrase"] == \
            "Tuesday the 4th of August at half past six in the evening"

    def test_the_confirmed_phrase_is_captured_once_and_goes_stale(self):
        """This is the production behaviour the gate has to cope with:
        connection.py captures it at the name request and never refreshes it. If
        the audit refreshed it, the staleness bug would be unreproducible here."""
        s = {}
        audit.advance_session(
            s, "bot",
            "So that's Tuesday the 4th of August at half past six in the "
            "evening — could I take your first name and surname?")
        audit.advance_session(
            s, "bot",
            "So that's Quinton, Wednesday the 5th of August at quarter past six "
            "in the evening — could I take your first name and surname?")
        assert s["v3_confirmed_slot_phrase"].startswith("Tuesday"), (
            "the audit must not refresh what production never refreshes"
        )

    def test_phone_confirmation_needs_the_question_then_the_answer(self):
        s = {}
        audit.advance_session(s, "user", "yes that's the best number")
        assert "phone_confirmed" not in s, "a bare yes with no question asked"

        audit.advance_session(
            s, "bot", "I've got you on 07502 211 207, is that the best number?")
        audit.advance_session(s, "user", "yes that's the best number")
        assert s["phone_confirmed"] is True

    def test_a_declined_phone_does_not_confirm(self):
        s = {}
        audit.advance_session(
            s, "bot", "I've got you on 07502 211 207, is that the best number?")
        audit.advance_session(s, "user", "no, use a different number")
        assert "phone_confirmed" not in s


class TestFiringsAreCounted:
    """Fix 2 — a rewrite that changes nothing on replay is still visible."""

    def _record(self, messages):
        rec = audit.GateRecorder()
        log = logging.getLogger("test.gate5.recorder")
        log.setLevel(logging.INFO)
        log.addHandler(rec)
        try:
            for m in messages:
                log.info(m)
        finally:
            log.removeHandler(rec)
        return rec.counts

    def test_gate_lines_are_counted_and_grouped(self):
        counts = self._record([
            "[ms_gate5] booking readback date corrected to confirmed slot: 'Tuesday the 4th'",
            "[ms_gate5] booking readback date corrected to confirmed slot: 'Friday the 8th'",
            "[ms_gate5] removed banned phrase (banned_opener)",
        ])
        assert counts["booking readback date corrected to confirmed slot"] == 2, (
            "the variable part must be stripped so the same gate groups together"
        )
        assert counts["removed banned phrase"] == 1

    def test_non_gate_lines_are_ignored(self):
        counts = self._record([
            "[ms_conn] barge-in: partial='yes'",
            "[ms_llm] iteration=1 model=claude-sonnet-4-6",
        ])
        assert counts == {}

    def test_a_recorder_survives_a_bad_record(self):
        """This runs over the whole population; one malformed log line must not
        abort the audit."""
        rec = audit.GateRecorder()

        class Bad:
            def getMessage(self):
                raise ValueError("boom")

        rec.emit(Bad())          # must not raise
        assert rec.counts == {}


class TestTheOldArmStillHasTheDefect:
    """The audit is only useful if OLD reproduces the behaviour being replaced.
    old_sanitise neutralises the stand-down; if a future edit forgets to, the
    comparison silently becomes a no-op — the failure mode this file exists for.
    """

    def test_the_stand_down_is_named_in_the_old_arm(self):
        src = Path("scripts/audit_gate5_blast_radius.py").read_text(encoding="utf-8")
        assert "_confirmed_slot_is_stale" in src, (
            "old_sanitise must neutralise the readback stand-down, or the audit "
            "reports 0 changes for a change that is really there"
        )

    def test_the_replay_shares_one_session_across_a_call(self):
        """Per-turn sessions were the original blind spot."""
        src = Path("scripts/audit_gate5_blast_radius.py").read_text(encoding="utf-8")
        assert "def speak(raw: str, sanitise, session: dict)" in src, (
            "speak() must take the call's session rather than making a fresh one"
        )
