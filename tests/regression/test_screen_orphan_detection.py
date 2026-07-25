"""Orphan screen detection — a screen Layer 2 asked that Layer 1 never armed.

Regression cover for the 16:20 call (2026-07-25, 7-call sweep): the caller
described an ankle problem with no mechanism of injury, so no trigger matched
and Layer 1 correctly stayed quiet — but the MODEL asked the trauma screen
anyway off the prompt's static protocol. pending_screen was never set, so the
SCREEN REQUIRED steer never drove and book_appointment's booking_blocked_reason()
backstop was inert. Nothing but the model's reading of a truncated half-sentence
stood between the caller and a booking, and nothing in the logs said so.

The negative cases matter as much as the positives here. The naive version of
this feature — running _question_was_asked unpinned across every screen —
matched the DVT screen on the two lead-in words 'before' and 'quickly' from a
bot turn that was asking about trauma. That would have graded the caller's
reply against DVT's red-flag keywords and could have fired a deterministic DVT
escalation for a screen nobody asked. test_leadin_does_not_match_wrong_screen
is that case; it must never be relaxed.
"""
from __future__ import annotations

import pytest

from app.media_streams import clinical_screening as cs


# ─────────────────────────────────────────────────────────────────────────
# Fixtures — the real jv_v1 screen questions, so the corpus-uniqueness brake
# is exercised against production config rather than a toy.
# ─────────────────────────────────────────────────────────────────────────
@pytest.fixture
def clinic():
    from app.clinic_config import get_clinic
    c = get_clinic("jv_v1")
    assert cs.screening_enabled(c), "jv_v1 clinical_screening must be enabled"
    return c


@pytest.fixture
def single_screen_clinic(clinic):
    """A clinic configured with ONE screen — no corpus to be unique against.

    This is the configuration where the corpus-uniqueness brake does nothing
    and the stoplist is the only thing standing between a lead-in phrase and a
    wrong-screen arm. Every cohort-1 clinic starts here.
    """
    dvt = cs.get_screen(clinic, "dvt")
    return {
        "clinical_screening": {"enabled": True, "screens": [dict(dvt)]}
    }


def _session(last_bot_prompt: str, **extra):
    s = {"last_bot_prompt": last_bot_prompt, "last_question": last_bot_prompt}
    s.update(extra)
    return s


# ─────────────────────────────────────────────────────────────────────────
# Positive — the model asked a screen, verbatim and paraphrased
# ─────────────────────────────────────────────────────────────────────────
def test_orphan_detected_on_verbatim_question(clinic):
    screen = cs.get_screen(clinic, "trauma_fracture")
    sess = _session(screen["screen_question"])
    assert cs.match_asked_screen(clinic, sess) == "trauma_fracture"


def test_orphan_detected_on_model_paraphrase(clinic):
    sess = _session(
        "Can I quickly check — are you able to put weight through it, "
        "and is there any swelling?"
    )
    assert cs.match_asked_screen(clinic, sess) == "trauma_fracture"


def test_orphan_arms_pending_and_grades_the_answer(clinic):
    """The whole point: pending_screen goes from unset to set, which is what
    makes booking_blocked_reason() apply on this path."""
    screen = cs.get_screen(clinic, "trauma_fracture")
    sess = _session(screen["screen_question"])
    assert sess.get(cs.PENDING_SCREEN_KEY) is None
    assert cs.booking_blocked_reason(sess, clinic) is None

    result = cs.update_screening_state(sess, clinic, "no it's all fine, no swelling")

    assert result["action"] == "none"
    assert "trauma_fracture" in sess[cs.SCREENS_COMPLETED_KEY]
    assert sess.get(cs.PENDING_SCREEN_KEY) is None  # cleared by a clear answer


def test_orphan_red_flag_answer_escalates_and_blocks_booking(clinic):
    """A positive answer to a screen Layer 1 never armed must still escalate
    deterministically and freeze booking — this is the path that was doing
    nothing at all."""
    screen = cs.get_screen(clinic, "trauma_fracture")
    sess = _session(screen["screen_question"])

    result = cs.update_screening_state(
        sess, clinic, "no I can't put weight on it and it looks out of shape"
    )

    assert result["action"] == "escalate"
    assert result["speak"]
    assert sess[cs.SCREEN_RED_FLAG_KEY] == "trauma_fracture"
    assert cs.booking_blocked_reason(sess, clinic) is not None


def test_orphan_unclear_answer_leaves_screen_pending(clinic):
    """An answer that resolves nothing must leave the flag set so the SCREEN
    REQUIRED steer re-drives the question and booking stays blocked."""
    screen = cs.get_screen(clinic, "cauda_equina")
    sess = _session(screen["screen_question"])

    result = cs.update_screening_state(sess, clinic, "sorry what do you mean")

    assert result["action"] == "none"
    assert sess[cs.PENDING_SCREEN_KEY] == "cauda_equina"
    assert "cauda_equina" not in (sess.get(cs.SCREENS_COMPLETED_KEY) or [])
    assert cs.booking_blocked_reason(sess, clinic) is not None


# ─────────────────────────────────────────────────────────────────────────
# Negative — the cases that make this safe to ship
# ─────────────────────────────────────────────────────────────────────────
def test_leadin_does_not_match_wrong_screen(clinic):
    """THE case. Verbatim from the 16:20 call, where the model was asking the
    TRAUMA screen. The naive matcher scored ['before', 'quickly'] against the
    DVT question and armed DVT. Do not relax this."""
    sess = _session("Before we look at getting that booked, can I quickly check —")
    assert cs.match_asked_screen(clinic, sess) is None


def test_leadin_does_not_match_on_a_single_screen_clinic(single_screen_clinic):
    """Same phrase, but with corpus-uniqueness unable to help — one screen
    means every word is unique. Only the stoplist is left, and it must hold."""
    sess = _session("Before we look at getting that booked, can I quickly check —")
    assert cs.match_asked_screen(single_screen_clinic, sess) is None


def test_booking_turn_does_not_match_inflammatory_screen(clinic):
    """'several' and 'morning' are both in the inflammatory screen question and
    both are ordinary booking vocabulary. Without the stoplist this is a match."""
    sess = _session("We have several morning slots this week — does one suit?")
    assert cs.match_asked_screen(clinic, sess) is None


def test_statement_turn_never_matches(clinic):
    """A screen is a question. A statement turn that happens to contain the
    vocabulary is not evidence one was asked."""
    screen = cs.get_screen(clinic, "vbi_neck")
    sess = _session(screen["screen_question"].replace("?", "."))
    assert cs.match_asked_screen(clinic, sess) is None


def test_completed_screen_is_not_regraded(clinic):
    """After a screen clears, its question is still sitting in last_bot_prompt
    for a turn. It must not be re-armed and re-graded."""
    screen = cs.get_screen(clinic, "dvt")
    sess = _session(
        screen["screen_question"], **{cs.SCREENS_COMPLETED_KEY: ["dvt"]}
    )
    assert cs.match_asked_screen(clinic, sess) is None


def test_no_bot_prompt_yields_nothing(clinic):
    assert cs.match_asked_screen(clinic, {}) is None
    assert cs.match_asked_screen(clinic, _session("")) is None


def test_word_boundary_respected(clinic):
    """_kw_in, not substring containment: 'doubled' is not 'double'."""
    sess = _session("Your physio has doubled up the visioning session — ok?")
    assert cs.match_asked_screen(clinic, sess) is None


# ─────────────────────────────────────────────────────────────────────────
# Ordering — the orphan guard must never pre-empt the paths above it
# ─────────────────────────────────────────────────────────────────────────
def test_emergency_still_pre_empts_orphan(clinic):
    screen = cs.get_screen(clinic, "trauma_fracture")
    sess = _session(screen["screen_question"])
    result = cs.update_screening_state(sess, clinic, "I've got chest pain")
    assert result["action"] == "emergency"


def test_trigger_still_wins_over_orphan(clinic):
    """An utterance that arms a screen properly must take the ARMED path, not
    be diverted into grading a different screen the model happened to ask.

    last_bot_prompt is the CAUDA question here, so the orphan matcher would
    return cauda_equina — but the utterance triggers trauma_fracture, and the
    trigger branch runs first. Deliberately not the trauma question: that hits
    the pre-existing double-ask guard instead, which is a different path (see
    test_double_ask_guard_still_owns_its_case)."""
    screen = cs.get_screen(clinic, "cauda_equina")
    sess = _session(screen["screen_question"])
    assert cs.match_asked_screen(clinic, sess) == "cauda_equina"

    result = cs.update_screening_state(sess, clinic, "I had a fall off my bike")

    assert result["action"] == "ask_screen"
    assert sess[cs.PENDING_SCREEN_KEY] == "trauma_fracture"


def test_double_ask_guard_still_owns_its_case(clinic):
    """Where the trigger hit AND the model already asked that same screen, the
    pre-existing double-ask guard grades the turn rather than re-asking. The
    orphan guard sits downstream of it and must not change this."""
    screen = cs.get_screen(clinic, "trauma_fracture")
    sess = _session(screen["screen_question"])

    result = cs.update_screening_state(sess, clinic, "I had a fall off my bike")

    assert result["action"] == "none"        # graded, not re-asked
    assert sess[cs.PENDING_SCREEN_KEY] == "trauma_fracture"


def test_pending_screen_is_never_overwritten_by_orphan(clinic):
    """A screen already pending owns the turn. The orphan matcher must not run."""
    screen = cs.get_screen(clinic, "dvt")
    sess = _session(
        screen["screen_question"], **{cs.PENDING_SCREEN_KEY: "cauda_equina"}
    )
    cs.update_screening_state(sess, clinic, "no nothing like that")
    assert sess.get(cs.PENDING_SCREEN_KEY) != "dvt"


def test_disabled_clinic_is_unaffected():
    sess = _session("Before we go further, can I quickly check — is the calf swollen?")
    result = cs.update_screening_state(sess, {}, "no it's fine")
    assert result == {"action": "none", "speak": None}


# ─────────────────────────────────────────────────────────────────────────
# Matcher invariants
# ─────────────────────────────────────────────────────────────────────────
def test_every_jv_v1_screen_is_detectable(clinic):
    """A screen left with fewer than the minimum evidence words can never be
    orphan-matched. That fails safe, but silently — assert the production
    config has not drifted into that state."""
    evidence = cs._screen_evidence_words(clinic)
    thin = {
        sid: sorted(w) for sid, w in evidence.items()
        if len(w) < cs._ORPHAN_MIN_EVIDENCE
    }
    assert not thin, f"screens with too few evidence words to detect: {thin}"


def test_every_jv_v1_screen_matches_its_own_question(clinic):
    for screen in clinic["clinical_screening"]["screens"]:
        sid = screen["id"]
        sess = _session(screen["screen_question"])
        assert cs.match_asked_screen(clinic, sess) == sid, sid
