"""CA89f061856a (14 Sep 2026, demo) — the model corrected itself aloud.

    Susie   "ankles can be tricky — worth getting it looked at properly."
    Susie   "Do you have a preference for when you'd like to come in?"
    Susie   "Wait, I already have urgency noted — let me check what we've got."
    Susie   "I've got a few days — Number 1, …"

keep_pre_slot_speech (northgate, jv_v1) keeps pre-tool text, and Gate 5 had
nothing for a self-correction. Rule `reasoning_wait_self_correction`: a
sentence that OPENS with "Wait" + comma/dash is removed, that sentence only.

Blast radius, measured 14 Sep 2026 with scripts/audit_gate5_blast_radius.py
over every recorded assistant turn (1,042 calls, 11,183 turns): the rule
fired 8 times, all 8 self-corrections, every one listed below. The only turn
emptied was the CA89f061856a pre-tool chunk itself, which live was followed
by the slot readout in the same reply (stop_reason tool_use -> no fallback).
"""
import pytest

from app.media_streams.turn_handler import sanitise_response

# Every occurrence in the corpus, verbatim.
CORPUS = [
    ("CA89f061856a", "Wait, I already have urgency noted — let me check what we've got.", ""),
    ("CA6e1024db24",
     "Wait — I don't actually have a slot confirmed yet. Do you have a preference for when "
     "you'd like to come in for your ankle appointment?",
     "Do you have a preference for when you'd like to come in for your ankle appointment?"),
    ("CA49947b7f0d",
     "Do you have a preference for when you'd like to reschedule to? Wait, let me look at "
     "what's actually available for you.",
     "Do you have a preference for when you'd like to reschedule to?"),
    ("CAec93b03204",
     "Could you tell me about the shoulder? Wait, I shouldn't ask that. Let me just ask "
     "about the shoulder itself.",
     "Could you tell me about the shoulder?"),
    ("CAfe6a41626d",
     "Do you have any changes in your bladder or bowel control? Wait, that's the wrong "
     "screen — that's for back pain.",
     "Do you have any changes in your bladder or bowel control?"),
]


def _s():
    return {"clinic_id": "northgate", "conversation_history": [], "collected": {}}


@pytest.mark.parametrize("sid, said, left", CORPUS, ids=[c[0] for c in CORPUS])
def test_the_self_correction_sentence_goes_and_only_it(sid, said, left):
    out = sanitise_response(said, _s()).strip()
    assert "wait" not in out.lower(), out
    if left:
        assert left in out, out


def test_the_nhs_111_advice_around_it_is_untouched():
    """CA9c98308ce2 (jv_v1): the red-flag advice must survive word for word."""
    advice = ("Those symptoms need checking urgently rather than waiting for a physio "
              "appointment — please contact NHS 111 now, or go straight to A&E if it's "
              "severe, and please don't have it massaged until you've been checked.")
    said = advice + " Wait — I can see from our conversation that you've already confirmed the calf isn't swollen or warm, so that's reassuring."
    out = sanitise_response(said, _s())
    assert advice in out
    assert "Wait" not in out


@pytest.mark.parametrize("keep", [
    "Please wait a moment while I check that.",
    "There's no need to wait — Tuesday at ten is free.",
    "Wait times are usually under a week.",
    "We do keep a waiting list if nothing suits.",
    "You won't have to wait long, there's a slot on Monday.",
])
def test_ordinary_uses_of_wait_are_left_alone(keep):
    out = sanitise_response(keep, _s())
    assert "wait" in out.lower(), (keep, out)
