"""
Regression: "what else on WEDNESDAY?" was answered about Friday, by name.

B-103. The half of CA890b511e that B-99 left open, and B-99's own docstring
says so:

    1. THE DAY WAS NOT IDENTIFIED. The caller had asked about "wednesday the
       2nd of september". The offer on the table spanned THREE days, and the
       follow-up takes "the day under discussion" from last_offered_slots[0]
       -- whichever sorts first, here Friday 28 August.

B-99 stopped that branch CLAIMING A DAY IS FULL when it cannot identify one.
It did not make it identify one. So on a multi-day offer:

    offer:   Friday 28 Aug | Wednesday 2 Sep | Friday 4 Sep
    caller:  "what else have you got on wednesday the 2nd of september"
    before:  "On Friday 28th August I also have 16:00."

Not a refusal and not a hedge -- a confident answer about a day nobody asked
about, announced under that day's own label. That is CA5c4fb14f's shape: the
sentence a patient acts on to turn up on the wrong date.

THE FIX is to prefer the day the CALLER named, resolved against the payload's
own day_label strings through the normaliser that already exists for exactly
this mismatch ("Wednesday 2nd September" vs "wednesday the 2nd of september").

SCOPE. Only an unambiguous FULL naming steers. A partial -- "that wednesday",
"friday the 28th" -- resolves to nothing and keeps the old behaviour untouched;
that is Tier 2 and it needs its own corpus first.

AMBIGUITY FALLS THROUGH, IT DOES NOT FALL BACK. When more than one day is
referred to, the scope is empty, the branch declines (a multi-day offer cannot
support an exhaustion claim) and the turn goes to a real lookup. Falling back
to last_offered_slots[0] there would produce the exact wrong-day answer this
whole change exists to remove -- in this function the fallback IS the defect,
so "bail to the old path" is not the safe direction it usually is.
"""
from __future__ import annotations

from app.tools.slot_followup import (
    caller_named_conflicting_days,
    day_named_by_caller,
    remaining_unspoken_on_current_day,
    try_unspoken_followup_speech,
)

FRI_AUG, WED_SEP, FRI_SEP = "2026-08-28", "2026-09-02", "2026-09-04"
LABELS = {
    FRI_AUG: "Friday 28th August",
    WED_SEP: "Wednesday 2nd September",
    FRI_SEP: "Friday 4th September",
}


def _day(date: str, times: list[str]) -> dict:
    label = LABELS[date]
    return {
        "date": date,
        "day_label": label,
        "slot_times": times,
        "slot_times_spoken": times,
        "slots": [
            {"start": f"{date}T{t}:00+01:00", "end": "", "spoken": t,
             "date": date, "day_label": label}
            for t in times
        ],
        "times_found_on_day": len(times),
        "times_not_shown": 0,
    }


DAYS = [
    _day(FRI_AUG, ["14:00", "16:00"]),
    _day(WED_SEP, ["10:00", "14:00"]),
    _day(FRI_SEP, ["13:00", "15:00"]),
]


def _session() -> dict:
    """A three-day offer, one slot read out per day -- CA890b511e's shape.

    last_offered_slots leads with Friday 28 August, which is what the old
    scoping took as "the day under discussion" no matter what was asked.
    """
    return {
        "available_days": DAYS,
        "last_offered_slots": [
            DAYS[0]["slots"][0], DAYS[1]["slots"][1], DAYS[2]["slots"][0],
        ],
    }


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_named_day_wins_over_the_first_slot_of_the_offer():
    speech = try_unspoken_followup_speech(
        _session(), "what else have you got on wednesday the 2nd of september"
    )

    assert speech is not None
    assert "Wednesday 2nd September" in speech, speech
    assert "Friday" not in speech, (
        "answered about a day the caller did not ask about, under that day's "
        "own label -- CA5c4fb14f's shape"
    )


def test_the_named_day_can_be_the_last_day_of_the_offer():
    speech = try_unspoken_followup_speech(
        _session(), "any other times on friday the 4th of september"
    )

    assert speech is not None and "Friday 4th September" in speech, speech


def test_the_scope_itself_is_the_named_day():
    scoped = remaining_unspoken_on_current_day(
        _session(), "what else is there on wednesday 2nd september"
    )

    assert {s["date"] for s in scoped} == {WED_SEP}


def test_the_payloads_own_wording_resolves_too():
    """The caller may echo the label verbatim rather than paraphrase it."""
    assert day_named_by_caller(DAYS, "anything else wednesday 2nd september") == WED_SEP


# ---------------------------------------------------------------------------
# Unchanged where nothing was named -- Tier 2 stays out of scope
# ---------------------------------------------------------------------------
def test_naming_no_day_still_uses_the_offer_on_the_table():
    speech = try_unspoken_followup_speech(_session(), "anything else that day")

    assert speech is not None and "Friday 28th August" in speech, speech


def test_a_partial_naming_is_left_alone():
    """Tier 2. It must resolve to nothing AND raise no conflict, so the old
    path runs exactly as before rather than falling through to a lookup."""
    text = "any other times on that wednesday"

    assert day_named_by_caller(DAYS, text) is None
    assert caller_named_conflicting_days(DAYS, text) is False
    speech = try_unspoken_followup_speech(_session(), text)
    assert speech is not None and "Friday 28th August" in speech, speech


def test_a_day_that_is_not_on_offer_changes_nothing():
    text = "what else have you got on monday the 7th of december"

    assert day_named_by_caller(DAYS, text) is None
    assert caller_named_conflicting_days(DAYS, text) is False


# ---------------------------------------------------------------------------
# More than one day -- fall THROUGH, never back
# ---------------------------------------------------------------------------
def test_two_full_namings_fall_through_to_a_real_lookup():
    text = ("anything else on friday the 28th of august or friday the 4th "
            "of september")

    assert caller_named_conflicting_days(DAYS, text) is True
    assert try_unspoken_followup_speech(_session(), text) is None, (
        "answered about one of two named days instead of looking it up"
    )


def test_an_elided_second_day_is_still_a_second_day():
    """The reason counting label matches is not enough on its own.

    "wednesday the 2nd or friday the 4th of september" names TWO days and
    matches ONE label -- the month is spoken once, so the first is a partial
    and invisible to the match. One hit then looks unambiguous.
    """
    text = "anything else on wednesday the 2nd or friday the 4th of september"

    assert caller_named_conflicting_days(DAYS, text) is True
    assert day_named_by_caller(DAYS, text) is None
    assert try_unspoken_followup_speech(_session(), text) is None


def test_punctuation_does_not_hide_a_second_weekday():
    """`_norm_day` keeps the comma, which silently under-counts weekdays.
    Caller speech is normalised with the punctuation-stripping sibling."""
    text = "not wednesday, what else on friday the 4th of september"

    assert caller_named_conflicting_days(DAYS, text) is True
    assert try_unspoken_followup_speech(_session(), text) is None


def test_ambiguity_never_answers_about_the_lead_day():
    """The point of falling through: the old fallback IS the defect."""
    for text in (
        "anything else on wednesday the 2nd or friday the 4th of september",
        "not wednesday, what else on friday the 4th of september",
    ):
        assert remaining_unspoken_on_current_day(_session(), text) == [], text


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
def test_the_default_argument_keeps_the_old_signature_working():
    """Existing callers pass a session only and must be unaffected."""
    scoped = remaining_unspoken_on_current_day(_session())

    assert {s["date"] for s in scoped} == {FRI_AUG}


def test_it_never_raises_on_junk():
    for days in (None, [], "nonsense", [None, 5, {}]):
        for text in (None, "", "   ", 5, "wednesday the 2nd of september"):
            assert day_named_by_caller(days, text) in (None, WED_SEP)
            assert isinstance(caller_named_conflicting_days(days, text), bool)


def test_a_day_with_no_label_or_no_date_is_skipped():
    days = [{"date": WED_SEP}, {"day_label": "Friday 4th September"}]

    assert day_named_by_caller(days, "wednesday the 2nd of september") is None
