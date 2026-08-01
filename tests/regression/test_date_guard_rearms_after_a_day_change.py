# tests/regression/test_date_guard_rearms_after_a_day_change.py
"""
The booking-readback date guard must not stay disarmed for the rest of a call.

CA2c2f9b6a (2 Aug 2026). The caller took Thursday, then moved to Friday and
booked it. The booking itself was correct — but Gate 5 logged, twice:

    [ms_gate5] booking readback date NOT corrected — v3_confirmed_slot_phrase
    'Thursday the 6th of August at quarter to seven in the evening' names a day
    the caller is no longer being offered; leaving the model's date alone

v3_confirmed_slot_phrase is captured once, at the name request. The caller
changed day after that, so it was never refreshed, and _confirmed_slot_is_stale
stood the guard down — correctly on that turn (forcing the abandoned day is what
lost CAb81fe651 / CA42486ff4 / CAec93b032), but it then stayed down for every
remaining turn. The C1 guard that exists because CA5c4fb14f said "Tuesday the
4th" and booked the 5th spent the rest of the call blind. Nothing sounds wrong
when it happens; the caller just arrives on the wrong day.

The fix moves the confirmed phrase onto the newly agreed day, so the guard
re-arms instead of standing down. The one condition that makes that safe is that
the new day must be the day check_availability last OFFERED — Gate 5 rewrites the
spoken text, so anything derived purely from speech could confirm the gate's own
correction and defeat the check.
"""
from __future__ import annotations

from app.media_streams.llm_stream import _note_spoken_slot_date
from app.media_streams.turn_handler import _confirmed_slot_is_stale, sanitise_response

THURSDAY = "Thursday the 6th of August at quarter to seven in the evening"
FRIDAY = "Friday the 7th of August at quarter to seven in the evening"

FRIDAY_COMMITMENT = (
    "So that's Sarah, Friday the 7th of August at quarter to seven in the "
    "evening — shall I go ahead and book that in?"
)
# What Gate 5 would produce if it were still forcing the abandoned day.
THURSDAY_REWRITE = (
    "So that's Sarah, Thursday the 6th of August at quarter to seven in the "
    "evening — shall I go ahead and book that in?"
)


def _session_mid_day_change() -> dict:
    """Thursday captured at the name request; Friday is what is now on offer."""
    return {
        "v3_confirmed_slot_phrase": THURSDAY,
        "v3_last_offered_day_iso": "2026-08-07",
        "phone_confirmed": True,
    }


class TestTheConfirmedPhraseFollowsTheCaller:
    def test_agreeing_the_new_day_moves_the_confirmed_phrase(self):
        session = _session_mid_day_change()
        _note_spoken_slot_date(session, FRIDAY_COMMITMENT)
        assert session["v3_confirmed_slot_phrase"] == FRIDAY

    def test_the_guard_is_armed_again_afterwards(self):
        session = _session_mid_day_change()
        assert _confirmed_slot_is_stale(THURSDAY, session) is True

        _note_spoken_slot_date(session, FRIDAY_COMMITMENT)
        assert _confirmed_slot_is_stale(
            session["v3_confirmed_slot_phrase"], session
        ) is False, "a refreshed phrase names the day on offer — nothing to stand down for"

    def test_a_drifted_date_is_corrected_again_after_the_change(self):
        """End to end: the C1 defect (correct day agreed, wrong day read back)
        must be caught on the turns AFTER a day change, not just before one."""
        session = _session_mid_day_change()
        _note_spoken_slot_date(session, FRIDAY_COMMITMENT)

        drifted = (
            "So that's Sarah Jenkins, Saturday the 8th of August at quarter to "
            "seven in the evening — shall I go ahead and book that in?"
        )
        assert "Friday the 7th of August" in sanitise_response(drifted, session)


class TestItCannotConfirmGate5sOwnCorrection:
    def test_a_rewrite_to_the_abandoned_day_refreshes_nothing(self):
        """The circular case. If the guard were still forcing Thursday, the
        spoken text would say Thursday — and taking that as "the caller agreed
        Thursday" would make the stale phrase permanent."""
        session = _session_mid_day_change()
        _note_spoken_slot_date(session, THURSDAY_REWRITE)
        assert session["v3_confirmed_slot_phrase"] == THURSDAY
        assert session["last_spoken_slot_phrase"].startswith("Thursday")

    def test_no_offered_day_means_no_refresh(self):
        """Fail toward today's behaviour: with nothing tool-derived to check
        against, the phrase is left exactly as it was."""
        session = _session_mid_day_change()
        session.pop("v3_last_offered_day_iso")
        _note_spoken_slot_date(session, FRIDAY_COMMITMENT)
        assert session["v3_confirmed_slot_phrase"] == THURSDAY


class TestItNeverInventsAConfirmedSlot:
    def test_the_key_is_not_created_before_the_name_request(self):
        """Three readers treat the key's PRESENCE as "a slot is locked" — the
        surname-straggler arm, the PHONE STEP OUTSTANDING line, and
        slot_followup's early return. Setting it early would move all three."""
        session = {"v3_last_offered_day_iso": "2026-08-07"}
        _note_spoken_slot_date(session, FRIDAY_COMMITMENT)
        assert "v3_confirmed_slot_phrase" not in session

    def test_an_unparseable_confirmed_phrase_is_left_alone(self):
        session = _session_mid_day_change()
        session["v3_confirmed_slot_phrase"] = "your usual time"
        _note_spoken_slot_date(session, FRIDAY_COMMITMENT)
        assert session["v3_confirmed_slot_phrase"] == "your usual time"

    def test_a_non_commitment_turn_changes_nothing(self):
        session = _session_mid_day_change()
        _note_spoken_slot_date(session, "Thanks Sarah — and your surname?")
        assert session["v3_confirmed_slot_phrase"] == THURSDAY
