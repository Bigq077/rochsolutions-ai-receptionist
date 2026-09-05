# tests/regression/test_a_screen_question_is_framed_as_routine.py
"""
A screen question normalises the ASKING. It must never normalise the ANSWER.

Phase 4 of the screening plan. With the Phase 3 trigger narrowing rejected
(test_a_screen_never_gates_on_its_own_answer.py), framing is the ONLY remaining
lever on "the bladder question is alarming for someone who just wants a
massage" — so it has to carry that weight without costing recall.

WHAT CHANGED. Four lead-ins. They used to apologise for the question or hint
that something was wrong:

    "Sorry to ask, but it helps me point you the right way — ..."
    "Just to be safe before we book anything — ..."
    "Before we go further, can I quickly check — ..."

A caller hears "sorry" and "just to be safe" as: she thinks there is something
wrong with me. The replacement says the opposite — this gets asked of everyone,
your answer is not why I am asking:

    "There's one routine question I ask everyone before booking back pain — ..."

The clinical half of every question is untouched, and trauma_fracture and
inflammatory were deliberately left alone: trauma_fracture carries a live
polarity fix (becd7f8) that should not be disturbed in the same pass, and
inflammatory's "Can I ask —" was already neutral and is advisory anyway.

THE LINE THIS FILE EXISTS TO HOLD. The plan's own example wording ended
"...almost everyone says no to these." That must never ship.
`classify_screen_answer` is a single-polarity keyword grader: a negative lead is
`clear`, full stop. Priming the caller toward "no" therefore does not merely
soften the question — it manufactures false CLEARS on the one path where a
false clear is the dangerous direction. Measured before rejecting it: "no",
"no i don't think so", "no nothing like that" and "erm no" all return `clear`
and unblock the booking. Normalise the asking; never suggest the answer.

THE SECOND HAZARD, which the plan did not know about. `_screen_evidence_words`
builds orphan detection out of the words >5 chars that are UNIQUE to one
screen's question across the whole corpus, with a floor of
`_ORPHAN_MIN_EVIDENCE`. Shared tone vocabulary is therefore not free: the first
draft of these lead-ins made "everyone" and "theres" unique evidence words for
cauda_equina, so any bot turn containing "there's" plus one more would have
logged a false ORPHAN against the screen B-20 is scored on. That is the
"proper" collision of 3 Aug 2026 repeating. Fixed by adding the scaffolding to
`_ORPHAN_STOPWORDS`, and pinned below: after the tone pass every screen's
evidence set is byte-identical to what it was before, so detection did not move
at all.

Config-only apart from those stopwords. Polarity is pinned by
test_a_screen_question_is_phrased_so_yes_means_concerning.py and body-part
assertions by test_screen_wording_no_body_part_assertion.py; this file does not
duplicate either.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams import clinical_screening as cs
from tests.screening_fixture import screening_clinic, screening_clinic_json

_SCREEN_IDS = (
    "cauda_equina", "dvt", "serious_spinal",
    "trauma_fracture", "vbi_neck", "inflammatory",
)

_QUESTION_FIELDS = ("screen_question", "screen_reask_question", "screen_probe_question")


@pytest.fixture()
def jv():
    return screening_clinic()


def _screen(jv, sid):
    s = cs.get_screen(jv, sid)
    if s is None:
        pytest.skip("%s not configured for jv_v1" % sid)
    return s


# -- 1. Never prime the answer -------------------------------------------
# Each of these tells the caller which answer is expected. With a
# single-polarity grader that is a manufactured false clear, not a kindness.
_ANSWER_PRIMING = (
    "says no",
    "say no",
    "everyone says",
    "most people say",
    "nearly always no",
    "almost always no",
    "usually nothing",
    "probably nothing",
    "i'm sure it's",
    "im sure its",
    "shouldn't be anything",
    "wouldn't expect",
    "doubt you",
    "don't expect",
)


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
@pytest.mark.parametrize("field", _QUESTION_FIELDS)
def test_a_screen_question_never_primes_the_answer(jv, screen_id, field):
    text = (_screen(jv, screen_id).get(field) or "").lower()
    if not text:
        return
    for bad in _ANSWER_PRIMING:
        assert bad not in text, (
            "%s.%s tells the caller which answer to give (%r). "
            "classify_screen_answer grades a negative lead as `clear` with no "
            "further reading, so this converts reassurance into a false clear "
            "and books a possible red flag in for hands-on physio. Normalise "
            "the asking, not the answer. Text: %r" % (screen_id, field, bad, text)
        )


# -- 2. Do not apologise for the question --------------------------------
# The framing that made a benign caller hear an accusation. Not applied to
# reask/probe: a re-ask legitimately says "sorry, I do need to check one thing".
_ALARMING_OPENERS = ("sorry to ask", "just to be safe", "to be on the safe side")


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_a_screen_question_does_not_apologise_for_itself(jv, screen_id):
    text = (_screen(jv, screen_id).get("screen_question") or "").lower()
    for bad in _ALARMING_OPENERS:
        assert bad not in text, (
            "%s.screen_question opens by apologising (%r), which tells the "
            "caller there is something to be worried about. Frame it as routine "
            "instead. Text: %r" % (screen_id, bad, text)
        )


# -- 3. The tone pass must not move orphan detection ---------------------
# Captured from the tree immediately BEFORE the Phase 4 rewording. A tone edit
# that changes any of these has changed which bot turns are read as having
# asked a screen -- which is the metric B-20 is scored on.
_EVIDENCE_BEFORE_PHASE_4 = {
    "cauda_equina":    {"bladder", "changes", "control", "numbness", "saddle"},
    "dvt":             {"compared", "illness", "journey", "recent", "sitting",
                        "surgery", "swollen"},
    "serious_spinal":  {"cancer", "fevers", "history", "sweats", "unexplained"},
    "trauma_fracture": {"marked", "painful", "swelling", "through"},
    "vbi_neck":        {"blackouts", "clumsiness", "dizziness", "double",
                        "unsteadiness", "vision"},
    "inflammatory":    {"joints", "lasting", "stiffness"},
}


def test_orphan_evidence_is_unchanged_by_the_tone_pass(jv):
    assert cs._screen_evidence_words(jv) == _EVIDENCE_BEFORE_PHASE_4, (
        "the tone pass moved orphan detection. New scaffolding vocabulary that "
        "is unique to one question becomes evidence for that screen (see the "
        "'proper' collision in _ORPHAN_STOPWORDS); shared vocabulary that "
        "collides with a clinical word removes it. Add scaffolding to "
        "_ORPHAN_STOPWORDS rather than accepting the drift."
    )


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_every_screen_keeps_enough_evidence_to_be_orphan_matched(jv, screen_id):
    words = cs._screen_evidence_words(jv).get(screen_id, set())
    assert len(words) >= cs._ORPHAN_MIN_EVIDENCE, (
        "%s has %d evidence words against a bar of %d, so Layer 2 can never be "
        "detected asking it and the orphan path is silently dead for this "
        "screen: %s" % (screen_id, len(words), cs._ORPHAN_MIN_EVIDENCE, sorted(words))
    )


# -- 4. B-31: a question long enough to lose its '?' ---------------------
@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
@pytest.mark.parametrize("field", _QUESTION_FIELDS)
def test_a_screen_question_fits_inside_the_prompt_cap(jv, screen_id, field):
    """Framing lengthens a question, and length is not free.

    A bot turn is stored in last_bot_prompt truncated to _LAST_BOT_PROMPT_CAP.
    B-31 (CA2ada6263, 2 Aug 2026): the model asked a DVT screen in 205
    characters, the '?' was the character that fell off the end, and the
    caller's red-flag answer was never graded. The deterministic path stores
    the question in full, but the model paraphrases these, and a paraphrase is
    rarely shorter than its source.
    """
    text = _screen(jv, screen_id).get(field) or ""
    if not text:
        return
    assert len(text) < cs._LAST_BOT_PROMPT_CAP, (
        "%s.%s is %d chars against a %d cap. A model paraphrase of it can lose "
        "its '?' in last_bot_prompt, which switches orphan matching off for the "
        "turn. Shorten the framing, not the clinical content. Text: %r"
        % (screen_id, field, len(text), cs._LAST_BOT_PROMPT_CAP, text)
    )


# -- 5. The clinical substance survived the rewording --------------------
_SUBSTANCE = {
    "cauda_equina":    ("numbness", "saddle", "bladder", "bowel"),
    "dvt":             ("swollen", "warm", "red", "surgery"),
    "serious_spinal":  ("weight loss", "fevers", "night sweats", "cancer"),
    "vbi_neck":        ("dizziness", "blackouts", "double vision", "clumsiness"),
    "inflammatory":    ("stiffness", "morning", "joints"),
}


@pytest.mark.parametrize("screen_id", sorted(_SUBSTANCE))
def test_reframing_did_not_drop_clinical_content(jv, screen_id):
    q = (_screen(jv, screen_id).get("screen_question") or "").lower()
    missing = [w for w in _SUBSTANCE[screen_id] if w not in q]
    assert not missing, "%s screen_question lost %s: %r" % (screen_id, missing, q)


def test_the_two_screens_left_alone_were_left_alone(jv):
    """trauma_fracture carries a live polarity fix under review (becd7f8) and
    inflammatory was already neutral. Both were deliberately out of scope."""
    assert (_screen(jv, "trauma_fracture")["screen_question"]).startswith(
        "That sounds like a proper knock"
    )
    assert (_screen(jv, "inflammatory")["screen_question"]).startswith("Can I ask")
