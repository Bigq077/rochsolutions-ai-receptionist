"""
B-127 - CA6a59e59f0a67fe964693a64690f70544, 1 Sept 2026, build 5ebe0211.

The first live call on the 3x2 multi_day readout. The offer was:

    Number 1, Tuesday 1st September  -- twenty past eleven, or ten past five.
    Number 2, Wednesday 2nd September -- eight in the morning, or ...
    Number 3, Thursday 3rd September -- ...

    09:54:41  caller: "uh yeah the second one please"
    09:54:43  v3_confirmed_slot_phrase captured:
              'Tuesday the 1st of September at twenty past eleven'

Number 2's position resolved to Number 1's day AND Number 1's first time, and
that pair was then latched as the confirmed slot. The caller had picked
Wednesday.

WHY EVERY EXISTING GUARD MISSED IT -- each for its own reason, which is why
this took a new rung rather than widening one of them:

  * `day_selected_by_position` (B-105) resolves exactly this and resolves it
    CORRECTLY. It had one caller, `remaining_unspoken_on_current_day`, which is
    the "what else have you got that day" follow-up. The SELECTION turn never
    consulted it.
  * `reconcile_readback_time` (B-95) checks the read-back against what was
    SPOKEN. The model named a time that genuinely belonged to the day it named,
    so the sentence was internally consistent and passed through untouched. It
    also corrects "the TIME to the DAY, never the reverse" -- and here the DAY
    was the wrong half.
  * The 3x2 cap change had additionally made B-95's correction branch inert on
    multi_day: it requires `len(offered) == 1` and two times per day is two.
    So the ordinal path lost its resolver and its backstop went passive in the
    same deploy.
  * The keypad was right throughout. Pressing 2 resolves through
    `v3_dtmf_slot_map` and takes Wednesday. Speech and keypad disagreed only
    because one of the two readers consulted the table.

THE FIX is not a new rule and deliberately not a new table: the spoken path now
reads the map the keypad already reads, and rewrites the utterance to the
mapped label exactly as the DTMF block does with a digit.
"""
from __future__ import annotations

import inspect

from app.tools.slot_followup import label_for_spoken_position

TUE, WED, THU = "2026-09-01", "2026-09-02", "2026-09-03"
LABELS = {
    TUE: "Tuesday 1st September",
    WED: "Wednesday 2nd September",
    THU: "Thursday 3rd September",
}
# Day-keyed, as multi_day builds it -- one entry per DAY, not per time.
SLOT_MAP = {"1": LABELS[TUE], "2": LABELS[WED], "3": LABELS[THU]}


def _days():
    return [
        {"date": d, "day_label": LABELS[d], "slot_times": ["11:20", "17:10"]}
        for d in (TUE, WED, THU)
    ]


def _session(**over):
    s = {"v3_dtmf_slot_map": dict(SLOT_MAP), "available_days": _days()}
    s.update(over)
    return s


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
def test_the_second_one_resolves_to_the_second_day():
    """The live utterance, verbatim from the transcript."""
    got = label_for_spoken_position(_session(), "uh yeah the second one please")
    assert got == "Wednesday 2nd September", got


def test_the_spoken_ordinal_and_the_keypad_agree_position_for_position():
    """The property the whole fix exists for. `test_the_ordinal_list_and_the_
    keypad_agree_position_for_position` asserts this of the MAP; it was untrue
    of the two READERS, which is where the caller actually lives."""
    for digit, phrase in (
        ("1", "the first one please"),
        ("2", "the second one please"),
        ("3", "the third one please"),
    ):
        spoken = label_for_spoken_position(_session(), phrase)
        pressed = SLOT_MAP[digit]          # what the DTMF block injects
        assert spoken == pressed, (digit, spoken, pressed)


def test_number_two_is_the_same_as_the_second_one():
    """Both phrasings are equally common after a numbered readout, and
    `_positions_named` unions two foldings precisely so both land."""
    assert (
        label_for_spoken_position(_session(), "number two")
        == label_for_spoken_position(_session(), "the second one")
        == "Wednesday 2nd September"
    )


def test_the_selection_turn_actually_calls_the_resolver():
    """The defect was never a wrong answer -- it was a correct answer nothing
    asked for. B-105's resolver had one caller and the selection turn was not
    it, so this pins the WIRING rather than the logic.
    """
    import app.media_streams.connection as conn

    src = inspect.getsource(conn)
    assert "label_for_spoken_position" in src, (
        "the slot-selection window no longer resolves a spoken ordinal "
        "through the keypad map -- B-127 has regressed"
    )


# ---------------------------------------------------------------------------
# Deny by default -- what must NOT be rewritten
# ---------------------------------------------------------------------------
def test_a_named_weekday_beats_the_position():
    """Resolving REPLACES the caller's words, so any weekday declines.

    Deliberately wider than `day_named_by_caller`, which needs a full naming
    ("wednesday the 2nd of september") and returns None for a bare weekday by
    design. That is right for B-105, which only scopes a query and leaves the
    words intact; it is not right for a destructive rewrite.
    """
    for text in (
        "the second one but thursday if you have it",
        "number two, or is there anything on friday",
    ):
        assert label_for_spoken_position(_session(), text) is None, text


def test_a_named_time_beats_the_position():
    """The caller has said something a day-keyed table cannot represent."""
    assert label_for_spoken_position(
        _session(), "the second one at eight in the morning"
    ) is None


def test_two_positions_decline_rather_than_guess():
    assert label_for_spoken_position(
        _session(), "number one or number two"
    ) is None


def test_a_date_is_not_a_position():
    """A bare number is a date far more often than an index. `_POSITION_RE`
    requires an explicit positional word, and this pins that it still does."""
    assert label_for_spoken_position(_session(), "the 2nd of september") is None


def test_a_superseded_map_declines():
    """B-80: a superseded map resolves to a time the caller was offered EARLIER
    and is no longer being offered -- a silent wrong-slot booking, which is
    worse than the phrase doing nothing."""
    assert label_for_spoken_position(
        _session(v3_slot_map_superseded=True), "the second one"
    ) is None


def test_no_map_declines():
    assert label_for_spoken_position(
        {"available_days": _days()}, "the second one"
    ) is None


def test_a_position_outside_the_map_declines():
    assert label_for_spoken_position(_session(), "number nine") is None


def test_it_never_raises():
    """This sits on the live selection turn. It must decline, never explode."""
    for session in (None, {}, [], "nonsense", _session(v3_dtmf_slot_map=None),
                    _session(v3_dtmf_slot_map="junk")):
        for text in (None, "", "   ", "the second one", 17):
            assert label_for_spoken_position(session, text) is None
