"""
After telling a caller to hang up and dial 999, Susie says nothing else.

northgate, 2026-09-05, CAe54de74b (build 0fc1c573ac29). The emergency intercept
worked exactly as intended -- `path=scripted ttfa_ms=114`, no LLM, no booking,
`outcome=safety_escalation` -- and spoke only the configured line, because
B-139 had just removed the transfer offer that used to be appended to it.

Removing that sentence left the turn ending on a STATEMENT. Seven seconds
later:

    09:21:26.341  [ms_watchdog] T-3 nudge armed — turn answered but asked
                  nothing and none was outstanding ('If you are experiencing a
                  medical emergency, please hang up '); armed "Anything else
                  you'd like to know?" rather than leaving the caller in
                  silence

The caller hung up two seconds into the ten-second window, so it never spoke.
A caller who paused to take in "call 999" would have been offered small talk.
That is worse than the sentence B-139 removed, and B-139 caused it: the
watchdog family exists to make sure a turn never ends in silence, and an
emergency turn is the one turn that MUST.

THE RULE ADDED. While `session["safety_escalation"]` is set, the watchdog arms
nothing and the dead-air net stays quiet. Dead air is the correct state.

All THREE watchdog arms are suppressed, not just the nudge that was observed:

  * BACKSTOP -- if the emergency arrives mid-booking a question is still
    outstanding, and re-asking "do you have a preference for when you'd like to
    come in?" after sending someone to A&E is the same defect on the other
    branch.
  * T-3 nudge -- the one that actually armed.
  * T-4 bare-ack -- same reasoning.

And `_silence_safety_net` too, because suppressing only the watchdog hands the
turn straight to it: its first fire is "Sorry, I can't quite hear you — how can
I help today?" and its second HANGS UP. Fixing the watchdog alone would have
swapped one wrong sentence for another -- the same fix-one-door mistake that
B-138 had to be redone for.

Nobody is stranded. The screening branch ends the TURN with `continue`, not the
call, and only the ARMING is suppressed -- never the transcript path. A caller
who speaks is routed normally; a caller who hangs up to dial 999 has done the
right thing.
"""

import ast
import inspect

import pytest

from app.media_streams import connection as conn
from app.media_streams.clinical_screening import (
    detect_emergency,
    emergency_response_text,
    update_screening_state,
)
from app.clinic_config import get_clinic


CHEST_PAIN = "yeah i've got chest pain and i can't breathe"
LIVE_CLINICS = ["northgate", "jv_v1", "vital_edge", "theorem_v2", "theorem_v3"]


def _tree():
    return ast.parse(inspect.getsource(conn))


def _assign_value(tree, name):
    """The single Assign node binding *name*, or None."""
    found = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
    ]
    assert len(found) == 1, f"expected one `{name}` assignment, found {len(found)}"
    return found[0].value


def _names(node):
    return {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}


# ---------------------------------------------------------------------------
# The precondition: the emergency turn is what sets the flag
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cid", LIVE_CLINICS)
def test_an_emergency_still_produces_the_scripted_line(cid):
    """The half that must never regress, re-pinned here alongside its fallout."""
    cl = get_clinic(cid)
    if not cl:
        pytest.skip(f"clinic {cid!r} not present on this branch")
    assert detect_emergency(CHEST_PAIN, cl) is True
    result = update_screening_state({}, cl, CHEST_PAIN)
    assert result["action"] == "emergency"
    assert result["speak"] == emergency_response_text(cl)
    assert "put you through" not in (result["speak"] or "").lower()


def test_the_emergency_branch_marks_the_call_as_escalated():
    """`safety_escalation` is the flag every suppression below keys on.

    If the emergency branch stops setting it, all four suppressions silently
    stop applying and nothing else fails.

    Asked of the parse tree, not a byte window: a first cut scanned 2000 bytes
    after the `_cs_line` binding and was pushed out of range by B-139's own
    comment block -- the exact false alarm this suite has now hit three times.
    """
    src = inspect.getsource(conn)
    tree = ast.parse(src)

    writes = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Subscript)
            and isinstance(getattr(t, "slice", None), ast.Constant)
            and t.slice.value == "safety_escalation"
            for t in n.targets
        )
    ]
    assert writes, (
        "nothing writes session['safety_escalation'] -- the watchdog and "
        "dead-air suppressions all key on it, and so does the outcome "
        "classifier that stops a 999 call being labelled 'abandoned'"
    )
    assert any(
        isinstance(w.value, ast.Constant) and w.value.value is True
        for w in writes
    ), "safety_escalation is never set to True"

    # ...and it is the emergency/escalate branch that does it.
    guarded = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and "escalate" in (ast.get_source_segment(src, n.test) or "")
        and "safety_escalation" in (ast.get_source_segment(src, n) or "")
    ]
    assert guarded, (
        "safety_escalation is no longer written under a test that mentions "
        "the escalate/emergency actions"
    )


# ---------------------------------------------------------------------------
# All three watchdog arms
# ---------------------------------------------------------------------------
def test_the_escalation_flag_is_read_from_the_session():
    value = _assign_value(_tree(), "_escalated_w")
    rendered = " ".join(
        (ast.get_source_segment(inspect.getsource(conn), value) or "").split()
    )
    assert "safety_escalation" in rendered, (
        f"_escalated_w is bound to {rendered!r}, which does not read the "
        "escalation flag"
    )


def test_the_backstop_is_suppressed():
    """A question outstanding mid-booking must not be re-asked after a 999."""
    value = _assign_value(_tree(), "_outstanding_q_w")
    assert "_escalated_w" in _names(value), (
        "the BACKSTOP's outstanding-question lookup ignores the escalation "
        "flag, so an emergency arriving mid-booking re-asks the booking "
        "question after sending the caller to A&E"
    )


def test_the_t3_nudge_is_suppressed():
    """This is the arm that actually armed on CAe54de74b."""
    value = _assign_value(_tree(), "_substantive_w")
    assert "_escalated_w" in _names(value), (
        "the T-3 nudge can still arm \"Anything else you'd like to know?\" "
        "against a 999 instruction"
    )


def test_the_t4_bare_ack_arm_is_suppressed():
    src = inspect.getsource(conn)
    tree = ast.parse(src)
    arms = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and "booking_flow_active" in (ast.get_source_segment(src, n.test) or "")
        and "_escalated_w" in (ast.get_source_segment(src, n.test) or "")
    ]
    assert arms, (
        "the T-4 bare-ack arm does not consult _escalated_w -- it is the third "
        "way this family can speak into an emergency turn"
    )


# ---------------------------------------------------------------------------
# ...and the dead-air net, which is what catches the turn if only the
# watchdog is suppressed
# ---------------------------------------------------------------------------
def test_the_dead_air_net_stays_quiet_on_an_escalation():
    """Suppressing the watchdog alone hands the turn to this instead.

    Its first fire is "Sorry, I can't quite hear you — how can I help today?"
    and its second hangs up. Asserted structurally on the guard's shape: a
    test on `safety_escalation` whose body ends the loop iteration.
    """
    src = inspect.getsource(conn.WebSocketCallHandler._silence_safety_net)
    tree = ast.parse(src.lstrip() if src.startswith(" ") else src)

    guards = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and "safety_escalation" in (ast.get_source_segment(src, n.test) or "")
    ]
    assert guards, (
        "_silence_safety_net has no safety_escalation guard -- after a 999 "
        "instruction it will re-ask, and on its second fire hang up on the "
        "caller"
    )
    assert any(
        isinstance(stmt, ast.Continue)
        for g in guards
        for stmt in ast.walk(g)
    ), (
        "the safety_escalation guard does not skip the tick -- it must stop "
        "the net, not merely log"
    )


def test_the_escalation_decision_is_logged_unconditionally():
    """B-140 had to be verifiable from its own log, and was not.

    CAb91776fd (5 Sep 2026): the caller hung up two seconds before the
    tts-finished callback that runs this family, so the block was never
    reached. The guard logged only when escalated, so the ABSENCE of its line
    was ambiguous between "the guard suppressed the nudge" and "the code never
    ran" -- and the fix could not be confirmed from a live call.

    One line on every question-less turn is cheap. An unfalsifiable safety fix
    is not.
    """
    src = inspect.getsource(conn)
    tree = ast.parse(src)

    # The unconditional log must be a direct statement of the arming family,
    # not nested inside `if _escalated_w`.
    parents = {
        child: node
        for node in ast.walk(tree)
        for child in ast.iter_child_nodes(node)
    }
    marker = "question-less turn reached the arming"
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and marker in (ast.get_source_segment(src, n) or "")
    ]
    assert calls, (
        "the arming family does not log unconditionally -- the absence of an "
        "escalation line cannot then be told apart from the block never being "
        "reached, which is exactly why B-140 went unverified"
    )

    for call in calls:
        node, guarded_by_escalation = call, False
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.If):
                if "_escalated_w" in (ast.get_source_segment(src, node.test) or ""):
                    guarded_by_escalation = True
                    break
        assert not guarded_by_escalation, (
            "the unconditional log sits inside `if _escalated_w`, which makes "
            "it conditional again and restores the ambiguity"
        )

    # ...and it must report the flag, not merely announce itself.
    assert any(
        "safety_escalation=%s" in (ast.get_source_segment(src, c) or "")
        for c in calls
    ), "the line does not report the value of the escalation flag"


def test_only_the_arming_is_suppressed_never_the_transcript_path():
    """A caller who keeps talking must still be heard.

    The suppressions live in the watchdog/dead-air paths. If `safety_escalation`
    ever starts gating transcript dispatch, a caller who says "actually it's
    eased off" would get silence instead of a receptionist.
    """
    src = inspect.getsource(conn)
    i = src.index("[ms_stt] FINAL")
    window = src[max(0, i - 4000):i + 4000]
    assert "safety_escalation" not in window, (
        "safety_escalation now appears near the transcript dispatch path -- "
        "the escalation must silence Susie's PROMPTS, never the caller's turn"
    )
