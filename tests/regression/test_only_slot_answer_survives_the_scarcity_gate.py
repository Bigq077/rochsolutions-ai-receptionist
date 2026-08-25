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
    return [{
        "date": "2026-09-01",
        "day_label": "Tuesday 1st September",
        "slot_times": ["17:00"],
        "slot_times_spoken": ["five in the evening"],
    }]


def _one_day_three_slots():
    return [{
        "date": "2026-09-01",
        "day_label": "Tuesday 1st September",
        "slot_times": ["17:00", "17:45", "18:30"],
        "slot_times_spoken": ["five in the evening", "quarter to six", "half six"],
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


# ---------------------------------------------------------------------------
# Containment — the ban must still do its job
# ---------------------------------------------------------------------------
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
