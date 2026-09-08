"""The guard against a re-read was the thing causing one.

`CA4215ab7f6c28a49f89a8f87ce781edd4`, theorem_v3, 2026-09-08 01:15, build
`08e99fab`. Reported as horrible behaviour, and it is:

    01:15:18  offer: Wednesday 9th September — Number 1, nine in the morning.
                     Number 2, ten in the morning. Number 3, three in the
                     afternoon.
    01:15:32  caller: "yeah three works"
              caller ACCEPTED 2026-09-09T15:00:00+01:00        <- correct

    01:15:39  [ms_gate5] deterministic offer STOOD DOWN — the model names the
              slot the caller just accepted, so it is confirming a pick, not
              presenting a list. Speaking the model instead (P6b).
              model="here's what we've got coming up — Number 1, Friday 11th
                     September — nine in the morning or two in the afternoon.
                     Number 2, Monday 14th ... Number 3, Tuesday 15th ..."

    01:15:56  tts_finished in 16.1s
    01:16:01  caller: "let me if the last one works for me"
    01:16:05  "no rush at all — take your time."
    01:16:10  caller hung up.   outcome=abandoned  score=2

Sixteen seconds of three days the caller had not asked for, immediately after
picking one — and then "the last one" resolved to the OLD pinned slot rather
than the last of what had just been read.

── THE MECHANISM ─────────────────────────────────────────────────────────────
`accepted_slot_is_named_in` falls back to the BARE label when the full one does
not match, and the bare label of an o'clock time is just a number.
`_time_norm` folds it to a digit:

    "three in the afternoon"  ->  "three"  ->  "3"

Every numbered read-out contains "Number 3", which normalises to "number 3",
and `\\b3\\b` matches it. So accepting a THREE o'clock slot made this function
true of **any** list with a third option.

B-114 settled this exact rule for the caller-facing resolver a fortnight
earlier — "a core that is a bare number word has to be USED as a time" — and
this function never adopted it. `_time_named_in`'s own docstring describes the
same class as B-126: "Plain containment then matched '9' inside 'Wednesday the
9th of September'".

Two guards, both already in this module, neither of which was being used here:

  * `_names_a_different_weekday` (B-138) — text naming only OTHER weekdays is a
    fresh list whatever its times are;
  * `_bare_hour_word_is_a_clock_reference` (B-114) — a bare number is a time
    only when something nearby says so.
"""

import pytest

from app.tools.receptionist_tools import _spoken_slot_time
from app.tools.slot_followup import ACCEPTED_SLOT_KEY, accepted_slot_is_named_in

_DAY = "2026-09-09"                       # a WEDNESDAY
_ACCEPTED = "%sT15:00:00+01:00" % _DAY    # "three in the afternoon"
_TIMES = ["09:00", "10:00", "15:00"]

#: What the model actually said on the call, verbatim from the Gate 5 log.
LIVE_MODEL_LIST = (
    "here's what we've got coming up — Number 1, Friday 11th September — "
    "nine in the morning or two in the afternoon. Number 2, Monday 14th "
    "September — nine in the morning or six in the evening. Number 3, "
    "Tuesday 15th September — nine in the morning or four in the afternoon. "
    "Any of those suit you?"
)


def _session(accepted=_ACCEPTED):
    day = {
        "date": _DAY, "day_label": "Wednesday 9th September",
        "slot_times": list(_TIMES),
        "slot_times_spoken": [_spoken_slot_time(t) for t in _TIMES],
        "slots": [{"start": "%sT%s:00+01:00" % (_DAY, t), "date": _DAY,
                   "day_label": "Wednesday 9th September", "time": t,
                   "spoken": _spoken_slot_time(t)} for t in _TIMES],
    }
    return {ACCEPTED_SLOT_KEY: accepted, "available_days": [day]}


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_the_live_call_a_new_three_day_list_is_not_a_confirmation():
    assert accepted_slot_is_named_in(_session(), LIVE_MODEL_LIST) is False, (
        "Gate 5 stands down on this and speaks sixteen seconds of days the "
        "caller did not ask for, right after they picked one"
    )


@pytest.mark.parametrize("n,other_day", [
    (1, "Friday 11th September"),
    (2, "Monday 14th September"),
    (3, "Tuesday 15th September"),
])
def test_no_option_number_can_stand_in_for_the_accepted_time(n, other_day):
    """The accepted slot is THREE o'clock. "Number 3" must not name it, and
    neither must any other option number."""
    text = ("here's what we've got coming up — Number %d, %s — nine in the "
            "morning or four in the afternoon. Any of those suit you?"
            % (n, other_day))
    assert accepted_slot_is_named_in(_session(), text) is False, text


@pytest.mark.parametrize("hhmm,digit", [
    ("13:00", 1), ("14:00", 2), ("15:00", 3),
    ("09:00", 9), ("10:00", 10), ("11:00", 11),
])
def test_the_whole_family_not_only_three(hhmm, digit):
    """Any accepted o'clock slot folds to a bare digit, so this was never
    specific to three. A list carrying that digit as an option number, a date
    or a day-of-month must not read as a confirmation."""
    sess = _session(accepted="%sT%s:00+01:00" % (_DAY, hhmm))
    text = ("here's what we've got coming up — Number %d, Friday %dth "
            "September — eight in the morning. Any of those suit you?"
            % (min(digit, 3), digit))
    assert accepted_slot_is_named_in(sess, text) is False, text


def test_a_different_weekday_is_never_a_confirmation():
    """Even with the RIGHT time, another day is another slot. The accepted slot
    is a Wednesday."""
    text = "On Friday 11th September I have three in the afternoon."
    assert accepted_slot_is_named_in(_session(), text) is False, text


# ---------------------------------------------------------------------------
# What must NOT change — P6b still has to work
# ---------------------------------------------------------------------------

def test_a_plain_confirmation_still_stands_the_offer_down():
    assert accepted_slot_is_named_in(
        _session(), "So that's Wednesday 9th September at three in the afternoon."
    ) is True


def test_the_numbered_confirmation_p6b_exists_for_still_works():
    """CA5a126fe4e6addcf812836220cdf7ea44 — the model's recovery WAS numbered
    and named the accepted slot. P6b was written for exactly this, and it must
    survive."""
    assert accepted_slot_is_named_in(
        _session(), "Wednesday 9th September — Number 1, three in the afternoon."
    ) is True


def test_a_confirmation_naming_no_day_still_works():
    """`_names_a_different_weekday` refuses only when a DIFFERENT weekday is
    named. Naming none is not naming another."""
    assert accepted_slot_is_named_in(
        _session(), "That's three in the afternoon then."
    ) is True


def test_a_bare_hour_used_as_a_time_still_confirms():
    """The bare fallback is not removed, only made to ask B-114's question. "at
    three" is a clock reference; "Number 3" is not."""
    assert accepted_slot_is_named_in(
        _session(), "Wednesday the 9th at three, then."
    ) is True


@pytest.mark.parametrize("junk", [None, "", "   ", 0, []])
def test_it_never_raises(junk):
    assert accepted_slot_is_named_in(_session(), junk) is False
    assert accepted_slot_is_named_in(junk, "anything") is False


# ---------------------------------------------------------------------------
# The bare-hour guard on its own
#
# The two guards must each earn their place. Everything above names OTHER
# weekdays, so `_names_a_different_weekday` alone refuses it and the bare-hour
# rule is never reached — neutering the bare-hour guard left all of it green,
# which is the B-134 failure ("tests that stayed green when the fix was
# neutered, because they exercised the helper and not the call site").
#
# These name NO other weekday, so only the bare-hour rule can refuse them.
# ---------------------------------------------------------------------------

def test_an_option_number_alone_is_not_the_accepted_time():
    """No weekday anywhere, so the B-138 guard cannot help. The accepted slot
    is THREE o'clock and the text's only "3" is an option number."""
    text = ("Number 1, nine in the morning. Number 2, ten in the morning. "
            "Number 3, four in the afternoon. Any of those work?")
    assert accepted_slot_is_named_in(_session(), text) is False, text


def test_a_re_read_of_the_SAME_day_is_still_not_a_confirmation():
    """The most dangerous shape: the right weekday, so B-138 passes it through,
    and a third option whose number folds onto the accepted hour."""
    text = ("Wednesday 9th September — Number 3, four in the afternoon. "
            "Any of those work?")
    assert accepted_slot_is_named_in(_session(), text) is False, text


@pytest.mark.parametrize("stray", [
    "I've got 3 others that day.",
    "There are 3 times left.",
    "That's 3 appointments this week.",
])
def test_a_stray_number_three_is_not_the_accepted_time(stray):
    """Any bare "3" in the sentence used to name a three o'clock slot."""
    assert accepted_slot_is_named_in(_session(), stray) is False, stray


# ---------------------------------------------------------------------------
# A clinic whose labels are ALREADY bare
#
# `speak_part_of_day: false` (northgate, 7 Sep) makes every o'clock label a
# bare number: "three", not "three in the afternoon". For those clinics the
# FULL label is the bare number, so it is matched by the first comparison and
# never reaches the bare fallback.
#
# The first version of this fix guarded only the fallback, which left exactly
# those clinics exposed -- and they are the ones the wording change was made
# for. Caught by asking "is this bug on latency-eval too?", which it was, and
# worse there.
# ---------------------------------------------------------------------------

def _bandless_session():
    times = ["09:00", "10:00", "15:00"]
    day = {
        "date": _DAY, "day_label": "Wednesday 9th September",
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t, False) for t in times],
        "slots": [{"start": "%sT%s:00+01:00" % (_DAY, t), "date": _DAY,
                   "day_label": "Wednesday 9th September", "time": t,
                   "spoken": _spoken_slot_time(t, False)} for t in times],
    }
    return {ACCEPTED_SLOT_KEY: _ACCEPTED, "available_days": [day]}


def test_the_bandless_label_is_the_bare_number_itself():
    """The premise, asserted so the rest of this block cannot quietly stop
    testing what it says it tests."""
    slot = _bandless_session()["available_days"][0]["slots"][-1]
    assert slot["spoken"] == "three", slot


@pytest.mark.parametrize("text", [
    "Wednesday 9th September — Number 3, four. Any of those work?",
    "Number 1, nine. Number 2, ten. Number 3, four. Any of those work?",
    "I've got 3 others that day.",
])
def test_a_bandless_clinic_is_guarded_on_the_full_label_too(text):
    assert accepted_slot_is_named_in(_bandless_session(), text) is False, text


def test_a_bandless_confirmation_still_stands_the_offer_down():
    """The guard must not cost these clinics P6b. "at three" is a clock
    reference; "Number 3" is not."""
    assert accepted_slot_is_named_in(
        _bandless_session(), "So that's Wednesday 9th September at three."
    ) is True
