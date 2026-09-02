# tests/regression/test_absence_is_not_unavailability.py
"""A gap in the payload must never be speakable as clinic state (10 Aug 2026).

Second half of the "Wednesday the 19th is fully booked" incident — see
test_month_first_date_is_honoured.py for the parse bug that put the caller on
this path in the first place.

The residual
------------
Even with a named date honoured, a hint that legitimately bypasses the week
filter ("evening slots", "as soon as possible") still produces a payload the
model cannot read honestly:

* the Acuity sweep finds N days,
* ``days_data[:3]`` keeps the soonest three for the spoken list,
* and ``total_days`` was set to the number of days *presented*.

So the model saw three days, no marker that any were withheld, and a count that
agreed with what it could see. Absence of a day was indistinguishable from
absence of availability on that day. On the incident call it resolved that
ambiguity the worst possible way and told a caller his day was full when it had
six free slots.

Why not just fix total_days
---------------------------
Because ``total_days`` already means something, in two places that run *after*
this one: ``_cap_presented_slots`` and ``_filter_same_day_slots`` both
(re)define it as the number of days in THIS payload. Redefining it here would
be overwritten downstream and would fight two other functions for a name. The
honest counts therefore go in fields nothing rewrites.

The signal
----------
``search_narrowed_to`` is the discriminator, and it is the field that actually
closes the bug:

* a date or range → the tool looked there, so a short or empty answer for that
  day IS clinic state and may be spoken as such;
* ``None`` → the tool looked at no particular day, so *no* statement about a
  specific day is supportable from the result.

``days_found_in_window`` and ``days_not_shown`` quantify what was withheld.

The rule
--------
The signal is inert unless the prompt reads it, and Theorem's live prompt —
``_build_theorem_v3``, not clinic.json — had no availability-field vocabulary
at all: no ``requested_day_empty``, no "fully booked" template, not even
``available_days``. The Google Calendar executor has both halves (it emits
``requested_day_empty`` and susie_system_prompt has a matching rule); the
Acuity path Theorem actually runs had neither. This closes the gap the same
way rather than inventing a third pattern.

Both halves are asserted here because either alone is dead: a signal no rule
reads changes nothing, and a rule naming fields the tool never emits is worse
than nothing.
"""

import datetime as _dt
import inspect
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.prompts.susie_system_prompt import _build_theorem_v3
from app.tools import receptionist_tools as rt
from tests.harness.clinic_dates import london as _london, open_days


# ---------------------------------------------------------------------------
# Half 0 — the payload itself, against a stubbed Acuity adapter.
#
# The source assertions further down are cheap and catch renames, but they
# cannot catch the counts being wired to the wrong list. This runs the real
# executor over a known slot set and reads what the model would be handed.
# ---------------------------------------------------------------------------
_TZ = ZoneInfo("Europe/London")
# Slots on +1..+4 and +9,+10 days: the sweep finds six days, the spoken list
# holds three, and the day the caller will name is one of the withheld ones —
# the incident's exact shape.
# Four open days close in, then two a week out: the sweep finds six days, the
# spoken list holds three, and the day the caller names is one of the withheld.
#
# These were fixed offsets (1, 2, 3, 4, 9, 10) until 2 Sep 2026. On a Wednesday
# two of them land on the weekend, Alcester is shut, and the sweep quietly finds
# four days instead of six -- so the test failed on the calendar rather than on
# the code. See tests/harness/clinic_dates.
_DAYS = open_days(4, start_offset=1) + open_days(2, start_offset=8)


class _Slot:
    def __init__(self, start, end):
        self.start_time, self.end_time = start, end


def _stub_slots():
    out = []
    for d in _DAYS:
        for hour in (10, 14, 15):
            out.append(_Slot(_london(d, hour, 0), _london(d, hour, 50)))
    return out


class _StubAdapter:
    async def get_available_slots(self, **_kw):
        return _stub_slots()


async def _availability(date_hint):
    session = {
        "clinic_id": "theorem",
        "selected_location": "alcester",
        "call_sid": "TEST",
    }
    with patch.object(rt, "_get_acuity_adapter",
                      lambda *a, **k: _StubAdapter(), create=True):
        return await rt._check_availability_acuity(
            {
                "service":   "msk_initial_assessment",
                "location":  "alcester",
                "date_hint": date_hint,
            },
            session,
        )


async def test_a_bypassed_sweep_admits_what_it_withheld():
    """The incident's payload. Six days found, three spoken. The model must be
    able to tell those apart, and must be told it looked at no particular day."""
    r = await _availability("evening slots")
    assert "error" not in r, r
    assert len(r["available_days"]) == 3, "the spoken cap changed; retune below"
    assert r["days_found_in_window"] == len(_DAYS), (
        f"days_found_in_window={r['days_found_in_window']} but the sweep found "
        f"{len(_DAYS)} days — the count is wired to the truncated list"
    )
    assert r["days_not_shown"] == len(_DAYS) - 3
    assert r["search_narrowed_to"] is None, (
        "a bypassed sweep claims to have searched a specific day — this is the "
        "field the prompt rule trusts, so a false value here licences the "
        "'fully booked' sentence outright"
    )


async def test_a_named_day_is_searched_and_says_so():
    """The other side of the discriminator: when the caller names the day the
    sweep would have withheld, it is searched, presented, and marked."""
    far = _DAYS[-2]
    r = await _availability(f"{far.strftime('%B')} {far.day}th")
    assert "error" not in r, r
    assert r["search_narrowed_to"] == far.isoformat(), (
        f"search_narrowed_to={r['search_narrowed_to']!r}; the named day was "
        f"not honoured, which is the parse bug this pairs with"
    )
    assert [d["date"] for d in r["available_days"]] == [far.isoformat()], (
        "the named day is not what was presented"
    )
    assert r["days_not_shown"] == 0


async def test_the_withheld_day_is_the_one_the_caller_asked_about():
    """Ties the two together: the day absent from the swept payload is exactly
    the day the named-date call returns. That absence was read as 'fully
    booked' on the incident call, and it is demonstrably not."""
    far = _DAYS[-2]
    swept = await _availability("evening slots")
    assert far.isoformat() not in [d["date"] for d in swept["available_days"]]
    assert swept["days_not_shown"] > 0, (
        "the payload hides the day and reports nothing withheld — exactly the "
        "state in which absence is indistinguishable from unavailability"
    )
    named = await _availability(f"{far.strftime('%B')} {far.day}th")
    assert named["available_days"], (
        "the day missing from the sweep does in fact have slots — the "
        "incident in one assertion"
    )


# ---------------------------------------------------------------------------
# Half 1 — the tool emits the signal.
# ---------------------------------------------------------------------------
_SRC = inspect.getsource(rt._check_availability_acuity)


@pytest.mark.parametrize(
    "field",
    ["days_found_in_window", "days_not_shown", "window_examined_days",
     "search_narrowed_to"],
)
def test_the_acuity_payload_carries_the_honesty_field(field):
    assert f'"{field}"' in _SRC, (
        f"{field} is no longer emitted by the Acuity availability path — the "
        f"model is back to reading a truncated day list as the whole truth"
    )


def test_days_not_shown_is_measured_against_what_is_spoken():
    """It must count what the SWEEP found minus what the caller will HEAR.

    This asserted `_days_found - len(_present_days)` until 26 Aug 2026, and
    that formula did the very thing this docstring warns about — went
    constantly zero and claimed completeness — in single_day mode.
    `_present_days` IS all of days_data there, while only `days_data[0]` ever
    becomes first_day.

    B-94, CA390f03d2 (theorem_v3): "have you got anything on a friday" showed
    Friday 28th August with its single slot and reported days_not_shown=0 while
    three further Fridays holding 4, 5 and 2 free slots sat in available_days.
    Susie said "that's the only slot we have" and the caller hung up.

    So the denominator is the SPOKEN breadth, not the presented list. multi_day
    is unchanged, because there the two are the same thing.
    """
    assert "_days_found = len(days_data)" in _SRC
    assert "max(0, _days_found - _spoken_days)" in _SRC, (
        "days_not_shown is no longer the difference between days found and "
        "days spoken"
    )
    assert '_presentation_mode == "single_day" and days_data' in _SRC, (
        "the spoken-day count no longer singles out single_day, so "
        "days_not_shown is measured against days nobody will hear"
    )


def test_search_narrowed_to_is_none_exactly_when_the_filter_was_bypassed():
    """The whole signal rests on this: null must mean 'no day was searched'.
    If it were ever populated on a bypassed sweep, the prompt rule below would
    licence precisely the sentence this fixes."""
    assert "_narrowed = None" in _SRC
    assert "if _week_range is not None:" in _SRC, (
        "search_narrowed_to is no longer keyed to the week filter actually "
        "having resolved a range"
    )


def test_the_honesty_fields_are_not_clobbered_downstream():
    """_cap_presented_slots and _filter_same_day_slots both rewrite total_days.
    They must not own the new names too — that is the entire reason the honest
    counts are not carried on total_days."""
    for fn in (rt._cap_presented_slots, rt._filter_same_day_slots):
        src = inspect.getsource(fn)
        for field in ("days_found_in_window", "days_not_shown",
                      "search_narrowed_to"):
            assert field not in src, (
                f"{fn.__name__} now writes {field}; the honesty fields have to "
                f"survive post-processing untouched or they report the "
                f"truncated view again"
            )


# ---------------------------------------------------------------------------
# Half 2 — Theorem's live prompt reads it.
# ---------------------------------------------------------------------------
def _theorem_prompt() -> str:
    out = _build_theorem_v3({"clinic_id": "theorem", "call_sid": "TEST"})
    return "\n".join(out) if isinstance(out, (list, tuple)) else str(out)


@pytest.mark.parametrize(
    "field", ["search_narrowed_to", "days_not_shown", "days_found_in_window"]
)
def test_the_live_theorem_prompt_names_the_field(field):
    """Rendered, not grepped from clinic.json — Theorem's prompt is hardcoded
    Python and clinic.json does not reach Mark's model."""
    assert field in _theorem_prompt(), (
        f"{field} is emitted by the tool but never explained to the model, so "
        f"it is decoration"
    )


def test_the_live_theorem_prompt_forbids_the_sentence():
    p = _theorem_prompt()
    assert "NEVER CALL A DAY FULL UNLESS THE TOOL LOOKED AT THAT DAY" in p
    assert "never evidence that the day is full" in p, (
        "the prompt no longer states the inference that caused the incident — "
        "that a missing day means an unavailable day"
    )


def test_the_prompt_gives_the_recovery_move_not_just_a_prohibition():
    """A bare ban would leave the model with nothing to say when the caller
    names a day it cannot see, which is how prohibitions turn into dead air or
    a hedge. It must be told to re-check that day."""
    p = _theorem_prompt()
    i = p.index("NEVER CALL A DAY FULL")
    block = p[i:i + 1200]
    assert "call check_availability again" in block
    assert "date_hint" in block


def test_the_ban_and_the_licence_are_both_present():
    """The rule must still permit the true statement, or a genuinely full day
    stops being reportable and the caller is strung along instead."""
    p = _theorem_prompt()
    i = p.index("NEVER CALL A DAY FULL")
    block = p[i:i + 1200]
    assert "only if THAT result is empty" in block, (
        "the prompt bans the false claim without licensing the true one"
    )
