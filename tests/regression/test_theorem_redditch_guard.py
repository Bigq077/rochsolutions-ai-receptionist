"""Item 4 of THEOREM_PORT_PLAN — the Redditch guard and its companion sync.

4a. `_location_not_bookable` is the deterministic backstop behind the prompt's
    REDDITCH REDIRECT block. The prompt tells Susie not to book Redditch; this
    guarantees it even if the prompt is ignored, which is the only version of
    that guarantee worth having.

4c. The location sync. main's comment records the live failure verbatim: a
    caller switched Redditch -> Awlstuh, was shown and agreed Alcester slots,
    but book_appointment fired with location='redditch', was blocked, and the
    caller "bounced after a full booking".

    THESE TWO MUST SHIP TOGETHER. The guard without the sync REPRODUCES that
    exact failure — it is the thing that blocks the stale location. Shipping 4a
    alone converts a prompt-level soft failure into a hard one.

Scope, and why this cannot regress the live clinic branches: the guard returns
False immediately unless clinic_id == "theorem_v3". jv_v1 and vital_edge are
single-site and never reach the location logic at all. There is an explicit
test for that below, because "it only affects Theorem" is the claim that makes
this port safe to land at all.
"""
import pytest

import app.clinic_config as cc
from app.media_streams.llm_stream import _location_not_bookable

REDDITCH = {"location": "redditch"}
ALCESTER = {"location": "alcester"}
THEOREM = {"clinic_id": "theorem_v3"}


# ── 4a. the guard ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", ["check_availability", "book_appointment"])
def test_new_booking_tools_are_blocked_for_redditch(tool):
    assert _location_not_bookable(tool, REDDITCH, THEOREM) is True


@pytest.mark.parametrize("tool", ["check_availability", "book_appointment"])
def test_alcester_is_never_blocked(tool):
    assert _location_not_bookable(tool, ALCESTER, THEOREM) is False


@pytest.mark.parametrize("tool", [
    "reschedule_appointment",
    "cancel_appointment",
    "lookup_patient",
    "transfer_to_human",
])
def test_existing_appointment_tools_are_untouched(tool):
    """Deliberately scoped to NEW bookings. A patient with an existing Redditch
    appointment must still be able to move or cancel it — blocking that would
    strand real appointments the clinic already holds."""
    assert _location_not_bookable(tool, REDDITCH, THEOREM) is False


def test_location_falls_back_to_session_when_args_omit_it():
    """The LLM frequently omits location once it is established, so reading
    args alone would let a Redditch booking through on the second call."""
    sess = {"clinic_id": "theorem_v3", "selected_location": "redditch"}
    assert _location_not_bookable("book_appointment", {}, sess) is True


def test_no_location_anywhere_does_not_block():
    """Fail OPEN when there is nothing to judge — a blocked call with no
    location would be an unexplainable dead end for the caller."""
    assert _location_not_bookable("book_appointment", {}, THEOREM) is False


def test_unknown_location_does_not_block():
    assert _location_not_bookable("book_appointment", {"location": "banbury"}, THEOREM) is False


@pytest.mark.parametrize("loc", ["REDDITCH", " Redditch ", "ReDdItCh"])
def test_case_and_whitespace_do_not_defeat_the_guard(loc):
    assert _location_not_bookable("book_appointment", {"location": loc}, THEOREM) is True


def test_guard_follows_the_bookable_flag_not_a_hardcoded_name():
    """The flag is the single toggle for restoring Redditch. If the guard were
    keyed on the literal "redditch", flipping the flag would leave it firing."""
    original = cc.THEOREM_LOCATIONS["redditch"]["bookable"]
    try:
        cc.THEOREM_LOCATIONS["redditch"]["bookable"] = True
        assert _location_not_bookable("book_appointment", REDDITCH, THEOREM) is False
    finally:
        cc.THEOREM_LOCATIONS["redditch"]["bookable"] = original


# ── the blast-radius claim ────────────────────────────────────────────────


@pytest.mark.parametrize("clinic_id", ["jv_v1", "vital_edge", "theorem", "theorem_v2", None])
def test_guard_is_a_NO_OP_for_every_clinic_except_theorem_v3(clinic_id):
    """This is what makes Item 4 safe to land. jv_v1 and vital_edge are live
    on their own branches; if this guard could fire for them, the port would
    be able to break a paying clinic that is not being ported."""
    assert _location_not_bookable(
        "book_appointment", REDDITCH, {"clinic_id": clinic_id}
    ) is False


# ── 4c. the companion sync ────────────────────────────────────────────────


def test_the_sync_exists_in_the_same_file_as_the_guard():
    """4c is a source-level assertion because it lives deep inside run_turn's
    tool loop and is not independently callable. The plan is explicit that the
    guard without the sync reproduces the exact bug that lost a booking, so its
    absence must fail loudly rather than silently."""
    import app.media_streams.llm_stream as ls
    src = open(ls.__file__, encoding="utf-8").read()
    assert 'session["selected_location"] = _av_loc' in src
    assert 'session["v3_location_confirmed"] = True' in src


def test_the_sync_is_gated_on_slots_actually_being_returned():
    """It must sync only after a check that RETURNED slots. Syncing on any
    check_availability would move the caller's location on a failed probe."""
    import app.media_streams.llm_stream as ls
    src = open(ls.__file__, encoding="utf-8").read()
    guard_line = 'if tool_name == "check_availability" and session.get("last_offered_slots"):'
    assert guard_line in src
    assert src.index(guard_line) < src.index('session["selected_location"] = _av_loc')
