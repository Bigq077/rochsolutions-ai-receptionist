"""
Regression: choosing a slot silently narrowed every later search.

B-90. `CAa415c88d` (26 Aug 2026, theorem_v3, build `f05c59f7`). Ground truth is
the clinic's own Acuity page, which showed **10:00 AM and 2:00 PM both free** on
Wednesday 2 September:

    17:38:44  tool -> slot_times ["10:00","14:00"]          BOTH present
    17:38:45  Susie: "Number 1, ten in the morning. Number 2, two in the afternoon."
    17:38:57  digit='1' -> injecting 'ten in the morning'
    17:38:57  time_of_day_preference captured: mornings
    17:39:13  tool: date_hint="morning Wednesday 2 September 2026"
    17:39:13  result -> slot_times ["10:00"]                 14:00 FILTERED OUT
    17:39:21  Susie: "That's all we have on Wednesday the 2nd of September
                      — just the ten in the morning."

The caller had asked the most direct question available — *"or is that all you
have that day"* — and was told yes while a 2pm sat free.

WHY NO GATE COULD CATCH IT: Susie's sentence is TRUE about the payload she was
handed. The tool filtered the day and reported the filtered view as complete.
It is invisible in a transcript, which is why it survived a night of log review
and was found by comparing against the provider's booking page.

TWO compounding causes, both pre-existing:

  A. A slot SELECTION is mined as a standing PREFERENCE. The capture block
     scans "every accepted utterance", and a keypress injects a synthetic
     transcript that is ALWAYS a time label. Picking option 1 means "I'll take
     that one", not "I only do mornings".
  B. It is never cleared — the code says so, and there are 0 clear sites on all
     four branches.

THIS FILE COVERS (A). The discrimination is on DATA, not a phrase list: if the
utterance IS one of the labels just offered, it is a selection. That covers the
keypad and the spoken form with one test, because both are the same string the
offer was read out with.

(B) is deliberately NOT changed here: with (A) fixed, a genuine "mornings
please" SHOULD persist. The residual — a caller who states a real preference and
later asks "is that all you have that day" — is recorded separately, because the
honest fix there is for the payload to disclose that a time filter removed
slots, not for the session to guess when to forget.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import _norm_offer_label


OFFER = ["ten in the morning", "two in the afternoon"]


def _offered():
    return {_norm_offer_label(x) for x in OFFER}


def _is_pick(utterance: str) -> bool:
    """Mirrors the engine: containment, so a choice wrapped in extra words
    ("I'll take two in the afternoon") still counts as a selection."""
    u = _norm_offer_label(utterance)
    return bool(u) and any(f" {lbl} " in f" {u} " for lbl in _offered())


# ---------------------------------------------------------------------------
# The live defect — the keypad path
# ---------------------------------------------------------------------------
def test_the_injected_keypad_label_is_a_selection():
    """A keypress injects the map value verbatim, so it is always an exact
    match for a label that was just read out."""
    assert _is_pick("ten in the morning")


def test_the_spoken_form_is_a_selection_too():
    """Fixing only the keypad would leave the common case live — a caller who
    says the time aloud sets the same preference."""
    for said in (
        "ten in the morning",
        "yeah ten in the morning please",
        "Ten In The Morning",
        "um, two in the afternoon",
        "I'll take two in the afternoon",
    ):
        assert _is_pick(said), said


# ---------------------------------------------------------------------------
# A real preference must still be captured
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("said", [
    "mornings please",
    "uh mornings",
    "anytime next week",
    "afternoons if you have them",
    "evenings work best",
])
def test_a_genuine_preference_is_not_mistaken_for_a_selection(said):
    """The fix must not make Susie deaf to "mornings please" — that IS a
    standing preference and filtering on it is correct."""
    assert not _is_pick(said)


def test_a_time_that_was_never_offered_is_not_a_selection():
    """Only the labels ACTUALLY read out count. A caller naming some other time
    is making a request, not picking from the list."""
    assert not _is_pick("nine in the morning")


# ---------------------------------------------------------------------------
# The normaliser itself
# ---------------------------------------------------------------------------
def test_normaliser_survives_nonsense():
    for bad in (None, "", 123, [], {}):
        assert _norm_offer_label(bad) == ""


def test_normaliser_strips_filler_not_content():
    assert _norm_offer_label("yeah, ten in the morning please") == \
        _norm_offer_label("ten in the morning")
    # Content words are never dropped.
    assert "morning" in _norm_offer_label("ten in the morning")
    assert "afternoon" in _norm_offer_label("two in the afternoon")


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def test_the_capture_site_consults_the_current_offer():
    import inspect

    from app.media_streams import connection as c

    src = inspect.getsource(c)
    # Anchor on the LOG CALL, not the bare phrase — this file's own comment
    # quotes that phrase, and src.index() would find the comment first. The
    # test then measures a window above the wrong line and passes or fails for
    # reasons unrelated to the wiring.
    i = src.index('"[ms_conn v3] time_of_day_preference captured: %s"')
    # Widened 3000 -> 8000 on 26 Aug 2026 (B-91/B-92). The window is a proxy
    # for "in the same block", and the block grew when the question gate and
    # its evidence landed above the log call. The anchors below are what this
    # test is actually about; the byte count is an implementation detail of
    # the test, and a too-small one fails for a reason unrelated to wiring.
    window = src[i - 8000:i]
    assert "_is_slot_pick" in window, (
        "the preference capture does not check whether the utterance was a "
        "selection from the offer just read out"
    )
    assert "slot_labels" in window and "v3_dtmf_slot_map" in window, (
        "both the spoken labels and the keypad map must be consulted — the "
        "keypad injects a map value, the caller speaks a slot_label"
    )


# ---------------------------------------------------------------------------
# B-91 — the SPOKEN half of the same guard, defeated by spelling
#
# `CA70cc833f` (26 Aug 2026, theorem_v3, build `1a711a54`). B-90 above was
# already deployed and fired correctly on the keypress:
#
#   18:28:38  'ten in the morning' is a slot SELECTION, not a time preference
#             — soft context not set (B-90)
#
# Four seconds later the caller asked about that same slot out loud, and the
# guard missed:
#
#   18:28:53  time_of_day_preference captured: mornings (from utterance 'um
#             actually on that wednesday do you have any other slots than the
#             10 in the morning is that all you have that day')
#
# The offer was generated as "ten"; AssemblyAI transcribed the caller's echo
# as "10". Containment is exact about the label, and ten != 10, so a spoken
# pick of an offered slot read as a fresh standing preference. The keypad half
# could never show this — there the label is injected verbatim from the map.
# ---------------------------------------------------------------------------
LIVE_QUESTION_B91 = (
    "um actually on that wednesday do you have any other slots than the "
    "10 in the morning is that all you have that day"
)


def test_a_numeral_echo_of_a_spoken_label_is_still_a_selection():
    """The live utterance. Before the fix this returned False."""
    assert _is_pick(LIVE_QUESTION_B91), (
        "the caller named a slot that had just been read out; spelling the "
        "hour as a numeral must not turn a selection into a preference"
    )


@pytest.mark.parametrize(
    "spoken,transcribed",
    [
        ("ten in the morning", "10 in the morning"),
        ("nine in the morning", "9 in the morning"),
        ("two in the afternoon", "2 in the afternoon"),
        ("half past ten", "half past 10"),
    ],
)
def test_word_and_numeral_spellings_normalise_alike(spoken, transcribed):
    assert _norm_offer_label(spoken) == _norm_offer_label(transcribed)


def test_the_one_oclock_label_keeps_its_hour():
    """"one" is a filler word, so "one in the afternoon" used to normalise to
    the alarmingly loose "in afternoon" — a label that would contain-match far
    too much. Folding the hour to a digit before filler removal rescues it."""
    assert "1" in _norm_offer_label("one in the afternoon").split()
