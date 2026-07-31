# tests/regression/test_gate5_never_rewrites_an_abandoned_day.py
"""
Gate 5's booking-readback date enforcement must not overwrite a day the caller
has since moved to.

THE BUG (CAec93b032, CA42486ff4, CAb81fe651 — 30/31 Jul 2026)
Three callers took a Tuesday slot, gave name and number, then changed to
Wednesday. All three heard Susie read back "Tuesday the 4th of August at quarter
past six" — Wednesday's TIME on Tuesday's DATE, an appointment that existed on no
calendar. All three hung up. No booking.

The model was not at fault. It generated "Wednesday the 5th of August at quarter
past six" correctly. Gate 5 rewrote the date on the way out.

The enforcement (2026-07-07) exists for a real defect: the model spoke "the 16th"
when "the 15th" had been confirmed, so once the phone is confirmed any
"<weekday> the <ordinal> of <month>" in a chunk is forced to the date inside
v3_confirmed_slot_phrase. That phrase is captured ONCE, at the name request, and
a caller who changes day afterwards never refreshes it — so the gate spends the
rest of the call forcing the abandoned day over the correct one.

WHY THIS WAS INVISIBLE FOR SO LONG
- The slot LIST survives: "Wednesday 5th August — Number 1…" has no "the" and no
  "of", so it does not match and is never rewritten. Availability sounded right
  while every readback was wrong.
- obs stores the SPOKEN text for calls after 29 Jul — i.e. post-Gate-5. Reading
  the transcript shows the rewritten sentence, not what the model produced, so
  the model looked guilty.
- Four fixes (7c140f4, 5b0c9c2, 6f63057, cdc2177) all targeted the model's input
  or generation. Gate 5 runs after generation, on the output string, so none of
  them could survive it.

THE RULE
Correcting a drifted date is the gate's job. Overriding a decision is not. When
the confirmed phrase names a different day from the slot actually being held, the
gate has no way to know which is right — and rewriting a correct day into a wrong
one is strictly worse than leaving a typo alone. It stands down.
"""
from __future__ import annotations

import pytest

from app.media_streams.turn_handler import sanitise_response

TUESDAY_CONFIRMED = "Tuesday the 4th of August at half past six in the evening"

# What the model generated on CAec93b032 after the caller moved to Wednesday.
MODEL_SAID = (
    "So that's Sarah, Wednesday the 5th of August at quarter past six in the "
    "evening — shall I go ahead and book that in?"
)
# What the caller actually heard.
CALLER_HEARD = (
    "So that's Sarah, Tuesday the 4th of August at quarter past six in the "
    "evening — shall I go ahead and book that in?"
)


# What check_availability returned after the caller asked for Wednesday. This is
# the signal staleness is judged against — it comes from the tool, so unlike the
# spoken transcript this gate cannot have rewritten it.
WEDNESDAY_OFFERED = [
    {"start": "2026-08-05T17:30:00", "end": "2026-08-05T18:00:00"},
    {"start": "2026-08-05T18:15:00", "end": "2026-08-05T18:45:00"},
]
TUESDAY_OFFERED = [
    {"start": "2026-08-04T17:45:00", "end": "2026-08-04T18:15:00"},
    {"start": "2026-08-04T18:30:00", "end": "2026-08-04T19:00:00"},
]


def _session(**over):
    s = {
        "v3_confirmed_slot_phrase": TUESDAY_CONFIRMED,
        "phone_confirmed": True,
        # The caller has moved to Wednesday and been offered Wednesday slots.
        "last_offered_slots": WEDNESDAY_OFFERED,
        "last_spoken_slot_date": "2026-08-05",
        "clinic_id": "jv_v1",
    }
    s.update(over)
    return s


class TestTheCallThatWasLost:
    def test_the_correct_day_survives_the_gate(self):
        """The exact regression. Fails before the fix, passes after."""
        out = sanitise_response(MODEL_SAID, _session())
        assert "Wednesday the 5th of August" in out, (
            "Gate 5 rewrote the day the caller had just chosen back to the one "
            "they abandoned — this is what three callers heard before hanging up"
        )
        assert out != CALLER_HEARD

    def test_the_hybrid_is_never_produced(self):
        """Wednesday's time on Tuesday's date is an appointment on no calendar."""
        out = sanitise_response(MODEL_SAID, _session())
        assert not ("Tuesday" in out and "quarter past six" in out), (
            "Tuesday never had a quarter-past-six slot — its offers were 5:45 "
            "and 6:30"
        )

    def test_the_write_guards_own_resteer_survives(self):
        """27c59a5 made the write-guard name the real day. Gate 5 rewrote that
        too, which is why that fix appeared not to work."""
        resteer = (
            "Just to double-check — the slot I have is Wednesday the 5th of "
            "August at quarter past six in the evening. Is that the one you'd like?"
        )
        assert "Wednesday the 5th of August" in sanitise_response(resteer, _session())


class TestTheOriginalDefectStaysFixed:
    """2026-07-07: confirmed "the 15th", readback drifted to "the 16th". That is
    what this enforcement is for and it must keep working."""

    def test_a_drifted_date_on_the_same_day_is_still_corrected(self):
        session = _session(
            v3_confirmed_slot_phrase="Wednesday the 15th of August at ten in the morning",
            last_offered_slots=[{"start": "2026-08-15T10:00:00"}],
            last_spoken_slot_date="2026-08-15",
        )
        out = sanitise_response(
            "So that's Sarah, Wednesday the 16th of August at ten in the "
            "morning — shall I go ahead and book that in?",
            session,
        )
        assert "Wednesday the 15th of August" in out, (
            "a genuine drift, with no day change, must still be corrected"
        )

    def test_correction_applies_while_the_confirmed_day_is_still_on_offer(self):
        """The caller has NOT moved: Tuesday is confirmed and Tuesday is what is
        being offered. Standing down here would re-open the 2026-07-07 drift."""
        out = sanitise_response(
            "So that's Sarah, Tuesday the 11th of August at half past six in "
            "the evening — shall I go ahead and book that in?",
            _session(last_offered_slots=TUESDAY_OFFERED),
        )
        assert "Tuesday the 4th of August" in out

    def test_correction_still_applies_when_no_day_change_is_recorded(self):
        """Nothing to compare against — the gate keeps its original behaviour, so
        this is not a regression for callers who never change day."""
        session = _session(
            v3_confirmed_slot_phrase="Wednesday the 15th of August at ten in the morning",
            last_offered_slots=None,
            last_spoken_slot_date=None,
        )
        out = sanitise_response(
            "So that's Sarah, Wednesday the 16th of August at ten in the morning.",
            session,
        )
        assert "Wednesday the 15th of August" in out


class TestItDoesNotOverreach:
    def test_the_slot_list_is_untouched(self):
        """It never matched the pattern, but pin it — a widened pattern that
        started rewriting availability would be far worse than the bug."""
        listing = (
            "Wednesday 5th August — Number 1, half past five in the evening. "
            "Number 2, quarter past six in the evening. Any of those work?"
        )
        assert sanitise_response(listing, _session()) == listing

    def test_nothing_happens_before_the_phone_is_confirmed(self):
        out = sanitise_response(MODEL_SAID, _session(phone_confirmed=False))
        assert "Wednesday the 5th of August" in out

    def test_nothing_happens_without_a_confirmed_slot(self):
        out = sanitise_response(MODEL_SAID, _session(v3_confirmed_slot_phrase=""))
        assert "Wednesday the 5th of August" in out

    @pytest.mark.parametrize("phrase", ["", "your usual time", "next week sometime"])
    def test_an_unparseable_confirmed_phrase_changes_nothing(self, phrase):
        """No date to force — the gate must leave the text alone rather than
        guess."""
        out = sanitise_response(MODEL_SAID, _session(v3_confirmed_slot_phrase=phrase))
        assert "Wednesday the 5th of August" in out


class TestItSurvivesTheSlotCacheClear:
    """CA6dce36c8 (31 Jul 2026). The first version of this check used
    last_offered_slots alone. The logs caught it failing mid-call:

        01:56:33  NOT corrected  — stood down, Wednesday spoken correctly
        01:56:46  slot cache cleared: day iso='2026-08-05'
        01:57:20  corrected to confirmed slot: 'Tuesday the 4th of August'

    Clearing the slot cache nulls last_offered_slots, the check returned "not
    stale" by design, and the gate resumed forcing the abandoned day. The caller
    heard Tuesday, hung up, and the booking never happened — even though the model
    had passed the CORRECT slot_iso (2026-08-05T18:15:00) to book_appointment.

    v3_last_offered_day_iso is the durable signal: preserved across slot-map
    clears, dropped only on a successful booking.
    """

    def test_stands_down_after_the_slot_cache_is_cleared(self):
        session = _session(
            last_offered_slots=None,               # cleared at 01:56:46
            v3_last_offered_day_iso="2026-08-05",  # survives
        )
        out = sanitise_response(MODEL_SAID, session)
        assert "Wednesday the 5th of August" in out, (
            "the day last OFFERED must outlive the slot cache — this is the "
            "exact state CA6dce36c8 was in when the gate resumed rewriting"
        )

    def test_the_day_iso_outranks_a_stale_offered_batch(self):
        """If both are present and disagree, the recorded offered DAY wins — it
        is the one that is deliberately preserved."""
        session = _session(
            last_offered_slots=TUESDAY_OFFERED,
            v3_last_offered_day_iso="2026-08-05",
        )
        assert "Wednesday the 5th of August" in sanitise_response(MODEL_SAID, session)

    def test_correction_still_applies_when_the_offered_day_matches(self):
        """No day change: the confirmed phrase IS the day on offer, so a drifted
        date must still be corrected."""
        session = _session(
            last_offered_slots=None,
            v3_last_offered_day_iso="2026-08-04",   # Tuesday, as confirmed
        )
        out = sanitise_response(
            "So that's Sara, Tuesday the 11th of August at half past six in "
            "the evening — shall I go ahead and book that in?",
            session,
        )
        assert "Tuesday the 4th of August" in out

    @pytest.mark.parametrize("bad", ["", None, "not-a-date", "2026"])
    def test_an_unusable_day_iso_falls_back_and_never_raises(self, bad):
        """Runs on every spoken chunk. An exception here costs the turn."""
        session = _session(
            last_offered_slots=WEDNESDAY_OFFERED,
            v3_last_offered_day_iso=bad,
        )
        assert "Wednesday the 5th of August" in sanitise_response(MODEL_SAID, session)
