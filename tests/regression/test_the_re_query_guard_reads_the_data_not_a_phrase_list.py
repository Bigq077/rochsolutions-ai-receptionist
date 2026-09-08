"""The guard that exists to stop this exact re-query was wired to the wrong reader.

`_one_streaming_call` already blocks a repeat check_availability when the caller
is answering a standing offer -- `status: slot_offer_still_live`, whose message
says in terms: *"Do NOT call check_availability again and Do NOT re-list the
times. If they accepted a specific time, confirm that slot..."*

On `CA4215ab7f` (theorem_v3, 8 Sep 2026, build `08e99fab`) the caller said
"yeah three works" and **it did not fire**. The model looked the diary up again,
read out three days the caller had not asked for, and they hung up.

-- THE TWO READERS ARE COMPLEMENTARY -----------------------------------------
The guard was gated on `utterance_accepts_offered_slot` alone. That function
takes TEXT and no session, so it cannot know whether "three" was one of the
times just offered. It catches the vague acceptances and misses every specific
one -- which are exactly the ones where the engine knows which slot was meant:

    "that works for me"         accepts=True   resolves=None
    "yes please"                accepts=True   resolves=None
    "yeah three works"          accepts=False  resolves 15:00
    "ten in the morning works"  accepts=False  resolves 10:00
    "the third one"             accepts=False  resolves 15:00

Neither is wrong. They answer different questions, and the guard needed both.

-- WHY THE FIX IS NOT ANOTHER PHRASE -----------------------------------------
Adding "three works" to `_SLOT_ACCEPT_PHRASES` leaves "ten in the morning
works", "the third one", and every wording after those. That is the mistake this
codebase keeps recording -- the matcher shape is the bug, not its coverage --
and the rule was already written down two thousand lines above the guard:
*"Discriminated on DATA, not a phrase list."*

`slot_accepted_by_caller` is that data test. It resolves only against times the
caller was actually read, and it declines requests and different-day moves on
its own, which is what makes it safe to widen a blocking guard with.

-- WHAT THIS FILE PINS -------------------------------------------------------
The disagreement itself, so that "fixing" either reader to cover the other's
cases shows up as a failure here rather than as a silently narrowed guard.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.media_streams import llm_stream
from app.tools.slot_offer import apply_offer_to_session
from app.tools.slot_followup import (
    slot_accepted_by_caller,
    utterance_accepts_offered_slot,
)

_DAY = "2026-09-09"
_TIMES = [("09:00", "nine in the morning"),
          ("10:00", "ten in the morning"),
          ("15:00", "three in the afternoon")]


def _session():
    day = {
        "date": _DAY, "day_label": "Wednesday 9th September",
        "slot_times": [t for t, _ in _TIMES],
        "slot_times_spoken": [s for _, s in _TIMES],
        "slots": [{"start": "%sT%s:00+01:00" % (_DAY, t), "date": _DAY,
                   "day_label": "Wednesday 9th September", "time": t,
                   "spoken": s} for t, s in _TIMES],
    }
    s = {"available_days": [day]}
    apply_offer_to_session(
        s,
        {"slots": day["slots"],
         "dtmf_map": {"1": _TIMES[0][1], "2": _TIMES[1][1], "3": _TIMES[2][1]},
         "mode": "single_day"},
        ["readout"],
    )
    s["available_days"] = [day]
    return s


def _guard_fires(utterance):
    """The guard's condition, as the source states it."""
    return bool(
        utterance_accepts_offered_slot(utterance)
        or slot_accepted_by_caller(_session(), utterance)
    )


# ---------------------------------------------------------------------------
# The live defect, and its family
# ---------------------------------------------------------------------------

def test_the_live_utterance_now_blocks_the_re_query():
    assert _guard_fires("yeah three works") is True


@pytest.mark.parametrize("said", [
    "yeah three works",
    "three works",
    "ten in the morning works",
    "the third one",
    "yeah 3 works",
    "yeah three o'clock works",
    "three in the afternoon works",
])
def test_a_specific_pick_blocks_the_re_query(said):
    """Every one of these was missed by the phrase list. Naming a time you were
    just offered is the clearest acceptance there is."""
    assert _guard_fires(said) is True, said


@pytest.mark.parametrize("said", ["that works for me", "yes please",
                                  "sounds good", "go ahead"])
def test_the_vague_acceptances_still_block_it(said):
    """The half the guard already had. Widening must not cost it."""
    assert _guard_fires(said) is True, said


# ---------------------------------------------------------------------------
# The disagreement itself, pinned
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said,by_phrase,by_data", [
    ("that works for me", True, False),
    ("yes please", True, False),
    ("yeah three works", False, True),
    ("ten in the morning works", False, True),
    ("the third one", False, True),
])
def test_the_two_readers_answer_different_questions(said, by_phrase, by_data):
    """If this ever fails, one reader has been taught the other's job. That is
    worth knowing: the guard would still pass, and the redundancy that makes it
    safe would be gone."""
    assert utterance_accepts_offered_slot(said) is by_phrase, said
    assert bool(slot_accepted_by_caller(_session(), said)) is by_data, said


# ---------------------------------------------------------------------------
# The guards. Widening a BLOCKING condition is the dangerous direction.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    "what else have you got",
    "have you got anything earlier",
    "anything on friday",
    "three works but have you got anything on friday",
    "three works, actually what about next week",
    "have you got anything the week after",
])
def test_a_request_never_blocks_the_lookup(said):
    """THE guard. This condition BLOCKS check_availability, so a false positive
    strands a caller who is asking for something new -- strictly worse than the
    defect being fixed. `slot_accepted_by_caller` declines all of these on its
    own, which is the property that makes it safe to widen with."""
    assert _guard_fires(said) is False, said


def test_an_empty_or_absent_utterance_blocks_nothing():
    for junk in ("", "   ", None):
        assert _guard_fires(junk) is False, repr(junk)


# ---------------------------------------------------------------------------
# The call site
# ---------------------------------------------------------------------------

def _guard_source():
    src = inspect.getsource(llm_stream)
    i = src.index('"status": "slot_offer_still_live"')
    return src[max(0, i - 3000):i]


def test_the_guard_consults_the_resolver_at_the_call_site():
    """The behavioural tests above rebuild the condition locally, so they would
    pass whether or not the call site uses it -- the failure mode B-134 named
    out loud and that this session has already hit once."""
    body = _guard_source()
    assert "slot_accepted_by_caller" in body or "_sabc(" in body, (
        "the re-query guard no longer reads the resolver; a specific pick "
        "falls through to already_retrieved and the caller hears a new list")
    m = re.search(r"if utterance_accepts_offered_slot\(_user\)([^\n:]*):", body)
    assert m, "the guard condition has moved - re-aim this test"
    assert "or" in m.group(1), (
        "the guard is back to the phrase list alone: %r" % m.group(0))


def test_the_resolver_failing_cannot_cost_the_caller_the_turn():
    """It runs inside the turn, so it must fall back rather than raise."""
    body = _guard_source()
    i = body.index("slot_accepted_by_caller")
    assert "except Exception:" in body[i:], body[i:][:400]
