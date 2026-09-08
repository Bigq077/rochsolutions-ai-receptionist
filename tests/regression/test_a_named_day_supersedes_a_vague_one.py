"""The first day-ish phrase of the call won for the rest of it.

`day_preference` was captured write-once:

    if not self.session.get("day_preference") and not _reason_answer:

so whatever the caller said first decided how every later readout was ordered.

`CAffe1e08713631e5178c6fe73f44e4037`, northgate, 2026-09-08.

    07:51:23  day_preference captured: this week
              (from utterance 'uh what have you got this week')
    ...
    07:52:29  caller: 'to have an appointment on tuesday around 10 am'
    07:52:35  check_availability {"date_hint": "around 10am", "day_window": 1}
    07:52:35  [slot_followup] caller asked for the soonest
              (day_preference='this week') -- reading this day from its
              earliest time, not from the ones they have not heard (B-142)

Nine turns after they had asked for a specific day and time,
`caller_wants_soonest` was still true, so B-142 kept ordering every readout by
earliest rather than by what they asked for. Tuesday came back as "twenty to
ten, or ten past five" for a caller who had said "around 10am".

This did not kill that call -- they took the twenty to ten -- and it is filed as
a latch defect rather than a P1 for that reason. The cost is a worse offer every
time, not a lost booking.

-- WHY THE FIX IS AT THE CAPTURE ---------------------------------------------
`caller_wants_soonest` reads `day_preference` and nothing else, and says why:

    Read from the captured `day_preference` rather than re-parsed from speech:
    the capture happens once, early, in connection.py, and re-deriving it here
    would be a second matcher to keep in step with the first.

That is right, so the capture is the only honest place to fix it.

-- ONE DIRECTION ONLY --------------------------------------------------------
A concrete weekday replaces a stored preference that means "soonest". Nothing
replaces a concrete day. Re-arming "soonest" from a later vague phrase is the
direction that makes readouts lead with the earliest again -- the behaviour
being corrected -- so it is refused.

`_SOONEST_DAY_PREFERENCES` is the discriminator, so the rule is stated in the
vocabulary that already exists rather than in a second list to keep in step.

-- WHAT KEEPS IT SAFE --------------------------------------------------------
The `_reason_answer` gate, five lines above, and B-138 records what happens
without it: "i did my back in on saturday" banks a saturday-only filter for the
rest of the call. Allowing a refresh widens that blast radius, so the reason
gate is load-bearing here in a way it was not when the capture was write-once.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams.connection import _extract_day_preference
from app.tools.slot_followup import (
    _SOONEST_DAY_PREFERENCES,
    caller_wants_soonest,
)


def _supersedes(prev, said):
    """The rule as the source states it."""
    new = _extract_day_preference(said)
    prev = str(prev or "").strip().lower()
    return bool(new) and (
        not prev
        or (prev in _SOONEST_DAY_PREFERENCES and new not in _SOONEST_DAY_PREFERENCES)
    )


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------

def test_the_live_turn_now_supersedes():
    assert _extract_day_preference("uh what have you got this week") == "this week"
    assert _supersedes("this week", "to have an appointment on tuesday around 10 am")
    assert _extract_day_preference(
        "to have an appointment on tuesday around 10 am") == "tuesday"


def test_the_stale_preference_kept_soonest_armed():
    """Pins the consequence, not just the value: `this week` is what made
    B-142 reorder every later readout."""
    assert caller_wants_soonest({"day_preference": "this week"}) is True
    assert caller_wants_soonest({"day_preference": "tuesday"}) is False


@pytest.mark.parametrize("vague", sorted(_SOONEST_DAY_PREFERENCES))
def test_any_soonest_preference_yields_to_a_named_day(vague):
    assert _supersedes(vague, "can I come in on thursday"), vague


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    "actually anything this week is fine",
    "as soon as possible please",
    "today if you have it",
    "whenever really",
])
def test_a_named_day_is_never_replaced_by_a_vague_one(said):
    """THE guard. Re-arming "soonest" is the direction that reintroduces the
    defect -- every later readout leads with the earliest days again."""
    assert not _supersedes("tuesday", said), said


def test_a_named_day_is_not_replaced_by_another_named_day():
    """Out of scope deliberately. Changing day mid-call is a real thing, but it
    is a different rule with a different blast radius -- `caller_wants_soonest`
    is false either way, so it changes no ordering. This commit only stops a
    vague preference outliving a specific one."""
    assert not _supersedes("tuesday", "what about friday")


def test_an_utterance_naming_no_day_changes_nothing():
    assert not _supersedes("this week", "yeah that sounds good")
    assert not _supersedes("", "yeah that sounds good")


def test_the_first_capture_still_works():
    """With nothing stored, any preference is taken -- unchanged behaviour."""
    assert _supersedes("", "uh what have you got this week")
    assert _supersedes(None, "can I come in on thursday")


@pytest.mark.parametrize("junk", [None, "", "   "])
def test_it_never_raises(junk):
    assert _extract_day_preference(junk or "") is None
    assert not _supersedes("this week", junk or "")


# ---------------------------------------------------------------------------
# The call site
# ---------------------------------------------------------------------------

def _capture_source():
    from app.media_streams import connection
    src = inspect.getsource(connection)
    i = src.index("_day_pref = _extract_day_preference(utterance)")
    return src[i - 2500:i + 1500]


def test_the_reason_gate_survives():
    """B-138. Without it, "i did my back in on saturday" banks a saturday-only
    filter -- and a REFRESHABLE capture makes that worse than it was, because
    it can now happen at any point in the call rather than only first."""
    assert "if not _reason_answer:" in _capture_source()


def test_the_capture_is_no_longer_write_once():
    """The old guard was `if not self.session.get("day_preference")`. If it
    comes back, this whole file is testing a rule the engine does not apply."""
    body = _capture_source()
    assert 'if not self.session.get("day_preference") and not _reason_answer:' \
        not in body


def test_the_rule_reads_the_shared_vocabulary():
    """Stated in `_SOONEST_DAY_PREFERENCES`, not a second list. Two lists drift,
    and `caller_wants_soonest` reads that one."""
    assert "_SOONEST_DAY_PREFERENCES" in _capture_source()
