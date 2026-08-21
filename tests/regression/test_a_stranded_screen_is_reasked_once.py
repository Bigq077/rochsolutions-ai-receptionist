# tests/regression/test_a_stranded_screen_is_reasked_once.py
"""
A safety screen could be left pending for the rest of the call, with no way for
any later caller turn to resolve it.

_question_was_asked grades a pending screen only while the last thing Susie said
still looks like the screen question. So ONE ungradable answer strands it: the
model replies, last_bot_prompt becomes that reply, and from that point no caller
turn can ever resolve the screen. pending_screen stays set for the rest of the
call.

The old code fell straight through on the assumption that "the SCREEN REQUIRED
steer forces it on the next model turn". It does not force WHEN. Measured over
the stored JV corpus with scripts/replay_screening.py, a stranded screen is what
produces the screening question surfacing at an arbitrary later moment —
call CAb0ff51e012, where the caller had just chosen an appointment slot:

    caller: "that that first one works great"
    Susie : "Right, before we go any further — can I ask, do you have any
             numbness around the saddle area between your legs, or any changes
             in your bladder or bowel control?"

and call CA2a020cfcd2, where a rolled ankle drew a fragment of the DVT screen:

    caller: "um just my left ankle nothing serious just rolled it yesterday at
             the gym want to get an assessment as soon as"
    Susie : "Or a long journey sitting still?"

Both read as random and alarming to the caller. Neither is the model
misbehaving: it is covering for a flag this layer left set and never cleared.

The fix asks the question again HERE — once, deterministically, in the screen's
own words, on the first turn after the answer window shut — instead of leaving
the model to raise it at a moment of its choosing.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams.clinical_screening import (
    PENDING_SCREEN_KEY,
    SCREEN_REASKS_KEY,
    _screens,
    update_screening_state,
)

ARMING = "ive got really bad back pain"
# Not gradable: no red-flag keyword, no negative, no affirmative lead.
UNGRADABLE = "wait wait wait"


def _clinic():
    return get_clinic("jv_v1")


def _question(screen_id: str = "cauda_equina") -> str:
    scr = next(s for s in _screens(_clinic()) if s.get("id") == screen_id)
    return scr["screen_question"]


def _say(session: dict, text: str) -> None:
    """Record that Susie said something — this closes/opens the answer window."""
    session["last_bot_prompt"] = text[:200]
    session["last_question"] = text[:200]


def _strand(session: dict) -> dict:
    """Arm a screen, fail to grade its answer, then have Susie move on."""
    clinic = _clinic()
    armed = update_screening_state(session, clinic, ARMING)
    assert armed["action"] == "ask_screen"
    _say(session, _question())
    graded = update_screening_state(session, clinic, UNGRADABLE)
    assert graded["action"] == "none"
    assert session[PENDING_SCREEN_KEY] == "cauda_equina", "should still be pending"
    # Susie says anything else — this is what shuts the window.
    _say(session, "Right, do you have a preference for when you would like to come in?")
    return session


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------

def test_a_stranded_screen_is_reasked_deterministically():
    session = _strand({})
    result = update_screening_state(session, _clinic(), "um what have you got thursday")

    assert result["action"] == "ask_screen", (
        "the screen was pending and ungradable; leaving it to the model is how "
        "it resurfaces mid-booking"
    )
    assert "numbness around the saddle area" in result["speak"]


def test_the_reask_does_not_stack_two_lead_ins():
    """
    Every screen_question carries its own preamble. Without the bare
    screen_reask_question the caller hears "...before we go on. Before we look
    at the next step, can I ask —".
    """
    session = _strand({})
    spoken = update_screening_state(session, _clinic(), "um thursday")["speak"]
    assert "Before we look at the next step" not in spoken
    assert spoken.count("can I ask") <= 1


def test_the_reask_is_capped_at_once_per_screen():
    """
    A caller who cannot be graded twice is not one to keep interrogating. The
    flag stays set (booking stays blocked), but Susie stops asking.
    """
    session = _strand({})
    first = update_screening_state(session, _clinic(), "um what have you got thursday")
    assert first["action"] == "ask_screen"
    _say(session, "Let me check that for you.")

    second = update_screening_state(session, _clinic(), "um thursday please")
    assert second["action"] == "none", "must not re-ask a second time"
    assert session[SCREEN_REASKS_KEY] == ["cauda_equina"]
    # Still pending, so booking is still blocked — the safe direction.
    assert session[PENDING_SCREEN_KEY] == "cauda_equina"


def test_the_reask_answer_is_graded_normally():
    """The re-ask must reopen the answer window, or it achieves nothing."""
    session = _strand({})
    spoken = update_screening_state(session, _clinic(), "um thursday")["speak"]
    _say(session, spoken)

    result = update_screening_state(session, _clinic(), "er yeah i do")
    assert result["action"] == "escalate"
    assert session[PENDING_SCREEN_KEY] is None


# ---------------------------------------------------------------------------
# It must not fire when the screen is being handled normally
# ---------------------------------------------------------------------------

def test_no_reask_while_the_answer_window_is_still_open():
    session: dict = {}
    update_screening_state(session, _clinic(), ARMING)
    _say(session, _question())
    # The caller answers straight away: graded, not re-asked.
    result = update_screening_state(session, _clinic(), "no none of those")
    assert result["action"] == "none"
    assert session[PENDING_SCREEN_KEY] is None
    assert not session.get(SCREEN_REASKS_KEY)


def test_a_cleared_screen_is_never_reasked():
    session: dict = {}
    update_screening_state(session, _clinic(), ARMING)
    _say(session, _question())
    update_screening_state(session, _clinic(), "no none of those")
    _say(session, "That is reassuring. When would suit you?")

    for utterance in ("thursday please", "yeah that works", "go for it"):
        assert update_screening_state(
            session, _clinic(), utterance)["action"] == "none"


def test_an_emergency_still_preempts_a_stranded_screen():
    session = _strand({})
    result = update_screening_state(session, _clinic(), "ive got chest pain")
    assert result["action"] == "emergency"


@pytest.mark.parametrize("screen_id", [
    "cauda_equina", "dvt", "serious_spinal",
    "trauma_fracture", "vbi_neck", "inflammatory",
])
def test_every_screen_has_a_bare_reask_question(screen_id):
    """
    Falling back to screen_question is safe but stacks preambles. Every screen
    carries its own, and it must stay clinically identical — a re-ask is not an
    opportunity to soften the question.
    """
    scr = next(s for s in _screens(_clinic()) if s.get("id") == screen_id)
    reask = scr.get("screen_reask_question")
    assert reask, f"{screen_id} has no screen_reask_question"
    assert reask.endswith("?")
    assert "can I ask" not in reask and "Before we" not in reask
