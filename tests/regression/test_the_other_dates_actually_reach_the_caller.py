"""
Regression: the payload named three Tuesdays and the caller heard none of them.

B-111. CA811ddccb03a09f081089da19a7e15029 (Theorem, 28 Aug 2026, build
e28c55aa556c, Alcester).

    09:32:56  caller: "yeah have you got anything on a tuesday"
    09:33:03  "The available slot for Tuesday 1st September is nine in
               the morning. Does that work?"
    09:33:10  caller: "do you have any other slots on that day"
    09:33:15  "That's the only slot we have on Tuesday 1st September."
    09:33:53  outcome=abandoned

Replaying that exact call against the deployed build shows the payload was
RIGHT. B-109 worked:

    days_found_in_window          4
    days_not_shown                3
    other_dates_for_requested_day Tuesday 8th (7), 15th (8), 22nd (9)
    guidance                      "...NAME those dates using their 'spoken'
                                   field and offer them..."

The caller was never told. The formatter is a second model (Haiku +
SLOT_FORMATTER_SYSTEM_PROMPT) and that prompt enumerates its inputs -
available_days, day_label, slot_times, presentation_mode - never mentions this
field, and for single_day says to use ONLY first_day. B-109 and B-110 wrote to
a consumer that is instructed not to look.

WHY THIS IS NOT FIXED BY TEACHING THE FORMATTER
-----------------------------------------------
That was the first plan and it is the wrong shape. This repo already ran the
experiment: 8de7e7d0 REMOVED the "a few others that day" example from that
same prompt because the model copied it onto a day with no further times and
invented availability. The prompt now says the model must never mention
further availability "in any wording", and that "the system adds that sentence
itself when more_times is true".

So this sentence is built the same way the more_times tail is: deterministically,
from the tool result, in _flush_slot_buf, immediately after
reconcile_extra_slots_claim. A model that cannot say it cannot invent it.

No TIMES are spoken for these dates. The payload deliberately carries none -
handing over a time for a date the caller never heard is the B-108b defect.
"""
from __future__ import annotations

import inspect

from app.media_streams import llm_stream
from app.media_streams.turn_handler import _BANNED_SENTENCE_RE
from app.tools.slot_followup import append_other_dates_offer

# Verbatim from the replayed payload of the live call.
_LIVE = [
    {"date": "2026-09-08", "spoken": "Tuesday 8th September", "times_available": 7},
    {"date": "2026-09-15", "spoken": "Tuesday 15th September", "times_available": 8},
    {"date": "2026-09-22", "spoken": "Tuesday 22nd September", "times_available": 9},
]
# What Susie actually said on the call.
_SAID = ("The available slot for Tuesday 1st September is nine in the morning. "
         "Does that work?")


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
def test_the_caller_is_told_the_other_tuesdays_exist():
    out, action = append_other_dates_offer(_SAID, _LIVE)
    assert action == "appended"
    for d in ("the 8th", "the 15th", "the 22nd"):
        assert d in out, f"{d} was in the payload and never reached the caller"


def test_the_day_that_was_read_out_is_left_alone():
    """This adds an offer. It must not touch the times already spoken."""
    out, _ = append_other_dates_offer(_SAID, _LIVE)
    assert out.startswith(_SAID)


def test_no_time_is_ever_spoken_for_those_dates():
    """The payload carries no times for them on purpose (B-108b). Naming one
    would be a slot the caller could accept that nobody has checked."""
    tail = append_other_dates_offer(_SAID, _LIVE)[0][len(_SAID):]
    for t in ("nine", "ten", "eleven", "morning", "afternoon", "evening",
              "o'clock", ":00", "7", "8 times", "9 times"):
        assert t not in tail.lower(), f"the tail spoke a time: {t!r} in {tail!r}"


def test_it_never_says_how_many_times_each_date_holds():
    """times_available is in the payload for the model's judgement, not for
    speech. "seven times on the 8th" is a promise about slots nobody checked."""
    tail = append_other_dates_offer(_SAID, _LIVE)[0][len(_SAID):]
    assert "7" not in tail and "seven" not in tail.lower()


# ---------------------------------------------------------------------------
# The sentence itself
# ---------------------------------------------------------------------------
def test_the_weekday_is_named_once_not_three_times():
    out, _ = append_other_dates_offer(_SAID, _LIVE)
    assert out.count("Tuesdays") == 1
    assert "Tuesday 8th September, Tuesday 15th September" not in out


def test_two_dates_and_one_date_read_naturally():
    two = append_other_dates_offer(_SAID, _LIVE[:2])[0]
    assert "the 8th and the 15th" in two and "either would suit" in two
    one = append_other_dates_offer(_SAID, _LIVE[:1])[0]
    assert "another Tuesday, the 8th" in one


def test_dates_on_different_weekdays_fall_back_to_full_labels():
    mixed = [
        {"date": "2026-09-08", "spoken": "Tuesday 8th September"},
        {"date": "2026-09-10", "spoken": "Thursday 10th September"},
    ]
    out, action = append_other_dates_offer(_SAID, mixed)
    assert action == "appended"
    assert "Tuesday 8th September and Thursday 10th September" in out


def test_the_tail_carries_no_tts_pause_punctuation():
    """An em dash or ellipsis is chunker input and would split the sentence
    across two synthesis calls."""
    tail = append_other_dates_offer(_SAID, _LIVE)[0][len(_SAID):]
    assert "—" not in tail and "…" not in tail


def test_the_tail_is_not_deleted_by_a_write_guard():
    """Checked against the real table: a sentence the guards strip is worse
    than no sentence, because it looks fixed in the payload and is silent on
    the phone."""
    out = append_other_dates_offer(_SAID, _LIVE)[0]
    hits = [desc for desc, pat in _BANNED_SENTENCE_RE if pat.search(out)]
    assert not hits, f"the offer would be deleted by: {hits}"


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_nothing_is_added_when_there_are_no_other_dates():
    for empty in (None, [], "", 0):
        assert append_other_dates_offer(_SAID, empty) == (_SAID, "unchanged")


def test_a_date_the_reply_already_named_is_not_repeated():
    said = _SAID + " I've also got Tuesday 8th September."
    assert append_other_dates_offer(said, _LIVE)[1] == "unchanged"


def test_an_empty_reply_is_left_alone():
    assert append_other_dates_offer("", _LIVE) == ("", "unchanged")


def test_malformed_entries_are_skipped_not_spoken():
    assert append_other_dates_offer(_SAID, [{"date": "2026-09-08"}])[1] == "unchanged"
    assert append_other_dates_offer(_SAID, ["nonsense", 7])[1] == "unchanged"


# ---------------------------------------------------------------------------
# The wiring. The payload was right for a day and never reached speech.
# ---------------------------------------------------------------------------
def test_the_field_is_captured_from_the_tool_result():
    src = inspect.getsource(llm_stream)
    assert 'session["_slot_other_dates"] = (' in src
    assert 'result.get("other_dates_for_requested_day")' in src


def test_the_flush_actually_calls_it():
    """B-109 shipped a correct payload with no consumer. This is the assertion
    that would have caught that."""
    src = inspect.getsource(llm_stream)
    assert "append_other_dates_offer(" in src, (
        "the other-dates offer is built but never appended to what is spoken"
    )


def test_it_runs_after_the_more_times_reconcile():
    """Appending before the reconciler would let reconcile_extra_slots_claim
    treat this sentence as an unfounded extra-availability claim and strip it."""
    src = inspect.getsource(llm_stream)
    assert src.find("reconcile_extra_slots_claim(") < src.find("append_other_dates_offer(")
