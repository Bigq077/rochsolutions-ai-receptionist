"""
Telling a caller to hang up and dial 999 must not be followed by an offer to
keep them on the line.

northgate, 2026-09-05, CA2a44f165 (build 18af71384782). The caller said

    "yeah i've got chest pain i can't breathe"

The intercept fired correctly and fast -- `[LAT] turn_seq=4 path=scripted
ttfa_ms=124`, no LLM in the loop, no booking, `outcome=safety_escalation`.
That part worked and must keep working.

What the caller then heard was two chunks:

    1. "If you are experiencing a medical emergency, please hang up and call
        999 immediately, or go to your nearest A&E."
    2. "Would you like me to put you through to someone now?"

The second sentence was appended in `connection.py` to every `action ==
"emergency"` line. It contradicts the first at the worst possible moment, and
it does not contradict it harmlessly: it invites a person who may be having a
cardiac event to stay on a physiotherapy line rather than dial 999. "Someone"
is a physio receptionist.

THE RULE ADDED. The scripted safety line reaches the caller exactly as the
clinic configured it. Nothing is appended to it.

This is deliberately NOT clinic-gated. All five live clinic configs were
checked: every one has the intercept armed, every one detects this utterance,
and not one of them offers a transfer in its own wording. The contradiction
came entirely from the shared append, and it is wrong for all of them --
`clinic.json` is the place for behaviour that differs between clinics, and
this does not.

Removing the sentence does not strand a false positive. The screening branch
ends the TURN, not the call, so a caller wrongly intercepted simply keeps
talking and the next turn routes normally. It also makes `emergency` behave
like `escalate`, which has never appended anything.
"""

import ast
import inspect

import pytest

from app.clinic_config import get_clinic
from app.media_streams import connection as conn
from app.media_streams.clinical_screening import (
    detect_emergency,
    emergency_intercept_enabled,
    emergency_response_text,
    update_screening_state,
)


CHEST_PAIN = "yeah i've got chest pain i can't breathe"

# Every clinic that answers a real phone number on this build.
LIVE_CLINICS = ["northgate", "jv_v1", "vital_edge", "theorem_v2", "theorem_v3"]


def _clinic(cid):
    cl = get_clinic(cid)
    if not cl:
        pytest.skip(f"clinic {cid!r} not present on this branch")
    return cl


# ---------------------------------------------------------------------------
# The intercept itself must not regress -- this is the load-bearing half
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cid", LIVE_CLINICS)
def test_the_intercept_still_fires(cid):
    """`emergency_keywords` is read independently of `screening.enabled`.

    northgate has screening OFF and this must still fire; that separation is
    the whole reason the screens could be switched off at all.
    """
    cl = _clinic(cid)
    assert emergency_intercept_enabled(cl) is True
    assert detect_emergency(CHEST_PAIN, cl) is True

    result = update_screening_state({}, cl, CHEST_PAIN)
    assert result["action"] == "emergency"
    assert "999" in (result["speak"] or "")


# ---------------------------------------------------------------------------
# ...and it must not contradict itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cid", LIVE_CLINICS)
def test_the_configured_wording_offers_no_transfer(cid):
    cl = _clinic(cid)
    spoken = emergency_response_text(cl).lower()
    assert "put you through" not in spoken
    assert "speak to someone" not in spoken


@pytest.mark.parametrize("cid", LIVE_CLINICS)
def test_the_screening_layer_returns_the_configured_text_unchanged(cid):
    cl = _clinic(cid)
    result = update_screening_state({}, cl, CHEST_PAIN)
    assert result["speak"] == emergency_response_text(cl)


def test_nothing_appends_to_the_scripted_safety_line():
    """Structural, because the append lived in `handle_transcript`.

    That method is ~15k lines and is under a change freeze, so the assertion
    is made against the parse tree rather than by calling it: `_cs_line` is
    bound exactly once, from the screening layer's own `speak`, and never
    rewritten. A text scan is not used deliberately -- on this same change a
    byte-window scan elsewhere in the suite was shown to match a COMMENT and
    pass with the real wiring deleted.
    """
    src = inspect.getsource(conn)
    tree = ast.parse(src)

    binds = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.Assign, ast.AugAssign))
        and any(
            isinstance(t, ast.Name) and t.id == "_cs_line"
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
        )
    ]
    assert len(binds) == 1, (
        "_cs_line is written %d times -- the scripted safety line is being "
        "rewritten after the screening layer produced it, which is how "
        '"hang up and call 999" acquired "would you like me to put you '
        'through to someone now?" (B-139, CA2a44f165)' % len(binds)
    )

    only = binds[0]
    assert isinstance(only, ast.Assign), "_cs_line is being augmented in place"
    rendered = " ".join((ast.get_source_segment(src, only.value) or "").split())
    assert rendered == '_cs_result["speak"] or ""', (
        "_cs_line is no longer the screening layer's own text verbatim; it is "
        f"{rendered!r}"
    )


def test_no_transfer_offer_survives_anywhere_in_the_emergency_branch():
    """The literal itself is gone, not merely detached from _cs_line."""
    src = inspect.getsource(conn)
    assert "through to someone now?" not in src, (
        "the transfer offer is still present in connection.py -- if it has "
        "been moved rather than removed it can reach a 999 caller again"
    )
