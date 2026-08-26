"""
Regression: the caller asked four times whether there was anything else, and
the answer was deleted every time.

CA45357d84, 25 Aug 2026, jv_v1 (Marcus's line). Tuesday 1 September had exactly
one slot, 17:00. Over 40 seconds:

    21:17:46  caller: "um do you have any other slots on that day"
              [ms_gate5] removed banned phrase (that_is_the_only)
              Susie:  "Would five in the evening work for you?"
    21:17:53  caller: "do you have any other slots on that day"
              [ms_gate5] removed banned phrase (that_is_the_only)
              Susie:  "Does that work for you?"
    21:18:12  caller: "i asked if you have any other slots on that tuesday"
              [ms_gate5] removed banned phrase (that_is_the_only)
              Susie:  "Does that work for you?"

Each time the model produced the truthful reply and Gate 5b deleted it, leaving
Susie re-offering the same time as a question — which reads as not listening.
The call was abandoned at 168s with no booking.

`that_is_the_only` bans scarcity framing. The ban is right when the claim is
unsupported. It is wrong when the claim is TRUE and the caller has asked for
alternatives, because then the banned sentence is the answer.

The discriminator is the DATA, not the caller's wording and not another literal
of Susie's speech: `available_days` is what supports or refutes the claim. It is
deliberately conservative — one slot, one day, nothing else on the table.

See also `the_only_slot_scarcity` immediately below it in `_BANNED_SENTENCE_RE`,
which was converted from a full-sentence strip to a prefix strip after the same
class of bug ("The only day available is Wednesday the 17th" → stripped to
nothing → fallback fired).
"""
from __future__ import annotations

import pytest

from app.media_streams.turn_handler import (
    _BANNED_SENTENCE_RE,
    _scarcity_claim_is_supported,
    sanitise_response,
)


def _session(days=None, **extra):
    s = {"clinic_id": "jv_v1"}
    if days is not None:
        s["available_days"] = days
    s.update(extra)
    return s


def _one_day_one_slot():
    """A day that really does hold one slot.

    times_found_on_day is what makes that TRUE rather than merely apparent.
    B-97 (CA6fa4b433) showed slot_times is the set left after the caller's
    time-of-day preference, so a one-entry slot_times can be one slot out of
    four. The gate now needs the day to say so, and fails closed without it.
    """
    return [{
        "date": "2026-09-01",
        "day_label": "Tuesday 1st September",
        "slot_times": ["17:00"],
        "slot_times_spoken": ["five in the evening"],
        "times_found_on_day": 1,
        "times_not_shown": 0,
    }]


def _one_day_three_slots():
    return [{
        "date": "2026-09-01",
        "day_label": "Tuesday 1st September",
        "slot_times": ["17:00", "17:45", "18:30"],
        "slot_times_spoken": ["five in the evening", "quarter to six", "half six"],
        "times_found_on_day": 3,
        "times_not_shown": 0,
    }]


def _two_days():
    return [
        {"date": "2026-09-01", "day_label": "Tuesday 1st September",
         "slot_times": ["17:00"]},
        {"date": "2026-09-03", "day_label": "Thursday 3rd September",
         "slot_times": ["19:30"]},
    ]


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("reply", [
    "That's the only slot I have on that day.",
    "That's the only one, I'm afraid.",
    "That is the only time available on Tuesday.",
    "That's the only slot available — five in the evening.",
])
def test_a_true_scarcity_answer_survives(reply):
    """The failing case. Red before the fix: every one of these was deleted."""
    out = sanitise_response(reply, _session(_one_day_one_slot()))
    assert out.strip(), (
        f"the answer to 'is there anything else?' was deleted: {reply!r} → {out!r}"
    )
    assert "only" in out.lower()


def test_the_answer_is_not_reduced_to_a_fragment():
    """A prefix strip was the other candidate fix and would have produced
    'one, I'm afraid.' here — worse than the deletion, because it reaches the
    caller as speech."""
    reply = "That's the only one, I'm afraid."
    out = sanitise_response(reply, _session(_one_day_one_slot()))
    assert out.strip() == reply


def test_surrounding_sentences_are_untouched():
    reply = (
        "That's the only slot on that day. "
        "Would five in the evening work for you?"
    )
    out = sanitise_response(reply, _session(_one_day_one_slot()))
    assert "only slot on that day" in out
    assert "Would five in the evening work for you?" in out


def _one_shown_of_four():
    """Friday 4 September on CA6fa4b433: four bookable slots, one afternoon,
    and a caller who had asked for afternoons."""
    return [{
        "date": "2026-09-04",
        "day_label": "Friday 4th September",
        "slot_times": ["13:00"],
        "slot_times_spoken": ["one in the afternoon"],
        "times_found_on_day": 4,
        "times_not_shown": 3,
    }]


# ---------------------------------------------------------------------------
# Containment — the ban must still do its job
# ---------------------------------------------------------------------------
def test_a_day_the_caller_has_only_partly_seen_cannot_support_the_claim():
    """B-97. One entry in slot_times is not one slot on the day. Counting the
    survivors of a time-of-day filter let this gate approve "that's the only
    one we have that day" about a day holding four, to a caller who had just
    said the offered time did not suit."""
    out = sanitise_response(
        "That's the only slot I have on that day.",
        _session(_one_shown_of_four()),
    )
    assert "only" not in out.lower()


def test_a_day_that_cannot_say_how_many_slots_it_has_fails_closed():
    """The pre-B-97 payload shape. Unverifiable is exactly what the ban is
    for, so the sentence goes."""
    out = sanitise_response(
        "That's the only slot I have on that day.",
        _session([{"date": "2026-09-01", "day_label": "Tuesday 1st September",
                   "slot_times": ["17:00"]}]),
    )
    assert "only" not in out.lower()

@pytest.mark.parametrize("days,why", [
    (_one_day_three_slots(), "three slots on the day — the claim is false"),
    (_two_days(),            "another day is still on the table"),
    ([],                     "nothing offered — nothing supports the claim"),
    (None,                   "no availability in session at all"),
])
def test_an_unsupported_scarcity_claim_is_still_stripped(days, why):
    """This is invented pressure, which is what the pattern exists to remove."""
    out = sanitise_response("That's the only slot I have.", _session(days))
    assert "only" not in out.lower(), why


@pytest.mark.parametrize("days", [
    "not a list",
    [{"slot_times": "17:00"}],          # times not a list
    [{"day_label": "Tuesday"}],         # no slot_times at all
    [None],
])
def test_a_malformed_session_fails_closed(days):
    """Fail CLOSED — an unreadable session must strip, which is today's
    behaviour. Failing open would let an invented scarcity claim reach a
    caller on exactly the calls where state is already suspect."""
    assert _scarcity_claim_is_supported(_session(days)) is False
    out = sanitise_response("That's the only slot I have.", _session(days))
    assert "only" not in out.lower()


def test_the_predicate_never_raises():
    """Runs per chunk on a live call."""
    for bad in [{}, {"available_days": 7}, {"available_days": [{"slot_times": None}]}]:
        assert _scarcity_claim_is_supported(bad) is False


# ---------------------------------------------------------------------------
# The exemption must not leak to the rest of the table
# ---------------------------------------------------------------------------
def test_other_banned_phrases_are_unaffected_by_a_single_slot():
    """Only `that_is_the_only` is conditional. A one-slot day must not become a
    general amnesty for the other ~40 patterns."""
    session = _session(_one_day_one_slot())
    for reply, gone in [
        ("Bear with me while I check.",            "bear with me"),
        ("Just a moment please.",                  "just a moment"),
        ("Are you still there?",                   "still there"),
        ("Is there anything else I can help with?", "anything else"),
    ]:
        out = sanitise_response(reply, session)
        assert gone not in out.lower(), f"{reply!r} survived as {out!r}"


def test_the_pattern_is_still_in_the_table():
    """Several call sites and tests check proposed wording against
    _BANNED_SENTENCE_RE. Lifting the pattern out of the table to make it
    conditional would hide it from all of them."""
    assert any(desc == "that_is_the_only" for desc, _ in _BANNED_SENTENCE_RE)



# ---------------------------------------------------------------------------
# The log line has to be usable as evidence
# ---------------------------------------------------------------------------
def test_the_keep_is_logged_only_when_something_was_kept(caplog):
    """It fired nine times in CAdf057714, including on "All booked - you're in
    for Friday the 28th" - a turn with no scarcity sentence in it.

    The logger.info sat BEFORE pattern.sub, so it announced a keep whenever the
    session state merely permitted one. Behaviour was never wrong, but the line
    could not be used to verify the fix had fired, which is its only purpose.
    """
    import logging

    from app.media_streams.turn_handler import sanitise_response

    # One day, one slot — the claim is supported, so the gate is in keep mode.
    session = {"available_days": [{"date": "2026-09-01",
                                   "slot_times": ["17:00"],
                                   "times_found_on_day": 1}]}

    with caplog.at_level(logging.INFO):
        sanitise_response("All booked, you're in for Friday the 28th.", session)
    assert not [r for r in caplog.records if "kept scarcity sentence" in r.message], (
        "announced a keep on a turn with no scarcity sentence"
    )

    caplog.clear()
    with caplog.at_level(logging.INFO):
        out = sanitise_response(
            "That's the only slot on Tuesday 1st September.", session,
        )
    assert "only slot" in out, "the sentence itself must still survive"
    assert [r for r in caplog.records if "kept scarcity sentence" in r.message], (
        "kept the sentence but said nothing — the fix is unverifiable from a log"
    )
