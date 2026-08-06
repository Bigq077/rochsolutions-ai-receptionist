"""
"Three" heard as "free" must still pick the slot — and a repeat must always land.

CAecb1eb29e39656e947f4a81eb0689947, 2026-08-06, offered
{1: three in the afternoon, 2: five in the evening}. The caller said "three".
AssemblyAI wrote "free" — th-fronting, ordinary in UK speech. Four attempts:

    22:11:11  'uh yeah free'       → open-availability continuation suppressed
    22:11:26  'i said free'        → slot fragment ignored — re-arming
    22:11:36  'hello i said free'  → open-availability continuation suppressed
    22:11:52  "i said 3 o'clock"   → accepted, because it contained a digit

41 seconds, three silences, and the only attempt that worked was the one with
a digit in it.

The word did not merely fail to match. 'free' is also a bare substring in
_OPEN_AVAILABILITY_SIGNALS, where it exists to catch "I'm free all week" — so
a misheard slot pick was actively reinterpreted as "I have no preference" and
then suppressed as a redundant continuation. The caller says "three"; the
system hears "I don't mind" and says nothing.

Three changes, tested here:
  1. number homophones are slot signals
  2. "I said …" always reaches the LLM — a caller repeating themselves is proof
     we already failed once
  3. both guards now call _note_utterance_lost, which they never did: the call
     above finished with lost_total=0 by_reason={} while discarding three
     answers. The metric called it clean.
"""

import inspect

import pytest

from app.media_streams import connection as c
from app.media_streams.connection import _is_slot_selection_candidate


# ── the exact call ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("attempt", [
    "uh yeah free",
    "i said free",
    "hello i said free",
])
def test_the_three_lost_attempts_now_reach_the_llm(attempt):
    assert _is_slot_selection_candidate(attempt), (
        f"{attempt!r} still discarded — this is the call that took four tries"
    )


def test_the_attempt_that_worked_still_works():
    assert _is_slot_selection_candidate("i said 3 o'clock")


# ── homophones ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("misheard,intended", [
    ("free", "three"),
    ("tree", "three"),
    ("won", "one"),
    ("ate", "eight"),
    ("fife", "five"),
])
def test_number_homophones_are_slot_signals(misheard, intended):
    assert _is_slot_selection_candidate(f"uh yeah {misheard}"), (
        f"{misheard!r} (for {intended!r}) is not recognised as a slot pick"
    )


def test_the_ambiguous_ones_are_deliberately_excluded():
    """
    "to"/"too"/"for" are NOT slot signals. They are too common in ordinary
    English — "I'd like to move to a video call" would become a slot pick and
    bypass the modality-switch and clarify branches. The "i said" rule covers
    the case where they are genuinely a misheard number.
    """
    from app.media_streams.connection import _SLOT_SIGNALS
    for word in ("to", "too", "for"):
        assert word not in _SLOT_SIGNALS
    # …but a repeat containing them still lands.
    assert _is_slot_selection_candidate("i said to")
    assert _is_slot_selection_candidate("i said for")


# ── the repeat rule ────────────────────────────────────────────────────────

@pytest.mark.parametrize("repeat", [
    "i said free",
    "i said the second one",
    "i said to",
    "i say three",
    "no i said half four",
])
def test_a_caller_repeating_themselves_always_reaches_the_llm(repeat):
    """
    Failing to hear someone once is bad luck. Doing it again after they have
    told you is the defect. This is the net that catches mishearings nobody
    has enumerated.
    """
    assert _is_slot_selection_candidate(repeat)


# ── Spec H must still hold ─────────────────────────────────────────────────

@pytest.mark.parametrize("fragment", ["with me", "suits me", "yes please", "actually"])
def test_spec_h_fragments_still_re_arm(fragment):
    """Widening must not become "everything passes"."""
    assert not _is_slot_selection_candidate(fragment)


# ── the discards are now counted ───────────────────────────────────────────

def test_both_slot_guards_report_the_loss():
    """
    _note_utterance_lost's docstring says "every guard that drops a real final
    transcript calls this". These two never did, which is why a call that
    discarded three answers reported lost_total=0 and looked clean.
    """
    src = inspect.getsource(c)

    # Anchored on the logger prefixes, which appear only at the call sites.
    # The bare phrases also appear in this module's own documentation of the
    # incident, and .index() finds those first.
    for marker, reason in (
        ("[ms_conn] slot fragment ignored", "slot_fragment"),
        ("[ms_conn v3] open-availability continuation",
         "open_availability_suppressed"),
    ):
        at = src.index(marker)
        window = src[at:at + 1400]
        assert "_note_utterance_lost" in window, (
            f"the {marker!r} guard still discards silently"
        )
        assert reason in window, f"expected reason tag {reason!r}"


# ── reaching the LLM is not enough — the word has to be a number ───────────
#
# CA4ab554ce0dd8530e40df3cb8f7e28588, 2026-08-06, the SECOND call of the same
# shape. Same offer, same mishearing, five losses across four guards:
#
#   22:29:46  'uh yeah free'          → open-availability suppressed
#   22:30:03  'uh free works'         → slot fragment ignored
#   22:30:04  'free'                  → slot fragment ignored
#   22:30:20  'i said free'           → slot fragment ignored
#   22:30:24  'i want it free'        → same_breath_straggler
#
# and one that did NOT lose:
#
#   22:30:22  'the free the free one' → reached the LLM ("one" is a signal)
#                                     → "Sorry, I didn't quite catch that."
#
# That last line is the proof that the signal list alone is insufficient. The
# model saw the utterance and could not resolve it, because it was handed the
# word "free". The fix is to rewrite the transcript at the door, inside the
# slot window, so everything downstream sees a real number.

from app.media_streams.connection import _apply_slot_day_aliases


@pytest.mark.parametrize("heard,expected", [
    ("uh yeah free",          "uh yeah three"),
    ("uh free works",         "uh three works"),
    ("free",                  "three"),
    ("i said free",           "i said three"),
    ("i want it free",        "i want it three"),
    ("the free the free one", "the three the three one"),
])
def test_every_lost_utterance_becomes_a_number(heard, expected):
    assert _apply_slot_day_aliases(heard) == expected


def test_the_one_that_reached_the_llm_now_carries_a_number():
    """
    'the free the free one' was never discarded — it was answered with
    "Sorry, I didn't quite catch that". Routing it was already working; what
    was missing was giving the model something resolvable.
    """
    rewritten = _apply_slot_day_aliases("the free the free one")
    assert "free" not in rewritten
    assert "three" in rewritten


def test_the_day_aliases_still_work():
    """The rewrite this function was built for must survive the extension."""
    assert _apply_slot_day_aliases("first year") == "thursday"
    assert _apply_slot_day_aliases("the firs year one") == "the thursday one"


@pytest.mark.parametrize("question", [
    "is parking free?",
    "is the parking free",
    "do you have free parking",
    "is the first appointment free",
    "how much is it, is it free",
    # Not shaped like a question, but still asking one — the copula guard.
    "just wondering if the parking is free",
    "i was told the first one is free",
])
def test_a_genuine_question_about_free_is_left_alone(question):
    """
    "free" is a real English word in a way "firs year" is not. Asking whether
    parking is free during slot selection is an ordinary FAQ, and rewriting it
    to "is parking three?" would answer nothing. Slot picks do not arrive as
    questions.
    """
    assert _apply_slot_day_aliases(question) == question


def test_to_and_for_are_never_rewritten():
    """
    Deliberately excluded from the alias table: rewriting them would corrupt
    ordinary sentences ("I want to book" → "I want two book"). The "i said"
    rule carries the genuine-mishearing case instead.
    """
    for sentence in (
        "i'd like to move to a video call",
        "can you do it for me",
        "i want to book",
    ):
        assert _apply_slot_day_aliases(sentence) == sentence
