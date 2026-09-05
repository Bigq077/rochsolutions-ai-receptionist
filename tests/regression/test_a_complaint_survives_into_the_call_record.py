"""
Two ways a caller's complaint was lost between their mouth and the call record.

Both found on northgate, 5 Sep 2026, build 39be56121005.

B-141a — THE STEMMER ATE A LEGITIMATE 's'.

`_part_stem` was `word.rstrip(".,!?;:'s")`. `rstrip` takes a CHARACTER SET, not
a suffix, so it also removed a terminal 's' that belonged to the word. Exactly
two entries of `_BODY_PARTS` became unreachable:

    'achilles' -> 'achille'      'pelvis' -> 'pelvi'

Neither stem is in the set, so Pass 1 never anchored on them. CA60cb41a0: "my
achilles is stiff the first few minutes every morning" fell through to the
injury-verb pass, which anchors on 'stiff', and the record read

    reason: 'stiff the first few minutes every'

-- a complaint with no body part in it.

B-141b — BACK-PLUS-REFERRED-LIMB READ AS TWO COMPLAINTS.

`_extract_reason` fails open on two distinct complaints because picking the
first-mentioned is a coin toss. That guard is deliberate and is NOT changed
here: "my knee and my ankle are both sore" still captures nothing.

But back-pain-with-leg-numbness is not two complaints, it is lumbar
radiculopathy -- one locus, the leg being the referral. `_usable_body_parts`
already makes exactly this distinction for "the back of my legs", which is one
locus spelled with two part words. CA6b241e20, and CAcb51bc27 before it:

    caller: "yeah my lower back's been really bad and my leg's gone numb"
    ...
    [call_summary] pre-summary reason: collected=None session=None -> None

No reason at all, on a call whose first sentence was the reason. That empties
the Sheets row and the follow-up SMS, and starves `book_appointment`'s A2 gate,
which refuses a booking carrying no reason.

It also blinded the B-138 scheduling gate, which asks `_extract_reason` whether
this utterance is the caller's complaint: with no reason extracted, a time word
in the same sentence was banked as a hard filter again.
"""

import pytest

from app.media_streams.connection import (
    _extract_time_preference,
    _time_preference_tier,
)
from app.media_streams.first_turn_extractor import (
    _BODY_PARTS,
    _extract_reason,
    _part_stem,
    _usable_body_parts,
    utterance_is_read_as_the_reason,
)


# Verbatim from the calls.
ACHILLES = (
    "um yeah my achilles is stiff the first few minutes every morning "
    "and eases as i walk"
)
BACK_NUMB = "yeah my lower back's been really bad and my leg's gone numb"


# ---------------------------------------------------------------------------
# B-141a: every body part the vocabulary claims must be reachable
# ---------------------------------------------------------------------------
def test_no_body_part_is_unreachable_through_the_stemmer():
    """The invariant, not the two words that happened to break it.

    A part whose stem is not itself in the set can never anchor Pass 1, and
    nothing else fails -- the reason is merely worse. Asserted over the whole
    vocabulary so a future addition ending in 's' cannot reintroduce this.
    """
    unreachable = sorted(p for p in _BODY_PARTS if _part_stem(p) not in _BODY_PARTS)
    assert unreachable == [], (
        f"{unreachable} cannot anchor the reason window: _part_stem maps each "
        "to something outside _BODY_PARTS, so Pass 1 skips them and the "
        "complaint falls through to the injury-verb pass"
    )


@pytest.mark.parametrize("word,stem", [
    ("achilles", "achilles"),
    ("pelvis", "pelvis"),
    # ...while the possessive and plural still strip exactly as before.
    ("back's", "back"),
    ("knees", "knee"),
    ("shoulder.", "shoulder"),
    ("ankle,", "ankle"),
])
def test_the_stemmer_strips_the_possessive_but_not_the_word(word, stem):
    assert _part_stem(word) == stem


def test_the_achilles_complaint_keeps_its_body_part():
    got = _extract_reason(ACHILLES)
    assert got is not None
    assert "achilles" in got.lower(), (
        f"the recorded reason is {got!r} -- an operator reading the call "
        "record learns the caller was stiff, but not what was stiff"
    )


def test_the_other_unreachable_part_is_fixed_too():
    got = _extract_reason("my pelvis has been really sore")
    assert got is not None and "pelvis" in got.lower(), got


# ---------------------------------------------------------------------------
# B-141b: the spine and a referred limb are one locus
# ---------------------------------------------------------------------------
def test_back_with_referred_numbness_is_one_locus():
    parts = _usable_body_parts(BACK_NUMB, BACK_NUMB.split())
    assert parts == {"back"}, (
        f"resolved {sorted(parts)}; the leg is the REFERRAL, not a second "
        "complaint, and counting it as one trips the two-complaint guard"
    )


def test_the_complaint_reaches_the_call_record():
    got = _extract_reason(BACK_NUMB)
    assert got is not None, (
        "the call ends with pre-summary reason=None, which empties the Sheets "
        "row and the SMS and starves book_appointment's A2 gate"
    )
    assert "back" in got.lower() and "numb" in got.lower(), got


@pytest.mark.parametrize("utterance", [
    "my neck's been sore and my arm has gone numb",
    "my back is agony and i get pins and needles down my leg",
    "lower back pain with tingling in the foot",
])
def test_other_referral_presentations_are_one_locus_too(utterance):
    assert _extract_reason(utterance) is not None, utterance


def test_both_halves_are_required():
    """A spinal part alone, or a limb with numbness alone, changes nothing.

    The collapse is narrow on purpose: it must not become a general "if two
    parts, keep the first" rule, which is the coin toss the fail-open guard
    exists to refuse.
    """
    # Two parts, no referral sign -> still two complaints, still nothing.
    assert _extract_reason("my back is sore and my knee is sore too") is None
    # One part with a referral sign -> unchanged, already captured.
    assert _extract_reason("my leg's gone numb") is not None


# ---------------------------------------------------------------------------
# The fail-open guards this must NOT weaken
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("utterance", [
    "my knee and my ankle are both sore",
    "i've got shoulder pain and my hip is stiff",
    "hi i'd like to book my knee and my shoulder are both sore",
    "not my knee it's my hip",
    "it isn't my shoulder it's my neck",
    "hi i'd like to book an appointment please",
    "hi can you call me back later",
])
def test_the_fail_open_guards_are_untouched(utterance):
    """Picking the first-mentioned is a coin toss; an extra question costs a
    turn, a wrong reason picks the wrong service. That decision stands."""
    assert _extract_reason(utterance) is None, utterance


def test_the_positional_back_is_still_dropped():
    """"the back of my legs" is one locus spelled with two part words -- the
    precedent this fix is modelled on, and it must keep working."""
    got = _extract_reason("the back of my legs is warm and swollen")
    assert got is not None and "leg" in got.lower(), got


# ---------------------------------------------------------------------------
# ...and the knock-on: the B-138 scheduling gate stops being blind
# ---------------------------------------------------------------------------
def test_the_scheduling_gate_is_no_longer_blind_to_this_phrasing():
    """`utterance_is_opening_reason` asks `_extract_reason`, so a complaint it
    cannot read is a complaint the gate cannot protect. This exact sentence
    leaked a hard AM filter while the extractor returned None."""
    leaky = BACK_NUMB + ", worse in the mornings"
    assert _extract_time_preference(leaky) == "mornings"
    gate = utterance_is_read_as_the_reason({}, leaky)
    assert gate is True
    assert _time_preference_tier(
        leaky, is_slot_pick=False, is_reason_answer=gate
    ) == "none"
