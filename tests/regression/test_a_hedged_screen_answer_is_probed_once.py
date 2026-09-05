# tests/regression/test_a_hedged_screen_answer_is_probed_once.py
"""
A hedged answer to a safety screen is graded, not handed to the model.

"I think so", "a bit", "sort of", "on and off" is the commonest honest answer
there is to a frightening clinical question, and every one of them used to
classify as `unclear`. `unclear` leaves the screen pending, which is not a
silent pass — booking stays blocked and the SCREEN REQUIRED steer re-drives the
question — but it does mean no deterministic escalation ever fires and the
safety decision falls to whatever the LLM happens to do with it. That is the
same asymmetry the affirmative branch was added to fix (B-74,
test_a_plain_yes_flags_a_red_flag_screen), one step further along.

Owner decision 2026-08-21: a hedge earns ONE narrowing question naming the
specific symptom; if the answer to THAT is not a clean no, escalate. Escalating
a hedge outright would send people to A&E over a sore back and teach the clinic
to ignore the alert.

Two things this file pins that are easy to break:

1. The promotion to red_flag must happen ABOVE the red_flag branch in
   _resolve_screen_answer. Written below it (as it first was), the promotion
   assigns a local nothing reads: the call falls through to the `unclear`
   return, so no escalation is spoken, the screen is never completed and
   SCREEN_RED_FLAG_KEY is never set — booking is NOT frozen. The action
   assertions below fail on that ordering; the verdict alone would not.

2. "sometimes" is a DE-escalation. It used to live in _AFFIRMATIVE_LEAD and
   return red_flag outright. Answering "sometimes" to "do you get dizziness
   when you move your neck" is a hedge, not a confirmation, so it now earns the
   probe instead. Pinned explicitly so the move is deliberate, not accidental.
"""
from __future__ import annotations

import re

import pytest

from app.clinic_config import get_clinic
from app.media_streams import clinical_screening as cs
from tests.screening_fixture import screening_clinic

_SCREEN_IDS = (
    "cauda_equina", "dvt", "serious_spinal",
    "trauma_fracture", "vbi_neck", "inflammatory",
)

# Every one of these classified as `unclear` before this fix.
_HEDGES = (
    "i think so", "a bit", "sort of", "kind of", "on and off",
    "maybe", "now and then", "occasionally", "possibly",
    "not sure", "could be", "i guess", "a little", "once or twice",
)


@pytest.fixture
def jv():
    return screening_clinic()


def _screen(jv, sid):
    screen = cs.get_screen(jv, sid)
    if screen is None:
        pytest.skip(f"{sid} not configured for jv_v1")
    return screen


# ── the classifier ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
@pytest.mark.parametrize("answer", _HEDGES)
def test_a_hedge_is_graded_as_hedged_not_unclear(jv, screen_id, answer):
    assert cs.classify_screen_answer(answer, _screen(jv, screen_id)) == "hedged"


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_sometimes_is_a_hedge_not_a_confirmation(jv, screen_id):
    """Deliberate de-escalation — see the module docstring."""
    assert cs.classify_screen_answer("sometimes", _screen(jv, screen_id)) == "hedged"
    assert "sometimes" not in cs._AFFIRMATIVE_LEAD


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_hedging_never_softens_a_yes_or_a_no(jv, screen_id):
    """The hedge branch runs after the affirmative and negative branches, so it
    can only ever capture what would otherwise have been `unclear`."""
    screen = _screen(jv, screen_id)
    for yes in ("yes", "yeah", "er yeah i do", "definitely"):
        assert cs.classify_screen_answer(yes, screen) == "red_flag", yes
    for no in ("no", "nope", "no not at all", "none of those"):
        assert cs.classify_screen_answer(no, screen) == "clear", no


# ── the resolver ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_every_screen_configures_a_narrowing_question(jv, screen_id):
    """Without one the hedge falls back to `unclear` and this layer is inert."""
    screen = _screen(jv, screen_id)
    probe = (screen.get("screen_probe_question") or "").strip()
    assert probe, f"{screen_id} has no screen_probe_question"
    assert probe != (screen.get("screen_question") or "").strip(), (
        f"{screen_id} probe just repeats the question the caller already hedged at"
    )


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_first_hedge_asks_the_narrowing_question(jv, screen_id):
    screen = _screen(jv, screen_id)
    session: dict = {}
    result = cs._resolve_screen_answer(session, jv, screen_id, screen, "i think so")
    assert result["action"] == "ask_screen"
    assert result["speak"] == screen["screen_probe_question"]
    assert session[cs.SCREEN_HEDGE_PROBES_KEY] == [screen_id]
    # Still pending: the screen is not resolved by a hedge.
    assert session.get(cs.SCREEN_RED_FLAG_KEY) is None
    assert screen_id not in (session.get(cs.SCREENS_COMPLETED_KEY) or [])


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_a_second_hedge_escalates_and_freezes_booking(jv, screen_id):
    """This is the assertion that catches the branch-ordering bug."""
    screen = _screen(jv, screen_id)
    session: dict = {}
    cs._resolve_screen_answer(session, jv, screen_id, screen, "i think so")
    result = cs._resolve_screen_answer(session, jv, screen_id, screen, "a bit")

    assert result["action"] == "escalate", (
        "a hedge repeated after the narrowing question must escalate; if this "
        "reads 'none', the hedge->red_flag promotion has been moved below the "
        "red_flag branch and now does nothing"
    )
    assert result["speak"]
    assert session[cs.PENDING_SCREEN_KEY] is None
    assert screen_id in session[cs.SCREENS_COMPLETED_KEY]
    if screen.get("block_booking", True):
        assert session[cs.SCREEN_RED_FLAG_KEY] == screen_id, (
            "booking was not frozen — the escalation was spoken but the caller "
            "can still book"
        )


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_an_ungradable_answer_to_the_probe_escalates(jv, screen_id):
    """Asked twice and still not gradable is not someone to book in unscreened."""
    screen = _screen(jv, screen_id)
    session: dict = {}
    cs._resolve_screen_answer(session, jv, screen_id, screen, "a bit")
    result = cs._resolve_screen_answer(
        session, jv, screen_id, screen, "well the thing is my brother had that",
    )
    assert result["action"] == "escalate"


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_a_clean_no_to_the_probe_still_clears(jv, screen_id):
    """The probe must remain escapable — otherwise it is just a slower escalation."""
    screen = _screen(jv, screen_id)
    session: dict = {}
    cs._resolve_screen_answer(session, jv, screen_id, screen, "i think so")
    result = cs._resolve_screen_answer(
        session, jv, screen_id, screen, "no, nothing like that",
    )
    assert result["action"] == "none"
    assert session.get(cs.SCREEN_RED_FLAG_KEY) is None
    assert screen_id in session[cs.SCREENS_COMPLETED_KEY]


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_the_probe_is_asked_at_most_once_per_screen(jv, screen_id):
    """A habitually vague caller must never be probed in a loop.

    Same cap shape as the truncation guard (SCREEN_TRUNCATED_KEY).
    """
    screen = _screen(jv, screen_id)
    session: dict = {}
    actions = [
        cs._resolve_screen_answer(session, jv, screen_id, screen, a)["action"]
        for a in ("a bit", "sort of", "maybe")
    ]
    assert actions[0] == "ask_screen"
    assert actions.count("ask_screen") == 1, actions
    assert session[cs.SCREEN_HEDGE_PROBES_KEY] == [screen_id]


@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_an_unconfigured_probe_falls_back_to_pending_not_escalation(jv, screen_id):
    """A screen with no narrowing question must not escalate on a single "a bit".

    Falling back to `unclear` keeps the pre-hedge behaviour: the screen stays
    pending, so booking stays blocked and the question is re-driven.
    """
    screen = dict(_screen(jv, screen_id))
    screen.pop("screen_probe_question", None)
    session: dict = {cs.PENDING_SCREEN_KEY: screen_id}
    result = cs._resolve_screen_answer(session, jv, screen_id, screen, "a bit")
    assert result["action"] == "none"
    assert session[cs.PENDING_SCREEN_KEY] == screen_id
    assert session.get(cs.SCREEN_RED_FLAG_KEY) is None


# ── wording discipline, same rules as the other caller-facing screen text ─────

@pytest.mark.parametrize("screen_id", _SCREEN_IDS)
def test_the_probe_is_answerable_yes_or_no(jv, screen_id):
    """A "yes" to the probe must mean CONCERNING.

    classify_screen_answer maps affirmatives to red_flag, so a probe phrased so
    that "yes" is the reassuring answer would escalate exactly the callers it is
    meant to release. An open question ("how long does it last?") grades
    `unclear`, which after a probe escalates — so those are wrong too.
    """
    probe = _screen(jv, screen_id)["screen_probe_question"].lower()
    assert probe.rstrip().endswith("?"), probe
    # A yes/no interrogative, not an open one. Checked positively by looking for
    # an interrogative auxiliary: a "does it start with a wh-word" heuristic
    # misreads a leading subordinate clause ("when you turn your head, do you
    # get...") as an open question.
    words = set(re.findall("[a-z']+", probe))
    assert words & {
        "do", "does", "did", "is", "are", "was", "were",
        "has", "have", "had", "can", "could", "will", "would",
    }, f"{screen_id} probe is not a yes/no question: {probe!r}"
