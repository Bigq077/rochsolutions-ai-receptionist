"""Two findings from the demo call of 2026-08-30, build 9e8d22bcc8fa.

That call confirmed all four fixes from the night before and was clean on the
call sheet's own criteria. These are what it surfaced that was new. Neither is
a safety defect; both are the kind of thing a caller notices.

  2. The same sentence spoken twice, six seconds apart.
  3. The session length asked twice, two minutes after it was answered.

THIS IS THE PATIENT-BRANCH COPY AND IT IS DELIBERATELY SHORTER THAN CANONICAL'S.
Findings 1 and 4 are both about hold speech and neither exists here: this branch
has no `app/hold_speech.py` (`git cat-file -e` says so on all three patient
branches), and no `offered_slot_labels` / `utterance_is_slot_selection` in
`app/media_streams/connection.py` either. Finding 1's section imported all five
of those names at module scope, so carrying it over does not fail -- it is a
COLLECTION error that interrupts the whole run and reports no failures at all.
Section 1 is therefore removed rather than skipped, so that the absence is a
fact about this branch rather than a silently-green test.

When hold speech is ported here, restore section 1 from canonical's copy of
this file; do not rewrite it.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    exhaustion_offer_signature,
    exhaustion_sentence_already_said,
    note_exhaustion_sentence_said,
)


# ═══ 2. The same sentence twice, six seconds apart ═════════════════════════
#
#   23:59:46  "I don't have any further times on that day — would you like me
#              to look at a different day?"
#   23:59:53  ... the identical sentence, after the caller said something new.

SLOTS = [{"start": "2026-09-05T10:05:00"}, {"start": "2026-09-05T11:10:00"}]


def test_the_exhaustion_sentence_is_earned_once_per_offer():
    session = {"last_offered_slots": list(SLOTS)}
    assert not exhaustion_sentence_already_said(session)
    note_exhaustion_sentence_said(session)
    assert exhaustion_sentence_already_said(session), (
        "the second ask would get the identical sentence again"
    )


def test_a_fresh_offer_earns_it_again():
    """A real lookup that puts new times on the table is a NEW fact. Latching
    on the day alone would swallow a correct answer."""
    session = {"last_offered_slots": list(SLOTS)}
    note_exhaustion_sentence_said(session)
    session["last_offered_slots"] = [{"start": "2026-09-05T12:15:00"}]
    assert not exhaustion_sentence_already_said(session)


def test_the_signature_is_order_independent():
    """The same two slots in the other order are the same offer."""
    a = {"last_offered_slots": list(SLOTS)}
    b = {"last_offered_slots": list(reversed(SLOTS))}
    assert exhaustion_offer_signature(a) == exhaustion_offer_signature(b)


def test_an_unreadable_session_never_suppresses():
    """Fails OPEN, and the direction is deliberate: the sentence is TRUE by the
    time it is reached (exhaustion_claim_is_supported has already passed), so a
    wrong False costs one repetition while a wrong True swallows a correct
    answer the first time the caller asks."""
    for bad in ({}, {"last_offered_slots": []}, {"last_offered_slots": "nope"}):
        assert exhaustion_sentence_already_said(bad) is False
        assert exhaustion_offer_signature(bad) == ""


def test_the_follow_up_path_declines_the_second_time():
    """End to end through the real dispatcher, not just the helper."""
    from app.tools import slot_followup as sf

    session = {
        "last_offered_slots": list(SLOTS),
        "available_days": [{"date": "2026-09-05", "times_not_shown": 0}],
        "collected": {},
    }
    first = sf.try_unspoken_followup_speech(session, "have you got anything else")
    assert first and "further times on that day" in first, first
    second = sf.try_unspoken_followup_speech(session, "anything else at all")
    assert second is None, (
        f"the identical sentence came back a second time: {second!r}"
    )


# ═══ 3. The session length was asked twice ════════════════════════════════
#
#   23:59:07  caller: "uh i care for a 60-minute one please"
#             engine: session length captured: 60 minutes
#   00:01:27  Susie:  "would you like a 30-minute session at thirty-eight
#                      pounds or a 60-minute at sixty-two?"
#
# The capture was right and the booking was written at a real 60 minutes. The
# model simply had no way to see that the question was settled.

def _a_template_v1_clinic() -> str:
    """A clinic id THIS branch actually ships, not a hardcoded one.

    Canonical renders `northgate` -- the demo line's clinic, which no patient
    branch has. The hardcoded version does not fail the assertion when ported:
    `get_clinic` on an unknown id returns a shape whose `services` is a list of
    strings, and the renderer dies with `AttributeError: 'str' object has no
    attribute 'get'` deep inside `clinic_template_prompt`. That reads as a
    broken port rather than as a test pinned to somebody else's clinic.

    `_b7_call_state` is per-renderer, not per-clinic, so any `template_v1`
    clinic proves the same thing.
    """
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "app" / "clinics"
    for d in sorted(root.iterdir()):
        try:
            cfg = json.loads((d / "clinic.json").read_text(encoding="utf-8"))
        except Exception:
            continue  # `demo/` is not always valid JSON on every branch
        if cfg.get("prompt_engine") == "template_v1":
            return d.name
    pytest.skip("this branch ships no template_v1 clinic")


CLINIC = _a_template_v1_clinic


def _call_state(session: dict) -> str:
    from app.clinic_config import get_clinic
    from app.prompts.clinic_template_prompt import build_clinic_prompt

    static, dynamic = build_clinic_prompt(session, get_clinic(session["clinic_id"]))
    for blob in (static, dynamic):
        for line in blob.split("\n"):
            if "already known (do NOT re-ask)" in line:
                return line
    return ""


def test_a_captured_session_length_reaches_the_model():
    line = _call_state({
        "clinic_id": CLINIC(),
        "turn_count": 3,
        "collected": {},
        "_service_duration_choice": 60,
    })
    assert "session_length=60 minutes" in line, (
        "the captured length is not in CALL STATE, so the model cannot know "
        "the question was answered and will ask it again — A4"
    )


def test_no_length_no_line():
    """It must not assert a length the caller never chose."""
    line = _call_state({
        "clinic_id": CLINIC(),
        "turn_count": 3,
        "collected": {"name": "Quentin"},
    })
    assert "session_length" not in line, line


def test_it_rides_in_the_do_not_re_ask_list_rather_than_a_new_rule():
    """Deliberately NOT a suppression rule.

    "Suppression cannot beat an instruction" — three times in this codebase a
    question was deleted from the output while the schema or a prompt line
    still asked for it, and the model simply re-asked in different words. The
    value being visibly KNOWN is what closes the question.
    """
    line = _call_state({
        "clinic_id": CLINIC(),
        "turn_count": 3,
        "collected": {"name": "Quentin"},
        "_service_duration_choice": 90,
    })
    assert line.startswith("CALL STATE:") or "already known" in line
    assert "session_length=90 minutes" in line
    assert "name=Quentin" in line, "it must not displace the existing facts"
