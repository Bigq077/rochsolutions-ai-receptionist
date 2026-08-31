"""The harness must run every capture connection.py runs before dispatch.

Found 2026-08-30, while trying to verify the CA86c320ef duration guarantee
end-to-end after O-1 moved the length question earlier in the flow.

`run_turn` is not a whole turn. connection.py's transcript handler runs
`capture_duration_choice` and `capture_under_age` against the raw utterance
BEFORE dispatching, and `ConversationDriver` did not. So every harness run
exercised the engine with its primary safeguards removed, and that is wrong in
both directions:

  * IT MANUFACTURES DEFECTS. The probe reported the diary taking 60 minutes when
    the caller had said "the ninety minute one please" — twice in four runs.
    With no capture, `_resolve_duration_minutes` has nothing to prefer and the
    model's `duration_minutes` argument wins by default. That is precisely the
    failure the capture exists to prevent, reproduced by omitting the capture.
    It looked like a live P1 in a flow change that had already been pushed to
    two patient branches.

  * IT HIDES REAL ONES. `capture_under_age` is the only under-age enforcement on
    the template clinics. Without it a run shows an under-age caller sailing
    through, and a test written against that output pins the wrong behaviour.

The first test below is the durable one: it reads the capture call sites out of
connection.py and fails when one is added and not mirrored. A hand-written list
would have to be remembered, which is how the gap opened.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.clinic_config import get_clinic
from app.media_streams import connection as conn
from tests.harness.driver import ConversationDriver


def _resolve_duration_minutes_or_skip():
    """Imported lazily, because it does not exist on every branch.

    `_resolve_duration_minutes` is absent on theorem-onboarding: Mark sells no
    service with a choice of lengths, so that branch never grew the resolver.
    A module-scope `from ... import _resolve_duration_minutes` therefore turns
    this file into a COLLECTION error there — pytest interrupts the whole run
    and reports ZERO failures, which looks like catastrophe and measures
    nothing. That is the third time in two days a canonical test file has been
    ported into that exact failure, so it is worth stating plainly: an import
    at module scope is a hard dependency on every branch the file can reach.
    """
    try:
        from app.tools.receptionist_tools import _resolve_duration_minutes
    except ImportError:
        pytest.skip(
            "_resolve_duration_minutes does not exist on this branch — it has "
            "no service with a choice of lengths, so the CA86c320ef guarantee "
            "is N-A here"
        )
    return _resolve_duration_minutes


#: `capture_x(self.session, utterance)` — the shape the transcript handler uses.
_CALL_SITE = re.compile(r"\b(capture_[a-z_]+)\(\s*self\.session\s*,\s*utterance\s*\)")


def _captures_connection_runs() -> set:
    return set(_CALL_SITE.findall(inspect.getsource(conn)))


def test_the_premise_holds():
    """If connection.py stops running captures this way the file is vacuous."""
    found = _captures_connection_runs()
    assert found, (
        "no `capture_*(self.session, utterance)` call sites found — the "
        "transcript handler has changed shape and this file is testing nothing"
    )
    assert "capture_duration_choice" in found


def test_the_harness_runs_every_pre_dispatch_capture():
    """The gap, and the guard against it reopening.

    Deliberately derived from the source rather than listed here: a list has to
    be kept in step by whoever adds the next capture, and that is exactly the
    step that was missed.
    """
    mirrored = inspect.getsource(ConversationDriver._pre_turn)
    missing = sorted(c for c in _captures_connection_runs() if c not in mirrored)
    assert not missing, (
        f"connection.py runs {missing} before dispatch and the harness does "
        f"not. Every run is now missing that safeguard — which shows up as the "
        f"ENGINE failing, not as the harness being incomplete. Mirror it in "
        f"_pre_turn (imported, never re-typed) or state in that docstring why "
        f"it cannot be modelled, as the DTMF path does."
    )


# ── Why it matters, at the level the omission actually bit ──────────────────

def _clinic_with_a_length_choice():
    for cid in ("vital_edge", "jv_v1", "northgate"):
        try:
            c = get_clinic(cid)
        except Exception:
            continue
        for svc in c.get("services") or []:
            if isinstance(svc, dict) and svc.get("typical_duration_minutes_options"):
                return c, svc
    pytest.skip("this branch ships no service with a choice of lengths")


def test_without_the_capture_the_models_argument_wins():
    """The false defect, pinned so the next reader recognises it on sight.

    This is not a bug — it is what the guarantee looks like with its first half
    removed, and it is what four harness runs reported before `_pre_turn`
    mirrored the capture.
    """
    _resolve_duration_minutes = _resolve_duration_minutes_or_skip()
    clinic, svc = _clinic_with_a_length_choice()
    opts = [int(o) for o in svc["typical_duration_minutes_options"]]
    shortest, longest = min(opts), max(opts)

    no_capture: dict = {"clinic_id": clinic.get("clinic_id")}
    assert _resolve_duration_minutes(
        clinic, svc["name"], {"duration_minutes": shortest}, no_capture, shortest
    ) == shortest


def test_with_the_capture_the_caller_wins():
    """The guarantee itself (CA86c320ef): the caller's spoken length outranks
    the model's argument, so a 90-minute booking cannot be written as 60."""
    _resolve_duration_minutes = _resolve_duration_minutes_or_skip()
    clinic, svc = _clinic_with_a_length_choice()
    opts = [int(o) for o in svc["typical_duration_minutes_options"]]
    shortest, longest = min(opts), max(opts)

    session: dict = {"clinic_id": clinic.get("clinic_id")}
    captured = conn.capture_duration_choice(session, f"the {longest} minute one please")
    assert captured == longest, (
        "the engine did not hear the caller's length — the rest of this test "
        "would pass for the wrong reason"
    )
    assert _resolve_duration_minutes(
        clinic, svc["name"], {"duration_minutes": shortest}, session, shortest
    ) == longest


def test_the_driver_now_captures_it_from_a_caller_utterance():
    """End to end through the driver's own pre-turn, without spending a model
    call: the capture must land in the session the engine will read."""
    clinic, svc = _clinic_with_a_length_choice()
    longest = max(int(o) for o in svc["typical_duration_minutes_options"])

    call = ConversationDriver.__new__(ConversationDriver)
    call.session = {"clinic_id": clinic.get("clinic_id")}
    call._pre_turn(f"the {longest} minute one please")

    assert call.session.get("_service_duration_choice") == longest, (
        "the driver dispatched a turn without the caller's length — every "
        "booking it makes is at whatever the model happened to pass"
    )
