# tests/regression/test_b66_cancel_is_boosted.py
"""
B-66 (2026-08-20) - "cancel it" was transcribed "can't see the rotator cuff".

Incident
--------
JV live call CAae25b8345d781f56b5d31eb69a353034.  Susie asked the cancel
retention question - "Would you like to reschedule this appointment, or cancel
it altogether?" - and the caller said "cancel it".  The AssemblyAI PARTIAL had
it right:

    10:23:47  barge-in: partial='uh cancel it'
    10:23:48  FINAL -> queue: "uh can't see the rotator cuff"

The final pass rewrote it.  Susie, handed a shoulder complaint, answered the
shoulder complaint.  The caller tried again - "let's cancel it altogether" -
and got "uh let's i can't see it all together".

`_cancel_reply_consents` then refused to fire `cancel_appointment`, twice, on
the corrupted text (it contains "n't", and any negation blocks).  That is the
FM-23 consent gate working exactly as designed - it is what stopped a wrong
appointment being deleted earlier the same day - so the repeated question the
caller heard was the symptom, not the bug.  Nothing in the flow is loosened
here, and nothing rewrites caller text: the fix is upstream, in what STT is
told to listen for.

Root cause
----------
Keyterm bias, and an asymmetric one.  Every word the transcript drifted toward
was boosted and the word it drifted away from was not:

    cancer   boosted     cancel        NOT boosted
    rotator  boosted     cancelled     NOT boosted
    cuff     boosted

"cancer" is one of jv_v1's own red-flag screening triggers, so the clinic's
clinical vocabulary was out-competing its own near-homophone.  `cancer` keeps
its slot - it arms a malignancy screen - so the fix is to stop the asymmetry by
boosting the control word too, not to remove the clinical one.

Scope, measured before writing this
-----------------------------------
Over the 1,942 caller utterances stored in obs, only the cancel family shows
any corruption: "cancel" 58 clean / 2 corrupted, while "book" (211),
"appointment" (144), "reschedule" (15) and "move" (37) transcribe cleanly every
time.  The booking and reschedule flows therefore need no equivalent fix, and
adding those words would repeat the mistake stt_stream.py already documents
twice - burning the capped budget on ordinary English STT never gets wrong.

These tests use synthetic clinic dicts rather than real clinic ids on purpose.
A test pinned to a clinic id measures whichever branch's clinic.json it lands
on, and has twice looked like a broken port when it was only the config
differing.  The invariants below are engine invariants; they must hold on every
branch and for every clinic.
"""

import pytest

from app.media_streams.stt_stream import (
    _CONTROL_KEYTERMS,
    _GENERIC_KEYTERMS,
    _KEYTERMS_MAX,
    build_keyterms,
)


def _lower(terms):
    return {t.lower() for t in terms}


def _screens(triggers=(), answers=()):
    return {
        "clinical_screening": {
            "screens": [{
                "id": "s1",
                "trigger_keywords": list(triggers),
                "red_flag_answer_keywords": list(answers),
            }]
        }
    }


# ---------------------------------------------------------------------------
# The regression itself.
# ---------------------------------------------------------------------------
def test_cancel_is_boosted_alongside_its_near_homophone():
    """The asymmetry that caused the incident: "cancer" boosted, "cancel" not."""
    clinic = _screens(triggers=["cancer", "rotator cuff"])
    boosted = _lower(build_keyterms(clinic))
    assert "cancer" in boosted, "the malignancy screen trigger must keep its slot"
    assert "cancel" in boosted, (
        "'cancer' is boosted against 'cancel' - that asymmetry rewrote a live "
        "caller's 'cancel it' into 'can't see the rotator cuff'"
    )


def test_the_corrupted_bigram_is_boosted_as_a_phrase():
    """The confusion is bigram-level: "cancel it" -> "can't see"."""
    assert "cancel it" in _lower(build_keyterms(None))


def test_cancelled_is_boosted_too():
    """Boosting is per literal term - the stem does not carry the inflection."""
    assert "cancelled" in _lower(build_keyterms(None))


# ---------------------------------------------------------------------------
# It must reach EVERY clinic. This is where the fix could silently half-land.
# ---------------------------------------------------------------------------
def test_control_vocabulary_reaches_a_clinic_that_opted_out_of_the_generic_list():
    """`use_generic: false` must not switch the cancel family off.

    A live clinic ships exactly this shape - its own vocabulary list with no
    control words in it.  Had the fix been added to _GENERIC_KEYTERMS it would
    have reached two clinics of three and skipped that one in silence, which is
    a failure mode this repo has hit three times.
    """
    clinic = {"stt_keyterms": {"use_generic": False, "terms": ["massage", "knots"]}}
    boosted = _lower(build_keyterms(clinic))
    assert "massage" in boosted, "the clinic's own list must still apply"
    assert not (_lower(_GENERIC_KEYTERMS) & boosted), "use_generic:false must still hold"
    for term in _CONTROL_KEYTERMS:
        assert term.lower() in boosted, f"{term!r} lost to use_generic:false"


def test_control_vocabulary_survives_a_clinic_that_fills_the_cap_with_its_own_terms():
    """A long clinic vocabulary must not starve the control tier."""
    clinic = {"stt_keyterms": {"terms": [f"term{i:03d}" for i in range(_KEYTERMS_MAX * 2)]}}
    boosted = _lower(build_keyterms(clinic))
    for term in _CONTROL_KEYTERMS:
        assert term.lower() in boosted, f"{term!r} starved by the clinic's own list"


# ---------------------------------------------------------------------------
# ...but never at the expense of the safety tiers.
# ---------------------------------------------------------------------------
def test_screening_vocabulary_still_outranks_the_control_tier():
    """A missed red flag is worse than a mis-heard cancel.

    A mis-heard cancel makes the consent gate refuse and re-ask - recoverable,
    and audibly so.  A screening trigger that loses its slot means the
    deterministic red-flag layer never arms and nobody finds out.  So if
    something has to be dropped at the cap, it is the control tier.
    """
    triggers = [f"trig{i:03d}" for i in range(_KEYTERMS_MAX)]
    boosted = _lower(build_keyterms(_screens(triggers=triggers)))
    assert _lower(triggers) <= boosted, "screening triggers lost their slots"
    assert not (_lower(_CONTROL_KEYTERMS) & boosted), (
        "the control tier outranked screening vocabulary"
    )


def test_red_flag_answer_keywords_still_outrank_the_control_tier():
    answers = [f"ans{i:03d}" for i in range(_KEYTERMS_MAX)]
    boosted = _lower(build_keyterms(_screens(answers=answers)))
    assert _lower(answers) <= boosted, "red-flag answer keywords lost their slots"


# ---------------------------------------------------------------------------
# Discipline. The cap is the scarce resource and this list is the tempting
# place to spend it.
# ---------------------------------------------------------------------------
def test_the_control_list_stays_the_cancel_family_only():
    """Guard against the documented mistake: boosting words STT gets right.

    Measured over 1,942 stored caller utterances, "book", "appointment",
    "reschedule" and "move" were transcribed correctly every single time - 400+
    utterances between them.  Boosting them buys nothing and costs the anatomy
    that jv_v1, already at the cap, would give up for it.  If you want to add a
    word here, measure its corruption rate in obs first and put the number in
    the commit message.
    """
    stray = [t for t in _CONTROL_KEYTERMS if "cancel" not in t.lower()]
    assert not stray, (
        f"only the cancel family has measured corruption evidence: {stray}"
    )
    assert len(_CONTROL_KEYTERMS) <= 6, "the control tier is eating the cap"


@pytest.mark.parametrize("clinic", [None, {}, {"stt_keyterms": None}])
def test_control_vocabulary_present_even_when_the_clinic_is_unresolved(clinic):
    """Env misconfiguration must not silently unboost a destructive action."""
    assert "cancel" in _lower(build_keyterms(clinic))
