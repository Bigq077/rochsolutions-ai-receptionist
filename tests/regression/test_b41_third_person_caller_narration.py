# tests/regression/test_b41_third_person_caller_narration.py
"""
B-41 — Susie said "Their choice is to cancel." out loud.

`CA12db707b1b887d38b7408aa36fc990d6`, 3 Aug 2026 10:16:19, build `9d9efddb9a22`:

    10:16:19.358  [ms_gate5] removed banned phrase (lookup_reasoning_leak)
    10:16:19.360  [ms_tts] synthesise_chunk: text='Their choice is to cancel.'
    10:16:19.984  [ms_gate5] turn complete: 0 chunk(s) dropped as reasoning

The model generated TWO reasoning sentences. `lookup_reasoning_leak` is a
**sentence-level** strip, so it removed only the one carrying "look up the
patient details" and its sibling reached TTS. Gate 5a's whole-chunk reasoning
drop did not fire either.

The gap was grammatical: the detectors covered first person and the literal
"The caller ..." (`reasoning_the_caller`), but not third-person **possessive**
narration of the caller's decision.

Fixed in Gate 5g rather than the flat `_BANNED_SENTENCE_RE` list, because 5g is
structural — it additionally requires no second-person reference and no question
mark, and those two guards are what make a broader pattern safe to add. Both are
exercised below.

Not a safety defect: the cancellation on that call was correct and correctly
confirmed. This is demo-audible.
"""
from __future__ import annotations

import pytest

from app.media_streams import turn_handler as th


def _session():
    return {"_clinical_depth_cache": "", "v3_cta_count": 0}


# ── The leak, and its near neighbours ─────────────────────────────────────
_THIRD_PERSON_NARRATION = [
    "Their choice is to cancel.",            # <- CA12db707b, verbatim
    "Their preference is to reschedule.",
    "Their decision is to book the Tuesday.",
    "Their intent is to move the appointment.",
    "Their intention is to cancel altogether.",
    "Their selection is the afternoon slot.",
    "Their wish is to speak to Marcus.",
]


@pytest.mark.parametrize("sentence", _THIRD_PERSON_NARRATION)
def test_third_person_narration_is_self_narration(sentence):
    assert th._is_self_narration(sentence) is True


@pytest.mark.parametrize("sentence", _THIRD_PERSON_NARRATION)
def test_third_person_narration_never_reaches_tts(sentence):
    assert th.sanitise_response(sentence, _session()) == ""


def test_the_verbatim_two_sentence_chunk_from_the_call():
    """The whole point: the sibling sentence used to survive.

    `lookup_reasoning_leak` removes the first sentence. Before this fix the
    second was spoken. Both must go now.
    """
    chunk = "I need to look up the patient details first. Their choice is to cancel."
    assert th.sanitise_response(chunk, _session()) == ""


# ── Legitimate clinic speech must survive ─────────────────────────────────
# The clinic is ALSO "they". These are the sentences a bare `they|their` arm
# would have eaten, which is why the pattern is scoped to decision nouns.
_LEGITIMATE = [
    "They're fully booked that day.",
    "They close at six.",
    "They open at half past eight on Saturdays.",
    "They don't offer that treatment, I'm afraid.",
    "They can see you on Tuesday.",
    "Your physio will confirm their availability.",
    "We'll pass on their preference to you.",
    "Marcus and Leanne both work from that site — their diaries are separate.",
]


@pytest.mark.parametrize("sentence", _LEGITIMATE)
def test_legitimate_third_party_speech_survives(sentence):
    assert th._is_self_narration(sentence) is False, (
        f"over-fire: {sentence!r} would be deleted from the caller's audio. "
        f"An over-fire here is the Gate 5c failure of 2026-06-12."
    )
    assert sentence.split(".")[0][:20].lower() in (
        th.sanitise_response(sentence, _session()).lower()
    )


# ── The two structural guards, exercised explicitly ───────────────────────
def test_second_person_exempts_the_sentence():
    """A sentence addressed to the caller is not the model talking to itself,
    even when it carries a decision noun."""
    s = "We'll confirm their preference with you before anything is booked."
    assert th._is_self_narration(s) is False
    assert "preference" in th.sanitise_response(s, _session())


def test_a_question_exempts_the_sentence():
    """Asking is addressing, never narrating."""
    s = "Is their preference the afternoon?"
    assert th._is_self_narration(s) is False
    assert th.sanitise_response(s, _session()).strip() != ""


# ── Design property ───────────────────────────────────────────────────────
def test_no_bare_third_person_pronoun_arm():
    """Guards the decision, not just the behaviour.

    A bare `they|their` arm would be simpler and would also cover
    "That is what they want." It is deliberately NOT there: the clinic is also
    "they", and "They close at six." has no second person and no question mark,
    so it would be stripped from real audio. Under-firing is the correct bias —
    a missed leak is embarrassing, a deleted sentence is a broken call.
    """
    pattern = th._SELF_NARRATION_RE.pattern
    assert "|their\\s+(?:" in pattern, "the B-41 arm has been reshaped — re-check the tests below"
    for bare in (r"|their\b", r"|they\b", r"|them\b"):
        assert bare not in pattern, (
            f"a bare third-person arm ({bare}) was added; "
            f'"They close at six." will now be deleted from caller audio'
        )


def test_known_uncovered_shape_is_recorded_not_silently_missed():
    """"That is what they want." is NOT caught, and that is a deliberate
    trade rather than an oversight. If someone later widens the pattern to
    cover it, this test failing is the prompt to re-check the legitimate list
    above — particularly the clinic-as-"they" sentences."""
    assert th._is_self_narration("That is what they want.") is False


# ── The existing 5g contract is unchanged ─────────────────────────────────
def test_the_keep_drop_pair_that_gate_5g_exists_for():
    """From the module comment: these two share "I have everything I need",
    and stripping the first once cost a completed booking."""
    keep = "I have everything I need to get that booked — shall I go ahead?"
    drop = "I need to book this in now — I have everything I need."
    assert th._is_self_narration(keep) is False
    assert th._is_self_narration(drop) is True


@pytest.mark.parametrize(
    "sentence",
    [
        "That's a soft affirmative to the booking offer — good.",
        "Scratch that.",
        "That's the wrong screen.",
    ],
)
def test_pre_existing_self_narration_still_caught(sentence):
    assert th._is_self_narration(sentence) is True
