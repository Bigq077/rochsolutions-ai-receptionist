"""
Regression: "the SECOND day suits me" was answered about the first day.

B-105 - CA0eb9a12c6b20d0d068f1e197810bf9eb, 27 Aug 2026, jv_v1, build e449791c.
Found on the JV go-live rehearsal, on the reschedule call.

The rung B-103 and B-104 left open. Those two taught this family to honour a
day the caller NAMES. A numbered readout invites the caller to pick by NUMBER
instead, and that phrasing still fell through to `last_offered_slots[0]`:

    13:58:40  offer: "Number 1, Monday 7th September - half past four.
                      Number 2, Tuesday 8th September - five in the evening."
              v3_last_offered_day_iso='2026-09-07'   <- day ONE, unconditionally
    13:59:08  caller: "uh the second day suits me could you give me all the
                       slots you have on that day"
              answer: "On MONDAY 7th September I also have quarter past five
                       in the evening, six in the evening, or quarter past
                       eight in the evening"

WHY THIS ONE IS WORSE THAN B-103. The caller then picked a time from the day
he was wrongly given - "yeah quarter past 8 works", which is Monday 20:15.
The read-back corrector saw a time that was not in TUESDAY's offer, and
"corrected" the TIME to fit the day the model had drifted to:

    13:59:22  read-back time corrected: 'quarter past eight in the evening'
              -> 'five in the evening' for Tuesday 8th September

    13:59:30  "Just to confirm - I'm moving your appointment to Tuesday the
               8th at five in the evening. Shall I go ahead and move it?"
    13:59:51  caller: "yeah go for it"

Three different days in one turn: the offer record said Monday, the model said
Tuesday, the caller was answering about Monday. The caller CONSENTED to a day
and time that were never on the table together. Nothing wrote only because the
model happened to re-run a lookup on that turn instead of the write tool -
ordering, not a guard.

THE FIX is a rung between "named" and "ambiguous": resolve a positional pick
through `v3_dtmf_slot_map`, the same index -> label table the keypad uses, so
a spoken "number two" and a pressed 2 cannot disagree.

SCOPE, and why each guard is here rather than looser:

  * A position must be FRAMED as one. A bare number is a date far more often
    than an index, so "the 2nd of September" must never read as "option 2".
  * A named day BEATS a positional pick. Both can appear in one utterance;
    the explicit date is the stronger signal.
  * A time_selection map must not scope a DAY. The identical map is built for
    "Number 1, half past five. Number 2, quarter past six." Resolving a day
    from it would be this very defect with the operands swapped. Matching the
    resolved label back against `available_days` is what separates them.
"""
from __future__ import annotations

from app.tools.slot_followup import (
    day_selected_by_position,
    remaining_unspoken_on_current_day,
    try_unspoken_followup_speech,
)

MON, TUE = "2026-09-07", "2026-09-08"
LABELS = {MON: "Monday 7th September", TUE: "Tuesday 8th September"}


def _day(date: str, times: list) -> dict:
    label = LABELS[date]
    return {
        "date": date,
        "day_label": label,
        "slot_times": times,
        "slot_times_spoken": times,
        "slots": [
            {"start": date + "T" + t + ":00+01:00", "end": "", "spoken": t,
             "date": date, "day_label": label}
            for t in times
        ],
        "times_found_on_day": len(times),
        "times_not_shown": 0,
    }


DAYS = [
    _day(MON, ["16:30", "17:15", "18:00", "20:15"]),
    _day(TUE, ["17:00", "17:45", "18:30", "19:15", "20:00"]),
]

DAY_MAP = {"1": "Monday 7th September", "2": "Tuesday 8th September"}
TIME_MAP = {"1": "half past five in the evening",
            "2": "quarter past six in the evening"}


def _session(slot_map=None) -> dict:
    """CA0eb9a12c's shape: two days offered, one time read out per day.

    last_offered_slots leads with Monday, which is what the old scoping took
    as "the day under discussion" whatever the caller then picked.
    """
    return {
        "available_days": DAYS,
        "last_offered_slots": [DAYS[0]["slots"][0], DAYS[1]["slots"][0]],
        "v3_dtmf_slot_map": DAY_MAP if slot_map is None else slot_map,
    }


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_picked_day_wins_over_the_first_slot_of_the_offer():
    speech = try_unspoken_followup_speech(
        _session(),
        "uh the second day suits me could you give me all the slots you have "
        "on that day",
    )

    assert speech is not None
    assert "Tuesday 8th September" in speech, speech
    assert "Monday" not in speech, (
        "answered about day one under its own label after the caller picked "
        "day two by number - CA0eb9a12c's shape, and the turn the caller "
        "then consented to a slot that was never offered"
    )


def test_the_scope_itself_is_the_picked_day():
    scoped = remaining_unspoken_on_current_day(
        _session(), "the second day suits me, what else is on that day"
    )

    assert {s["date"] for s in scoped} == {TUE}


def test_number_two_is_the_same_pick_as_the_second():
    """Callers say "number two" as readily as "the second one"."""
    assert day_selected_by_position(
        DAYS, _session(), "number two please, anything else that day"
    ) == TUE


def test_the_second_one_is_a_pick_too():
    """The commonest phrasing of all, and the one the foldings ate.

    "the second one" folds to "2 one" by ordinals and then to "2 1" by
    cardinals, which matches nothing. Caught only because this test was
    written against the DAY map: the time-map test below uses the SAME words
    and must answer None, so a parser that simply never matched them would
    have passed that one and left this defect live.
    """
    assert day_selected_by_position(
        DAYS, _session(), "the second one, anything else that day"
    ) == TUE


def test_a_pressed_key_and_a_spoken_number_resolve_alike():
    """One table, so the keypad and the voice cannot disagree."""
    assert day_selected_by_position(DAYS, _session(), "day 1") == MON
    assert day_selected_by_position(DAYS, _session(), "option 2") == TUE


# ---------------------------------------------------------------------------
# The guards. Each of these, loosened, reintroduces a wrong-day answer.
# ---------------------------------------------------------------------------
def test_a_named_day_beats_a_positional_pick():
    """Both signals in one utterance - the explicit date is the stronger."""
    speech = try_unspoken_followup_speech(
        _session(),
        "the second one, sorry I mean monday 7th september, what else is on",
    )

    assert speech is not None and "Monday 7th September" in speech, speech


def test_a_time_selection_map_never_scopes_a_day():
    """The identical map is built for a numbered TIME readout.

    "the second one" there means quarter past six, not Tuesday. Resolving a
    day from it is this defect with the operands swapped.
    """
    assert day_selected_by_position(
        DAYS, _session(TIME_MAP), "the second one, anything else that day"
    ) is None


def test_a_spoken_date_is_not_read_as_a_position():
    """The one false positive that would matter: a date is not an index."""
    assert day_selected_by_position(
        DAYS, _session(), "what about the 2nd of september"
    ) is None
    assert day_selected_by_position(
        DAYS, _session(), "the twenty second"
    ) is None


def test_two_positions_named_decline_rather_than_guess():
    assert day_selected_by_position(
        DAYS, _session(), "is that number one or number two"
    ) is None


def test_no_map_leaves_the_old_behaviour_exactly_as_it_was():
    assert day_selected_by_position(DAYS, _session({}), "the second day") is None


def test_an_option_joined_with_at_still_resolves():
    """The wording nothing enforces, and the reason this match is containment.

    `extract_slot_options` cuts an option at an em dash, an en dash or a full
    stop, and at nothing else. The model is free to write "Tuesday 8th
    September at five in the evening" instead, and then the stored label is the
    whole line. Under an equality test that matches no day label at all, and
    this guard goes silently inert on the exact call shape it exists to catch.
    """
    at_map = {
        "1": "Monday 7th September at half past four in the afternoon",
        "2": "Tuesday 8th September at five in the evening",
    }

    assert day_selected_by_position(
        DAYS, _session(at_map), "the second day suits me"
    ) == TUE


def test_an_option_naming_two_days_declines():
    """Loosening to containment must not become a guess.

    Two labels inside one option cannot be a day pick, so it falls back to the
    old behaviour rather than picking whichever was listed first.
    """
    both_map = {
        "1": "Monday 7th September or Tuesday 8th September",
        "2": "Tuesday 8th September at five in the evening",
    }

    assert day_selected_by_position(
        DAYS, _session(both_map), "number one please"
    ) is None
