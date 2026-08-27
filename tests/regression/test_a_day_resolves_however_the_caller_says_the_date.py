"""
Regression: "the twenty second" named a day the code could not recognise.

B-104, found while writing B-103's live test script on 27 Aug 2026. B-103 makes
the day the CALLER NAMED win over the first slot of the offer, resolved by
matching the payload's own day_label. It matched exactly one spoken form:

    payload:  "Tuesday 22nd September"

    "tuesday the 22nd of september"           -> resolved
    "tuesday the twenty second of september"  -> None, fell back
    "tuesday the 22 of september"             -> None, fell back

and falling back means scoping by last_offered_slots[0], which is B-103's
defect reached by PHRASING instead of by code. A caller who says the date the
other way gets answered about a different day, exactly as before the fix.

Not hypothetical for the reason that makes it worth fixing: nothing chooses
which form arrives. It is whatever AssemblyAI renders. Live calls on 27 Aug
rendered '28th' and '22nd', so the digit form is common — but that is an
observation about a transcriber, not a property anything here controls, and
B-103's correctness should not rest on it.

THE FIX folds ordinals to a bare number on BOTH sides, so all three land on
"tuesday 22 september". Doing it to the speech only would move the mismatch
rather than remove it, since the label carries an ordinal too.

ORDINALS ONLY, NOT CARDINALS. A date is spoken as an ordinal. Mapping "two"
as well would fold "two in the afternoon" into a bare 2 for no gain, on a path
whose whole job is deciding which day a caller means. The tests below pin that
time phrases stay untouched.

Bounded vocabulary, in the same spirit as _WEEKDAY_WORDS: a day of the month is
1..31, so this stays a lookup table and does not become date parsing. Tier 2
("that tuesday") is still out of scope and still resolves to nothing.
"""
from __future__ import annotations

from app.tools.slot_followup import (
    _caller_norm,
    _fold_ordinals,
    caller_named_conflicting_days,
    day_named_by_caller,
)

MON, TUE, WED = "2026-09-21", "2026-09-22", "2026-09-23"
DAYS = [
    {"date": MON, "day_label": "Monday 21st September", "slots": []},
    {"date": TUE, "day_label": "Tuesday 22nd September", "slots": []},
    {"date": WED, "day_label": "Wednesday 23rd September", "slots": []},
]


# ---------------------------------------------------------------------------
# The three spoken forms must reach the same day
# ---------------------------------------------------------------------------
def test_the_digit_ordinal_still_resolves():
    """The form that already worked. First, because this fix must not cost it
    -- an early draft broke exactly this while adding the others."""
    assert day_named_by_caller(
        DAYS, "what else have you got on tuesday the 22nd of september"
    ) == TUE


def test_the_word_ordinal_now_resolves():
    assert day_named_by_caller(
        DAYS, "what else have you got on tuesday the twenty second of september"
    ) == TUE


def test_the_bare_number_now_resolves():
    assert day_named_by_caller(
        DAYS, "what else have you got on tuesday the 22 of september"
    ) == TUE


def test_every_form_folds_onto_the_label():
    label = _caller_norm("Tuesday 22nd September")

    for spoken in (
        "tuesday the 22nd of september",
        "tuesday the twenty second of september",
        "tuesday the 22 of september",
        "Tuesday 22nd September",
    ):
        assert label in _caller_norm(spoken), spoken


def test_the_teens_and_the_compounds_and_the_edges():
    """1..31 is the whole domain, so the awkward shapes are worth naming:
    the -th teens, the -ieth tens, and the two-word compounds."""
    for spoken, written in (
        ("first", "1st"), ("second", "2nd"), ("third", "3rd"),
        ("fifth", "5th"), ("eighth", "8th"), ("ninth", "9th"),
        ("twelfth", "12th"), ("thirteenth", "13th"),
        ("nineteenth", "19th"), ("twentieth", "20th"),
        ("twenty first", "21st"), ("twenty second", "22nd"),
        ("twenty ninth", "29th"), ("thirtieth", "30th"),
        ("thirty first", "31st"),
    ):
        assert _fold_ordinals(spoken) == _fold_ordinals(written), spoken


def test_a_compound_is_not_read_as_two_numbers():
    """"twenty second" is 22, not "20 2". Compounds are substituted first."""
    assert _fold_ordinals("twenty second") == "22"
    assert _fold_ordinals("thirty first") == "31"


# ---------------------------------------------------------------------------
# Cardinals are NOT folded -- times must not become dates
# ---------------------------------------------------------------------------
def test_a_time_phrase_is_left_alone():
    for phrase in (
        "two in the afternoon",
        "one in the afternoon",
        "nine in the morning",
        "number two",
    ):
        assert _fold_ordinals(phrase) == phrase, phrase


def test_a_time_phrase_names_no_day():
    for text in (
        "do you have anything at two in the afternoon",
        "anything at one in the afternoon on that day",
    ):
        assert day_named_by_caller(DAYS, text) is None, text
        assert caller_named_conflicting_days(DAYS, text) is False, text


def test_an_ordinal_without_a_weekday_and_month_names_no_day():
    """"the second one" folds to a bare 2, which is correct and harmless: a
    label match needs the weekday and the month adjacent to it."""
    assert day_named_by_caller(DAYS, "what else have you got on the second one") is None


# ---------------------------------------------------------------------------
# The B-103 guards still hold across the new forms
# ---------------------------------------------------------------------------
def test_two_days_conflict_however_they_are_said():
    for text in (
        "anything else on monday the 21st or tuesday the 22nd of september",
        "anything else on monday the twenty first or tuesday the twenty second "
        "of september",
        "anything else on monday the 21st or tuesday the twenty second of "
        "september",
    ):
        assert caller_named_conflicting_days(DAYS, text) is True, text
        assert day_named_by_caller(DAYS, text) is None, text


def test_a_partial_naming_is_still_tier_two():
    text = "what else on that tuesday"

    assert day_named_by_caller(DAYS, text) is None
    assert caller_named_conflicting_days(DAYS, text) is False


def test_every_day_of_a_month_round_trips():
    """The whole domain, against a label written the way the payload writes it."""
    for dom in range(1, 32):
        suffix = (
            "th" if 11 <= dom <= 13
            else {1: "st", 2: "nd", 3: "rd"}.get(dom % 10, "th")
        )
        days = [{"date": f"2026-01-{dom:02d}",
                 "day_label": f"Thursday {dom}{suffix} January", "slots": []}]
        spoken = f"what else on thursday the {dom} of january"

        assert day_named_by_caller(days, spoken) == f"2026-01-{dom:02d}", dom


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
def test_folding_never_raises_on_junk():
    for junk in ("", "   ", "no digits here", "999999", "1st 2nd 3rd"):
        assert isinstance(_fold_ordinals(junk), str), junk


def test_the_normaliser_still_handles_non_strings():
    for junk in (None, 5, [], {}):
        assert _caller_norm(junk) == ""
