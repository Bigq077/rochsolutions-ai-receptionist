"""A clinic that is shut on a bank holiday must not offer slots on one.

THE DEFECT (found 2026-08-28 on the first live call to the fourth clinic)

    caller: "can I book a sports massage, weekday morning?"
    diary:  Monday 31 August 2026, 08:00-09:00

31 August 2026 is the England/Wales Summer bank holiday, and the engine knows
it -- the date is hardcoded in _UK_BANK_HOLIDAYS_FALLBACK. It just never asked.
`_fetch_uk_bank_holidays()` had exactly ONE consumer,
`_check_availability_acuity`. The Google-Calendar reader, the diary reader and
the published reader never called it, so of the four live clinics only Theorem
was protected. The other three would book a patient onto a bank holiday, the
call would sound perfect, the confirmation SMS would be correct, and the
patient would arrive at a locked door.

On the diary reader it is worse than neutral: that reader treats an empty day
as FREE, so a bank holiday nobody works reads as wide open -- the day most
likely to be offered, not least.

THE FIX, and what these tests hold in place

One filter in `generate_candidate_slots`, the single day-loop every generated
reader shares, rather than one filter per reader -- the same remedy as the
service pin in f2ba13d8, and for the same reason: a per-reader filter is one
new reader away from being wrong again. `test_every_generator_call_passes_the_filter`
is the test that matters; it fails if someone adds a sixth call site without it.

DEFAULT IS CLOSED, deliberately. The two mistakes are not symmetric: a clinic
wrongly closed costs a caller one day of options, a clinic wrongly open sends
someone to a locked door. A clinic that genuinely works bank holidays sets
operational.open_on_bank_holidays true.

Deterministic: no model, no network, no calendar.
"""
from __future__ import annotations

import ast
import datetime as dt
from datetime import datetime
from pathlib import Path

import pytest

import app.clinic_config as cc
from app.tools import receptionist_tools as rt
from app.tools.slots import generate_candidate_slots

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "app" / "tools" / "receptionist_tools.py"

# The one the caller was actually booked onto.
AUGUST_BANK_HOLIDAY = dt.date(2026, 8, 31)

# Northgate's real Monday: 08:00-17:30, which is why 08:00 was offered.
WORKING_HOURS = {
    "mon": (8, 17.5), "tue": (8, 17.5), "wed": (8, 17.5),
    "thu": (8, 19), "fri": (8, 16), "sat": (9, 12.5), "sun": None,
}


def _monday_slots(closed_dates):
    return generate_candidate_slots(
        datetime(2026, 8, 31, 0, 0), datetime(2026, 8, 31, 23, 59),
        duration_min=60, clinic_working_hours=WORKING_HOURS,
        closed_dates=closed_dates,
    )


# ---------------------------------------------------------------------------
# The day-loop, which is where the filter now lives
# ---------------------------------------------------------------------------

def test_the_booked_slot_can_no_longer_be_generated():
    """08:00 on the August bank holiday — the exact slot that was booked."""
    assert _monday_slots(frozenset({AUGUST_BANK_HOLIDAY})) == []


def test_the_same_monday_is_bookable_when_it_is_not_a_holiday():
    """The filter must remove a DATE, not a weekday."""
    slots = _monday_slots(frozenset())
    assert slots, "Monday 08:00 must still be offered on an ordinary Monday"
    assert slots[0][0].strftime("%H:%M") == "08:00"


def test_an_empty_closed_set_does_not_disable_the_filter():
    """bool(frozenset()) is False.

    Guarding the filter on truthiness rather than `is not None` would make a
    clinic with no closures in the window silently unfiltered — the identical
    trap the Acuity site already carries a warning about.
    """
    assert len(_monday_slots(frozenset())) == len(_monday_slots(None))


# ---------------------------------------------------------------------------
# The clinic key
# ---------------------------------------------------------------------------

async def test_a_clinic_is_closed_on_bank_holidays_by_default():
    closed = await rt._closed_dates_for({})
    assert AUGUST_BANK_HOLIDAY in closed


async def test_a_clinic_can_opt_in_to_working_them():
    assert await rt._closed_dates_for({"open_on_bank_holidays": True}) == frozenset()


async def test_the_opt_in_returns_an_empty_set_not_none():
    """The caller passes this straight through to `closed_dates`, and None
    there means 'no filter'. For an opting-in clinic that is the same outcome,
    but only by accident — an empty set says it deliberately."""
    assert isinstance(await rt._closed_dates_for({"open_on_bank_holidays": True}),
                      frozenset)


def test_every_generated_clinic_on_this_branch_is_closed_by_default():
    """The clinics that were exposed — resolved from disk, not hardcoded.

    Naming jv_v1 / vital_edge / northgate here would measure whichever of them
    happens to exist on the branch under test: `northgate` lives only on
    canonical, and on a clinic branch get_clinic() would fall back to DEMO and
    quietly assert nothing. That is the clinic-pinned-test trap, and this file
    has to survive being ported to three branches.
    """
    checked = []
    for path in sorted((REPO / "app" / "clinics").glob("*/clinic.json")):
        cid = path.parent.name
        # Must be a clinic that genuinely LOADS from json: get_clinic always
        # injects clinic_id, even on the demo fallback, so the returned id
        # cannot tell you whether the file parsed. app/clinics/demo/clinic.json
        # is Python source with a .json extension and resolves to the legacy
        # CLINICS dict, which never goes through the mapper and so has no such
        # key at all — falsy, hence closed, but not the contract under test.
        if cc._load_clinic_json(cid) is None:
            continue
        clinic = cc.get_clinic(cid)
        if cid in cc.CLINICS:
            continue                      # legacy dict wins over the file
        if not (clinic.get("booking_system") or "").startswith("google_calendar"):
            continue                      # Acuity clinics generate no slots here
        checked.append(cid)
        assert clinic.get("open_on_bank_holidays") is False, (
            f"{cid} opts into bank holidays — deliberate? If so this test "
            "needs to know about it; if not, it will book someone onto one.")

    assert checked, "no google_calendar clinic found — this would prove nothing"


# ---------------------------------------------------------------------------
# The structural pin — the one that stops this coming back
# ---------------------------------------------------------------------------

def test_every_generator_call_passes_the_filter():
    """A new reader must not be able to skip it by simply not opting in.

    The defect was never that the filter was wrong; it was that it existed at
    one call site out of several. Enumerating readers by hand is what failed,
    so this enumerates them mechanically instead.
    """
    tree = ast.parse(TOOLS.read_text(encoding="utf-8"))
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name != "generate_candidate_slots":
            continue
        if not any(kw.arg == "closed_dates" for kw in node.keywords):
            missing.append(node.lineno)

    assert not missing, (
        f"generate_candidate_slots is called without closed_dates at "
        f"{missing} in {TOOLS.name}. Every reader must pass it — resolve the "
        "set once with _closed_dates_for(clinic) and hand it through, rather "
        "than filtering per reader."
    )


def test_the_acuity_reader_shares_the_same_switch():
    """Theorem had the only working filter; it must now read the same key.

    Otherwise `open_on_bank_holidays: true` would be honoured by three readers
    and ignored by the fourth, which is the shape of the original bug wearing
    different clothes.
    """
    tree = ast.parse(TOOLS.read_text(encoding="utf-8"))

    # Map every call of the raw fetcher to the function that contains it. A
    # text search would match the COMMENTS that explain the fetcher, which is
    # prose, not a call — the same distinction that made the tenancy scan
    # switch to the AST.
    callers = set()
    for parent in ast.walk(tree):
        if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(parent):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "_fetch_uk_bank_holidays"):
                callers.add(parent.name)

    assert callers == {"_closed_dates_for"}, (
        f"_fetch_uk_bank_holidays is called from {sorted(callers)}. It must "
        "have exactly one caller, _closed_dates_for — every reader going "
        "through that helper is what makes one clinic.json key govern them "
        "all. Reading the raw set directly opts a reader out of the switch."
    )
