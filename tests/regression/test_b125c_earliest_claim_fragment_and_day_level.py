r"""B-125c: the earliest-claim strip spoke a fragment, and deleted a true claim.

`CAe5c2f6e00d58` (2026-09-04 15:37, northgate demo line, build 9385a0d6) — the
call placed to verify the ASAP path. Both bad moments of that call trace to
this one guard, eight seconds apart.

    15:37:26  Susie: "Saturday the 5th is tomorrow - that's the soonest we
                      have. Would that work, or would you like me to look a
                      bit further ahead?"
              -> "Saturday the 5th is tomorrow. Would that work, or would you
                  like me to look a bit further ahead?"

Saturday the 5th WAS the soonest. The guard judged a DAY-level claim with
TIME-level evidence: it looked for the day's first spoken time inside a
sentence that names no time at all, so the claim could never be supported.
The sentence it kept destroying is the one that would have ended the call --
the caller said "not soon enough" three times, and she was left offering to
look FURTHER AHEAD to someone asking for the soonest.

    15:37:34  Susie: "The very tomorrow, Saturday the 5th."

The model wrote "the very earliest I have is tomorrow, Saturday the 5th". The
optional prefix was `[Tt]he\s+` alone, so "very" sat outside it, the match
began at `earliest`, and `earliest I have is ` was cut from the middle of the
phrase.

TWO CHANGES, AND THE SECOND IS THE MORE VALUABLE. Absorbing the intensifier
fixes the one word that reached a caller. The dangling-seam rule catches the
intensifier nobody has thought of yet -- this is the third instance in a week
of a strip whose remainder is not a sentence (B-140, `_ORPHAN_LEAD`'s original
six in August, this).
"""
from __future__ import annotations

import pytest

from app.media_streams.turn_handler import (
    _claim_strip_would_fragment,
    _day_is_named_in,
    _earliest_claim_is_supported,
    _names_an_earliest_claim,
    _strip_earliest_claim,
    sanitise_response,
)

# The two days on the payload for that call. Saturday is the earliest, and it
# holds a bookable 09:00 that the caller was never read.
SATURDAY = {
    "date": "2026-09-05",
    "day_label": "Saturday 5th September",
    "slot_times": ["09:00", "09:50", "11:00"],
    "slot_times_spoken": [
        "nine in the morning",
        "ten to ten in the morning",
        "eleven in the morning",
    ],
}
MONDAY = {
    "date": "2026-09-07",
    "day_label": "Monday 7th September",
    "slot_times": ["10:00"],
    "slot_times_spoken": ["ten in the morning"],
}


def _session(**over):
    s = {"clinic_id": "northgate", "available_days": [SATURDAY, MONDAY]}
    s.update(over)
    return s


# -- The fragment -----------------------------------------------------------

LIVE_FRAGMENT_SOURCE = "The very earliest I have is tomorrow, Saturday the 5th."


def test_the_intensifier_is_absorbed_not_stranded():
    """The exact sentence, through the exact gate the caller heard."""
    out = sanitise_response(LIVE_FRAGMENT_SOURCE, _session())
    assert "the very tomorrow" not in out.lower(), (
        f"a fragment reached the caller: {out!r}"
    )


@pytest.mark.parametrize("intensifier", ["very", "absolute", "absolutely"])
def test_the_strip_leaves_a_whole_sentence_in_both_frames(intensifier):
    """Whatever the guard decides, what it leaves must be a sentence.

    Judged on a payload where the claim is FALSE, so the strip actually runs:
    Monday is not the earliest day.
    """
    lead = f"The {intensifier} earliest I have is Monday 7th September."
    post = f"Monday 7th September - that's the {intensifier} soonest I've got."
    for claim in (lead, post):
        assert _names_an_earliest_claim(claim), claim
        assert not _earliest_claim_is_supported(claim, _session()), claim
        out = sanitise_response(claim, _session())
        assert "earliest" not in out.lower(), out
        assert "soonest" not in out.lower(), out
        assert not out.lower().startswith(("the very", "the absolute")), out


def test_very_first_still_matches_with_the_intensifier_group_present():
    """`very first` sits in the value alternation of both patterns. The new
    optional group must backtrack rather than swallow the word and strand the
    rest -- adding the group must not un-catch what was already caught."""
    claim = "Monday 7th September - and that would be the very first."
    assert _names_an_earliest_claim(claim)
    assert "very first" not in sanitise_response(claim, _session()).lower()


# -- The seam rule: the intensifier nobody has thought of --------------------

def test_an_unknown_intensifier_drops_the_sentence_rather_than_speak_it():
    """The point of the rule. "really" is in no alternation and never will be;
    the remainder is judged instead of the vocabulary."""
    claim = "The really earliest I have is Monday 7th September."
    assert _claim_strip_would_fragment(claim)
    out = _strip_earliest_claim(claim)
    assert "the really" not in out.lower(), out


def test_the_seam_rule_drops_only_the_offending_sentence():
    """A turn is not one sentence. Dropping the fragment must not take the
    slot readout with it -- that is the trade this guard exists to refuse."""
    text = (
        "The really earliest I have is Monday. "
        "Number 1, eight in the morning."
    )
    out = _strip_earliest_claim(text)
    assert "Number 1, eight in the morning." in out, out
    assert "really" not in out.lower(), out


def test_a_claim_that_starts_its_sentence_is_not_treated_as_a_fragment():
    """Over-firing here deletes a whole sentence, so the rule must want
    something genuinely dangling in front of the cut."""
    for claim in (
        "The earliest I have is Monday 7th September.",
        "The very earliest I have is Monday 7th September.",
        "Monday 7th September - that's the soonest I've got.",
    ):
        assert not _claim_strip_would_fragment(claim), claim


def test_the_whole_text_emptying_leaves_the_original_standing():
    """`sanitise_response` keeps the original when the strip empties the text.
    A false ranking is the smaller fault than silence -- the same call the
    opener strip makes. Pinned so the seam rule cannot quietly mute a turn."""
    claim = "The really earliest I have is Monday 7th September."
    assert _strip_earliest_claim(claim) == ""
    assert sanitise_response(claim, _session()) == claim


# -- The day-level claim ----------------------------------------------------

LIVE_TRUE_DAY_CLAIM = (
    "Saturday the 5th is tomorrow - that's the soonest we have. "
    "Would that work, or would you like me to look a bit further ahead?"
)


def test_a_true_day_level_claim_survives():
    """The sentence the guard kept destroying. It names no time, so there is
    no time for it to be wrong about."""
    assert _earliest_claim_is_supported(LIVE_TRUE_DAY_CLAIM, _session())
    assert sanitise_response(LIVE_TRUE_DAY_CLAIM, _session()) == LIVE_TRUE_DAY_CLAIM


@pytest.mark.parametrize("claim", [
    "The soonest I have is Monday the 7th.",              # leading frame
    "Monday the 7th - that's the soonest we have.",       # trailing frame
])
def test_a_false_day_level_claim_is_still_stripped(claim):
    """Conditional, not a licence. Monday is not the soonest while Saturday
    is on the payload. Both frames, because checking one and forgetting the
    other is the whole of B-125b."""
    assert not _earliest_claim_is_supported(claim, _session())
    assert "soonest" not in sanitise_response(claim, _session()).lower()


def test_the_copula_first_frame_is_caught():
    """A SHAPE IS NOT A FAMILY -- the lesson of B-125b, one frame later.

    Both original patterns put the superlative before its copula ("the soonest
    ... IS Monday") or behind a pronoun ("that's the soonest"). A bare subject
    with the copula first is neither, and it makes exactly the same assertion.

    Left OPEN as a strict xfail on the evening of 4 Sep, on the reasoning that
    widening a strip guard wanted a call first. It reached a live caller four
    hours later. Closed as B-125d; the frame's own file is
    test_b142_soonest_means_earliest.py.
    """
    claim = "Monday the 7th is the soonest we have."
    assert _names_an_earliest_claim(claim)
    assert not _earliest_claim_is_supported(claim, _session())


def test_a_day_level_claim_naming_no_day_still_fails_closed():
    """Unverifiable is not the same as true. Same asymmetry as the siblings."""
    claim = "That's the soonest we have."
    assert not _earliest_claim_is_supported(claim, _session())


def test_a_time_level_claim_on_the_earliest_day_is_still_judged_on_the_time():
    """The day being right does not make the time right. 09:50 is on the
    earliest day and is not that day's earliest."""
    claim = "The earliest I have is Saturday the 5th at ten to ten in the morning."
    assert not _earliest_claim_is_supported(claim, _session())
    claim_true = "The earliest I have is Saturday the 5th, nine in the morning."
    assert _earliest_claim_is_supported(claim_true, _session())


def test_a_true_time_on_a_LATER_day_is_not_the_earliest():
    """Monday's ten in the morning is Monday's first slot and is not the
    soonest anything. Judged against the payload, not against one day."""
    claim = "The earliest I have is Monday 7th September, ten in the morning."
    assert not _earliest_claim_is_supported(claim, _session())


# -- Naming a day the way the model actually names it -----------------------

@pytest.mark.parametrize("phrasing", [
    "Saturday 5th September",
    "Saturday the 5th",
    "saturday the 5th of september",
    "Saturday, 5 September",
])
def test_the_day_is_identified_however_it_is_worded(phrasing):
    """`day_label` containment alone missed "Saturday the 5th" and the claim
    became uncheckable -- which is how a true sentence was deleted."""
    assert _day_is_named_in(SATURDAY, phrasing.lower()), phrasing
    assert not _day_is_named_in(MONDAY, phrasing.lower()), phrasing


@pytest.mark.parametrize("not_this_day", [
    "the earliest is five past nine",   # a number inside a TIME, not a date
    "saturday",                          # weekday alone repeats every 7 days
    "the 5th",                           # a number alone names no weekday
])
def test_a_day_needs_both_signals(not_this_day):
    """Weekday alone repeats every seven days; a bare number appears inside
    every clock time on the payload. Either on its own names the wrong day."""
    assert not _day_is_named_in(SATURDAY, not_this_day)
