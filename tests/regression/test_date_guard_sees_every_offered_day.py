# tests/regression/test_date_guard_sees_every_offered_day.py
"""
The booking-readback date guard must judge staleness against every day on
offer, not just the first one in the payload.

CA6e1024db (2 Aug 2026). The caller said "not really mate, anytime", so
check_availability ran with date_hint:"any" and returned a MULTI-DAY payload.
They took the third day (Saturday the 8th) and never changed their mind. Gate 5
logged, four times:

    [ms_gate5] booking readback date NOT corrected — v3_confirmed_slot_phrase
    'Saturday the 8th of August at half past ten in the morning' names a day the
    caller is no longer being offered; leaving the model's date alone

The phrase was never stale. v3_last_offered_day_iso is WRITTEN as days[0] of the
payload (llm_stream ~1410, connection ~9280) but was READ here as "the day the
caller is being offered". Those are the same thing only when the payload has one
day — and date_hint:"any", i.e. "no preference", is the commonest answer a real
caller gives. So the guard reported "no longer being offered" about the only day
the caller had ever chosen, and stayed blind for the rest of the call.

session["available_days"] is the correct signal: _filter_same_day_slots
(receptionist_tools ~3795) assigns the SAME filtered list to the tool result and
to the session, so it is byte-identical to the model's offer surface — and it
comes from the tool, so Gate 5 cannot rewrite it. That last property is
load-bearing: this gate edits the spoken text, so any signal derived from speech
would feed the gate its own output and self-confirm.

The day-set alone is not sufficient. A caller who moves between two days that
are BOTH in one payload leaves an abandoned day that is still "on offer", and
the set test would call the stale phrase current — CAb81fe651 / CA42486ff4 /
CAec93b032, where three callers heard Wednesday's time on Tuesday's date and
hung up. v3_slot_phrase_superseded covers exactly that gap, set from the
caller's own transcript by the DIFFERENT DAY REQUESTED steer.
"""
from __future__ import annotations

import inspect

from app.media_streams.llm_stream import _note_spoken_slot_date
from app.media_streams.turn_handler import _confirmed_slot_is_stale, sanitise_response

SATURDAY = "Saturday the 8th of August at half past ten in the morning"
THURSDAY = "Thursday the 6th of August at half past four in the afternoon"

# The payload CA6e1024db actually received: three days, Saturday third.
MULTI_DAY = [
    {"date": "2026-08-06", "day_label": "Thursday 6th August"},
    {"date": "2026-08-07", "day_label": "Friday 7th August"},
    {"date": "2026-08-08", "day_label": "Saturday 8th August"},
]


def _ca6e1024db() -> dict:
    """Saturday confirmed off a multi-day offer; the scalar still names day one."""
    return {
        "v3_confirmed_slot_phrase": SATURDAY,
        "v3_last_offered_day_iso": "2026-08-06",   # days[0] — the wrong signal
        "available_days": list(MULTI_DAY),
        "phone_confirmed": True,
    }


class TestADayFurtherDownThePayloadIsNotStale:
    def test_the_confirmed_day_is_current_when_it_is_still_on_offer(self):
        """The defect, exactly. Pre-fix this returned True off the day-one
        scalar and disarmed the guard for the whole call."""
        assert _confirmed_slot_is_stale(SATURDAY, _ca6e1024db()) is False

    def test_the_guard_actually_corrects_a_drifted_date_again(self):
        """Not just 'not stale' — the guard has to do its job. A readback that
        drifts to a day the caller never picked must be pulled back."""
        session = _ca6e1024db()
        drifted = (
            "So that's Quentin, Sunday the 9th of August at half past ten in "
            "the morning — shall I go ahead and book that in?"
        )
        out = sanitise_response(drifted, session)
        assert "Saturday the 8th of August" in out
        assert "Sunday the 9th of August" not in out

    def test_a_day_absent_from_the_payload_is_still_stale(self):
        """The widening must not swallow real staleness — a day in no current
        payload is genuinely abandoned."""
        session = _ca6e1024db()
        session["v3_confirmed_slot_phrase"] = "Tuesday the 4th of August at six"
        assert _confirmed_slot_is_stale(
            "Tuesday the 4th of August at six", session
        ) is True

    def test_every_day_in_the_payload_counts_not_only_the_last(self):
        for phrase, ok in (
            ("Thursday the 6th of August at four", False),
            ("Friday the 7th of August at four", False),
            ("Saturday the 8th of August at four", False),
            ("Monday the 10th of August at four", True),
        ):
            session = _ca6e1024db()
            session["v3_confirmed_slot_phrase"] = phrase
            assert _confirmed_slot_is_stale(phrase, session) is ok, phrase


class TestAnExplicitDayChangeStillStandsTheGuardDown:
    """CAb81fe651 preserved. Both days are in the payload, so the day-set test
    alone would call the abandoned day current and force it forever."""

    def test_superseded_beats_the_day_set(self):
        session = _ca6e1024db()
        session["v3_slot_phrase_superseded"] = True
        assert _confirmed_slot_is_stale(SATURDAY, session) is True

    def test_the_gate_leaves_the_new_day_alone_while_superseded(self):
        session = _ca6e1024db()
        session["v3_slot_phrase_superseded"] = True
        moving = (
            "So that's Quentin, Thursday the 6th of August at half past four "
            "in the afternoon — could I take your first name and surname?"
        )
        assert "Thursday the 6th of August" in sanitise_response(moving, session)

    def test_the_steer_is_what_sets_it(self):
        """Set from the caller's transcript, the one input to this decision
        Gate 5 cannot rewrite. If this moves to a speech-derived signal the
        guard starts confirming its own corrections."""
        src = inspect.getsource(
            __import__("app.media_streams.llm_stream", fromlist=["x"])
        )
        i_steer = src.index("DIFFERENT DAY REQUESTED steer applied")
        i_flag = src.index('session["v3_slot_phrase_superseded"] = True')
        assert abs(src.count('"v3_slot_phrase_superseded"') - 2) <= 1
        assert i_flag < i_steer, "the flag must be set in the steer block"


class TestTheFlagIsAlwaysCleared:
    """A flag that is never cleared disarms the guard permanently — strictly
    worse than the bug it replaces."""

    def test_refreshing_the_phrase_clears_it(self):
        session = {
            "v3_confirmed_slot_phrase": THURSDAY,
            "v3_last_offered_day_iso": "2026-08-08",
            "v3_slot_phrase_superseded": True,
        }
        _note_spoken_slot_date(session, f"So that's {SATURDAY} — your name?")
        assert session["v3_confirmed_slot_phrase"] == SATURDAY
        assert not session.get("v3_slot_phrase_superseded")

    def test_capturing_a_fresh_phrase_clears_it(self):
        """The backstop. _refresh_confirmed_slot_phrase early-returns whenever
        the new day is not the payload's FIRST day — the exact multi-day case
        this change is about — so the connection.py capture must clear it too or
        the guard stays down for the rest of the call."""
        import app.media_streams.connection as conn

        src = inspect.getsource(conn.WebSocketCallHandler)
        i_assign = src.index('"v3_confirmed_slot_phrase"\n')
        i_pop = src.index('"v3_slot_phrase_superseded", None')
        i_log = src.index('" captured: %r"')
        assert i_assign < i_pop < i_log, (
            "the capture site must clear v3_slot_phrase_superseded between "
            "assigning the phrase and logging the capture"
        )


class TestTheOldSignalsStillWorkWhereTheyAreAllThereIs:
    """Three existing regression tests drive this function with no
    available_days. They must keep taking the original path, byte for byte."""

    def test_scalar_only_session_is_unchanged_both_ways(self):
        assert _confirmed_slot_is_stale(
            SATURDAY, {"v3_last_offered_day_iso": "2026-08-08"}
        ) is False
        assert _confirmed_slot_is_stale(
            SATURDAY, {"v3_last_offered_day_iso": "2026-08-06"}
        ) is True

    def test_an_empty_payload_falls_through_to_the_scalar(self):
        assert _confirmed_slot_is_stale(
            SATURDAY, {"available_days": [], "v3_last_offered_day_iso": "2026-08-08"}
        ) is False

    def test_an_unparseable_payload_falls_through_to_the_scalar(self):
        session = {
            "available_days": [{"day_label": "Saturday"}, "not-a-dict"],
            "v3_last_offered_day_iso": "2026-08-08",
        }
        assert _confirmed_slot_is_stale(SATURDAY, session) is False

    def test_no_signal_at_all_keeps_the_correction(self):
        """Genuine uncertainty must not stand the guard down — that direction
        reopens the 2026-07-07 drift defect."""
        assert _confirmed_slot_is_stale(SATURDAY, {}) is False
