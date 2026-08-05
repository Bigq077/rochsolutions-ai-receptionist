"""
T-3 — a bare FAQ answer must not leave the caller in silence.

Observed on CA281dab02, 2026-08-05 09:53:46, on the joint-injections call:

    09:53:46  tts_finished fired: 'Generally a maximum of three in a single
              joint within a twelve-month period, to protect long-term joint
              health.'
    09:53:46  Spec W: turn asked nothing and no question is outstanding —
              nothing to re-ask

A correct, complete answer — followed by nothing at all. The caller happened to
hang up two seconds later so it never bit, but a real caller pausing to think
gets dead air until they speak again or give up.

Spec W arms the watchdog in two cases: the turn ended on a question, or a
question is still outstanding (the BACKSTOP). The third case — answered and
stopped — only logged. This pins the nudge that now fills it.

Why a nudge rather than reusing the closer: Gate 5b strips "is there anything
else I can help with" on purpose, because a premium receptionist ends an ANSWER
with the answer, not a scripted sign-off. Breaking a silence that has already
happened is a different act. The wording has to avoid that literal so the two
rules do not contradict each other — which is what the last test here checks.
"""

import inspect
import re

from app.media_streams import connection as conn
from app.media_streams.turn_handler import _BANNED_SENTENCE_RE

NUDGE = "Anything else you'd like to know?"


def _spec_w_source() -> str:
    """The Spec W block, comments stripped — these assert on behaviour."""
    src = inspect.getsource(conn.WebSocketCallHandler)
    start = src.index("Spec W: watchdog restart for informational responses")
    end = src.index("── end Spec W", start)
    block = src[start:end]
    return "\n".join(
        line for line in block.split("\n")
        if not line.lstrip().startswith("#")
    )


def test_the_dead_end_branch_now_arms_a_watchdog():
    """The branch that used to only log must restart the timer."""
    block = _spec_w_source()
    assert NUDGE in block, (
        "the T-3 nudge is gone from Spec W — a bare FAQ answer will leave the "
        "caller in silence again"
    )
    # The nudge and the restart must be in the same branch, or the phrase is
    # seeded and nothing ever speaks it.
    tail = block[block.index(NUDGE):]
    assert "_restart_timer()" in tail, (
        "the nudge is seeded but no watchdog is armed — nothing will say it"
    )


def test_the_nudge_is_seeded_where_the_fire_path_reads_it():
    """The fire path prefers session['last_question'], falling back to the
    handler attribute. Seeding only one of them works by luck, not design."""
    tail = _spec_w_source()
    tail = tail[tail.index(NUDGE):]
    assert "_sh_w.last_question" in tail, "handler last_question not seeded"
    assert 'last_question"] = _nudge_w' in tail or "last_question'] = _nudge_w" in tail, (
        "session last_question not seeded — the fire path reads it first"
    )


def test_the_nudge_passes_the_same_question_predicate_as_the_backstop():
    """Spec Z Gate 2 inside _restart_timer drops a last_question with no
    question in it. A statement nudge would arm nothing and fail silently —
    the exact failure mode this fix exists to remove."""
    handler = conn.SilenceHandler.__new__(conn.SilenceHandler)
    assert conn.SilenceHandler._prompt_contains_question(handler, NUDGE) is True


def test_the_nudge_is_not_a_phrase_gate5_bans():
    """Otherwise the engine speaks a line the prompt is forbidden to produce,
    and the next person to read both concludes one of them is wrong."""
    hits = [name for name, pat in _BANNED_SENTENCE_RE if pat.search(NUDGE)]
    assert not hits, f"the T-3 nudge is caught by Gate 5 rules: {hits}"

    # Control: the closer it deliberately avoids IS banned, so this test is
    # measuring something.
    banned = "Is there anything else I can help with?"
    assert any(pat.search(banned) for _, pat in _BANNED_SENTENCE_RE), (
        "the banned closer is no longer banned — re-check whether the nudge "
        "still needs to differ from it"
    )


# ── behavioural, through the real Spec W block ─────────────────────────────
#
# Reuses the 02:30 incident's own harness so the two fixes are tested against
# the same production code path, and a change that satisfies one by breaking
# the other fails here.

async def test_the_failing_answer_now_arms_a_watchdog():
    """The exact sentence from CA281dab02 at 09:53:46."""
    from tests.regression.test_questionless_turn_backstop import StubSH, _fire, _handler

    sh = StubSH("")   # nothing outstanding — the dead-end branch
    session = {}
    await _fire(_handler(sh, session), (
        "Generally a maximum of three in a single joint within a twelve-month "
        "period, to protect long-term joint health."
    ))

    assert sh.restart_calls == 1, (
        "a complete FAQ answer still arms nothing — the caller gets silence"
    )
    assert sh.last_question == NUDGE
    assert session.get("last_question") == NUDGE, (
        "the fire path reads session['last_question'] first"
    )


async def test_a_bare_acknowledgement_still_stays_silent():
    """"Right —" is the booking flow's step 1, not an answer.

    Its recovery is the outstanding booking question (the BACKSTOP), not an
    open invitation that would walk the caller out of the booking. This is the
    02:30 incident, and the nudge must not take it over.
    """
    from tests.regression.test_questionless_turn_backstop import StubSH, _fire, _handler

    sh = StubSH("")
    session = {}
    await _fire(_handler(sh, session), "Right —")

    assert sh.restart_calls == 0, "the nudge hijacked a bare booking ack"
    assert session.get("last_question") is None


def test_the_two_arming_paths_above_are_untouched():
    """The question case and the BACKSTOP are the paths that already worked."""
    block = _spec_w_source()
    assert "_has_question_w or _loc_active_w" in block, (
        "the question / location arming condition changed"
    )
    assert "BACKSTOP armed" in block, (
        "the outstanding-question backstop is gone"
    )
