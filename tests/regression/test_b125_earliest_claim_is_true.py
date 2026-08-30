r"""B-125: "the earliest I have is" named a time that was not the earliest.

`CA7182593819eac0a8e87a22928f137eb7` (2026-08-30 07:32, northgate demo line,
the call that verified the four findings of 2026-08-30).

    07:31:58  tool -> Tuesday 1st September
                      slot_times ["08:00","09:05","11:15","12:20",
                                  "13:25","14:30","15:35","16:40"]
    07:31:59  Susie: "Tuesday 1st September — Number 1, eight in the morning.
                      Number 2, quarter past eleven. Number 3, twenty past twelve."
    07:32:15  caller: "uh actually what's the soonest that you've got"
    07:32:18  [ms_llm] check_availability BLOCKED — slots already retrieved
    07:32:20  Susie: "The EARLIEST I have is Tuesday 1st September —
                      Number 1, five past nine in the morning. ..."

Eight in the morning was bookable, on that day, and had been read out twenty
seconds earlier. The caller asked the most direct question available and was
told 9:05.

WHAT IS AND IS NOT WRONG HERE. 09:05 is a real slot and offering the day's
unspoken remainder is sensible — that is the follow-up path doing its job. The
defect is the SUPERLATIVE attached to it. Same family as B-92/B-97: a ranking
or completeness claim the payload does not support, from a producer no
availability guard was watching.

The read-back guard saw it and correctly declined to act —

    [ms_gate5] read-back time NOT in the offer and not safely correctable

— because from its point of view this is a new offer, not a bad read-back. A
ranking claim needs its own predicate, which is what this file pins.

WHY THE CLAUSE AND NOT THE SENTENCE. `that_is_the_only` bans a whole sentence
because the sentence is nothing but the claim. Here the sentence carries the
slot readout, so banning it would leave the turn with no times in it — trading
a false ranking for silence, which is the worse of the two.
"""
from __future__ import annotations

import pytest

from app.media_streams.turn_handler import (
    _EARLIEST_CLAIM_POST_RE,
    _EARLIEST_CLAIM_RE,
    _earliest_claim_is_supported,
    _names_an_earliest_claim,
    sanitise_response,
)

# The payload from the call, verbatim. slot_times_spoken[0] is the day's
# earliest and is the exact wording the readout uses — which is what makes the
# comparison containment rather than time parsing.
TUESDAY = {
    "date": "2026-09-01",
    "day_label": "Tuesday 1st September",
    "slot_times_spoken": [
        "eight in the morning",
        "five past nine in the morning",
        "quarter past eleven in the morning",
        "twenty past twelve in the afternoon",
        "twenty-five past one in the afternoon",
        "half past two in the afternoon",
        "twenty-five to four in the afternoon",
        "twenty to five in the afternoon",
    ],
}

# The sentence that was spoken to the caller.
LIVE_CLAIM = (
    "The earliest I have is Tuesday 1st September — Number 1, five past nine "
    "in the morning. Number 2, twenty-five past one in the afternoon. "
    "Number 3, half past two in the afternoon."
)


def _session(**over):
    s = {"clinic_id": "northgate", "available_days": [TUESDAY]}
    s.update(over)
    return s


# ── The defect ─────────────────────────────────────────────────────────────

def test_the_live_sentence_loses_its_ranking():
    out = sanitise_response(LIVE_CLAIM, _session())
    assert "earliest" not in out.lower(), (
        f"a false ranking reached the caller: {out!r}"
    )


def test_the_times_survive_the_strip():
    """The whole point of stripping the clause rather than the sentence.

    Banning the sentence would take the slot readout with it and leave the turn
    with nothing — silence in place of a wrong adjective, which is a trade in
    the wrong direction.
    """
    out = sanitise_response(LIVE_CLAIM, _session())
    for kept in (
        "Tuesday 1st September",
        "five past nine in the morning",
        "twenty-five past one in the afternoon",
        "half past two in the afternoon",
    ):
        assert kept in out, f"{kept!r} was lost: {out!r}"
    assert out.startswith("Tuesday 1st September"), out


# ── A TRUE claim must survive ──────────────────────────────────────────────

def test_a_true_earliest_claim_is_left_alone():
    """The guard is conditional, not a ban. When the day's first slot IS the
    one being named, the sentence is correct and the caller is owed it."""
    true_claim = (
        "The earliest I have is Tuesday 1st September — Number 1, eight in "
        "the morning."
    )
    assert _earliest_claim_is_supported(true_claim, _session())
    assert sanitise_response(true_claim, _session()) == true_claim


def test_a_sentence_with_no_ranking_is_untouched():
    plain = "Tuesday 1st September — Number 1, five past nine in the morning."
    assert _earliest_claim_is_supported(plain, _session())
    assert sanitise_response(plain, _session()) == plain


# ── Fails CLOSED, like its two siblings ────────────────────────────────────

@pytest.mark.parametrize("session", [
    {"clinic_id": "northgate"},                                  # no payload
    {"clinic_id": "northgate", "available_days": []},            # empty
    {"clinic_id": "northgate", "available_days": "nonsense"},    # unreadable
    {"clinic_id": "northgate",
     "available_days": [dict(TUESDAY, slot_times_spoken=[])]},   # no times
])
def test_an_unverifiable_ranking_is_stripped(session):
    """`_scarcity_claim_is_supported` fails closed and says why: an unverifiable
    claim is exactly the case the ban exists for. Same asymmetry here — silence
    about a ranking costs the caller nothing, and a wrong ranking sent this
    caller past a slot that was free."""
    assert not _earliest_claim_is_supported(LIVE_CLAIM, session)
    assert "earliest" not in sanitise_response(LIVE_CLAIM, session).lower()


def test_a_claim_spanning_two_days_cannot_be_checked_and_is_stripped():
    """"That day" has to be one day. With two on the table the sentence cannot
    be judged against a single day's first slot, so it is not asserted."""
    wed = dict(TUESDAY, date="2026-09-02", day_label="Wednesday 2nd September")
    both = "The earliest I have is Tuesday 1st September or Wednesday 2nd September."
    assert not _earliest_claim_is_supported(both, _session(available_days=[TUESDAY, wed]))


# ── The wording it has to catch ────────────────────────────────────────────

@pytest.mark.parametrize("claim", [
    "The earliest I have is Tuesday 1st September at five past nine.",
    "The soonest I have is Tuesday 1st September at five past nine.",
    "The earliest I've got is Tuesday 1st September at five past nine.",
    "The first available is Tuesday 1st September at five past nine.",
    "The next available appointment is Tuesday 1st September at five past nine.",
    "The earliest slot I can do is Tuesday 1st September at five past nine.",
])
def test_the_family_of_phrasings_is_caught(claim):
    """The model does not say it one way twice. Six phrasings from the same
    family, all of which make the same assertion.

    A ranking claim must never depend on matching ONE literal of model speech —
    that family of defect has cost this codebase four fixes already. The guard
    is a shape (superlative + copula), and what makes it safe is that the thing
    it checks against is our own generated string, not the model's.
    """
    assert _EARLIEST_CLAIM_RE.search(claim), claim
    assert not _earliest_claim_is_supported(claim, _session())


@pytest.mark.parametrize("innocent", [
    "Your appointment is at eight in the morning.",
    "I can do first thing on Tuesday if that helps.",
    "That's the earliest appointment you've had with us.",
    "Tuesday 1st September — Number 1, eight in the morning.",
])
def test_it_does_not_fire_on_ordinary_speech(innocent):
    """Over-firing here deletes a clause from a correct sentence, so the pattern
    has to want a superlative AND a copula introducing a value."""
    assert sanitise_response(innocent, _session()) == innocent


# ═══ B-125b — the same claim, the other way round ═══════════════════════════
#
# `CA4fff84ae5013b517fda72b914d83e01c` (2026-08-30 10:11, build 42fb5f703b8e) —
# the call placed to VERIFY B-125. The guard fired correctly on the first ask:
#
#   10:11:31  [ms_gate5] removed an unsupported EARLIEST claim ...
#
# The caller asked again, and heard:
#
#   10:11:48  "Five past nine on Tuesday the 1st of September — that's the
#              earliest I've got. Does that work for you?"
#
# Straight through. Eight in the morning was still bookable and had been read
# out thirty seconds earlier.
#
# ── The lesson, and it is not the one already on the list ──────────────────
#
# `test_the_family_of_phrasings_is_caught` above pins six wordings and calls
# them a family. All six are superlative-then-value. It was written to honour
# "never match one literal of model speech" and it DID widen past the literal —
# it just never left the one syntactic frame, and six variants of a single
# shape read exactly like coverage.
#
#   A SHAPE IS NOT A FAMILY. Vary the STRUCTURE, not just the vocabulary.
#
# The trailing form is the more dangerous of the two, which makes missing it
# worse: a leading claim arrives in front of a numbered list the caller can
# weigh for themselves, while this one is a bare closing assertion with "Does
# that work for you?" attached.

LIVE_CLAIM_POST = (
    "Five past nine on Tuesday the 1st of September — that's the earliest "
    "I've got. Does that work for you?"
)


def test_the_second_live_sentence_loses_its_ranking():
    """The one that went through on the verification call."""
    out = sanitise_response(LIVE_CLAIM_POST, _session())
    assert "earliest" not in out.lower(), out
    assert out == (
        "Five past nine on Tuesday the 1st of September. Does that work for you?"
    ), out


def test_the_trailing_form_keeps_its_sentence_boundary():
    """Deleting a trailing claim outright welds the value onto what follows —
    "Five past nine on Tuesday Does that work for you?" — so it is replaced by
    a full stop rather than removed. The question after it must survive intact."""
    out = sanitise_response(LIVE_CLAIM_POST, _session())
    assert out.count(".") == 1, out
    assert out.endswith("Does that work for you?"), out


@pytest.mark.parametrize("claim", [
    # value first, claim after — the frame B-125 missed entirely
    "Five past nine on Tuesday 1st September — that's the earliest I've got.",
    "Five past nine on Tuesday 1st September, which is the soonest we have.",
    "Five past nine on Tuesday 1st September — that is the first available.",
    "Five past nine on Tuesday 1st September. That's the soonest.",
    "Five past nine on Tuesday 1st September — it's the earliest slot we can do.",
    "Five past nine on Tuesday 1st September, and that would be the very first.",
])
def test_the_trailing_frame_is_caught_too(claim):
    assert _EARLIEST_CLAIM_POST_RE.search(claim), claim
    assert _names_an_earliest_claim(claim), claim
    assert not _earliest_claim_is_supported(claim, _session())
    assert "earliest" not in sanitise_response(claim, _session()).lower()
    assert "soonest" not in sanitise_response(claim, _session()).lower()


def test_a_true_trailing_claim_survives():
    """Conditional in both frames, not just the leading one."""
    true_post = (
        "Eight in the morning on Tuesday 1st September — that's the earliest "
        "I've got. Does that work?"
    )
    assert _earliest_claim_is_supported(true_post, _session())
    assert sanitise_response(true_post, _session()) == true_post


def test_both_frames_go_through_one_predicate():
    """`_names_an_earliest_claim` is what stops the next reader checking one and
    forgetting the other — which is the whole of B-125b."""
    assert _names_an_earliest_claim(LIVE_CLAIM)
    assert _names_an_earliest_claim(LIVE_CLAIM_POST)
    assert not _names_an_earliest_claim(
        "Tuesday 1st September — Number 1, eight in the morning."
    )


@pytest.mark.parametrize("innocent", [
    "That's the earliest appointment you've had with us.",
    "Is that the earliest you can manage?",
    "Your appointment is at eight in the morning.",
])
def test_the_trailing_pattern_does_not_eat_ordinary_speech(innocent):
    """It fires on a ranking of what the CLINIC has. A caller's own sentence and
    a question about the past are neither."""
    out = sanitise_response(innocent, _session())
    assert out == innocent, out
