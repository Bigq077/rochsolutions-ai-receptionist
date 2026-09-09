"""A caller's "12" and a diary's "12:10" are the same time (northgate CA82c05845).

9 Sep 2026, judge 2, abandoned after 92s. The caller asked for "around 12
o'clock" THREE times and was offered eight in the morning, ten past five, ten to
nine and twenty past four. Wednesday 16th held 12:10 the whole time.

Two sites asked "which slot did the caller name?" and both answered with exact
string equality:

    _pin_requested_time_index   `str(t)[:5] in wanted`      (the readout, D8)
    resolve_requested_time      `s.get("time") in candidates` (the follow-up)

northgate's grid runs in FIFTY-minute steps -- measured across 383 real day-grids
in the obs store: northgate 50min x2588, theorem_v3 60min x300, jv_v1 45min. Only
08:00 and 13:00 are round hours in a whole northgate day, so a caller naming
nine, ten, eleven, twelve, two, three or four could never match anything.

`resolve_requested_time` was doubly blind: `_candidate_hhmm_from_text` reads the
WORD form and returns [] for every DIGIT form, so it had no candidates at all.
"""
import pytest

from app.tools.slot_followup import (
    NEAREST_TIME_TOLERANCE_MIN,
    _candidate_hhmm_from_text,
    nearest_time_index,
    requested_clock_times,
    resolve_requested_time,
)

# Wednesday 16th September, verbatim from the 17:26:09 tool result.
WED16 = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
         "13:00", "13:50", "14:40", "15:30", "16:20", "17:10"]


def _remaining(times):
    return [{"time": t, "spoken": "", "start": f"2026-09-16T{t}:00+01:00"} for t in times]


# ── the defect ───────────────────────────────────────────────────────────────
def test_the_call_that_caused_this():
    """"around 12 o'clock" must reach 12:10, which was bookable throughout."""
    idx = nearest_time_index(WED16, requested_clock_times("around 12 o'clock"))
    assert WED16[idx] == "12:10"


def test_the_callers_third_ask_now_resolves():
    """Verbatim from the transcript, on the third time of asking."""
    hit = resolve_requested_time(
        "do you have any slots at 12 on the first wednesday", _remaining(WED16), [],
    )
    assert hit is not None and hit["time"] == "12:10"


def test_the_digit_form_parser_gap_is_covered():
    """`_candidate_hhmm_from_text` returns NOTHING for a digit form.

    This is why the follow-up path could not match even an exact 12:00. Pinned
    so that removing the union in `resolve_requested_time` fails loudly rather
    than silently restoring a resolver that cannot read "at 12".
    """
    for digits in ["at 12", "12 o'clock", "any slots at 12"]:
        assert _candidate_hhmm_from_text(digits) == [], (
            "the weak parser started reading digit forms -- re-check whether the "
            "union in resolve_requested_time is still needed"
        )
        assert requested_clock_times(digits) == ["12:00"]


@pytest.mark.parametrize("utterance, expect", [
    ("around 12 o'clock", "12:10"),
    ("at 12",             "12:10"),
    ("at 9",              "08:50"),
    ("half past 4",       "16:20"),
])
def test_round_and_half_hours_reach_the_grid(utterance, expect):
    idx = nearest_time_index(WED16, requested_clock_times(utterance))
    assert WED16[idx] == expect


# ── the hazard the corpus found IN THIS FIX, before it shipped ───────────────
@pytest.mark.parametrize("utterance, exact", [
    ("20 to 10",                    "09:40"),
    ("ten to nine in the morning",  "08:50"),
])
def test_a_precise_time_is_never_dragged_to_a_round_one(utterance, exact):
    """Replaying real grids caught this fix about to make things WORSE.

    "oh yeah 20 to 10 will work" resolved to 09:40 and was being pulled to
    10:00; "ten to nine" -> 08:50 pulled to 09:00. A caller saying twenty-to-ten
    is quoting a slot back, not approximating, and moving them twenty minutes is
    worse than the defect being fixed. Only o'clock / quarter / half / quarter-to
    get tolerance; anything else must match exactly.
    """
    idx = nearest_time_index(WED16, requested_clock_times(utterance))
    assert WED16[idx] == exact, "a diary-precise time drifted"

    # ...and with that slot absent it must decline, not reach for a neighbour.
    without = [t for t in WED16 if t != exact]
    assert nearest_time_index(without, requested_clock_times(utterance)) is None


# ── the decline rules, unchanged from the exact-match version ────────────────
def test_a_genuine_twelve_hour_twin_still_declines():
    """"at 5" is 05:00 and 17:00. A day holding both cannot know which."""
    both = ["05:10", "09:00", "17:10"]
    assert nearest_time_index(both, requested_clock_times("at 5")) is None


def test_a_band_word_collapses_the_twin():
    both = ["05:10", "09:00", "17:10"]
    idx = nearest_time_index(both, requested_clock_times("at 5 in the evening"))
    assert both[idx] == "17:10"


def test_one_reading_landing_is_not_ambiguity():
    """"at 5" on a day that opens at eight resolves to the evening, correctly."""
    idx = nearest_time_index(WED16, requested_clock_times("at 5"))
    assert WED16[idx] == "17:10", "05:00 is unbookable, so only one reading lands"


def test_a_time_the_day_does_not_hold_declines_silently():
    assert nearest_time_index(WED16, ["06:00"]) is None


# ── the tolerance itself ────────────────────────────────────────────────────
def test_the_tolerance_boundary_is_inclusive():
    assert NEAREST_TIME_TOLERANCE_MIN == 20
    assert nearest_time_index(["12:20"], ["12:00"]) == 0        # exactly 20
    assert nearest_time_index(["12:21"], ["12:00"]) is None     # 21


def test_nearest_wins_and_ties_go_to_the_earlier_slot():
    assert nearest_time_index(["11:20", "12:10"], ["12:00"]) == 1   # 10 beats 40
    assert nearest_time_index(["11:40", "12:20"], ["12:00"]) == 0   # tie -> earlier


def test_it_is_a_strict_superset_of_exact_matching():
    """Anything the old exact match found must still be found, on real grids."""
    for grid in (WED16, ["09:00", "10:00", "11:00"], ["08:00", "13:00"]):
        for i, t in enumerate(grid):
            assert nearest_time_index(grid, [t]) == i, (
                f"exact match on {t} regressed"
            )


def test_never_raises_on_hostile_input():
    """A readout preference must not be the thing that fails a lookup."""
    for times, wanted in [
        (None, ["12:00"]), (["12:00"], None), ("12:00", ["12:00"]),
        (["12:00"], "12:00"), ([None, 42], ["12:00"]), (["nonsense"], ["12:00"]),
        (["12:00"], [None]), (["12:00"], ["99:99"]), ([], []),
    ]:
        assert nearest_time_index(times, wanted) is None


def test_both_sites_share_one_owner():
    """The exact-match bug was written twice. A second copy is how they drift."""
    import inspect
    from app.tools import slot_followup as sf

    for fn in (sf.resolve_requested_time, sf._pin_requested_time_index):
        assert "nearest_time_index(" in inspect.getsource(fn), (
            f"{fn.__name__} no longer uses the shared matcher"
        )
