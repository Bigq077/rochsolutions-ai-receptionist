"""The caller names a weekday and the model drops `day_window` (northgate CA420ea8c4).

9 Sep 2026, judge 2, caller abandoned:

    caller : "um yeah a wednesday at 12 please"
    engine : [ms_conn v3] day_preference captured: wednesday
    model  : check_availability(date_hint="12 o'clock", after_date="2026-09-09")
                                                        ^ day_window MISSING
    Susie  : "I've got a few days -- Number 1, Wednesday 9th September ...
              Number 2, Thursday 10th ... Number 3, Friday 11th ..."

Measured over the 956 stored calls: on the two Google Calendar clinics a caller
who names ONE day is handed other weekdays in ~10% of cases (jv_v1 11/123,
northgate 4/38). On Acuity it is 0 of 44 -- same filter, different PROMPT: the
Acuity prompt puts the weekday in `date_hint` where `_named_weekdays` can see it,
the template prompt routes it to `day_window` where a dropped argument erases it.

These tests pin the guard that closes that gap, and -- more importantly -- pin
the two ways it must DECLINE, both found by replaying real utterances.
"""
import ast
import datetime as dt
import inspect

import pytest

from app.tools.receptionist_tools import (
    _filter_tuples_by_preference,
    _named_weekdays,
    restore_named_day,
)

# 2026-09-07 Mon, -08 Tue, -09 Wed, -10 Thu, -11 Fri, -12 Sat, -13 Sun
MON, TUE, WED, THU = "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"


# ── the defect itself ────────────────────────────────────────────────────────
def test_the_failing_call_restores_wednesday():
    """The exact arguments from CA420ea8c4."""
    out = restore_named_day("12 o'clock", WED, {"day_preference": "wednesday"})
    assert "wednesday" in out.lower()
    assert "12 o'clock" in out, "the caller's TIME must survive alongside the day"


def test_a_bare_day_with_no_time_is_the_commonest_form():
    """"have you got anything on saturday" -- no time at all in the hint.

    The model has nothing to put in date_hint, so the weekday's ONLY carrier is
    the day_window it dropped. This is the majority shape of the 15 corpus
    instances, and an empty preference must still be restored.
    """
    assert restore_named_day("", WED, {"day_preference": "wednesday"}) == "wednesday"


def test_restored_day_actually_narrows_the_readout():
    """The point of the fix: `_filter_tuples_by_preference` must now filter.

    Without this the guard is a string edit that changes nothing.
    """
    from app.tools.receptionist_tools import LONDON_TZ

    now = dt.datetime.now(LONDON_TZ)
    wed = now + dt.timedelta(days=(2 - now.weekday()) % 7 + 7)   # a FUTURE Wednesday

    def at(d, h, m):
        return d.replace(hour=h, minute=m, second=0, microsecond=0)

    tuples = [
        (at(wed, 17, 10), at(wed, 18, 10)),                                    # Wed
        (at(wed + dt.timedelta(days=1), 8, 0), at(wed + dt.timedelta(days=1), 9, 0)),
        (at(wed + dt.timedelta(days=2), 8, 0), at(wed + dt.timedelta(days=2), 9, 0)),
    ]
    before = _filter_tuples_by_preference(tuples, "12 o'clock")
    assert len({s.date() for s, _ in before}) == 3, "baseline: three days offered"

    after = _filter_tuples_by_preference(
        tuples,
        restore_named_day("12 o'clock", wed.date().isoformat(),
                          {"day_preference": "wednesday"}),
    )
    assert {s.date() for s, _ in after} == {wed.date()}, "only the day they asked for"


# ── the two ways it MUST decline, both found in the corpus ───────────────────
@pytest.mark.parametrize("utterance, banked, after_date", [
    # `day_preference` banks a day the caller RULED OUT. The model is hunting
    # alternatives, so after_date points elsewhere and the guard stands down.
    ("monday doesn't work",                          "monday",   THU),
    ("uh yeah monday doesn't work what else do you have", "monday", THU),
    ("um tuesday doesn't work",                      "tuesday",  THU),
    ("not tuesday",                                  "tuesday",  WED),
    ("no no saturday doesn't work i need one now",   "saturday", MON),
])
def test_declines_when_the_caller_ruled_that_day_out(utterance, banked, after_date):
    assert restore_named_day("", after_date, {"day_preference": banked}) == ""


def test_declines_when_the_capture_names_the_wrong_day_of_two():
    """'the wednesday instead of the tuesday' banks TUESDAY -- the refused one.

    after_date is the model's own reading and points at the Wednesday, so the
    two disagree and nothing is narrowed. Without this the caller would be
    filtered onto the exact day they rejected.
    """
    assert restore_named_day("", WED, {"day_preference": "tuesday"}) == ""


# ── everything it must leave alone ───────────────────────────────────────────
@pytest.mark.parametrize("pref, after_date, session", [
    ("wednesday morning", WED,   {"day_preference": "wednesday"}),  # model said it
    ("12 o'clock",        "",    {"day_preference": "wednesday"}),  # no after_date
    ("12 o'clock",        WED,   {"day_preference": "as soon as possible"}),
    ("12 o'clock",        WED,   {"day_preference": "next week"}),
    ("12 o'clock",        WED,   {"day_preference": "tomorrow"}),
    ("12 o'clock",        WED,   {"day_preference": "whenever"}),
    ("12 o'clock",        WED,   {}),
    ("12 o'clock",        WED,   None),
    ("12 o'clock",        "not-a-date", {"day_preference": "wednesday"}),
])
def test_leaves_the_preference_alone(pref, after_date, session):
    assert restore_named_day(pref, after_date, session) == (pref or "")


def test_never_raises_on_a_hostile_session():
    """A readout preference must never be the thing that fails a lookup.

    D8 shipped with exactly this hole -- a non-dict session value raised inside
    the pin -- and it was found by its own test, not by a caller.
    """
    for bad in ["a string", 42, [1, 2], object(), {"day_preference": ["wednesday"]}]:
        assert restore_named_day("12 o'clock", WED, bad) == "12 o'clock"


def test_only_ever_narrows_never_widens():
    """The returned preference is the original, optionally with ONE day prepended.

    It must never drop a constraint the caller stated -- the time especially.
    """
    for pref in ["12 o'clock", "afternoon", "", "around 3", "evenings"]:
        out = restore_named_day(pref, WED, {"day_preference": "wednesday"})
        assert out.endswith(pref), f"{pref!r} was not preserved in {out!r}"
        assert len(_named_weekdays(out)) <= 1, "at most one day may be named"


# ── the wiring, so a refactor cannot silently drop it ────────────────────────
def test_the_guard_is_actually_called_by_the_availability_reader():
    """D10's lesson: a fix published at one call site and not the others is a
    fix that reaches one clinic. This asserts the reader really invokes it."""
    import app.tools.receptionist_tools as rt

    src = inspect.getsource(rt._exec_check_availability)
    assert "restore_named_day(" in src, (
        "_exec_check_availability no longer calls restore_named_day -- the "
        "named-day guard is disconnected and northgate/JV regress to ~10%"
    )
    # ...and it must run BEFORE the preference is consumed by the builders.
    assert src.index("restore_named_day(") < src.index("_build_days_data("), (
        "the guard must run before _build_days_data reads the preference"
    )


def test_guard_is_a_pure_function_of_its_arguments():
    """No session writes, no I/O -- so it can be replayed and reasoned about."""
    sess = {"day_preference": "wednesday"}
    snapshot = dict(sess)
    restore_named_day("12 o'clock", WED, sess)
    assert sess == snapshot, "restore_named_day must not mutate the session"


def test_restored_day_also_arms_the_widen_arm():
    """The knock-on that makes this reach the "day absent entirely" cases.

    Further down the same function, `_pref_weekdays = _named_weekdays(_pref)`
    gates a one-shot widen for a caller whose day the window never reached
    (jv_v1, 24 Aug: "have you got Tuesday?" -> "Tuesday isn't available",
    while Tuesday 1 Sep held four free slots one day past the window).

    That arm reads the SAME `_pref` this guard rewrites, and nothing rebinds it
    in between -- so restoring the weekday arms a safety net that was dark on
    exactly the calls where the model omitted it. If a refactor stops the guard
    feeding it, the absent-day cases silently regress.
    """
    restored = restore_named_day("12 o'clock", WED, {"day_preference": "wednesday"})
    assert _named_weekdays(restored) == [2], "Wednesday == weekday index 2"

    import inspect
    import app.tools.receptionist_tools as rt

    src = inspect.getsource(rt._exec_check_availability)
    assert src.index("restore_named_day(") < src.index("_pref_weekdays = _named_weekdays("), (
        "the guard must run BEFORE the widen arm reads the preference"
    )
    between = src[src.index("restore_named_day("):src.index("_pref_weekdays = _named_weekdays(")]
    assert "\n    _pref = " not in between, (
        "_pref is rebound between the guard and the widen arm -- the restored "
        "weekday no longer reaches it"
    )


@pytest.mark.parametrize("band, expect_hours", [
    ("morning",   [8, 11]),
    ("afternoon", [15]),
    ("evening",   [18]),
])
def test_the_time_band_still_applies_alongside_the_restored_day(band, expect_hours):
    """Prepending the weekday must not disturb band parsing.

    `_filter_tuples_by_preference` reads the day and the band out of the SAME
    string, so a guard that rewrites it could silently swallow the band the
    caller stated -- offering them a morning slot on the right day when they
    asked for the evening. Both constraints must survive together.
    """
    from app.tools.receptionist_tools import LONDON_TZ

    now = dt.datetime.now(LONDON_TZ)
    wed = now + dt.timedelta(days=(2 - now.weekday()) % 7 + 7)

    def at(d, h, m):
        return d.replace(hour=h, minute=m, second=0, microsecond=0)

    tuples = [
        (at(wed + dt.timedelta(days=off), h, m), at(wed + dt.timedelta(days=off), h + 1, m))
        for off in (0, 1)
        for h, m in ((8, 0), (11, 0), (15, 30), (18, 50))
    ]
    restored = restore_named_day(band, wed.date().isoformat(),
                                 {"day_preference": "wednesday"})
    out = _filter_tuples_by_preference(tuples, restored)
    assert {s.date() for s, _ in out} == {wed.date()}, "day narrowed"
    assert sorted({s.hour for s, _ in out}) == expect_hours, "band preserved"


def test_only_the_google_calendar_clinics_are_touched():
    """Theorem (Acuity), Vital Edge (diary) and published must be unreachable.

    Theorem is 0-for-44 on this defect precisely because its prompt already puts
    the weekday in date_hint. Nothing here should perturb that, and the corpus
    number is only evidence for the fix while it stays a control group.

    The dispatcher returns for acuity/diary/published BEFORE the guard runs, so
    this asserts the ordering rather than trusting it.
    """
    import inspect
    import app.tools.receptionist_tools as rt

    src = inspect.getsource(rt._exec_check_availability)
    guard = src.index("restore_named_day(")
    for earlier in ("if uses_acuity(session):",
                    "_check_availability_diary(",
                    "_check_availability_published("):
        assert src.index(earlier) < guard, (
            f"{earlier} no longer returns before the named-day guard -- a clinic "
            f"that is not on the Google Calendar path can now reach it"
        )


# ── CAd7495e58: "wednesday around 12" answered with MONDAY ───────────────────
# 9 Sep 2026, judge 2, abandoned. A regression introduced by the nearest-time
# matcher: once "around 12" could resolve at all, it resolved onto whatever day
# happened to hold that time. Two independent faults, both fixed here.
def _slot(date, time="12:10"):
    return {"time": time, "spoken": "ten past twelve in the afternoon",
            "start": f"{date}T{time}:00+01:00"}


_DAYS = [{"date": "2026-09-10", "day_label": "Thursday 10th September"},
         {"date": "2026-09-14", "day_label": "Monday 14th September"},
         {"date": "2026-09-16", "day_label": "Wednesday 16th September"}]


def test_a_bare_weekday_refuses_a_slot_on_another_day():
    """`day_named_by_caller` returns None for a BARE weekday -- its docstring
    defers that as Tier 2. Safe while a bare time could not resolve; not safe
    once it could. Refusing on a weekday needs no corpus: the worst a false
    positive does is decline and let the caller be asked again."""
    from app.tools.slot_followup import resolve_requested_time

    hit = resolve_requested_time(
        "oh i saw at wednesday around 12", [_slot("2026-09-14")], _DAYS,
    )
    assert hit is None, "a Wednesday request was answered with Monday"


def test_the_same_time_on_two_days_declines_rather_than_guessing():
    from app.tools.slot_followup import resolve_requested_time

    hit = resolve_requested_time(
        "oh i saw at wednesday around 12",
        [_slot("2026-09-14"), _slot("2026-09-16")], _DAYS,
    )
    assert hit is None, "an exact tie across two days must not pick one"


@pytest.mark.parametrize("utterance, slots, expect", [
    # The day they asked for IS the day held -> serve it.
    ("wednesday around 12",           [_slot("2026-09-16")], "2026-09-16"),
    # Naming the day they are ACCEPTING must not refuse it.
    ("wednesday at 12 works",         [_slot("2026-09-16")], "2026-09-16"),
    # No day named -> the guard says nothing, exactly as before.
    ("do you have any slots at 12",   [_slot("2026-09-16")], "2026-09-16"),
    # Two weekdays names neither, so the hit stands (pre-existing behaviour).
    ("monday or wednesday around 12", [_slot("2026-09-16")], "2026-09-16"),
])
def test_the_weekday_refusal_stays_silent_when_it_should(utterance, slots, expect):
    from app.tools.slot_followup import resolve_requested_time

    hit = resolve_requested_time(utterance, slots, _DAYS)
    assert hit is not None and hit["start"].startswith(expect)


def test_the_weekday_pattern_is_a_real_word_boundary():
    """Written once as a literal backspace byte (0x08) instead of \b, which
    compiled fine and matched NOTHING -- the guard was silently inert."""
    from app.tools.slot_followup import _WEEKDAY_RE

    assert "\x08" not in _WEEKDAY_RE.pattern, "a control byte is in the pattern"
    assert [m.group(1) for m in _WEEKDAY_RE.finditer("on wednesday please")] == ["wednesday"]
    assert not _WEEKDAY_RE.search("wednesdayish nonsense words")
