# tests/regression/test_b09_date_anchors.py
"""
B-09 — "next Friday" resolved +12 days.

Not model arithmetic. **Our own off-by-one-week**, in three duplicated copies of
the same calculation:

    days_until_sunday = (6 - weekday) % 7          # == 0 on a Sunday
    this_sunday = now + (days_until_sunday if days_until_sunday > 0 else 7)
    next_monday = this_sunday + 1

On a Sunday the ``else 7`` fired, so "this Sunday" became *next* Sunday and
``next_monday`` — literally tomorrow — was handed to the model as **eight days
away**. Counting Friday from that anchor gives **+12**, the number the row was
filed under. A fourth implementation, in `_extract_week_range`, was correct all
along, so on Sundays the two halves of the system disagreed by exactly 7 days —
and the `check_availability` schema explicitly instructs the model to use the
wrong one by passing next Monday's literal date.

Wrong on Sundays only. One day in seven, which is why it survived two months and
why **a dial-time check on any other weekday proves nothing** — these tests, not
a call, are the verification.
"""
from __future__ import annotations

import inspect
from datetime import date, timedelta

import pytest

from app.date_context import CLINIC_TZ, WeekAnchors, week_anchors


# Monday 3 Aug 2026 … Sunday 9 Aug 2026 — one full week, BST.
_WEEK = [date(2026, 8, 3) + timedelta(days=i) for i in range(7)]
# A GMT week, to prove nothing depends on the DST offset.
_WINTER_WEEK = [date(2026, 1, 5) + timedelta(days=i) for i in range(7)]


# ── The invariants, on every weekday ──────────────────────────────────────
@pytest.mark.parametrize("today", _WEEK + _WINTER_WEEK, ids=lambda d: d.strftime("%a-%d-%b"))
def test_this_sunday_is_the_sunday_of_this_week(today):
    a = week_anchors(today)
    assert a.this_sunday.weekday() == 6
    assert 0 <= (a.this_sunday - today).days <= 6, (
        "this_sunday must be within the current week — the old code pushed it "
        "a week out whenever today was already Sunday"
    )


@pytest.mark.parametrize("today", _WEEK + _WINTER_WEEK, ids=lambda d: d.strftime("%a-%d-%b"))
def test_next_monday_is_a_monday_and_follows_this_sunday(today):
    a = week_anchors(today)
    assert a.next_monday.weekday() == 0
    assert a.next_monday == a.this_sunday + timedelta(days=1)
    assert 1 <= (a.next_monday - today).days <= 7, (
        f"next_monday is {(a.next_monday - today).days} days out — it must "
        f"never be more than 7"
    )


@pytest.mark.parametrize("today", _WEEK + _WINTER_WEEK, ids=lambda d: d.strftime("%a-%d-%b"))
def test_next_sunday_closes_the_following_week(today):
    a = week_anchors(today)
    assert a.next_sunday == a.next_monday + timedelta(days=6)
    assert a.next_sunday.weekday() == 6


# ── The Sunday case, named explicitly ─────────────────────────────────────
def test_on_a_sunday_next_monday_is_tomorrow():
    """The whole defect in one assertion. Before the fix this was +8."""
    sunday = date(2026, 8, 9)
    assert sunday.weekday() == 6, "fixture drift: that is not a Sunday"
    a = week_anchors(sunday)
    assert (a.next_monday - sunday).days == 1
    assert (a.this_sunday - sunday).days == 0, "on a Sunday, this Sunday is today"


def test_the_plus_twelve_symptom_is_gone():
    """The row's title. A model counting Friday from the anchor we hand it
    landed +12 on Sundays; it must land +5."""
    sunday = date(2026, 8, 9)
    model_friday = week_anchors(sunday).next_monday + timedelta(days=4)
    assert (model_friday - sunday).days == 5
    assert model_friday.weekday() == 4


def test_the_old_arithmetic_really_did_produce_twelve():
    """Pins the defect so the fix cannot be quietly reverted to 'simplify'."""
    sunday = date(2026, 8, 9)
    days_until_sunday = (6 - sunday.weekday()) % 7
    old_this_sunday = sunday + timedelta(
        days=(days_until_sunday if days_until_sunday > 0 else 7)
    )
    old_next_monday = old_this_sunday + timedelta(days=1)
    assert (old_next_monday - sunday).days == 8
    assert (old_next_monday + timedelta(days=4) - sunday).days == 12
    assert old_next_monday != week_anchors(sunday).next_monday


# ── Agreement with the resolver that was already correct ──────────────────
@pytest.mark.parametrize("today", _WEEK, ids=lambda d: d.strftime("%a-%d-%b"))
def test_matches_the_tool_side_resolver(today):
    """`_extract_week_range._next_monday` uses `7 - today.weekday()` and was
    right all along. The shared helper must reproduce it exactly, or the two
    halves of the system disagree again."""
    tool_side = today + timedelta(days=7 - today.weekday())
    assert week_anchors(today).next_monday == tool_side


# ── No copy may keep its own arithmetic ───────────────────────────────────
@pytest.mark.parametrize(
    "module_path,func_name",
    [
        ("app.prompts.clinic_template_prompt", "_date_context"),
        ("app.media_streams.llm_stream", "_build_date_prefix"),
    ],
)
def test_call_sites_use_the_shared_helper(module_path, func_name):
    import importlib
    src = inspect.getsource(getattr(importlib.import_module(module_path), func_name))
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    assert "week_anchors" in code, f"{func_name} no longer uses the shared anchors"
    assert "% 7" not in code, f"{func_name} has grown its own week arithmetic again"
    assert "else 7" not in code, f"{func_name} reintroduced the Sunday special case"


def test_the_config_block_uses_the_shared_helper():
    import app.media_streams.config as cfg
    src = inspect.getsource(cfg)
    assert "from app.date_context import week_anchors" in src
    assert "_days_to_sunday" not in src, "config.py kept its own copy"


# ── Timezone is explicit, never the server's ──────────────────────────────
def test_the_date_line_does_not_use_server_local_time():
    """`_build_date_prefix` used a bare `date.today()`. On a UTC container under
    BST that is a day behind between 23:00 and midnight London."""
    src = inspect.getsource(
        __import__("app.media_streams.llm_stream", fromlist=["x"])._build_date_prefix
    )
    # Executable lines only — the comment above the fix necessarily *names* the
    # old call, and a naive substring check would match its own explanation.
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    assert "date.today()" not in code, "server-local date is back in the date prefix"
    assert "clinic_today" in code


def test_the_clinic_timezone_is_london():
    assert CLINIC_TZ == "Europe/London"


def test_anchors_are_injectable_so_the_seven_day_sweep_is_possible():
    """If `today` stopped being injectable these tests could only ever exercise
    whatever weekday CI happened to run on — which is how a 1-in-7 bug survives."""
    assert isinstance(week_anchors(date(2026, 8, 9)), WeekAnchors)
    assert week_anchors(date(2026, 8, 9)).today == date(2026, 8, 9)
