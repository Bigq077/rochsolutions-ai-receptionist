"""B-31 — the orphan detector must survive last_bot_prompt's 200-char cap.

Sweep call 2 (CA2ada6263, 2026-08-02 21:47). The caller had an ankle injury.
Layer 1 correctly armed nothing. The MODEL asked a full DVT screen anyway —
the B-20 over-screening defect — and phrased it in **205 characters**:

    Before we look at getting you booked in, can I quickly check - is the
    ankle swollen, warm or red compared with the other side, and have you
    had any recent surgery, illness, or a long journey sitting still?

llm_stream.py stores session["last_bot_prompt"] truncated to 200. The '?' was
character 205. match_asked_screen's cheap "a screen is a QUESTION" test saw no
'?' in the stored 200 and returned None — **silently, with no log line of any
kind**. The caller answered "i had a long journey sitting still", which is in
dvt.red_flag_answer_keywords verbatim, and nothing graded it. Susie said
"That's reassuring" and booked the appointment.

Replayed offline through these same functions, the layer returns
action='escalate', block=True, NHS 111. Five characters of model
throat-clearing switched the clinical safety layer off for a whole call.

Two things this file pins, and they pull in opposite directions:

  * a TRUNCATED question must still be detected (the defect), by falling back
    to last_question, which is not truncated; and
  * that fallback must NOT reach for a stale last_question when one of
    connection.py's ~20 short deterministic writers (fillers, keypad prompts)
    has overwritten last_bot_prompt. Those are nowhere near the cap, which is
    why the fallback is length-gated rather than unconditional.

test_the_fallback_does_not_reach_a_stale_last_question is the second half.
Relaxing it turns this fix into a source of false NHS 111 escalations.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.media_streams import clinical_screening as cs
from tests.screening_fixture import screening_clinic


# Deliberately a local literal, NOT cs._LAST_BOT_PROMPT_CAP.
#
# Every test here must be runnable against the PARENT commit, where that
# constant does not exist. Reaching for it would make the whole file die with
# AttributeError on the parent — which proves nothing about the defect and,
# worse, would let the preserved-invariant tests below "fail" there for a
# reason that has nothing to do with what they assert. (Learned the hard way
# on B-25 two hours earlier.) test_the_module_constant_matches_this_file wires
# the two together; it is the only test allowed to touch the constant.
CAP = 200

# The model's paraphrase from sweep call 2, character for character as stored
# in obs. Its length is the whole finding — do not reflow it.
CALL_2_QUESTION = (
    "Before we look at getting you booked in, can I quickly check — is the "
    "ankle swollen, warm or red compared with the other side, and have you "
    "had any recent surgery, illness, or a long journey sitting still?"
)

# The caller's reply. "long journey" is a dvt red-flag keyword verbatim.
CALL_2_ANSWER = (
    "um i mean i had a long journey sitting still but i don't think that's "
    "why it's a problem i'm kind of confused just just want to get assessed "
    "mate i don't know why you're asking me this question"
)


@pytest.fixture
def clinic():
    from app.clinic_config import get_clinic
    c = screening_clinic()
    assert cs.screening_enabled(c), "jv_v1 clinical_screening must be enabled"
    return c


def _truncated_session(full_reply: str, **extra):
    """The session as connection.py actually sees it after an LLM turn.

    last_bot_prompt is capped; last_question holds the extracted question
    sentence and is NOT capped. Mirrors llm_stream.py's two writes.
    """
    from app.media_streams.llm_stream import _question_from_response
    s = {
        "last_bot_prompt": full_reply[:CAP],
        "last_question": _question_from_response(full_reply),
    }
    s.update(extra)
    return s


# ─────────────────────────────────────────────────────────────────────────
# The defect
# ─────────────────────────────────────────────────────────────────────────
def test_the_call_2_question_is_over_the_cap():
    """The premise. If this ever fails the reproduction has drifted and every
    other test in this file is testing something else."""
    assert len(CALL_2_QUESTION) == 205
    assert len(CALL_2_QUESTION) > CAP
    assert "?" not in CALL_2_QUESTION[:CAP]


def test_the_truncated_call_2_question_is_still_detected(clinic):
    """THE fails-before case. On the parent this returns None."""
    sess = _truncated_session(CALL_2_QUESTION)
    assert "?" not in sess["last_bot_prompt"]
    assert cs.match_asked_screen(clinic, sess) == "dvt"


def test_the_truncated_screen_escalates_and_blocks_the_booking(clinic):
    """What the caller should have got. The whole cost of B-31 is that this
    did not happen on a real call."""
    sess = _truncated_session(CALL_2_QUESTION)

    result = cs.update_screening_state(sess, clinic, CALL_2_ANSWER)

    assert result["action"] == "escalate"
    assert result["speak"]
    assert sess[cs.SCREEN_RED_FLAG_KEY] == "dvt"
    assert sess[cs.SCREEN_ARM_PATHS_KEY]["dvt"] == cs.ARM_ORPHAN
    assert cs.booking_blocked_reason(sess, clinic) is not None


def test_one_character_over_the_cap_is_enough_to_lose_the_question(clinic):
    """Length-driven, not paraphrase-driven: the defect is arithmetic, so pin
    it arithmetically rather than trusting one sentence to stay 205 chars."""
    dvt = cs.get_screen(clinic, "dvt")
    q = dvt["screen_question"]
    pad = "Right, so before we get you booked in for that, "
    while len(pad + q) <= CAP:
        pad += "just one moment - "
    over = pad + q
    assert len(over) > CAP
    assert "?" not in over[:CAP]

    assert cs.match_asked_screen(clinic, _truncated_session(over)) == "dvt"


# ─────────────────────────────────────────────────────────────────────────
# The half that keeps the fix from becoming a false-escalation source.
# All of these pass on the parent commit too — that is what makes them
# invariants rather than new behaviour.
# ─────────────────────────────────────────────────────────────────────────
def test_the_fallback_does_not_reach_a_stale_last_question(clinic):
    """THE safety case. A filler overwrites last_bot_prompt without touching
    last_question, so a screening question from an earlier turn is still
    sitting there. A short bot turn must never resurrect it.

    Without the length gate this arms DVT off a filler and escalates a caller
    who was never asked anything. Do not relax this.
    """
    sess = {
        "last_bot_prompt": "Just locking that in now...",
        "last_question": CALL_2_QUESTION,
    }
    assert len(sess["last_bot_prompt"]) < CAP
    assert cs.match_asked_screen(clinic, sess) is None


def test_a_truncated_turn_with_no_question_anywhere_matches_nothing(clinic):
    """Truncated AND genuinely not a question — last_question is '' because
    _question_from_response returns '' for a statement-only reply."""
    dvt = cs.get_screen(clinic, "dvt")
    statement = ("So here is what I have got for you. " + dvt["screen_question"]).replace("?", ".")
    sess = _truncated_session(statement)
    assert sess["last_question"] == ""
    assert cs.match_asked_screen(clinic, sess) is None


def test_a_short_statement_turn_still_matches_nothing(clinic):
    """The pre-existing statement guard is untouched below the cap."""
    dvt = cs.get_screen(clinic, "dvt")
    sess = {
        "last_bot_prompt": dvt["screen_question"].replace("?", "."),
        "last_question": "",
    }
    assert cs.match_asked_screen(clinic, sess) is None


def test_a_completed_screen_is_not_regraded_via_the_fallback(clinic):
    """The completed-screen guard sits downstream of the source selection and
    must still hold when the question arrives through the fallback."""
    sess = _truncated_session(
        CALL_2_QUESTION, **{cs.SCREENS_COMPLETED_KEY: ["dvt"]}
    )
    assert cs.match_asked_screen(clinic, sess) is None


def test_an_empty_session_still_yields_nothing(clinic):
    assert cs.match_asked_screen(clinic, {}) is None
    assert cs.match_asked_screen(
        clinic, {"last_bot_prompt": "", "last_question": ""}
    ) is None


def test_every_screen_still_matches_its_own_untruncated_question(clinic):
    """The ordinary path is unchanged. Every configured question is under the
    cap, so none of them go anywhere near the new branch."""
    for screen in clinic["clinical_screening"]["screens"]:
        sid = screen["id"]
        q = screen["screen_question"]
        sess = {"last_bot_prompt": q, "last_question": q}
        assert cs.match_asked_screen(clinic, sess) == sid, sid


# ─────────────────────────────────────────────────────────────────────────
# Why only a paraphrase can overrun — and the cap this all depends on
# ─────────────────────────────────────────────────────────────────────────
def test_no_configured_screen_question_exceeds_the_cap(clinic):
    """Layer 1's own questions are 150-185 chars, so they never truncate. That
    is why B-31 fires only on the Layer 2 orphan path. If config drifts past
    the cap, Layer 1's own screens become droppable too — fail loudly here."""
    over = {
        s["id"]: len(s["screen_question"])
        for s in clinic["clinical_screening"]["screens"]
        if len(s["screen_question"]) > CAP
    }
    assert not over, f"screen questions past the {CAP}-char cap: {over}"


def test_the_module_constant_matches_this_file():
    """The one test allowed to touch cs._LAST_BOT_PROMPT_CAP — see the note on
    CAP above. It wires this file's literal to the module's, so the two cannot
    drift apart while every other test stays runnable on the parent."""
    assert cs._LAST_BOT_PROMPT_CAP == CAP


def test_the_cap_constant_still_matches_llm_stream():
    """The cap is a literal in another module and a copy here. If the real cap
    moves and the copy does not, the length gate silently stops recognising
    truncation — which is B-31 again, wearing a hat."""
    src = Path(cs.__file__).with_name("llm_stream.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert f"[:{CAP}]" in src, (
        f"llm_stream.py no longer truncates at {CAP} — "
        "update clinical_screening._LAST_BOT_PROMPT_CAP to match"
    )


# ─────────────────────────────────────────────────────────────────────────
# Visibility. B-31's real cost was not a wrong answer, it was an empty log:
# "found nothing", "never ran" and "ran and was suppressed" were the same.
# ─────────────────────────────────────────────────────────────────────────
def test_the_truncation_fallback_is_logged(clinic, caplog):
    with caplog.at_level(logging.WARNING, logger=cs.__name__):
        cs.match_asked_screen(clinic, _truncated_session(CALL_2_QUESTION))
    assert any(
        "truncated" in r.getMessage() and "B-31" in r.getMessage()
        for r in caplog.records
    ), "a suppressed-then-recovered screen must leave a trace"


def test_a_near_miss_is_logged(clinic, caplog):
    """One evidence word is below threshold and correctly arms nothing — but
    silence there is what let B-31 hide. Say so."""
    sess = {
        "last_bot_prompt": "Have you had any recent problems with it?",
        "last_question": "Have you had any recent problems with it?",
    }
    with caplog.at_level(logging.INFO, logger=cs.__name__):
        assert cs.match_asked_screen(clinic, sess) is None
    assert any("NEAR MISS" in r.getMessage() for r in caplog.records)


def test_a_clean_match_logs_no_near_miss(clinic, caplog):
    """The near-miss line must not fire when a screen actually armed, or it
    becomes noise on every screening call."""
    dvt = cs.get_screen(clinic, "dvt")
    sess = {
        "last_bot_prompt": dvt["screen_question"],
        "last_question": dvt["screen_question"],
    }
    with caplog.at_level(logging.INFO, logger=cs.__name__):
        assert cs.match_asked_screen(clinic, sess) == "dvt"
    assert not any("NEAR MISS" in r.getMessage() for r in caplog.records)


def test_an_ordinary_booking_turn_logs_nothing(clinic, caplog):
    """The common case must stay quiet. A near-miss line on every turn would
    be the same failure as no line at all."""
    sess = {
        "last_bot_prompt": "Could I take your first name and surname?",
        "last_question": "Could I take your first name and surname?",
    }
    with caplog.at_level(logging.INFO, logger=cs.__name__):
        assert cs.match_asked_screen(clinic, sess) is None
    assert not any("NEAR MISS" in r.getMessage() for r in caplog.records)


def test_a_truncated_turn_with_no_fallback_is_logged_too(clinic, caplog):
    """B-31's real cost was an empty log, and one path still had one.

    Truncated, the '?' gone, and `last_question` carrying no question either:
    nothing can be recovered, so `match_asked_screen` returns None -- which is
    correct. It used to do so in total silence, which is the state B-31 says is
    indistinguishable from "never ran". A screen that cannot be graded is
    exactly the thing an operator needs to see.
    """
    sess = {
        "last_bot_prompt": "x" * cs._LAST_BOT_PROMPT_CAP,   # no '?'
        "last_question": "Right, let me get that sorted for you.",  # no '?'
    }
    with caplog.at_level(logging.WARNING, logger=cs.__name__):
        assert cs.match_asked_screen(clinic, sess) is None
    assert any(
        "CANNOT be orphan-matched" in r.getMessage() and "B-31" in r.getMessage()
        for r in caplog.records
    ), "a screen that cannot be graded must leave a trace"


def test_a_short_turn_with_no_question_stays_quiet(clinic, caplog):
    """The new line must fire on TRUNCATION, not on every statement turn.

    connection.py has ~20 short deterministic writers (fillers, keypad
    prompts). A warning on each of those is the same failure as no warning at
    all -- it is why the fallback is gated on the length in the first place.
    """
    sess = {
        "last_bot_prompt": "Right, let me get that sorted for you.",
        "last_question": "",
    }
    with caplog.at_level(logging.WARNING, logger=cs.__name__):
        assert cs.match_asked_screen(clinic, sess) is None
    assert not any("orphan-matched" in r.getMessage() for r in caplog.records)
