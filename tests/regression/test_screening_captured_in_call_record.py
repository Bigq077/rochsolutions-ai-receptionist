"""Screening state must reach the durable call record, not just the log text.

Jules's 2026-07-25 sweep produced `dvt ORPHAN×1, ARMED×0` — the deterministic
layer dormant and the model silently doing the whole job. That is the most
important finding of the night and it was visible only to a human reading a full
call log: none of it was captured, so it could not be queried, trended across a
sweep, or alerted on.

`screening.arm_paths` is the field that fixes it: {screen_id: how_it_armed}.
"trigger" means Layer 1 caught the presentation; "orphan" means only the model
did. An "orphan" with no "trigger" anywhere in a day's calls is the dormant-
Layer-1 signature, and it is now one query.
"""
from __future__ import annotations

import pytest

from app.call_logger import CallLogger
from app.media_streams import clinical_screening as cs
from tests.screening_fixture import screening_clinic


@pytest.fixture
def clinic():
    from app.clinic_config import get_clinic
    c = screening_clinic()
    assert cs.screening_enabled(c)
    return c


def _record(session):
    return CallLogger("CAtest", session).build_record()


# ─────────────────────────────────────────────────────────────────────────
# Arm paths — the point of the change
# ─────────────────────────────────────────────────────────────────────────
def test_trigger_arm_is_recorded_as_trigger(clinic):
    """Layer 1 caught the presentation — the healthy path."""
    sess = {}
    cs.update_screening_state(sess, clinic, "I had a fall off my bike")

    assert sess[cs.SCREEN_ARM_PATHS_KEY] == {"trauma_fracture": cs.ARM_TRIGGER}
    assert _record(sess)["screening"]["arm_paths"] == {
        "trauma_fracture": "trigger"
    }


def test_orphan_arm_is_recorded_as_orphan(clinic):
    """The model asked it; Layer 1 never armed. THE signature to be able to query."""
    q = cs.get_screen(clinic, "trauma_fracture")["screen_question"]
    sess = {"last_bot_prompt": q, "last_question": q}

    cs.update_screening_state(sess, clinic, "no its all fine, no swelling")

    assert sess[cs.SCREEN_ARM_PATHS_KEY] == {"trauma_fracture": cs.ARM_ORPHAN}
    assert _record(sess)["screening"]["arm_paths"] == {
        "trauma_fracture": "orphan"
    }


def test_dormant_layer_1_is_detectable_from_the_record_alone(clinic):
    """The sweep finding, reconstructed: an orphan with no trigger anywhere.

    This is the assertion an alert or a dashboard query would make.
    """
    q = cs.get_screen(clinic, "dvt")["screen_question"]
    sess = {"last_bot_prompt": q, "last_question": q}
    # The answer must contain no DVT trigger word — otherwise Layer 1 legitimately
    # arms and the path is 'model_asked', not 'orphan'. (Was "no its not swollen or
    # warm", which now matches the STT-robust "swollen warm" symptom-combo trigger
    # added 2026-07-27 for F-017.)
    cs.update_screening_state(sess, clinic, "no nothing like that at all")

    paths = _record(sess)["screening"]["arm_paths"]
    assert "orphan" in paths.values()
    assert "trigger" not in paths.values()   # <- dormant Layer 1


def test_first_arm_wins_when_a_screen_re_arms(clinic):
    """A re-arm is not new information; the original path must not be overwritten."""
    sess = {}
    cs.update_screening_state(sess, clinic, "I had a fall off my bike")
    assert sess[cs.SCREEN_ARM_PATHS_KEY]["trauma_fracture"] == cs.ARM_TRIGGER

    cs._arm(sess, "trauma_fracture", cs.ARM_ORPHAN)

    assert sess[cs.SCREEN_ARM_PATHS_KEY]["trauma_fracture"] == cs.ARM_TRIGGER


def _turn(sess, clinic, said):
    """One caller turn, mirroring connection.py: dispatch, then record whatever
    Susie spoke as last_bot_prompt. Without that write the screen can never be
    resolved (_question_was_asked reads it), so a test that omits it silently
    leaves the first screen pending forever and no second screen can arm."""
    result = cs.update_screening_state(sess, clinic, said)
    if result.get("speak"):
        sess["last_bot_prompt"] = sess["last_question"] = result["speak"]
    return result


def test_two_screens_record_independently(clinic):
    sess = {}
    _turn(sess, clinic, "I had a fall off my bike")   # arms trauma, asks it
    _turn(sess, clinic, "no its fine")                # clears trauma
    _turn(sess, clinic, "my lower back hurts")        # arms cauda

    assert _record(sess)["screening"]["arm_paths"] == {
        "trauma_fracture": "trigger",
        "cauda_equina": "trigger",
    }
    assert _record(sess)["screening"]["completed"] == ["trauma_fracture"]


# ─────────────────────────────────────────────────────────────────────────
# The rest of the block
# ─────────────────────────────────────────────────────────────────────────
def test_red_flag_and_escalation_are_captured(clinic):
    sess = {}
    cs.update_screening_state(sess, clinic, "my lower back hurts")
    q = cs.get_screen(clinic, "cauda_equina")["screen_question"]
    sess["last_bot_prompt"] = sess["last_question"] = q
    sess["safety_escalation"] = True          # set by connection.py on escalate
    cs.update_screening_state(sess, clinic, "yes Ive gone numb between my legs")

    block = _record(sess)["screening"]
    assert block["red_flag"] == "cauda_equina"
    assert block["safety_escalation"] is True
    assert "cauda_equina" in block["completed"]


def test_unresolved_screen_shows_as_pending_at_end(clinic):
    """A screen still pending at teardown was never answered — worth querying."""
    sess = {}
    cs.update_screening_state(sess, clinic, "my lower back hurts")

    assert _record(sess)["screening"]["pending_at_end"] == "cauda_equina"


def test_truncated_answer_is_captured(clinic):
    """The 188e478 guard firing is itself a finding worth trending."""
    q = cs.get_screen(clinic, "trauma_fracture")["screen_question"]
    sess = {"last_bot_prompt": q, "last_question": q,
            cs.PENDING_SCREEN_KEY: "trauma_fracture"}

    cs.update_screening_state(sess, clinic, "it fine and there's no marks where")

    assert _record(sess)["screening"]["truncated"] == ["trauma_fracture"]


def test_no_screening_yields_an_empty_block(clinic):
    """A benign call must leave the column null, not full of empty structures."""
    sess = {}
    cs.update_screening_state(sess, clinic, "my hamstring is tight from running")

    assert _record(sess)["screening"] == {}


def test_record_shape_is_unchanged_otherwise():
    """Additive only — nothing existing may have moved."""
    rec = _record({})
    for key in ("call_sid", "clinic_id", "duration_s", "collected",
                "booking_confirmed", "turn_count", "total_retries", "tone"):
        assert key in rec, key
    assert rec["screening"] == {}


# ─────────────────────────────────────────────────────────────────────────
# The obs row
# ─────────────────────────────────────────────────────────────────────────
def test_screening_reaches_the_obs_row(clinic):
    """It must survive the record → Call row mapping, or the column stays null."""
    from app.obs.store import _row_from_record

    sess = {}
    cs.update_screening_state(sess, clinic, "I had a fall off my bike")
    row = _row_from_record(_record(sess), [{"role": "user", "text": "hi"}])

    assert row.screening["arm_paths"] == {"trauma_fracture": "trigger"}


def test_empty_screening_is_stored_as_null_not_empty_dict(clinic):
    from app.obs.store import _row_from_record

    row = _row_from_record(_record({}), [])
    assert row.screening is None


def test_column_is_registered_for_additive_migration():
    """Existing databases get the column via _ensure_new_columns, not a rebuild."""
    from app.obs.store import _ADDED_COLUMNS
    assert _ADDED_COLUMNS.get("screening") == "JSON"


def test_to_dict_exposes_screening():
    """replay / to_scenario / reports all read through to_dict."""
    from app.obs.models import Call
    c = Call(call_sid="CAx", screening={"arm_paths": {"dvt": "orphan"}})
    assert c.to_dict()["screening"] == {"arm_paths": {"dvt": "orphan"}}
