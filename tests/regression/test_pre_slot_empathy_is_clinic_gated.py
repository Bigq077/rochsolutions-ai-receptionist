"""Job 3c.3 / CAce1457d1 — JV keeps pre-slot empathy; others still suppress.

CAce1457d1 logged:

    [ms_tts] pre-slot chunk suppressed — check_availability detected this turn:
             "I'm sorry to hear that — ankle problems can really stop you "

The model produced the physio line; `_pre_slot_cancelled` dropped it. The
suppress is engine-wide on purpose (stops half-finished text before the slot
list) and must not be deleted. Joint Venture opts in via
prompt_facts.keep_pre_slot_speech so only that clinic hears the line.
"""
from __future__ import annotations

from app.media_streams.llm_stream import _clinic_keeps_pre_slot_speech


def test_jv_opts_in_to_keep_pre_slot_speech():
    assert _clinic_keeps_pre_slot_speech({"clinic_id": "jv_v1"}) is True


def test_other_clinics_still_suppress_by_default():
    assert _clinic_keeps_pre_slot_speech({"clinic_id": "vital_edge"}) is False
    assert _clinic_keeps_pre_slot_speech({"clinic_id": "theorem_v3"}) is False
    assert _clinic_keeps_pre_slot_speech({"clinic_id": "demo"}) is False


def test_cancel_path_is_clinic_gated_in_source():
    """Pin the call site: cancel must consult the helper, not fire unconditionally."""
    from pathlib import Path
    src = Path("app/media_streams/llm_stream.py").read_text()
    assert "_clinic_keeps_pre_slot_speech(session)" in src
    # Default path still cancels.
    assert 'session["_pre_slot_cancelled"] = True' in src
