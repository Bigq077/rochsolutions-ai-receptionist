"""
Regression: "I don't have any further times on that day" about a day with one.

B-99, CA890b511e1bbcda1a9c1cecf7a95f8207 (27 Aug 2026, theorem_v3, Alcester).
The call that verified B-98, which is how this was caught: Susie contradicted
herself inside fifty seconds.

    08:42:23  caller: "uh afternoons"
              time_of_day_preference captured: afternoons (tier=hard)
    08:42:28  check_availability date_hint="afternoons"
              2026-08-28 - 2 raw slot(s) from Acuity  ->  slot_times ["14:00"]
    08:42:30  "Here's what we've got coming up - Number 1, Friday 28th August.
               Number 2, Wednesday 2nd September - two in the afternoon.
               Number 3, Friday 4th September - one in the afternoon."
              slot buf: spoken options span 3 days - offer record left unchanged

    08:42:43  caller: "um do you have any other availability on wednesday the
                       2nd of september during the 2 o'clock slot"
    08:42:49  path=slot_followup  llm_ttft_ms=1
              "I don't have any further times on that day - would you like me
               to look at a different day?"

    08:43:39  band 'afternoons friday' is SPENT on ['2026-08-28', ...]
              -> slot_times ["12:00", "14:00"]
    08:43:40  "Friday 28th August - Number 1, midday. Number 2, two in the
               afternoon."

Both halves of that sentence were wrong at once:

  1. THE DAY WAS NOT IDENTIFIED. The caller named Wednesday 2 September. The
     offer on the table spanned THREE days, and remaining_unspoken_on_current_day
     takes "the day under discussion" from last_offered_slots[0] - whichever
     sorts first, here Friday 28 August. So the answer was about a day nobody
     asked about, in words ("that day") that sound like it was about the one
     they did.

  2. THE DAY WAS NOT EXHAUSTED. "afternoons" had already removed midday from
     Friday before the session ever saw it. Every follow-up here subtracts the
     spoken times from available_days - the SURVIVORS - so it reaches zero
     while a bookable appointment sits behind the filter. The same call proved
     it: fifty seconds later that day produced the midday.

This is NOT a regression from B-98. remaining_unspoken has always read
available_days; B-97 and B-98 both fixed the check_availability path and never
touched this module. B-98 only made it visible, by contradicting it.

THE FIX. "I don't have any further times on that day" is a completeness claim
about a DAY - the same claim B-97 caught in "that's the only one we have that
day", made by a producer no banned-phrase table and no availability guard can
see. exhaustion_claim_is_supported is the one place that asks whether it holds:
exactly one day on the table, and that day complete. Anything else and the
paths decline and let a real lookup answer, which post-B-98 can reach the
hidden times.

Fails CLOSED, like _scarcity_claim_is_supported.
"""
from __future__ import annotations

import inspect

from app.tools.slot_followup import (
    apply_next_batch_to_session,
    exhaustion_claim_is_supported,
    offer_day_hides_times,
    record_spoken_slots,
    try_unspoken_followup_speech,
)


def _day(date, times, found=None):
    """One day of available_days. `found` is what the day really holds."""
    found = len(times) if found is None else found
    return {
        "date": date,
        "day_label": date,
        "slot_times": times,
        "slot_times_spoken": times,
        "slots": [{"start": f"{date}T{t}:00+01:00", "end": ""} for t in times],
        "times_found_on_day": found,
        "times_not_shown": max(0, found - len(times)),
    }


def _offer(*starts):
    return [{"start": s, "end": ""} for s in starts]


def _live_session():
    """CA890b511e as it stood at 08:42:49, band-filtered, three days offered."""
    session = {
        "available_days": [
            _day("2026-08-28", ["14:00"], found=2),   # midday hidden by the band
            _day("2026-09-02", ["14:00"], found=2),   # one hidden by the band
            _day("2026-09-04", ["13:00"], found=1),
            _day("2026-09-07", ["14:00", "15:00"], found=2),
            _day("2026-09-08", ["14:00", "15:00"], found=2),
        ],
        "last_offered_slots": _offer(
            "2026-08-28T14:00:00+01:00",
            "2026-09-02T14:00:00+01:00",
            "2026-09-04T13:00:00+01:00",
        ),
    }
    record_spoken_slots(session, session["last_offered_slots"])
    return session


_LIVE_UTTERANCE = (
    "um do you have any other availability on wednesday the 2nd of september "
    "during the 2 o'clock slot"
)
_FALSE_SENTENCE = "I don't have any further times on that day"


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------
def test_the_live_defect_the_sentence_is_not_spoken():
    """The fast path must decline, not assert. Falling through lets the model
    run a real lookup, which B-98 opens up."""
    spoken = try_unspoken_followup_speech(_live_session(), _LIVE_UTTERANCE)
    assert spoken is None or _FALSE_SENTENCE not in spoken, (
        f"the 08:42:49 answer is still reachable: {spoken!r}"
    )


def test_the_live_offer_cannot_support_the_claim():
    assert exhaustion_claim_is_supported(_live_session()) is False


def test_the_live_offer_is_known_to_hide_times():
    assert offer_day_hides_times(_live_session()) is True


# ---------------------------------------------------------------------------
# Both halves have to fail it independently
# ---------------------------------------------------------------------------
def test_a_multi_day_offer_alone_defeats_the_claim():
    """Every day complete, but three of them — so "that day" names nothing."""
    session = {
        "available_days": [_day("2026-08-28", ["14:00"]),
                           _day("2026-09-02", ["14:00"])],
        "last_offered_slots": _offer("2026-08-28T14:00:00+01:00",
                                     "2026-09-02T14:00:00+01:00"),
    }
    record_spoken_slots(session, session["last_offered_slots"])
    assert offer_day_hides_times(session) is False, "nothing is hidden here"
    assert exhaustion_claim_is_supported(session) is False, (
        "a single day has to be identified before it can be called full"
    )


def test_a_hidden_time_alone_defeats_the_claim():
    """One day, unambiguous — but the band took midday off it."""
    session = {
        "available_days": [_day("2026-08-28", ["14:00"], found=2)],
        "last_offered_slots": _offer("2026-08-28T14:00:00+01:00"),
    }
    record_spoken_slots(session, session["last_offered_slots"])
    assert offer_day_hides_times(session) is True
    assert exhaustion_claim_is_supported(session) is False


# ---------------------------------------------------------------------------
# The honest case must survive — this is not a licence to stop answering
# ---------------------------------------------------------------------------
def test_one_complete_day_still_says_so():
    """A day that really is finished gets the deterministic sentence. Losing
    this is how "have you got anything else?" goes back to the model, which is
    what said "those are the two available slots" with three unoffered."""
    session = {
        "available_days": [_day("2026-08-28", ["12:00", "14:00"], found=2)],
        "last_offered_slots": _offer("2026-08-28T12:00:00+01:00",
                                     "2026-08-28T14:00:00+01:00"),
    }
    record_spoken_slots(session, session["last_offered_slots"])
    assert exhaustion_claim_is_supported(session) is True
    spoken = try_unspoken_followup_speech(session, "have you got anything else")
    assert spoken is not None and _FALSE_SENTENCE in spoken


def test_one_complete_day_says_so_even_when_other_days_remain():
    """The second producer of the same sentence — this DAY is finished while
    the sweep still holds later days."""
    session = {
        "available_days": [_day("2026-08-28", ["12:00", "14:00"], found=2),
                           _day("2026-09-04", ["13:00"], found=1)],
        "last_offered_slots": _offer("2026-08-28T12:00:00+01:00",
                                     "2026-08-28T14:00:00+01:00"),
    }
    record_spoken_slots(session, session["last_offered_slots"])
    assert exhaustion_claim_is_supported(session) is True
    spoken = try_unspoken_followup_speech(session, "any other times that day")
    assert spoken is not None and _FALSE_SENTENCE in spoken


def test_an_unspoken_survivor_is_still_offered_first():
    """Declining the CLAIM must not stop the module OFFERING. The caller asked
    for afternoons; the unspoken afternoon slot is served before any lookup
    reaches behind the band."""
    session = {
        "available_days": [_day("2026-08-28", ["14:00", "15:00"], found=3)],
        "last_offered_slots": _offer("2026-08-28T14:00:00+01:00"),
    }
    record_spoken_slots(session, session["last_offered_slots"])
    spoken = try_unspoken_followup_speech(session, "any other times that day")
    assert spoken is not None
    assert "15:00" in spoken or "three" in spoken.lower(), spoken
    assert _FALSE_SENTENCE not in spoken


# ---------------------------------------------------------------------------
# Fails closed
# ---------------------------------------------------------------------------
def test_the_claim_fails_closed_on_junk():
    """It gates a sentence about real availability. Unreadable state declines
    to make the claim rather than making it unverified."""
    for junk in ({}, {"last_offered_slots": []},
                 {"last_offered_slots": [None]},
                 {"last_offered_slots": "nonsense"},
                 {"last_offered_slots": _offer("2026-08-28T14:00:00+01:00"),
                  "available_days": "nonsense"},
                 {"last_offered_slots": _offer("2026-08-28T14:00:00+01:00"),
                  "available_days": [{"date": "2026-08-28",
                                      "times_not_shown": "x"}]}):
        assert exhaustion_claim_is_supported(junk) is False, junk


def test_hides_times_never_raises_on_junk():
    for junk in ({}, {"available_days": None}, {"available_days": [None]},
                 {"last_offered_slots": [None], "available_days": [{}]}):
        assert offer_day_hides_times(junk) is False, junk


# ---------------------------------------------------------------------------
# The guard in llm_stream
# ---------------------------------------------------------------------------
def test_the_guard_yields_to_a_real_lookup_when_the_day_hides_times():
    from app.media_streams.llm_stream import (
        _followup_must_yield_to_a_real_lookup,
    )
    msgs = [{"role": "user", "content": _LIVE_UTTERANCE}]
    assert _followup_must_yield_to_a_real_lookup(_live_session(), msgs) is True


def test_the_guard_still_intercepts_an_acceptance():
    """CAce1457d1: on "that works for me" the model re-calls check_availability
    and the guard must stop it re-listing. Letting an acceptance through to a
    real lookup made the caller accept twice."""
    from app.media_streams.llm_stream import (
        _followup_must_yield_to_a_real_lookup,
    )
    msgs = [{"role": "user", "content": "yeah that works for me"}]
    assert _followup_must_yield_to_a_real_lookup(_live_session(), msgs) is False


def test_the_guard_is_unchanged_when_nothing_is_hidden():
    from app.media_streams.llm_stream import (
        _followup_must_yield_to_a_real_lookup,
    )
    session = {
        "available_days": [_day("2026-08-28", ["12:00", "14:00"], found=2)],
        "last_offered_slots": _offer("2026-08-28T12:00:00+01:00"),
    }
    msgs = [{"role": "user", "content": "any other times that day"}]
    assert _followup_must_yield_to_a_real_lookup(session, msgs) is False


def test_the_guard_never_raises_on_junk():
    from app.media_streams.llm_stream import (
        _followup_must_yield_to_a_real_lookup,
    )
    assert _followup_must_yield_to_a_real_lookup(None, None) is False
    assert _followup_must_yield_to_a_real_lookup({}, []) is False


# ---------------------------------------------------------------------------
# An empty batch must never reach apply_next_batch_to_session
# ---------------------------------------------------------------------------
def test_an_empty_batch_would_destroy_the_offer_record():
    """Why the call site has to be guarded: last_offered_slots is the only
    record of what is on the table, and every follow-up path opens by reading
    it. Wiping it does not just lose this answer, it disarms the module."""
    session = {
        "available_days": [_day("2026-08-28", ["14:00"])],
        "last_offered_slots": _offer("2026-08-28T14:00:00+01:00"),
    }
    apply_next_batch_to_session(session, [], False)
    assert session["last_offered_slots"] == [], (
        "if this ever stops being destructive the guard below can be relaxed"
    )


def test_the_guard_never_applies_an_empty_batch():
    """Source check: the apply call must sit behind `if not _batch`."""
    import app.media_streams.llm_stream as ls

    src = inspect.getsource(ls).replace("\r\n", "\n")
    i = src.index("elif _remaining and utterance_requests_more_slots(_user):")
    j = src.index("apply_next_batch_to_session(session, _batch, _more)", i)
    between = src[i:j]
    assert "if not _batch:" in between, (
        "an empty batch reaches apply_next_batch_to_session again — it wipes "
        "last_offered_slots and emits the B-99 sentence"
    )


def test_both_producers_of_the_sentence_ask_the_same_question():
    """One owner for the claim. A third producer that does not consult
    exhaustion_claim_is_supported is the whole defect coming back."""
    import app.tools.slot_followup as sf

    src = inspect.getsource(sf).replace("\r\n", "\n")
    calls = src.count("return format_next_batch_speech([], False)")
    gates = src.count("if not exhaustion_claim_is_supported(session):")
    assert calls >= 2, "the deterministic sentence has moved — re-aim this test"
    assert gates == calls, (
        f"{calls} site(s) speak the exhaustion sentence but {gates} check "
        f"whether it is supported"
    )
