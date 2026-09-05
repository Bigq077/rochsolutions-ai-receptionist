"""
Describing a complaint by its timing is not asking for that time of day.

northgate, 2026-09-05, CA04219aeb (build 18af71384782). Susie asked

    "what's the appointment for?"

and the caller answered

    "yeah my achilles is stiff for the first few minutes every morning
     and it eases as i walk"

`_time_preference_tier` saw a band, no question mark and no slot pick, and
returned "hard". The capture block then banked

    [ms_conn v3] time_of_day_preference captured: mornings (tier=hard, ...)

which put `date_hint="mornings"` into `check_availability`, and every one of
the six slots offered was AM: 09:00, 11:30, 08:00, 11:20, 08:50, 11:20.

The caller had expressed no scheduling preference whatsoever. "every morning"
was the diagnostic detail that makes it Achilles tendinopathy rather than a
tear. The answer to "what's the appointment for?" is the one utterance in a
call most likely to carry a time word that is not a request -- "worse at
night", "stiff every morning", "I did it on Saturday" -- because a complaint
is very often described BY its timing.

THE RULE ADDED. While the reason answer is pending, an utterance carries no
scheduling authority at all. BOTH captures are gated, not just the band:
`_extract_day_preference` had no tier gate of any kind and bare-matches
weekdays, so "i did my back in on saturday" is the identical defect five lines
further down, and fixing only the band would have left it live.

NOT "soft". Soft still renders "Caller's time preference: mornings" into the
prompt, which is the sentence that produced the date_hint on this very call.

The window is the whole pending window, not just the next turn:
`_REASON_ANSWER_MAX_TURNS` is 2 so that one filler is tolerated, and a caller
who says "um" before describing the complaint arrives here on turn two.
"""

import inspect

import pytest

from app.media_streams.connection import (
    _extract_day_preference,
    _extract_time_preference,
    _time_preference_tier,
)
from app.media_streams.first_turn_extractor import (
    commit_reason_answer,
    utterance_is_reason_answer,
)


# The verbatim transcript from CA04219aeb.
LIVE = (
    "yeah my achilles is stiff for the first few minutes every morning "
    "and it eases as i walk"
)
# The utterance that PROVOKED the reason question on that same call.
PROVOKER = "yeah i'd like to book an appointment"


def _pending(armed_on=PROVOKER):
    """A session in the state it is in when the caller answers the question."""
    return {
        "_reason_answer_pending": True,
        "_reason_answer_armed_on": armed_on,
    }


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------
def test_the_live_utterance_still_reads_as_hard_without_the_gate():
    """Red-anchor: without the gate this really is the bug, not a mis-diagnosis.

    If this ever stops being "hard", the gate below starts passing for a
    reason that has nothing to do with B-138, and this file has quietly
    stopped covering it.
    """
    assert _extract_time_preference(LIVE) == "mornings"
    assert _time_preference_tier(LIVE, is_slot_pick=False) == "hard"


def test_a_reason_answer_earns_no_time_authority():
    assert _time_preference_tier(
        LIVE, is_slot_pick=False, is_reason_answer=True
    ) == "none"


@pytest.mark.parametrize(
    "utterance",
    [
        LIVE,
        "it's my lower back, it's always worse first thing in the morning",
        "my knee gives way in the evenings",
        "i did my back in on saturday",
        "i twisted my ankle today",
    ],
)
def test_no_scheduling_state_is_banked_off_a_reason_answer(utterance):
    """Neither capture may claim authority while the reason answer is pending."""
    assert _time_preference_tier(
        utterance, is_slot_pick=False, is_reason_answer=True
    ) == "none"


def test_the_day_capture_is_the_second_door():
    """`_extract_day_preference` bare-matches weekdays, so it needs the gate too."""
    assert _extract_day_preference("i did my back in on saturday") == "saturday"
    assert _extract_day_preference("i twisted my ankle today") == "today"


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------
def test_the_answering_turn_is_recognised():
    assert utterance_is_reason_answer(_pending(), LIVE) is True


def test_the_arming_turn_is_not_the_answering_turn():
    """The utterance that provoked the question is not its answer."""
    assert utterance_is_reason_answer(_pending(), PROVOKER) is False


def test_before_the_question_is_armed_nothing_is_pending():
    assert utterance_is_reason_answer({}, LIVE) is False
    assert utterance_is_reason_answer(
        {"_reason_answer_pending": True, "_reason_answer_armed_on": None}, LIVE
    ) is False


def test_the_probe_never_consumes_the_reason():
    """Pure. Asking must not spend the flag the consumer is waiting on."""
    session = _pending()
    before = dict(session)
    utterance_is_reason_answer(session, LIVE)
    utterance_is_reason_answer(session, LIVE)
    assert session == before
    # ...and the reason still lands afterwards.
    assert commit_reason_answer(session, LIVE) is True
    assert session["reason"] == LIVE


def test_a_preference_stated_after_the_reason_landed_still_latches_hard():
    """The gate must not swallow the answers the capture block exists for.

    Once `commit_reason_answer` has consumed its flag, "mornings please" is a
    scheduling answer again -- which is the whole point of the capture.
    """
    session = _pending()
    assert commit_reason_answer(session, LIVE) is True
    assert session.get("_reason_answer_pending") is None

    later = utterance_is_reason_answer(session, "mornings please")
    assert later is False
    assert _time_preference_tier(
        "mornings please", is_slot_pick=False, is_reason_answer=later
    ) == "hard"


def test_a_filler_does_not_spend_the_gate():
    """`_REASON_ANSWER_MAX_TURNS` tolerates one "um"; so must the gate.

    A turn-zero gate would let the complaint through on the second turn, which
    is the common shape -- callers hesitate before describing a symptom.
    """
    session = _pending()
    assert commit_reason_answer(session, "um") is False
    assert session.get("_reason_answer_pending") is True
    assert utterance_is_reason_answer(session, LIVE) is True


def test_a_slot_pick_still_wins_over_the_reason_gate():
    """The pre-existing B-90 arm is not disturbed by the new one."""
    assert _time_preference_tier(
        "number two", is_slot_pick=True, is_reason_answer=False
    ) == "none"
    assert _time_preference_tier(
        "number two", is_slot_pick=True, is_reason_answer=True
    ) == "none"


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def test_the_capture_site_asks_whether_this_is_the_reason_answer():
    """Asked of the parse tree, not of a text window.

    A byte-window scan of connection.py was tried and rejected. On this
    very change one such scan (test_choosing_a_slot_is_not_a_time_
    preference) first false-ALARMED because the window was too short, and
    then, re-anchored, false-PASSED because the block's own prose names
    the symbol it was scanning for. A comment cannot satisfy an AST test.
    """
    import ast
    import inspect

    from app.media_streams import connection as c

    tree = ast.parse(inspect.getsource(c))

    # 1. The flag actually reaches the tier function. Computing it and not
    #    passing it is the shape that left _is_slot_pick unread.
    tier_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "_time_preference_tier"
    ]
    assert tier_calls, "the capture no longer asks for a tier at all"
    for call in tier_calls:
        kwargs = [k.arg for k in call.keywords]
        assert "is_reason_answer" in kwargs, (
            "a _time_preference_tier call does not pass is_reason_answer -- "
            "an utterance answering 'what's the appointment for?' can bank "
            "a hard time filter again (B-138, CA04219aeb)"
        )

    # 2. ...and the flag is derived from the module that OWNS the reason
    #    flags, rather than re-derived here from session keys.
    imported = any(
        isinstance(n, ast.ImportFrom)
        and "first_turn_extractor" in (n.module or "")
        and any(a.name == "utterance_is_reason_answer" for a in n.names)
        for n in ast.walk(tree)
    )
    assert imported, (
        "connection.py no longer imports utterance_is_reason_answer -- if it "
        "has grown its own reading of the reason flags, that reading and "
        "commit_reason_answer can disagree"
    )


def test_the_day_capture_is_gated_too():
    """The day capture has no tier of its own, so the gate is all there is.

    Keyed on the NEAREST enclosing `if`. A first cut collected every `If`
    whose source segment contained the call, which also matched the
    `if is_freeform_clinic(clinic_id)` wrapping the entire turn loop -- and
    reported a second, ungated call site that does not exist. There is
    exactly one.
    """
    import ast
    import inspect

    from app.media_streams import connection as c

    tree = ast.parse(inspect.getsource(c))
    parents = {
        child: node
        for node in ast.walk(tree)
        for child in ast.iter_child_nodes(node)
    }
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "_extract_day_preference"
    ]
    assert calls, "the day-preference capture has gone missing"

    for call in calls:
        node, guard = call, None
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.If):
                guard = node
                break
        assert guard is not None, (
            "the day-preference capture is no longer inside a guard at all"
        )
        names = {
            x.id for x in ast.walk(guard.test) if isinstance(x, ast.Name)
        }
        assert "_reason_answer" in names, (
            "the nearest guard on _extract_day_preference does not consult "
            "_reason_answer. 'i did my back in on saturday' is an answer to "
            "the reason question, and it banks a saturday-only filter for "
            "the rest of the call"
        )

def test_the_consumer_and_the_gate_share_one_predicate():
    """Two answers to "is this the reason answer?" is how these drift apart."""
    from app.media_streams import first_turn_extractor as fte

    src = inspect.getsource(fte.commit_reason_answer)
    assert "utterance_is_reason_answer" in src, (
        "commit_reason_answer must ASK the shared predicate, not restate it"
    )
