# tests/regression/test_b65_theorem_acuity_reports_the_cancelled_id.py
"""
B-65 on Theorem — the half-port that would have left the guard disarmed.

B-65's different-target guard refuses a second cancel in the same call when the
recorded id differs from the new target, so a retry cannot delete an
appointment nobody discussed. It reads that id from the executor's success
payload via `_note_write_result`:

    _cid = str((result or {}).get("cancelled_appointment_id") or "").strip()
    if _cid:
        session[CANCEL_SUCCEEDED_ID_KEY] = _cid

"Recorded only when the executor actually reports an id - an empty value leaves
the guard disarmed, which is today behaviour." That empty-value branch is the
trap this file exists for.

`_exec_cancel_appointment` short-circuits to `_cancel_appointment_acuity` for
`theorem`/`theorem_v2`/`theorem_v3` before it ever reaches the Google Calendar
code that B-65 changed. So the upstream commit, cherry-picked as-is, lands the
payload fix and the guard on this branch and gives the guard nothing to read —
it would sit permanently disarmed on the one clinic while protecting the other
three. Ported 2026-08-20 with the executor hunk that makes it real.

Same shape as B-44, which was ported to Theorem and only half-worked for the
same reason, and as the Theorem owner alerts, which looked enabled while three
of four events could never fire.

Like `test_theorem_owner_alerts.py`, these tests never CALL the Acuity
executor — that path cancels real appointments (see the 60 accidental Acuity
bookings from tests/auto). The wiring is pinned by source inspection.
"""

import ast
import inspect
import textwrap

import pytest

from app.tools import receptionist_tools as rt

THEOREM_IDS = ("theorem", "theorem_v2", "theorem_v3")

ID_KEY = "cancelled_appointment_id"


def _success_returns(func):
    """Every `return {...}` in *func* whose dict says success=True.

    Returns a list of (lineno, {key: value-node}) for the dict literals.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
            continue
        pairs = {}
        for k, v in zip(node.value.keys, node.value.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                pairs[k.value] = v
        truthy = pairs.get("success")
        if isinstance(truthy, ast.Constant) and truthy.value is True:
            out.append((node.lineno, pairs))
    return out


# ---------------------------------------------------------------------------
# The premise: Theorem really does bypass the Google Calendar cancel path.
# If this ever stops being true, the tests below are asking the wrong question.
# ---------------------------------------------------------------------------
def test_theorem_short_circuits_to_the_acuity_cancel_executor():
    src = inspect.getsource(rt._exec_cancel_appointment)
    assert "_cancel_appointment_acuity" in src
    for cid in THEOREM_IDS:
        assert f'"{cid}"' in src, (
            f"{cid} no longer routes to the Acuity executor — re-check whether "
            f"this file still describes the live cancel path"
        )


# ---------------------------------------------------------------------------
# The regression itself.
# ---------------------------------------------------------------------------
def test_the_acuity_executor_has_success_paths_to_check():
    """Guard the guard: a source change that hides the returns must not pass."""
    assert len(_success_returns(rt._cancel_appointment_acuity)) >= 3


def test_every_acuity_cancel_success_reports_the_id():
    missing = [
        lineno
        for lineno, pairs in _success_returns(rt._cancel_appointment_acuity)
        if ID_KEY not in pairs
    ]
    assert not missing, (
        f"_cancel_appointment_acuity returns success without {ID_KEY!r} at "
        f"line(s) {missing} of its own source. B-65's different-target guard "
        f"reads that key and disarms itself when it is absent, so a second "
        f"cancel aimed at a DIFFERENT appointment would not be refused."
    )


def test_the_reported_id_is_a_real_value_not_a_placeholder():
    """`_cid or ''` disarms the guard, so None/'' is the same as not porting."""
    for lineno, pairs in _success_returns(rt._cancel_appointment_acuity):
        node = pairs.get(ID_KEY)
        assert node is not None, lineno
        if isinstance(node, ast.Constant):
            pytest.fail(
                f"line {lineno}: {ID_KEY} is the literal {node.value!r}. An "
                f"empty or None id leaves the guard disarmed exactly as if the "
                f"key were absent."
            )


# ---------------------------------------------------------------------------
# The Google Calendar path keeps it too — the other clinics on this branch.
# ---------------------------------------------------------------------------
def test_the_google_calendar_cancel_still_reports_the_id():
    src = inspect.getsource(rt._exec_cancel_appointment)
    assert ID_KEY in src, (
        f"the Google Calendar cancel executor stopped reporting {ID_KEY} — "
        f"that is the upstream B-65 fix being undone"
    )
