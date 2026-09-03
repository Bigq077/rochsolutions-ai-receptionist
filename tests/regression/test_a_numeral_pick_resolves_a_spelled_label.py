"""
A caller who echoes a slot back in numerals has still picked it.

northgate, 2026-09-02. Susie offered "twenty past twelve in the afternoon";
the caller said "20 past 12" and the pick did not resolve. `_readback_norm`
folds filler and punctuation but leaves a number word where it found it, so
the two sides of the containment test never met:

    label   -> "twenty past twelve in afternoon"
    caller  -> "20 past 12"

D1. The sibling `_norm_offer_label` in connection.py had exactly this defect
fixed as B-91 (26 Aug) and the lesson never crossed the file boundary.

WHY COPYING B-91 WAS NOT ENOUGH. B-91's table maps the HOURS one..twelve. Run
against this sentence it produces "twenty past 12" versus "20 past 12" -- a
different mismatch, not a match. Clock-face labels lead with a MINUTE word
("twenty past", "quarter to", "twenty-five past"), and those were the half
that was missing. The test below pins both halves.

WHY THE FOLD IS NOT IN `_readback_norm`. That normaliser also feeds
`_caller_norm`, which applies `_fold_ordinals` afterwards. A cardinal fold
running first turns "twenty second" into "20 2", the compound regex stops
matching, and B-104's date matching silently breaks. So the fold lives in
`_time_norm` and is applied at the TIME comparisons only. The last test here
is the guard on that separation -- it fails if anyone later "simplifies" the
two normalisers back into one.
"""

import pytest

from app.tools.receptionist_tools import _spoken_slot_time
from app.tools.slot_followup import (
    _caller_norm,
    _fold_clock_words,
    _time_named_in,
    _readback_norm,
    _time_norm,
    accepted_slot_is_named_in,
    record_spoken_slots,
    slot_accepted_by_caller,
)

DATE = "2026-09-04"
DAY_LABEL = "Friday 4th September"
# Two times on the day, both "twenty past", so the match has to discriminate
# on the HOUR and cannot pass by naming the minute alone.
TIMES = ["12:20", "16:20"]


def _session(times=TIMES):
    slots = [
        {
            "start": f"{DATE}T{t}:00",
            "end": f"{DATE}T{t}:00",
            "date": DATE,
            "spoken": _spoken_slot_time(t),
        }
        for t in times
    ]
    session = {
        "last_offered_slots": [dict(s) for s in slots],
        "available_days": [
            {
                "date": DATE,
                "day_label": DAY_LABEL,
                "slots": [dict(s) for s in slots],
                # The spoken label rides on the DAY, not on the slot -- it is
                # `slot_times_spoken` that flatten_bookable_slots reads. A
                # fixture that sets it per-slot silently gets "12:20" back and
                # tests nothing this file is about.
                "slot_times": list(times),
                "slot_times_spoken": [s["spoken"] for s in slots],
            }
        ],
        "slot_labels": [s["spoken"] for s in slots],
    }
    record_spoken_slots(session, [{"start": s["start"]} for s in slots])
    return session


# -- the reproduction ------------------------------------------------------

def test_the_label_really_is_spelled_out():
    """Pins the other side of the comparison. If the offer ever stops spelling
    its minutes, this defect changes shape and the fold below is aimed wrong."""
    assert _spoken_slot_time("12:20") == "twenty past twelve in the afternoon"


# The resolver requires a DAY as well as a time -- step 2, deny by default,
# and nothing to do with D1. Every sentence below names the Friday so that the
# assertion is about the TIME half and cannot pass or fail for the other
# reason. See test_the_day_is_still_required_on_its_own.
def test_a_numeral_echo_resolves_the_pick():
    assert (
        slot_accepted_by_caller(_session(), "the friday at 20 past 12")
        == f"{DATE}T12:20:00"
    )


def test_the_words_the_offer_used_still_resolve():
    assert (
        slot_accepted_by_caller(_session(), "the friday at twenty past twelve")
        == f"{DATE}T12:20:00"
    )


def test_the_other_hour_is_not_the_one_that_resolves():
    """The minute word is shared by both slots; the hour is what separates
    them. A fold that lost the hour would return either slot at random."""
    assert (
        slot_accepted_by_caller(_session(), "the friday at 20 past 4")
        == f"{DATE}T16:20:00"
    )


def test_a_bare_time_resolves_when_the_offer_holds_ONE_day():
    """The separate defect this test used to mark as unfixed. Now fixed.

    Its previous form asserted `"20 past 12"` declines, and said why: "the
    fold below is necessary but not sufficient and THE DAY HALF IS A SEPARATE
    DEFECT". That defect was closed on 2026-09-03, so this is the same
    assertion pointed at the answer rather than at the gap -- not a fixture
    rewritten to match new behaviour.

    This offer holds ONE day, so a caller naming only a time has left nothing
    ambiguous. What it cost while open, live 13:07:24 that day:

        13:07:24  'um 10 past 5 in the evening works'
        13:07:25  situational head (time_band):
                  "Let me see what I've got in the evening —"
        13:07:27  "So that's Monday the 7th of September at ten past five"

    A promised lookup that never happened, reached by the most ordinary answer
    a caller can give after Susie narrows to a day and re-reads its times.
    """
    assert slot_accepted_by_caller(_session(), "20 past 12") == f"{DATE}T12:20:00"


def test_a_bare_time_still_declines_when_TWO_days_are_on_offer():
    """The invariant the old test was really protecting, kept intact.

    The resolver may infer the day only when there is nothing to infer. Two
    days on offer, each holding a twenty-past-twelve, and a caller saying "20
    past 12" has genuinely not said which -- so it declines, as step 2 has
    always required. Guessing here would pin a slot on the wrong DAY, which is
    the failure the whole deny-by-default ladder exists to prevent.
    """
    session = _session()
    other = "2026-09-07"
    session["available_days"] = list(session["available_days"]) + [{
        "date": other,
        "day_label": "Monday 7th September",
        "slot_times": ["12:20"],
        "slot_times_spoken": ["twenty past twelve in the afternoon"],
        "times_not_shown": 0,
        "slots": [{"start": f"{other}T12:20:00", "end": ""}],
    }]
    session["last_offered_slots"] = list(session["last_offered_slots"]) + [
        {"start": f"{other}T12:20:00", "end": ""}
    ]
    record_spoken_slots(session, [
        {"start": f"{DATE}T12:20:00", "spoken": "twenty past twelve in the afternoon",
         "date": DATE},
        {"start": f"{other}T12:20:00", "spoken": "twenty past twelve in the afternoon",
         "date": other},
    ])
    assert slot_accepted_by_caller(session, "20 past 12") is None


def test_b91s_table_alone_would_not_have_fixed_it():
    """The hours-only fold, applied to this sentence, still misses. Stated so
    the next person does not 'simplify' _CLOCK_UNITS down to B-91's table."""
    hours_only = {
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
        "eleven": "11", "twelve": "12",
    }
    label = _readback_norm("twenty past twelve")
    label = " ".join(hours_only.get(w, w) for w in label.split())
    assert label == "twenty past 12"
    assert label not in _readback_norm("20 past 12")


# -- the shapes the fold has to survive ------------------------------------

@pytest.mark.parametrize(
    "hhmm,said",
    [
        ("12:20", "20 past 12"),
        ("17:15", "quarter past 5"),
        ("09:40", "20 to 10"),
        ("16:25", "25 past 4"),
        ("16:25", "twenty five past four"),
        ("13:00", "1 in the afternoon"),
    ],
)
def test_every_clock_face_form_folds(hhmm, said):
    assert _time_norm(_spoken_slot_time(hhmm)).startswith(_time_norm(said))


def test_a_compound_minute_is_one_number_not_two():
    """Compounds before units, or "twenty five past" becomes "20 5 past"."""
    assert _fold_clock_words("twenty five past four") == "25 past 4"


# -- deny by default -------------------------------------------------------

def test_a_different_hour_does_not_resolve():
    assert slot_accepted_by_caller(_session(), "the friday at 20 past 1") is None


def test_past_and_to_stay_distinct():
    assert slot_accepted_by_caller(_session(), "the friday at 20 to 12") is None


def test_a_time_that_was_never_offered_declines():
    assert slot_accepted_by_caller(_session(), "the friday at half past 3") is None


# -- the read-back guard reads the same fold -------------------------------

def test_a_numeral_readback_names_the_accepted_slot():
    """`accepted_slot_is_named_in` is the P6b stand-down. Left on the old
    normaliser it would call a correct confirmation a mismatch and let the
    payload offer replace the slot the caller had just accepted."""
    session = _session()
    session["accepted_slot"] = f"{DATE}T12:20:00"
    from app.tools.slot_followup import ACCEPTED_SLOT_KEY

    session[ACCEPTED_SLOT_KEY] = f"{DATE}T12:20:00"
    assert accepted_slot_is_named_in(session, "so that is 20 past 12, is that right?")


# -- the fold's own hazard: a folded hour is one or two digits -------------

def test_a_folded_hour_does_not_match_the_date():
    """Caught by B-126's tests while this fix was being written, and kept here
    because it is THIS fold that creates the hazard.

    "nine in the morning" strips to the bare "nine" and folds to "9". A
    read-back names its date in digits too, so plain containment found "9"
    inside "Wednesday the 9th of September" and reported that the sentence
    named an offered TIME when it named only the DAY. The guard then stood
    down on a read-back that was genuinely wrong -- B-126's failure mode, where
    the caller is told a time the diary does not hold.

    Word-bounded containment is what makes the fold safe, not a nicety.
    """
    phrase = _time_norm(
        "So that's Wednesday the 9th of September at six in the evening"
    )
    assert "9" in phrase                       # the date really is in there
    assert _time_named_in(phrase, "nine in the morning") is False
    assert _time_named_in(phrase, "nine") is False
    assert _time_named_in(phrase, "six in the evening") is True


def test_the_boundary_does_not_cost_a_real_match():
    """The other direction: bounding must not break the pick it was added for."""
    assert _time_named_in(_time_norm("the friday at 20 past 12"), "twenty past twelve")


# -- the separation from the date path -------------------------------------

def test_the_clock_fold_never_reaches_a_date():
    """B-104. If _fold_clock_words is ever moved inside _readback_norm, this
    fails: "twenty second" folds to "20 2" and the ordinal compound is lost."""
    assert _caller_norm("tuesday the twenty second of september") == (
        "tuesday 22 september"
    )
    assert _caller_norm("tuesday the 22nd of september") == "tuesday 22 september"


def test_readback_norm_itself_is_unfolded():
    """The two normalisers are deliberately different functions. Pinned so a
    later tidy-up cannot quietly make them one."""
    assert _readback_norm("twenty past twelve") == "twenty past twelve"
    assert _time_norm("twenty past twelve") == "20 past 12"
