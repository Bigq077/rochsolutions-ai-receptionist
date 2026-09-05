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

from app.media_streams.connection import (
    _norm_offer_label,
    offered_slot_labels,
    utterance_is_slot_selection,
)


OFFER = ["ten in the morning", "two in the afternoon"]


def _offered():
    return {_norm_offer_label(x) for x in OFFER}


def _is_pick(utterance: str) -> bool:
    """The engine's own rule, called directly.

    This used to MIRROR it -- containment re-implemented here by hand -- and
    said so. A mirror agrees with itself for as long as nobody edits both
    copies, which is exactly the failure this file exists to catch elsewhere.
    `utterance_is_slot_selection` was given a name on 2026-08-30 so the
    hold-speech head classifier could ask the same question; taking the mirror
    out is the other half of that.
    """
    return utterance_is_slot_selection(utterance, {"slot_labels": list(OFFER)})


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
    """The preference capture must ask whether this was a SELECTION first.

    This was a text scan over an 8000-byte window of connection.py, looking for
    the literals `slot_labels` and `v3_dtmf_slot_map` near the capture log
    line. It broke on 2026-08-30 when the rule was extracted to
    `utterance_is_slot_selection` -- the literals moved to module scope and the
    window stopped containing them, while the wiring got BETTER rather than
    worse. Fourth instance in this codebase of a text scan failing to tell
    coupling from something that is not coupling.

    Asserted structurally instead: the capture site CALLS the shared
    predicate, and the shared predicate reads both sources.

    FIFTH instance, 2026-09-05, and this time the SCAN was the fault. The
    window was a fixed 8000 bytes backwards from the log line -- a byte
    count standing in for "the capture block" -- with ~600 bytes of
    headroom. B-138 added a reason-answer probe to that block, the call to
    `utterance_is_slot_selection` slid to 8439 bytes away, and this test
    reported that the capture site had grown its own copy of the rule. The
    wiring was untouched; the ruler was too short.

    Re-anchoring the window on the block's own header comment FIXED the
    false alarm and introduced a false negative instead: the block's prose
    names `utterance_is_slot_selection` twice, so the scan then matched a
    COMMENT and passed with the real call deleted. Verified -- neutering
    the call left it green.

    So the text scan is gone. The question "does the capture site call the
    shared predicate?" is a question about code, and it is now asked of
    the parse tree: the single `_is_slot_pick` assignment must BE a call to
    `utterance_is_slot_selection`. Comments cannot satisfy that, and no
    window can be too short.
    """
    import ast
    import inspect

    from app.media_streams import connection as c

    src = inspect.getsource(c)
    tree = ast.parse(src)
    calls = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "_is_slot_pick"
            for t in n.targets
        )
    ]
    assert len(calls) == 1, (
        f"expected exactly one _is_slot_pick assignment, found {len(calls)} "
        "-- the preference capture's selection guard has been duplicated or "
        "removed"
    )
    bound = calls[0]
    assert isinstance(bound, ast.Call), (
        "_is_slot_pick is no longer bound to a call -- the preference "
        "capture does not ask whether the utterance was a selection from "
        "the offer just read out"
    )
    called = getattr(bound.func, "id", None) or getattr(bound.func, "attr", None)
    assert called == "utterance_is_slot_selection", (
        f"_is_slot_pick is bound to {called!r}, not the shared selection "
        "predicate -- the capture site has grown its own copy of the rule, "
        "which is how the two sides drift"
    )

    # ...and the predicate consults BOTH sources. The keypad injects a map
    # value; the caller speaks a slot_label. Reading one is half a guard.
    reader = inspect.getsource(offered_slot_labels)
    assert "slot_labels" in reader and "v3_dtmf_slot_map" in reader, reader

    # One reader of the offer, not a family. The claim is narrow and exact:
    # exactly one module-level function may read BOTH label sources, because
    # that pair IS the definition of "what is on the table" and two readers of
    # it drift. `_is_slot_selection_candidate` is deliberately not caught here
    # -- it answers a much looser routing question ("does this carry any slot
    # signal at all?") and never consults the offer.
    tree = ast.parse(src)
    readers = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.get_source_segment(src, node) or ""
        if "slot_labels" in body and "v3_dtmf_slot_map" in body:
            readers.append(node.name)
    assert readers == ["offered_slot_labels"], (
        "more than one function reads the offered-label pair: %s -- that pair "
        "is what 'on the table' MEANS, and two readers of it drift" % readers
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
