"""
Regression: "anything around 12" during selection is answered by the engine,
on the day under discussion, and says when the time is the nearest.

Spec rows DT-7 and DT-8; precedence level 3 (what they asked for) above
level 4 (novelty); invariant 20.

CA34942aee, demo line, build a590abaa, 11 Sep 2026:

    08:51:06  'uh yeah tell me about monday'
              -> Monday 14th September - Number 1, eight ... (payload, 139 ms)
    08:51:21  'uh what have you got around 12'
              [ms_gate5] removed banned phrase (closest_ive_got)
              -> "Sorry, still with you - Does that work, or would you like
                  to try a different day?"
    08:51:33  'i said what have you got around 12'

The model answered from its context, wrote "the closest I've got is ...",
Gate 5 stripped that whole sentence, and the caller heard a question about
a time that was never spoken. On CA778651b7 (08:44) the identical question
happened to trigger a tool call and D8 pinned 12:10 inside the executor.
Which of the two a caller got depended on the model.

The producer path existed and declined: `resolve_requested_time` ran over
the WHOLE sweep, 12:10 sits on every day of a uniform grid, and the
"exactly one" discipline (right for a 12-hour twin) refused a time the diary
held on the very day being discussed. The fix scopes the attempt to the day
under discussion first -- the same scope the more-times branch already uses.
"""
from __future__ import annotations

import pytest

from app.tools import slot_fact_guard as guard
from app.tools import slot_followup as sf
from app.tools.receptionist_tools import _spoken_slot_time
from app.tools.slot_offer import (
    apply_offer_to_session,
    build_slot_offer,
    offer_as_record,
)

MON, TUE, WED, THU = "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"
GRID = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10", "13:00",
        "13:50", "14:40", "15:30", "16:20", "17:10"]


def _day(date, label, times):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t) for t in times],
        "slots": [{"start": f"{date}T{t}:00+01:00"} for t in times],
    }


def _week():
    return [
        _day(MON, "Monday 14th September", GRID),
        _day(TUE, "Tuesday 15th September", GRID),
        _day(WED, "Wednesday 16th September", GRID),
        _day(THU, "Thursday 17th September", GRID),
    ]


def _after_the_week_readout():
    days = _week()
    s = {"clinic_id": "northgate", "available_days": days}
    offer = build_slot_offer(days[:3], pretrimmed=False)
    apply_offer_to_session(s, offer_as_record(offer), offer.chunks)
    s["_slot_presentation_mode"] = "multi_day"
    return s


def _say(s, text):
    """What the live path does: the guard notes the caller's words at turn
    start, then the dispatcher runs."""
    guard.note_caller_speech(s, text)
    return sf.try_unspoken_followup_speech(s, text)


# ── the call ───────────────────────────────────────────────────────────────

def test_the_08_51_turn_is_answered_by_a_producer_on_monday():
    s = _after_the_week_readout()
    assert _say(s, "uh yeah tell me about monday")
    out = _say(s, "uh what have you got around 12")
    assert out, "the turn reached no producer -- the model would answer it"
    assert "ten past twelve" in out
    assert "Monday 14th September" in out
    assert "Tuesday" not in out and "Wednesday" not in out


def test_the_nearest_time_says_it_is_the_nearest():
    """DT-8: 'and says so'. A caller who asked for twelve and hears 'yes, ten
    past twelve' asks again."""
    s = _after_the_week_readout()
    _say(s, "tell me about monday")
    out = _say(s, "uh what have you got around 12")
    assert out.startswith("The nearest I've got to midday is ten past twelve")


def test_an_exact_time_reads_as_a_plain_yes():
    s = _after_the_week_readout()
    _say(s, "tell me about monday")
    out = _say(s, "anything at ten past twelve")
    assert out.startswith("Yes — ten past twelve")


def test_the_guard_passes_the_nearest_sentence():
    """It names 12:00 -- the caller's time, bookable nowhere -- inside a
    Monday clause. That is the caller's own word, not a wrong-day fact."""
    s = _after_the_week_readout()
    _say(s, "tell me about monday")
    out = _say(s, "uh what have you got around 12")
    v = guard.check_outgoing(s, out)
    assert v.clean, v.warnings + v.violations


def test_the_asked_time_becomes_the_offer_on_the_table():
    """DT-7: 'that time, first' -- the offer is now this one time, so an
    ordinal or a keypress cannot pick a stale option (B-80)."""
    s = _after_the_week_readout()
    _say(s, "tell me about monday")
    _say(s, "uh what have you got around 12")
    assert [o["start"][:16] for o in s["last_offered_slots"]] == [f"{MON}T12:10"]


# ── scope ──────────────────────────────────────────────────────────────────

def test_a_weekday_named_with_the_time_scopes_to_that_day():
    """'ten past twelve on tuesday': a bare weekday is a partial naming, so
    the default scope was Monday, the weekday refusal (rightly) dropped the
    Monday hit, and the named-day producer read Tuesday's three -- 12:10 not
    among them. Same row, one door along."""
    s = _after_the_week_readout()
    out = _say(s, "any slots at ten past twelve on tuesday")
    assert out.startswith("Yes — ten past twelve in the afternoon on Tuesday 15th September")


def test_after_the_week_menu_with_no_day_named_the_first_offered_day_answers():
    """'around 12' straight after Mon/Tue/Wed: the day under discussion is
    the offer's first (B-103's rule 3), which is also the soonest."""
    s = _after_the_week_readout()
    out = _say(s, "have you got anything around 12")
    assert "Monday 14th September" in out and "ten past twelve" in out


def test_a_time_on_no_day_under_discussion_still_resolves_across_the_sweep():
    """The whole-sweep call underneath is unchanged: a time that is unique
    across the payload resolves wherever it is."""
    days = [_day(MON, "Monday 14th September", ["08:00", "10:30", "17:10"]),
            _day(THU, "Thursday 17th September", ["09:00", "12:10", "16:20"])]
    s = {"clinic_id": "northgate", "available_days": days}
    offer = build_slot_offer(days, pretrimmed=False)
    apply_offer_to_session(s, offer_as_record(offer), offer.chunks)
    s["_slot_presentation_mode"] = "multi_day"
    out = _say(s, "anything around 12")
    assert out and "Thursday 17th September" in out and "ten past twelve" in out


# ── declines that must stay declines ───────────────────────────────────────

def test_a_tie_for_nearest_still_declines():
    """DT-9: 08:25 sits exactly between 08:00 and 08:50."""
    s = _after_the_week_readout()
    _say(s, "tell me about monday")
    assert _say(s, "anything at twenty five past eight") is None


@pytest.mark.parametrize("said", [
    "none of those work for me",         # B-114: 'one' is a pronoun here
    "could someone phone me back",
    "what else have you got",
    "anything else",
    "say that again",
])
def test_these_are_not_time_requests(said):
    s = _after_the_week_readout()
    _say(s, "tell me about monday")
    out = _say(s, said)
    assert not (out or "").startswith(("Yes —", "The nearest")), out


# ── why the scope is needed ────────────────────────────────────────────────

def test_the_whole_sweep_alone_declines_on_a_uniform_grid():
    """The cause, kept as a test: over the whole sweep 12:10 is on four days,
    so the resolver's uniqueness rule refuses it. This is what sent the 08:51
    turn to the model."""
    s = _after_the_week_readout()
    _say(s, "tell me about monday")
    remaining = sf.remaining_unspoken(s)
    assert sum(1 for r in remaining if r["time"] == "12:10") == 4
    assert sf.resolve_requested_time(
        "uh what have you got around 12", remaining, s["available_days"]) is None
