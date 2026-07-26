# tests/regression/test_collection_sequence_prompt.py
"""
Block A/B of the pre-demo fix queue, pinned at the prompt level.

Three defects from call `CA4969580082db5e757c3b1d04dd38e7ae` (2026-07-26), all in
one stretch of conversation:

  B1  six slots across three days in one breath — 24.1 s on the worst turn; on
      the 25 Jul test call the caller hung up nine seconds into it.
  A2  the reason was asked AFTER the slots were offered, the caller ignored it
      and answered the slot, and the booking completed with reason=None.
  A1  a whole turn spent asking for a number we had from caller ID, followed by
      "Thanks — I already have your number confirmed."

These are prompt contracts, so they are asserted against the rendered prompt
rather than driven through a call. They are wording-sensitive by nature: if you
deliberately reword a step, update the assertion — but do not delete it, because
each one marks a defect that reached a real caller.

`_phone_step_asked` (here and in llm_stream) is what stops the phone steer and
the book_appointment backstop from looping, and it matches on SPOKEN phrases. The
last test is the important one: the new Step 8 wording must still be recognised
as "the phone question was asked", or the steer re-fires forever.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import (
    build_clinic_prompt,
    _phone_step_asked,
)

CLINIC_ID = "jv_v1"


@pytest.fixture()
def prompt() -> str:
    """The full rendered prompt, with a caller ID present — CALL STATE only
    renders the phone line when twilio_from_local is set, which on a real call
    it always is."""
    clinic = get_clinic(CLINIC_ID)
    session = {"clinic_id": CLINIC_ID, "twilio_from_local": "07502211207"}
    static, dynamic = build_clinic_prompt(session, clinic)
    return f"{static}\n{dynamic}".lower()


# ── B1 · two slots, not six ───────────────────────────────────────────────
def test_slot_presentation_offers_two_times(prompt):
    assert "offer exactly two times" in prompt
    assert "two — not three, not six" in prompt


def test_slot_presentation_no_longer_asks_for_three_days(prompt):
    assert "present exactly three days" not in prompt
    assert "number 3, [day] the [date]" not in prompt, (
        "the three-day numbered list is what measured 24.1 s"
    )


# ── A2 · reason before availability ───────────────────────────────────────
def test_reason_step_exists_before_timing(prompt):
    assert "1b. reason" in prompt
    assert prompt.index("1b. reason") < prompt.index("4. say one filler"), (
        "the reason step must come before the availability call"
    )


def test_availability_is_gated_on_the_reason(prompt):
    assert "until you know both the reason (step 1b) and the timing" in prompt


def test_reason_must_not_be_asked_after_slots(prompt):
    assert "never ask it after presenting slots" in prompt


def test_book_appointment_is_told_to_pass_the_reason(prompt):
    assert "refuses the booking without one" in prompt, (
        "the model must know the tool gate exists, or it will ask twice"
    )


# ── A1 · read the number back, never ask for it ───────────────────────────
def test_phone_step_reads_the_number_back(prompt):
    assert "read it back; do not ask them for it" in prompt
    assert "is that the best number for the booking" in prompt


def test_phone_step_no_longer_teaches_a_magic_phrase(prompt):
    # The old Step 8 question, verbatim. (The phrase still appears inside the
    # NEGATIVE instruction telling the model never to say it — so match the
    # question form, not the bare phrase.)
    assert "if so, just say use this number" not in prompt, (
        "the caller had to utter a set phrase; a plain yes now works"
    )
    assert "never ask the caller to say a set phrase" in prompt


def test_call_state_stops_advertising_no_readback(prompt):
    assert "no readback needed" not in prompt
    assert "never ask the caller to supply a number you are holding" in prompt


# ── The anti-loop contract ────────────────────────────────────────────────
@pytest.mark.parametrize("spoken", [
    "I've got you on oh seven five oh two, two one one, two oh seven — is that "
    "the best number for the booking?",
    "I've got you on 07502 211207 — is that the best number for the booking?",
    # Clipped mid-sentence by a barge-in: the opener alone must still count.
    "I've got you on oh seven five oh two",
])
def test_new_phone_wording_counts_as_asked(spoken):
    """If this fails, the phone steer and the book_appointment backstop both
    re-fire after the question has already been put to the caller."""
    session = {"last_bot_prompt": spoken, "conversation_history": []}
    assert _phone_step_asked(session) is True


def test_marker_lists_stay_in_sync():
    """The list is deliberately duplicated to avoid a media_streams -> prompts
    import cycle; duplicated means it can drift."""
    from app.media_streams.llm_stream import _PHONE_STEP_MARKERS as ms
    from app.prompts.clinic_template_prompt import _PHONE_STEP_MARKERS as pr
    assert set(ms) == set(pr)
