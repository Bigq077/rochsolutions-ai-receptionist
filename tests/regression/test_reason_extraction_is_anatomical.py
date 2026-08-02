"""B-23 step 1 — the reason extractor only captures anatomy, and fails open when unsure.

`_extract_reason` matched BARE body-part words. Two entries in `_BODY_PARTS` are
also ordinary English: "back" and "arm". Measured against 967 stored caller turns
from obs, "back" collided once — *"hi can you call me back later"* captured
`reason='you call me back later'`.

That is currently harmless only because nothing on the template_v1 path calls
this module. It stops being harmless the moment it is wired in: the captured
reason picks the SERVICE (jv_v1 has ten, 30-60 minutes) and can satisfy
`book_appointment`'s reason guard by accident.

The clinical screening config solved this exact problem and did not use bare
words — `cauda_equina` keys on "my back", "back pain", "sore back", never "back".
These tests pin that same discipline here.

Utterances below are written by hand, modelled on shapes seen in the corpus.
Real transcripts are health-adjacent personal data and do not belong in the repo.

WHAT IS DELIBERATELY NOT GUARDED: a third-party complaint ("my son hurt his
ankle"). `extract_first_turn_signals` already answers "whose complaint is this?"
via `first_turn_patient_is_caller`, and the child policy gate needs the reason
for a paediatric booking. Attribution is a consumer question, not an extraction
one — asserted at the bottom so it is not "fixed" again.
"""
import pytest

from app.media_streams.first_turn_extractor import (
    _extract_reason,
    extract_first_turn_signals,
)


# ── The defect: an ambiguous word used non-anatomically ─────────────────────

@pytest.mark.parametrize("utterance", [
    "hi can you call me back later",
    "could you ring me back later please",
    "i'll get back to you",
    "can you call me back tomorrow",
    "i'll call back when i know my shifts",
])
def test_back_as_an_adverb_is_not_a_complaint(utterance):
    assert _extract_reason(utterance) is None, utterance


@pytest.mark.parametrize("utterance", [
    "the alarm went off and i missed the appointment",
    "we can disarm that concern",
])
def test_arm_inside_another_word_is_not_a_complaint(utterance):
    assert _extract_reason(utterance) is None, utterance


# ── The true positives that must survive ────────────────────────────────────

@pytest.mark.parametrize("utterance", [
    "my back has been sore for a fortnight",
    "i've got lower back pain",
    "my lower back is killing me",
    "i've done my back in",
    "bad back again i'm afraid",
])
def test_an_anatomical_back_is_still_captured(utterance):
    got = _extract_reason(utterance)
    assert got and "back" in got.lower(), f"{utterance!r} -> {got!r}"


@pytest.mark.parametrize("utterance,part", [
    ("i'd like to book an appointment for my knee", "knee"),
    ("i'm looking to book for shoulder pain", "shoulder"),
    ("i've done my ankle playing football", "ankle"),
    ("i hurt my arm at the gym", "arm"),
    ("my elbow's been giving me trouble", "elbow"),
])
def test_unambiguous_body_parts_are_untouched(utterance, part):
    """These were never at risk. Narrowing them would cost captures for nothing,
    so the hardening must leave them exactly as they were."""
    got = _extract_reason(utterance)
    assert got and part in got.lower(), f"{utterance!r} -> {got!r}"


def test_a_complaint_still_wins_when_the_turn_also_says_call_back():
    """"back" is adverbial here, but the knee is a real complaint — the guard
    must drop the word, not the whole utterance."""
    got = _extract_reason("i'll call you back about my knee")
    assert got and "knee" in got.lower(), got


# ── "back of my <part>" is positional, and it is the DVT presentation ───────

def test_back_of_my_legs_captures_the_legs_not_the_back():
    """The anatomy is the legs. Reading "back" as the anatomical back windowed
    onto 'the back of my' and lost the leg entirely — and this exact phrasing is
    the DVT screen's presentation, so it is the last capture to lose."""
    got = _extract_reason("the back of my legs is warm and swollen")
    assert got is not None
    assert "leg" in got.lower(), got


# ── Fail open: better to ask than to guess ──────────────────────────────────

@pytest.mark.parametrize("utterance", [
    "my knee and my ankle are both sore",
    "i've got shoulder pain and my hip is stiff",
])
def test_two_complaints_capture_nothing(utterance):
    """Picking the first-mentioned is a coin toss. An extra question costs a
    turn; a wrong reason picks the wrong service."""
    assert _extract_reason(utterance) is None, utterance


@pytest.mark.parametrize("utterance", [
    "not my knee it's my hip",
    "it isn't my shoulder it's my neck",
])
def test_an_explicit_correction_captures_nothing(utterance):
    assert _extract_reason(utterance) is None, utterance


def test_a_greeting_with_no_complaint_captures_nothing():
    assert _extract_reason("hi i'd like to book an appointment please") is None


# ── Pass 2 is deliberately unchanged ────────────────────────────────────────

def test_an_injury_verb_still_captures_when_stt_mangles_the_body_part():
    """The corpus has "my call's been very sore" — calf, mis-heard. The
    injury-verb pass anchors on the symptom rather than on a word that might not
    be anatomy, and it is what keeps those callers' reasons."""
    got = _extract_reason("my call's been very sore lately")
    assert got and "sore" in got.lower(), got


# ── The guard that must NOT be added ────────────────────────────────────────

def test_a_third_party_complaint_is_still_captured():
    """B-23's plan proposed failing open here. That was wrong.

    extract_first_turn_signals already reports whose complaint it is, and the
    child policy gate needs the reason for a paediatric booking. Two long-standing
    tests (test_ankle_body_part, test_booking_plus_child) assert this. Attribution
    belongs to the consumer, which must read first_turn_patient_is_caller
    alongside the reason.
    """
    got = _extract_reason("my son hurt his ankle playing football")
    assert got and "ankle" in got.lower(), got

    signals = extract_first_turn_signals("my son hurt his ankle playing football")
    assert signals["first_turn_patient_is_caller"] is False
    assert signals["first_turn_reason_captured"] is True
