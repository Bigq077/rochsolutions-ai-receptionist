"""
The booking-ack bridge filler was played after the question it was meant to
precede.

northgate, CAcb51bc27, 5 Sep 2026. The caller said "uh yes please" to the
booking CTA and heard:

    "Do you have a preference for when you'd like to come in? … Still with you —"

and then silence. From the log, in order:

    booking ack filler — FAQ session detected q_gen=5
    booking-ack next Q: 'Is there a particular day or time that works best...'
    timing Q suppressed — LLM response already contains a question this turn
    synthesise: "Do you have a preference for when you'd like to come in?"
    synthesise: "Still with you —"   head=True
    BACKSTOP armed — turn asked nothing ('Still with you —') but a question is
      still outstanding: "Do you have a preference for when you'd like to..."

THE PREMISE WAS FALSE. That filler exists for one reason, stated in its own
comment: to cover the 1–2 s gap between the LLM's ack and the first booking
question. When the LLM's turn already ends on a question, `_next_q` is set to
None — there is no following question, so there is no gap — and the "bridge"
lands after a complete question, leaving the turn ending on a non-question and
forcing the backstop to re-arm.

THE RULE ADDED. Play a bridge if and only if there is something to bridge to.
Keyed on the SAME two conditions that set `_next_q = None`, so the filler and
the question it precedes cannot disagree about whether a question is coming.

Intermittent in the wild, which is why it survived a round of testing: it is
gated on `q_gen >= 5` and the FAQ-session classification, and the later calls
of 5 Sep did not classify that way.
"""

import ast
import inspect

from app.media_streams import connection as conn


def _suppression_guard():
    """The `if` that decides whether the bridge filler speaks."""
    src = inspect.getsource(conn)
    tree = ast.parse(src)
    found = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "_bridge_has_nothing_to_cover"
            for t in n.targets
        )
    ]
    assert len(found) == 1, (
        f"expected one _bridge_has_nothing_to_cover assignment, found "
        f"{len(found)} -- the bridge filler no longer asks whether there is a "
        "gap to bridge, so it can be spoken after a completed question"
    )
    return src, found[0]


def test_the_bridge_asks_whether_there_is_a_gap():
    _suppression_guard()


def test_it_keys_on_the_same_conditions_as_the_question_suppression():
    """Not a copy of the idea -- the same two facts.

    `_next_q` is set to None when the LLM's turn already contains a question,
    or when slots were presented this turn. Those are exactly the cases with
    no following question, so they are exactly the cases with no gap.
    """
    src, node = _suppression_guard()
    rendered = " ".join((ast.get_source_segment(src, node.value) or "").split())
    assert '"?" in _last_bot' in rendered, (
        "the bridge does not check whether the LLM's own turn already ended "
        f"on a question; it reads {rendered!r}"
    )
    assert "v3_awaiting_slot_selection" in rendered, (
        "the bridge does not check whether slots were just presented -- the "
        "slot CTA already invites a response, so there is no gap there either"
    )


def test_the_flag_actually_gates_the_speech():
    """Computing it and not consulting it is the shape that left `_is_slot_pick`
    unread for a release."""
    src = inspect.getsource(conn)
    tree = ast.parse(src)

    gated = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and "_bridge_has_nothing_to_cover"
        in (ast.get_source_segment(src, n.test) or "")
        and "_hs_bridge.speak" in (ast.get_source_segment(src, n.test) or "")
    ]
    assert gated, (
        "no `if` guards the bridge filler on both _hs_bridge.speak and "
        "_bridge_has_nothing_to_cover -- the filler can still be queued after "
        "the turn's own question"
    )
    test_src = ast.get_source_segment(src, gated[0].test) or ""
    assert "not _bridge_has_nothing_to_cover" in " ".join(test_src.split()), (
        "the guard does not NEGATE the flag; as written it would speak the "
        "bridge only when there is nothing to bridge"
    )


def test_the_next_question_suppressions_still_exist():
    """The bridge borrows its conditions from these. If they move, the bridge
    guard is keying on facts nothing else uses any more."""
    src = inspect.getsource(conn)
    assert "timing Q suppressed" in src, (
        "the _next_q suppressions have been removed or renamed; the bridge "
        "guard's two conditions were taken from them and should move together"
    )
