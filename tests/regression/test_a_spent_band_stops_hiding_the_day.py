"""
Regression: Susie promised "a few others that day" and then could not deliver.

B-98, the successor call to `CA6fa4b4339c567e19e3fb2b47b2847dde` (26 Aug 2026,
theorem_v3, Alcester). B-97 shipped that evening and was verified on this call.
It worked -- and it turned a false statement into an unkeepable promise:

    22:40:38  caller: "uh afternoons"
              time_of_day_preference captured: afternoons (tier=hard)
    22:41:05  date_hint 'afternoons' -> 'afternoon Wednesday 2 September 2026'
              2026-09-02 -- 2 raw slot(s)  ->  slot_times: ["14:00"]
    22:41:06  slot buf: appended more-times tail (more_times=True, n_offered=1)
              "Wednesday 2nd September -- two in the afternoon.
               And I've a few others that day if that doesn't suit."

    22:41:16  caller: "what are the others do you have that day"
    22:41:20  date_hint = "Wednesday 2 September 2026 afternoon" -> ["14:00"]
              -> the identical sentence, tail and all

    22:41:30  caller: "yeah what are the few others you have that day"
    22:41:34  date_hint = "afternoon Wednesday 2 September 2026" -> ["14:00"]
              -> the identical sentence, a third time

    22:41:4x  caller hung up.   obs judge score=1.

B-97 set `more_times` from `times_not_shown`, so the tail is now TRUE: the day
really does hold another slot. But every follow-up lookup re-applied the same
time-of-day band, so the slot it advertises is the one slot the retrieval path
can never return. Before B-97 this was a false statement that ended the call.
After it, it is a true statement that LOOPS. The booking is lost either way and
the loop sounds broken in a way the falsehood did not.

THE FIX. Once every in-band slot on a day has actually been SPOKEN, re-applying
that band to that day is a guaranteed no-op -- by construction it can only
return what was already read out. So the band is spent there, and that day
alone keeps all of its slots. `_filter_tuples_by_preference` is the one
chokepoint `_build_days_data` (available_days) and `_select_presented_tuples`
(slot_labels) share, so both presentation surfaces agree -- the C5-5 contract.

Keyed on the CUMULATIVE SPOKEN RECORD, never on the caller's words. "what are
the others", "anything else", "go on then" and a silent re-ask are the same
request and a phrase table would have to guess which -- the recurring
`screening-triggers-need-verb-plus-bodypart` shape, where adding more phrases
is the trap and the matcher is the bug. Firing without being asked is bounded:
dropping a band only ever ADDS slots, never removes the one the caller asked
for, and their stated preference is still in the prompt.

This is the behaviour change B-97's commit message explicitly deferred --
"offering the hidden slots when the preferred band comes up empty" -- and the
repo owner made that call on 26 Aug after the loop was reported.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta

from app.tools.receptionist_tools import (
    LONDON_TZ,
    _build_days_data,
    _select_presented_tuples,
)
from app.tools.slot_followup import record_spoken_slots, spoken_starts_for_offer


# The band filter drops past slots, so these must be in the future. Anchored
# RELATIVE to today on purpose: the live call's own dates (2 September 2026)
# would make this file start failing a week after it was written, which is how
# a regression test quietly stops defending anything.
def _day_after(days: int) -> datetime:
    return datetime.now(LONDON_TZ) + timedelta(days=days)


def _slots(offset_days: int, *times: str):
    """(start, end) tuples on one future day, one hour each."""
    base = _day_after(offset_days)
    out = []
    for t in times:
        h, m = (int(x) for x in t.split(":"))
        start = base.replace(hour=h, minute=m, second=0, microsecond=0)
        out.append((start, start + timedelta(hours=1)))
    return out


def _iso(t) -> str:
    return t[0].isoformat()[:19]


# ---------------------------------------------------------------------------
# The live loop
# ---------------------------------------------------------------------------
def test_the_live_defect_the_second_lookup_reaches_the_hidden_slot():
    """Wednesday's 2pm has been read out. The 10am must now be reachable."""
    day_slots = _slots(6, "10:00", "14:00")
    heard = {_iso(day_slots[1])}          # she read out "two in the afternoon"

    day = _build_days_data(day_slots, preference="afternoon",
                           spoken_starts=heard)[0]

    assert day["slot_times"] == ["10:00", "14:00"], (
        "the caller asked for the others and the band still hid one"
    )
    # And the promise closes: there is nothing left to advertise.
    assert day["times_not_shown"] == 0
    assert day["times_found_on_day"] == 2


def test_the_first_lookup_is_unchanged():
    """Nothing spoken yet -- the caller's band is honoured in full (B-97)."""
    day = _build_days_data(_slots(6, "10:00", "14:00"),
                           preference="afternoon", spoken_starts=set())[0]
    assert day["slot_times"] == ["14:00"]
    assert day["times_not_shown"] == 1, "B-97's honest count must survive"


def test_no_spoken_record_behaves_exactly_as_before():
    """The argument is optional and absent means 'band as usual'."""
    with_none = _build_days_data(_slots(6, "10:00", "14:00"),
                                 preference="afternoon")
    with_empty = _build_days_data(_slots(6, "10:00", "14:00"),
                                  preference="afternoon", spoken_starts=set())
    assert with_none[0]["slot_times"] == with_empty[0]["slot_times"] == ["14:00"]


# ---------------------------------------------------------------------------
# The band is spent only when it has nothing left to give
# ---------------------------------------------------------------------------
def test_a_partly_served_band_keeps_its_band():
    """Two afternoon slots, one heard. The band can still offer the other, so
    the morning slot must NOT jump the queue ahead of it."""
    day_slots = _slots(6, "09:00", "14:00", "15:00")
    heard = {_iso(day_slots[1])}          # only the 2pm

    day = _build_days_data(day_slots, preference="afternoon",
                           spoken_starts=heard)[0]
    assert day["slot_times"] == ["14:00", "15:00"]
    assert day["times_not_shown"] == 1


def test_a_band_that_hides_nothing_is_never_spent():
    """Every slot on the day is in the band. There is nothing to free, and the
    day must not be reshaped just because the caller heard it all."""
    day_slots = _slots(6, "14:00", "15:00")
    heard = {_iso(day_slots[0]), _iso(day_slots[1])}

    day = _build_days_data(day_slots, preference="afternoon",
                           spoken_starts=heard)[0]
    assert day["slot_times"] == ["14:00", "15:00"]
    assert day["times_not_shown"] == 0


def test_a_spent_band_frees_only_the_day_it_was_served_on():
    """Wednesday's afternoon was served; Friday's has never been offered. The
    caller asked for afternoons and Friday must still respect that."""
    wed = _slots(6, "10:00", "14:00")
    fri = _slots(8, "11:00", "16:00")
    heard = {_iso(wed[1])}

    by_date = {
        d["date"]: d
        for d in _build_days_data(wed + fri, preference="afternoon",
                                  spoken_starts=heard)
    }
    wed_date = wed[0][0].date().isoformat()
    fri_date = fri[0][0].date().isoformat()

    assert by_date[wed_date]["slot_times"] == ["10:00", "14:00"]
    assert by_date[fri_date]["slot_times"] == ["16:00"], (
        "a day the caller has never heard must keep the band"
    )


def test_mornings_and_evenings_are_freed_the_same_way():
    """The rule belongs to the band mechanism, not to one band."""
    morning = _slots(6, "09:00", "14:00")
    day = _build_days_data(morning, preference="morning",
                           spoken_starts={_iso(morning[0])})[0]
    assert day["slot_times"] == ["09:00", "14:00"]

    evening = _slots(6, "11:00", "18:00")
    day = _build_days_data(evening, preference="evening",
                           spoken_starts={_iso(evening[1])})[0]
    assert day["slot_times"] == ["11:00", "18:00"]


# ---------------------------------------------------------------------------
# Both presentation surfaces have to agree (bug C5-5)
# ---------------------------------------------------------------------------
def test_slot_labels_and_available_days_free_the_same_slot():
    """_select_presented_tuples builds slot_labels and _build_days_data builds
    available_days. If only one of them frees the slot, the ordinal the caller
    picks resolves to a different time than the one they heard."""
    day_slots = _slots(6, "10:00", "14:00")
    heard = {_iso(day_slots[1])}

    presented = _select_presented_tuples(day_slots, preference="afternoon",
                                         spoken_starts=heard)
    day = _build_days_data(day_slots, preference="afternoon",
                           spoken_starts=heard)[0]

    assert {p[0].strftime("%H:%M") for p in presented} == set(day["slot_times"])


# ---------------------------------------------------------------------------
# Through real session state, not a hand-made set
# ---------------------------------------------------------------------------
def test_the_loop_closes_across_two_real_lookups():
    """Lookup 1 -> she speaks the 2pm -> lookup 2 must offer the 10am.

    Driven through the session the way the call does it, so the spoken record's
    fingerprint reset is exercised rather than assumed.
    """
    day_slots = _slots(6, "10:00", "14:00")
    session: dict = {}

    # Lookup 1 -- the band applies, one slot survives, and it is advertised.
    session["available_days"] = _build_days_data(
        day_slots, preference="afternoon",
        spoken_starts=spoken_starts_for_offer(session),
    )
    assert session["available_days"][0]["slot_times"] == ["14:00"]
    assert session["available_days"][0]["times_not_shown"] == 1

    # She reads it out.
    record_spoken_slots(session, [{"start": _iso(day_slots[1])}])

    # Lookup 2 -- "what are the others do you have that day".
    session["available_days"] = _build_days_data(
        day_slots, preference="afternoon",
        spoken_starts=spoken_starts_for_offer(session),
    )
    assert session["available_days"][0]["slot_times"] == ["10:00", "14:00"], (
        "the third identical answer that ended CA6fa4b433's successor call"
    )
    assert session["available_days"][0]["times_not_shown"] == 0, (
        "nothing left hidden, so the 'a few others' tail must not fire again"
    )


# ---------------------------------------------------------------------------
# Structural -- the wiring cannot be dropped by a new call site
# ---------------------------------------------------------------------------
def test_every_presentation_builder_is_given_the_spoken_record():
    """The two builders share one filter, and a call site that forgets to pass
    the record silently restores the loop -- with no error and no log line.
    There are seven pairs across four availability readers; this counts them."""
    import app.tools.receptionist_tools as rt

    src = inspect.getsource(rt)
    for fn in ("_build_days_data(", "_select_presented_tuples("):
        calls = [ln for ln in src.splitlines() if fn in ln and "def " not in ln]
        assert calls, f"no call sites found for {fn}"
        missing = [ln.strip() for ln in calls if "spoken_starts" not in ln]
        assert not missing, f"{fn} called without the spoken record: {missing}"


def test_the_exemption_is_keyed_on_what_was_heard_not_on_what_was_said():
    """Guards the SHAPE of the trigger. An utterance matcher here would have to
    enumerate every way a caller asks for more, and would fire on a day the
    caller had never been offered."""
    import app.tools.receptionist_tools as rt

    fn = rt._days_where_the_band_is_spent
    # The prose quotes the very phrases the code must not contain -- that is
    # what it is explaining -- so judge the CODE. Parsed rather than sliced by
    # string: receptionist_tools.py is CRLF on disk while __doc__ is normalised
    # to LF, so cutting the docstring out textually silently cuts nothing.
    body = ast.parse(inspect.getsource(fn)).body[0].body
    if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]                       # drop the docstring
    code = " ".join(ast.unparse(node) for node in body)
    assert "spoken_starts" in code
    for phrase in ("what else", "anything else", "the others", "transcript",
                   "utterance", "last_user"):
        assert phrase not in code.lower(), (
            f"the band exemption must not read the caller's words ({phrase!r})"
        )


# ---------------------------------------------------------------------------
# The 90-second cache is a second door onto the same loop
# ---------------------------------------------------------------------------
def test_the_cache_stands_down_once_its_offer_is_exhausted():
    """CA6fa4b433's successor escaped the cache only by luck: the model
    reworded the hint each time ("afternoon Wednesday 2 September 2026" ->
    "Wednesday 2 September 2026 afternoon"), so the text key missed. A model
    that repeats itself verbatim inside 90 seconds would have replayed the
    band-filtered day with no fetch at all -- the same loop, served from
    memory, and the band-spent rule never consulted."""
    import app.tools.receptionist_tools as rt

    day_slots = _slots(6, "10:00", "14:00")
    cached = _build_days_data(day_slots, preference="afternoon",
                              spoken_starts=set())
    assert cached[0]["times_not_shown"] == 1, "the cache holds a hidden time"

    heard = {_iso(day_slots[1])}
    assert rt._cached_offer_is_exhausted(cached, heard) is True

    # Not yet heard -> the cache is still honest and must keep serving.
    assert rt._cached_offer_is_exhausted(cached, set()) is False


def test_a_cache_that_hides_nothing_keeps_serving():
    """The stand-down must not disable the cache generally -- it exists to
    skip a 30-call Acuity fetch when the caller picks a slot they just heard."""
    import app.tools.receptionist_tools as rt

    day_slots = _slots(6, "14:00", "15:00")
    cached = _build_days_data(day_slots, preference="afternoon",
                              spoken_starts=set())
    assert cached[0]["times_not_shown"] == 0
    heard = {_iso(day_slots[0]), _iso(day_slots[1])}
    assert rt._cached_offer_is_exhausted(cached, heard) is False


def test_a_partly_heard_cache_keeps_serving():
    """Only one of the two shown slots has been spoken -- the payload still has
    something to say, so the fetch must not be forced."""
    import app.tools.receptionist_tools as rt

    day_slots = _slots(6, "09:00", "14:00", "15:00")
    cached = _build_days_data(day_slots, preference="afternoon",
                              spoken_starts=set())
    assert cached[0]["slot_times"] == ["14:00", "15:00"]
    assert rt._cached_offer_is_exhausted(cached, {_iso(day_slots[1])}) is False


def test_the_cache_predicate_never_raises_on_junk():
    """It gates a live lookup. A malformed payload must degrade to 'serve the
    cache', which is exactly today's behaviour, not blow up the call."""
    import app.tools.receptionist_tools as rt

    for junk in (None, "not a list", [None], [{"times_not_shown": "x"}],
                 [{"times_not_shown": 1, "slots": None}],
                 [{"times_not_shown": 1, "slots": [None]}]):
        assert rt._cached_offer_is_exhausted(junk, {"anything"}) is False
