"""A turn the endpointer cut mid-clause must not clear a clinical screen.

The 16:20 call, 2026-07-25:

    16:20:49.782  partial 'it fine and there's no marks'
    16:20:49.896  FINAL   'it fine and there's no marks where'

114 ms apart, cut mid-clause while the caller was still speaking.
classify_screen_answer read the fragment as a plain negative and cleared the
screen. Had the full sentence been "there's no marks where I can see, but it
does look out of shape", the truncation would have inverted the clinical
meaning — 'out of shape' is a configured trauma red flag.

Two properties carry the safety here and neither may be relaxed:

  * red_flag is NEVER downgraded. The guard only moves a verdict in the safe
    direction. test_red_flag_survives_truncation pins that.
  * the complete-answer corpus below must stay clean. A false positive re-asks
    a safety question the caller already answered, which is audible and
    demo-visible; the corpus is the brake on widening _OPEN_CLAUSE_TAIL.
"""
from __future__ import annotations

import pytest

from app.media_streams import clinical_screening as cs


# The literal transcript from the call, as AssemblyAI delivered it
# (format_turns=false, so no capitalisation and no terminal punctuation).
OBSERVED_TRUNCATED_FINAL = "it fine and there's no marks where"

# The sentence the caller may well have been part-way through. This is the
# whole reason the guard exists: the fragment above and this string carry
# opposite clinical meanings.
OBSERVED_FULL_SENTENCE = (
    "it fine and there's no marks where I can see, but it does look out of shape"
)


@pytest.fixture
def clinic():
    from app.clinic_config import get_clinic
    c = get_clinic("jv_v1")
    assert cs.screening_enabled(c)
    return c


def _asked(clinic, screen_id, **extra):
    """Session with `screen_id` armed and its question already asked."""
    q = cs.get_screen(clinic, screen_id)["screen_question"]
    s = {
        "last_bot_prompt": q,
        "last_question": q,
        cs.PENDING_SCREEN_KEY: screen_id,
    }
    s.update(extra)
    return s


# ─────────────────────────────────────────────────────────────────────────
# The detector
# ─────────────────────────────────────────────────────────────────────────
def test_observed_final_is_detected_as_truncated():
    assert cs._looks_truncated(OBSERVED_TRUNCATED_FINAL)


@pytest.mark.parametrize("text", [
    "no its fine and",
    "theres no numbness but my",
    "no I havent had any surgery or",
    "no its not swollen but it does",
    "no nothing like that although",
    "no its fine because I",
    "no marks where I can see but",
])
def test_mid_clause_cuts_are_detected(text):
    assert cs._looks_truncated(text), text


@pytest.mark.parametrize("text", [
    # plain negatives
    "no", "nope", "nah", "no nothing at all", "no nothing like that",
    "none of those", "no none of that",
    # negated auxiliaries — the commonest complete answer in the corpus
    "no I havent", "no I have not", "no I dont think so", "no I havent been",
    "no its not swollen or warm", "no it doesnt",
    # stranded prepositions — legitimate clause endings, NOT truncation
    "not that I know of", "nothing Im aware of", "nothing to speak of",
    "not that Ive noticed",
    # clause-final adverbs
    "not really", "no its fine though", "no not at all", "definitely not",
    "thankfully not",
    # ordinary complete sentences
    "its all fine", "everything is fine", "no swelling at all",
    "I can put weight on it", "no I can walk on it fine", "thats it",
    "no bladder or bowel problems", "no surgery no flights nothing",
])
def test_complete_answers_are_not_flagged(text):
    """The brake on widening _OPEN_CLAUSE_TAIL. A regression here re-asks a
    safety question the caller already answered."""
    assert not cs._looks_truncated(text), text


def test_every_configured_clear_answer_survives(clinic):
    """Whatever ends up in _NEGATIVE_PATTERNS must still be able to clear a
    screen in its natural full form."""
    for phrase in ("not that i know of", "nothing like that", "no changes",
                   "everything is fine", "i havent", "definitely not"):
        assert not cs._looks_truncated(phrase), phrase


def test_single_word_is_never_truncated():
    """A bare 'no' is the commonest complete answer there is; single-word
    debris is _is_junk_fragment's job, not this guard's."""
    assert not cs._looks_truncated("no")
    assert not cs._looks_truncated("and")
    assert not cs._looks_truncated("")


# ─────────────────────────────────────────────────────────────────────────
# The downgrade
# ─────────────────────────────────────────────────────────────────────────
def test_truncated_clear_does_not_clear_the_screen(clinic):
    """THE case. Before this guard the screen was marked completed and booking
    was unblocked on the strength of half a sentence."""
    sess = _asked(clinic, "trauma_fracture")

    result = cs.update_screening_state(sess, clinic, OBSERVED_TRUNCATED_FINAL)

    assert result["action"] == "none"
    assert sess[cs.PENDING_SCREEN_KEY] == "trauma_fracture"
    assert "trauma_fracture" not in (sess.get(cs.SCREENS_COMPLETED_KEY) or [])
    assert cs.booking_blocked_reason(sess, clinic) is not None


def test_untruncated_clear_still_clears(clinic):
    """The guard must not make every screen answer unclear."""
    sess = _asked(clinic, "trauma_fracture")

    cs.update_screening_state(sess, clinic, "no its all fine, no swelling")

    assert sess.get(cs.PENDING_SCREEN_KEY) is None
    assert "trauma_fracture" in sess[cs.SCREENS_COMPLETED_KEY]
    assert cs.booking_blocked_reason(sess, clinic) is None


def test_red_flag_survives_truncation(clinic):
    """Asymmetry. A truncated POSITIVE is still a positive — never softened."""
    sess = _asked(clinic, "trauma_fracture")

    result = cs.update_screening_state(
        sess, clinic, "it does look out of shape and"
    )

    assert result["action"] == "escalate"
    assert sess[cs.SCREEN_RED_FLAG_KEY] == "trauma_fracture"
    assert cs.booking_blocked_reason(sess, clinic) is not None
    # and the downgrade never ran
    assert not sess.get(cs.SCREEN_TRUNCATED_KEY)


def test_full_sentence_would_have_escalated(clinic):
    """What the fragment was hiding. The same turn, uncut, is a red flag —
    which is why clearing on the fragment was the serious defect."""
    sess = _asked(clinic, "trauma_fracture")

    result = cs.update_screening_state(sess, clinic, OBSERVED_FULL_SENTENCE)

    assert result["action"] == "escalate"
    assert sess[cs.SCREEN_RED_FLAG_KEY] == "trauma_fracture"


def test_downgrade_is_capped_at_once_per_screen(clinic):
    """Without the cap, a caller who habitually trails off is re-asked the
    same safety question forever."""
    sess = _asked(clinic, "trauma_fracture")

    cs.update_screening_state(sess, clinic, OBSERVED_TRUNCATED_FINAL)
    assert sess[cs.PENDING_SCREEN_KEY] == "trauma_fracture"
    assert sess[cs.SCREEN_TRUNCATED_KEY] == ["trauma_fracture"]

    # second truncated answer to the same screen — graded as it comes
    cs.update_screening_state(sess, clinic, "no its fine and")

    assert sess.get(cs.PENDING_SCREEN_KEY) is None
    assert "trauma_fracture" in sess[cs.SCREENS_COMPLETED_KEY]


def test_cap_is_per_screen_not_per_call(clinic):
    """One screen using up its re-ask must not disarm the guard for another."""
    sess = _asked(clinic, "trauma_fracture")
    cs.update_screening_state(sess, clinic, OBSERVED_TRUNCATED_FINAL)
    cs.update_screening_state(sess, clinic, "no its fine and")   # clears it

    # a second screen, armed later in the same call
    q = cs.get_screen(clinic, "cauda_equina")["screen_question"]
    sess["last_bot_prompt"] = sess["last_question"] = q
    sess[cs.PENDING_SCREEN_KEY] = "cauda_equina"

    cs.update_screening_state(sess, clinic, "no nothing and")

    assert sess[cs.PENDING_SCREEN_KEY] == "cauda_equina"
    assert sorted(sess[cs.SCREEN_TRUNCATED_KEY]) == [
        "cauda_equina", "trauma_fracture"
    ]


def test_advisory_screen_is_guarded_too(clinic):
    """block_booking=false screens are still safety screens; a truncated
    answer must not complete them either."""
    sess = _asked(clinic, "inflammatory")

    cs.update_screening_state(sess, clinic, "no its fine and")

    assert sess[cs.PENDING_SCREEN_KEY] == "inflammatory"
    assert "inflammatory" not in (sess.get(cs.SCREENS_COMPLETED_KEY) or [])


# ─────────────────────────────────────────────────────────────────────────
# Integration with the orphan path (2485229) — the actual 16:20 call
# ─────────────────────────────────────────────────────────────────────────
def test_orphaned_screen_with_truncated_answer_blocks_booking(clinic):
    """End-to-end reconstruction of the 16:20 call: no mechanism of injury so
    Layer 1 never armed, the model screened anyway, and the answer was cut
    mid-clause. Before these two commits this sequence cleared nothing,
    logged nothing, and left booking wide open."""
    q = cs.get_screen(clinic, "trauma_fracture")["screen_question"]
    sess = {"last_bot_prompt": q, "last_question": q}   # NB: no pending_screen

    assert cs.booking_blocked_reason(sess, clinic) is None

    result = cs.update_screening_state(sess, clinic, OBSERVED_TRUNCATED_FINAL)

    assert result["action"] == "none"
    assert sess[cs.PENDING_SCREEN_KEY] == "trauma_fracture"   # orphan armed it
    assert sess[cs.SCREEN_TRUNCATED_KEY] == ["trauma_fracture"]
    assert "trauma_fracture" not in (sess.get(cs.SCREENS_COMPLETED_KEY) or [])
    assert cs.booking_blocked_reason(sess, clinic) is not None


def test_logs_name_both_conditions(clinic, caplog):
    """Both events must be visible in the call log — the orphan and the
    truncation are separately worth alerting on."""
    import logging
    q = cs.get_screen(clinic, "trauma_fracture")["screen_question"]
    sess = {"last_bot_prompt": q, "last_question": q}

    with caplog.at_level(logging.WARNING, logger=cs.logger.name):
        cs.update_screening_state(sess, clinic, OBSERVED_TRUNCATED_FINAL)

    text = caplog.text
    assert "ORPHAN" in text
    assert "TRUNCATED" in text
