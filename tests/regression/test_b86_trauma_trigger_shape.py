"""B-86 — an injury described in ordinary words armed no screen.

`trauma_fracture` carried 35 CONTIGUOUS "VERB my BODYPART" literals —
"twisted my ankle", "rolled my knee", "done my wrist". Callers do not talk like
that. Replayed over 543 stored jv_v1 calls: 13 described an injury, **11 armed
nothing**, and the whole screen armed once.

What the callers actually said (verbatim from the corpus):

    "just my left ankle nothing serious just ROLLED IT yesterday at the gym"
    "i've HURT MY ankle"                       <- "hurt" was in no literal
    "yeah i just TWISTED IT but it's feeling a bit better"
    "the ankle is fine ... i SLIPPED one time"

The fix decomposes the bigram into two groups — mechanism AND body part — the
same two-signal form `vbi_neck` already uses. `trigger_keywords` is left
untouched and the two forms are OR-ed, so nothing that armed before stops
arming.

These tests pin the RULE and, just as importantly, the two variants that were
measured and REJECTED.
"""

import json
from pathlib import Path

import pytest

from app.media_streams.clinical_screening import match_screen_trigger

CLINIC = json.loads(
    (Path("app/clinics/jv_v1/clinic.json")).read_text(encoding="utf-8")
)


def _screen():
    return next(
        s for s in CLINIC["clinical_screening"]["screens"]
        if s["id"] == "trauma_fracture"
    )


def _arms(utterance):
    return match_screen_trigger(utterance, CLINIC, {})


# ── The defect: real caller utterances from the corpus ─────────────────────

@pytest.mark.parametrize("utterance", [
    # pronoun after the body part was already named — the bigram cannot form
    "um just my left ankle nothing serious just rolled it yesterday at the gym",
    "um just my left ankle i twisted it while i was going to the gym",
    # NOTE the corpus also holds "yeah i just twisted it but it's feeling a bit
    # better" — deliberately NOT here. It names no body part in any utterance
    # (STT wrote "angles" for "ankle"), and "it" cannot join the body-part group:
    # see test_the_pronoun_is_not_a_body_part below.
    # a mechanism verb the 35 literals never carried
    "i've hurt my ankle",
    "um yeah i've hurt my ankle",
    "hi i'd like to book only that i've actually hurt my ankle",
    # mechanism and part in one sentence, neither adjacent
    "the ankle is fine it's just a bit of pain because i've kind of over walking "
    "and i slipped one time",
])
def test_real_injury_utterances_now_arm(utterance):
    assert _arms(utterance) == "trauma_fracture", (
        f"armed nothing for a described injury: {utterance!r}"
    )


# ── The rejected variant: bare pain words must NOT arm ─────────────────────

@pytest.mark.parametrize("utterance", [
    "hi i'd like to book an appointment my neck's been hurting a bit recently",
    "um just my left ankle's a little bit sore",
    "hi my calf's been very sore lately",
    "hi i'd like to book for shoulder pain",
    "hi i've got a tight hamstring from running and i'd like a sports massage",
    "i'm looking to have an appointment for my shoulder",
])
def test_gradual_soreness_does_not_arm(utterance):
    """B-20: screening is conditional, not a checklist.

    Adding bare "hurt" / "hurting" / "sore" to the mechanism group was measured
    and rejected — it armed on every one of these. A screening question the
    caller's problem does not call for is not a safety check, it is an alarming
    question about a condition they have no reason to think they have.
    """
    assert _arms(utterance) != "trauma_fracture", (
        f"over-screened a gradual-onset complaint: {utterance!r}"
    )


@pytest.mark.parametrize("utterance", [
    # _phrase_in tolerates 3 filler words, so a bare verb reaches across a
    # clause. Every one of these armed during development and was designed out.
    "i rolled over in my sleep and my neck is stiff",
    "i rolled over in bed and my neck went",
    "i turned up late and my knee was fine",
    "i went over my exercises and my shoulder ached",
    "she fell pregnant and my back is sore",
    "i rolled it up and my back hurts",
    "i twisted the lid off and my hand is sore",
    "we twisted my referral around the appointment",
])
def test_gap_tolerance_does_not_reach_across_a_clause(utterance):
    """Bare 'rolled' / 'turned' / 'went over' are why this fails.

    Their verb+object forms ('rolled my ankle', 'turned my ankle') are already
    in trigger_keywords, so the groups must NOT carry the bare verb.
    """
    assert _arms(utterance) != "trauma_fracture", utterance


def test_the_pronoun_is_not_a_body_part():
    """'it' would catch the commonest phrasing and is still forbidden.

    This screen's own question contains it — "is it too painful to use it" — so
    admitting 'it' lets the screen arm off its own question, turning a screen
    into a confirmation of a red flag the caller already volunteered.
    Cross-checked by test_a_screen_never_gates_on_its_own_answer.py.
    """
    parts = {p.lower() for p in _screen()["trigger_all_groups"][1]}
    assert "it" not in parts
    assert "it" in _screen()["screen_question"].lower()


def test_a_bare_body_part_does_not_arm():
    """Two signals, always. One is never enough."""
    for u in ("my ankle", "it's my knee", "my left ankle nothing serious"):
        assert _arms(u) != "trauma_fracture", u


def test_a_mechanism_with_no_body_part_does_not_arm_via_the_groups():
    """"I twisted the lid off" must not arm.

    The groups require BOTH. (Some bare-mechanism phrases still arm via the
    decisive keyword list — "had a fall", "car accident" — and should.)
    """
    assert _arms("i twisted the lid off the jar") != "trauma_fracture"


# ── Nothing that armed before may stop arming ──────────────────────────────

@pytest.mark.parametrize("utterance", [
    "i came off my bike",          # no body part — survives only as a keyword
    "it was a bad tackle",
    "i crashed the car",
    "i landed badly",
    "i had a fall",
    "i heard a crack",
    "i can't put weight on it",
    "i twisted my ankle",          # the original bigram still works
    "i've done my shoulder",
])
def test_existing_triggers_still_arm(utterance):
    """The change is purely ADDITIVE.

    `came off my bike`, `bad tackle` and `crashed` have no body part to pair
    with, so they survive only because trigger_keywords was left intact. An
    "elegant" rewrite that replaces the keyword list with the groups silently
    drops them — that was caught in review, not by the corpus, because no
    stored call uses those phrasings.
    """
    assert _arms(utterance) == "trauma_fracture", utterance


# ── Config shape ───────────────────────────────────────────────────────────

def test_keyword_list_is_unchanged():
    """35 literals. If this count moves, the additive property was broken."""
    assert len(_screen()["trigger_keywords"]) == 35


def test_mechanism_group_holds_no_bare_pain_words():
    """Pins the rejected variant at config level, not just behaviourally."""
    mech = {m.lower() for m in _screen()["trigger_all_groups"][0]}
    for banned in ("hurt", "hurting", "sore", "painful", "pain", "aching",
                   "rolled", "turned", "twisted", "went over"):
        assert banned not in mech, (
            f"{banned!r} is a gradual-onset word — measured as over-screening"
        )
    assert "hurt my" in mech, "the phrase form is the one that is safe"
    assert "rolled it" in mech and "twisted it" in mech, (
        "the object-bound forms are safe and carry two of the corpus wins"
    )


def test_other_screens_were_not_touched():
    """Measured: no other screen's arm count moved, and no call switched screen.

    match_screen_trigger returns the FIRST matching screen in config order, so
    widening one screen can steal calls from another.
    """
    assert _arms("my lower back has been really painful") == "cauda_equina"
    assert _arms("my calf is swollen and warm") == "dvt"
