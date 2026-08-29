"""
Regression: the offer record named two dates the caller never heard.

B-108b. Same call as B-108 — CA1b7b2c58 (Theorem, 27 Aug 2026, Alcester),
found while live-verifying B-86. Different mechanism, so a separate commit.

`_check_availability_acuity` writes the offer record far above the point where
the presentation mode is decided:

    line ~3233   last_offered_slots = _select_presented_tuples(...)   <- 3 days
                 slot_labels        = "%a %d %b at %H:%M" for each
    line ~3288   _presentation_mode = "single_day"                    <- 1 day
    line ~3433   _result["first_day"] = days_data[0]

On this call `_select_presented_tuples` returned one slot per day for the
soonest three Tuesdays — 1, 8 and 15 September — while single_day mode spoke
1 September and nothing else. So the session's record of "what was offered"
held two dates that were never read out.

THIS IS NOT COSMETIC — IT IS A ROUTE TO BOOKING AN UNSPOKEN DATE
----------------------------------------------------------------
`_try_slot_selection` (fast_path) and `_resolve_slot_iso` both resolve an
ordinal by INDEX into last_offered_slots. Index 1 was 8 September.

Severity, stated honestly: this did NOT fire on the live call. It is gated on
_SLOT_TRIGGER_PHRASES appearing in the prior prompt, and Susie closed with
"Would that work for you?" — the list holds "does that work", not "would that
work". It is latent there and NOT latent in general: "would you like" IS in
the list and is an ordinary way to close a single-slot offer.

One correction to an earlier reading of this defect: the utterance that
reaches it is "the second", not "the second one". "the second one" contains
"one", so it matches _SLOT_ONE_PATTERNS and _SLOT_TWO_PATTERNS at once, and
the conflict check drops it. The tests below use the form that actually fires.

THE FIX
-------
The Acuity body now calls `_sync_last_offered_to_spoken` on the single_day
path, as the other five executors already did — it rewrites both keys from
first_day, restoring the 1:1 mapping the presentation code's own header
asserts. The 90-second availability cache is reconciled at the same point,
because it was filled with the unsynced lists and a CACHE HIT restores them
verbatim into the session: the same defect through a second door.

Scoped to single_day. The multi_day seam — speech naming a second time per day
that the list does not hold — is older, documented at the resolver, and is not
this commit.
"""
from __future__ import annotations

import inspect

from app.fast_path import _try_slot_selection
from app.tools import receptionist_tools
from app.tools.receptionist_tools import _sync_last_offered_to_spoken


# The three Tuesdays _select_presented_tuples returned, one slot each.
_CROSS_DAY_OFFER = [
    {"start": "2026-09-01T09:00:00+01:00", "end": "2026-09-01T10:00:00+01:00"},
    {"start": "2026-09-08T09:00:00+01:00", "end": "2026-09-08T10:00:00+01:00"},
    {"start": "2026-09-15T09:00:00+01:00", "end": "2026-09-15T10:00:00+01:00"},
]

# What single_day actually spoke: 1 September, its one slot.
_SINGLE_DAY_RESULT = {
    "presentation_mode": "single_day",
    "first_day": {
        "date": "2026-09-01",
        "slot_times": ["09:00"],
        "slot_times_spoken": ["nine in the morning"],
        "slots": [_CROSS_DAY_OFFER[0]],
        "times_found_on_day": 1,
        "times_not_shown": 0,
    },
}


def _spoken_session():
    """A session as it stands after the fixed executor has run."""
    session = {
        "last_offered_slots": list(_CROSS_DAY_OFFER),
        "slot_labels": ["Tue 01 Sep at 09:00",
                        "Tue 08 Sep at 09:00",
                        "Tue 15 Sep at 09:00"],
    }
    _sync_last_offered_to_spoken(session, _SINGLE_DAY_RESULT)
    return session


# ---------------------------------------------------------------------------
# The defect, and that it is gone
# ---------------------------------------------------------------------------
def test_the_record_holds_only_the_day_that_was_spoken():
    session = _spoken_session()
    assert session["last_offered_slots"] == [_CROSS_DAY_OFFER[0]], (
        "the offer record still names 8 and 15 September, two dates the "
        "caller was never read out"
    )


def test_an_ordinal_can_no_longer_reach_an_unspoken_date():
    """The money test. "the second" against a one-slot offer must resolve to
    nothing, not to 8 September."""
    session = _spoken_session()
    _try_slot_selection("would you like", "the second", "the second", session)
    assert "selected_slot" not in session, (
        f"an ordinal selected {session.get('selected_slot')!r} — a date that "
        f"was never spoken to the caller"
    )


def test_the_unsynced_record_is_what_made_that_reachable():
    """Pins the mechanism rather than trusting the description of it: with the
    pre-fix record in place, "the second" books 8 September."""
    session = {"last_offered_slots": list(_CROSS_DAY_OFFER)}
    _try_slot_selection("would you like", "the second", "the second", session)
    assert session.get("selected_slot") == _CROSS_DAY_OFFER[1]
    assert session["selected_slot"]["start"].startswith("2026-09-08")


def test_the_first_ordinal_still_books_the_day_that_was_spoken():
    """The fix must not take the working case with it."""
    session = _spoken_session()
    _try_slot_selection("would you like", "the first", "the first", session)
    assert session.get("selected_slot") == _CROSS_DAY_OFFER[0]


def test_the_labels_are_the_strings_the_caller_actually_heard():
    """connection.py matches the caller's utterance against slot_labels, and
    _build_slot_clarify speaks them, so the spoken form is the one both live
    readers want."""
    session = _spoken_session()
    assert session["slot_labels"] == ["nine in the morning"]


# ---------------------------------------------------------------------------
# The second door
# ---------------------------------------------------------------------------
def test_the_cache_is_reconciled_with_what_was_spoken():
    """A CACHE HIT restores last_offered_slots verbatim. Left holding the
    unsynced list it re-opens the same defect whenever the caller asks again
    inside 90 seconds with the same hint."""
    src = inspect.getsource(receptionist_tools)
    assert '_cache_now["last_offered_slots"] = session.get("last_offered_slots")' in src
    assert '_cache_now["slot_labels"] = session.get("slot_labels")' in src


def test_every_producer_that_writes_the_record_also_aligns_it():
    """The invariant, across all seven producers rather than the one call site.

    This test used to read the Acuity body for the string
    "_sync_last_offered_to_spoken(session, _result)", and a second one asserted
    it sat at indent 12. Both pinned one producer and one layout: a seventh
    producer could be added, or an existing one could stop aligning, with the
    file still green — and re-indenting a working body turned it red. A text
    scan cannot tell coupling from prose (third instance in this repo), and an
    indent is not behaviour.

    Stated as the property instead: any function that writes the offer record
    must also align it with what was spoken. Walked over the AST, so a new
    reader is caught the day it is written.

    `_sync_last_offered_to_spoken` is itself excluded — writing the record is
    what it exists to do.
    """
    import ast

    tree = ast.parse(inspect.getsource(receptionist_tools))
    offenders = []
    producers = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == "_sync_last_offered_to_spoken":
            continue
        writes = any(
            isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Name)
            and n.value.id == "session"
            and isinstance(n.slice, ast.Constant)
            and n.slice.value == "last_offered_slots"
            and isinstance(getattr(n, "ctx", None), ast.Store)
            for n in ast.walk(node)
        )
        if not writes:
            continue
        producers.append(node.name)
        aligns = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_sync_last_offered_to_spoken"
            for n in ast.walk(node)
        )
        if not aligns:
            offenders.append(node.name)

    assert not offenders, (
        f"{offenders} write session['last_offered_slots'] and never align it "
        f"with what was spoken. The record is indexed BY POSITION by "
        f"_try_slot_selection and _resolve_slot_iso, so an unaligned record is "
        f"a route to booking a date the caller never heard (B-108b)."
    )
    assert producers, "the AST walk found no producers at all — it has rotted"


def test_the_producer_count_is_stated_out_loud():
    """A change to this number is a change worth reading in a diff.

    Not a cap: adding a reader is fine, and the test above is what makes it
    safe. This says how many there are, so nobody has to count them again from
    a grep — which is how "the other readers already carry this" was got wrong
    once before (B-110).
    """
    import ast

    tree = ast.parse(inspect.getsource(receptionist_tools))
    sites = sum(
        1 for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_sync_last_offered_to_spoken"
    )
    assert sites == 7, (
        f"{sites} call sites align the offer record, not 7. The seven are: the "
        f"Acuity single_day path, the generic reader's four returns (no tokens, "
        f"freebusy failed, the widen branch, the main path), the diary reader "
        f"and the published reader."
    )


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_a_multi_day_result_leaves_the_record_alone():
    """The Acuity payload names its presented days `available_days`, not
    `presented_days`, so the aligner finds no spoken slots. Guarded by the
    aligner's own `if spoken_slots:` — the record must survive intact."""
    session = {"last_offered_slots": list(_CROSS_DAY_OFFER)}
    _sync_last_offered_to_spoken(
        session,
        {"presentation_mode": "multi_day", "available_days": []},
    )
    assert session["last_offered_slots"] == _CROSS_DAY_OFFER


# ---------------------------------------------------------------------------
# End to end, through the real executor. This is the half that fails before the
# fix: everything above calls the aligner directly, and the aligner already
# existed — the defect was that the Acuity body never called it.
# ---------------------------------------------------------------------------
import datetime as _dt
from unittest.mock import patch

import pytz

import app.tools.receptionist_tools as rt

_TZ = pytz.timezone("Europe/London")


class _Slot:
    def __init__(self, start, end):
        self.start_time, self.end_time = start, end


def _three_of_one_weekday():
    """Three occurrences of one weekday, thinnest first — the live shape.

    Anchored on the real today rather than a fixed date: a pin that names a
    weekday dies at midnight (b55), and this one has to keep meaning "the same
    weekday, three times".
    """
    base = _dt.date.today() + _dt.timedelta(days=4)
    plan = {base: 1, base + _dt.timedelta(days=7): 4,
            base + _dt.timedelta(days=14): 5}
    out = []
    for d, n in plan.items():
        for hour in (9, 10, 11, 14, 15)[:n]:
            out.append(_Slot(
                _dt.datetime(d.year, d.month, d.day, hour, 0, tzinfo=_TZ),
                _dt.datetime(d.year, d.month, d.day, hour, 50, tzinfo=_TZ),
            ))
    return base, out


async def _run_executor():
    base, slots = _three_of_one_weekday()

    class _Stub:
        async def get_available_slots(self, **_kw):
            return slots

    session = {
        "clinic_id": "theorem",
        "selected_location": "alcester",
        "call_sid": "TEST",
    }
    with patch.object(rt, "_get_acuity_adapter",
                      lambda *a, **k: _Stub(), create=True):
        result = await rt._check_availability_acuity(
            {
                "service":   "msk_initial_assessment",
                "location":  "alcester",
                "date_hint": base.strftime("%A"),   # a BARE weekday
            },
            session,
        )
    return base, result, session


async def test_end_to_end_the_record_holds_only_the_spoken_day():
    base, result, session = await _run_executor()
    assert result.get("presentation_mode") == "single_day"
    dates = sorted({s["start"][:10] for s in session["last_offered_slots"]})
    assert dates == [base.isoformat()], (
        f"the offer record spans {dates}, but only {base.isoformat()} was "
        f"spoken — the later dates were never read out to the caller"
    )


async def test_end_to_end_an_ordinal_cannot_reach_an_unspoken_date():
    base, result, session = await _run_executor()
    _try_slot_selection("would you like", "the second", "the second", session)
    picked = session.get("selected_slot")
    assert picked is None or picked["start"].startswith(base.isoformat()), (
        f"an ordinal selected {picked!r} — a date never spoken to the caller"
    )


async def test_end_to_end_the_cache_replays_the_spoken_day():
    """A CACHE HIT restores last_offered_slots verbatim, so the cache has to
    carry the spoken list too, not the one built before the mode was known."""
    base, result, session = await _run_executor()
    cache = session.get("_availability_cache") or {}
    dates = sorted({s["start"][:10] for s in cache.get("last_offered_slots", [])})
    assert dates == [base.isoformat()], (
        f"the 90s cache still holds {dates} — a second ask inside 90 seconds "
        f"restores the unspoken dates into the session"
    )
