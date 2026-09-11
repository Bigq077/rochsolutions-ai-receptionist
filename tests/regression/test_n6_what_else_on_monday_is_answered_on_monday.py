"""
Regression: "what else have you got on Monday" is answered with more MONDAY
times, not with the days the caller has not heard.

Spec row DT-14 (N6); invariants 4 and 12.

CAf80eb02d, northgate demo line, build 83f47aad, 11 Sep 2026 10:47:

    Susie : Monday 14th -- Number 1, eight in the morning. Number 2, twenty
            to three in the afternoon. Number 3, ten past five in the evening.
            And I've a few others that day. Any of those work?
    caller: "uh what else have you got on monday"
    Susie : Here's what we've got coming up -- Number 1, Thursday 17th ...
            Number 2, Friday 18th ... Number 3, Saturday 19th ...
    log   : [slot_followup] 'what else' answered with 3 day(s) he has not
            heard: ['2026-09-17', '2026-09-18', '2026-09-19']

Found 10 Sep, anchored to this call. The more-slots branch of the dispatcher
already scoped "what else" to a day the caller NAMED -- but through
`day_named_by_caller`, which resolves the full label ("Monday 14th
September"), and a bare weekday is a partial naming to it (B-148). "on
monday" therefore counted as no day named, and `more_days_speech` -- lead
with the days not yet heard -- took the turn. The scoped branch beneath it
already resolves the weekday through `_payload_day_by_weekday`; the fix is
that the same reader now decides which branch runs.

Also closes the scorer's own blind spot: DT-14 reported UNREACHABLE on the
argument that N6 lived in `handle_transcript`. It lived in
`try_unspoken_followup_speech`, which the scorer already drives.
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
LABEL = {MON: "Monday 14th September", TUE: "Tuesday 15th September",
         WED: "Wednesday 16th September", THU: "Thursday 17th September"}


def _day(date, times):
    return {
        "date": date,
        "day_label": LABEL[date],
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t) for t in times],
        "slots": [{"start": f"{date}T{t}:00+01:00"} for t in times],
    }


def _week_then_monday():
    days = [_day(d, GRID) for d in (MON, TUE, WED, THU)]
    s = {"clinic_id": "northgate", "available_days": days}
    first = build_slot_offer(days[:3], pretrimmed=False)
    assert first.mode == "multi_day"
    apply_offer_to_session(s, offer_as_record(first), first.chunks)
    s["_slot_presentation_mode"] = first.mode
    monday = _say(s, "um tell me about monday")
    assert monday.startswith("Monday 14th September")
    return s, monday


def _say(s, text):
    guard.note_caller_speech(s, text)
    return sf.try_unspoken_followup_speech(s, text)


def _monday_times_in(text):
    return [t for t in GRID if _spoken_slot_time(t) in text]


# ── the call ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("said", [
    "uh what else have you got on monday",      # 10:47, verbatim
    "what else on monday",
    "anything else monday",
    "what other times have you got on monday",
])
def test_what_else_on_monday_is_more_monday_times(said):
    s, monday = _week_then_monday()
    heard = _monday_times_in(monday)
    out = _say(s, said)
    assert out, said
    low = out.lower()
    assert "monday" in low and not any(d in low for d in ("tuesday", "wednesday", "thursday")), out
    more = _monday_times_in(out)
    assert len(more) == 3, out
    assert not set(more) & set(heard), (more, heard)       # inv. 12: unheard
    assert guard.check_outgoing(s, out).clean


def test_the_day_scoped_repeat_then_follows_the_newer_monday_readout():
    """D-q composes with this: 'what about Monday' after 'what else on
    Monday' repeats the what-else readout, the most recent thing said."""
    s, monday = _week_then_monday()
    more = _say(s, "what else have you got on monday")
    assert _say(s, "what about monday") == more
    assert more != monday


def test_a_second_what_else_on_monday_walks_further_through_the_day():
    s, monday = _week_then_monday()
    a = _say(s, "what else on monday")
    b = _say(s, "what else on monday")
    assert b and b != a
    assert not set(_monday_times_in(b)) & (set(_monday_times_in(a)) | set(_monday_times_in(monday)))


# ── what it must not touch ─────────────────────────────────────────────────

def test_unscoped_what_else_after_a_week_menu_still_leads_with_unheard_days():
    days = [_day(d, GRID) for d in (MON, TUE, WED, THU)]
    s = {"clinic_id": "northgate", "available_days": days}
    first = build_slot_offer(days[:3], pretrimmed=False)
    apply_offer_to_session(s, offer_as_record(first), first.chunks)
    s["_slot_presentation_mode"] = first.mode
    out = _say(s, "what else have you got")
    assert out and "Thursday" in out and "Monday" not in out


def test_a_refused_day_still_unscopes(caplog):
    """B-147: 'monday doesn't work, what else have you got' names Monday to
    rule it OUT; the answer is the days not heard, never more Monday."""
    s, _ = _week_then_monday()
    out = _say(s, "monday doesn't work, what else have you got")
    assert out and "Monday" not in out


def test_a_day_the_payload_lacks_is_not_a_scope():
    s, _ = _week_then_monday()
    out = _say(s, "what else have you got on friday")
    assert not out or "Friday" not in out
