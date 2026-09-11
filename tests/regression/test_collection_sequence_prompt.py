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


# ── B1, SUPERSEDED · the engine speaks the times, numbered ────────────────
#
# B1 asked this prompt for "exactly TWO times ... as ONE natural sentence with
# no numbered list", on a measurement that three days by two times took 24.1 s.
# Two owner decisions superseded it, and `receptionist_tools` records both at
# `_MAX_PRESENTED_TIMES_MULTI_DAY`:
#
#   1 Sep 2026 — two times per day across up to three days. Each day is ONE
#                numbered option carrying two times, so the caller holds three
#                choices and not six, and the deterministic sentence is fixed
#                and short where the 24.1 s was a model improvising around the
#                list. Its length is asserted by
#                test_the_three_by_two_readout_stays_short.
#   9 Sep 2026 — a week is a MENU OF DAYS; a named day is three numbered times.
#
# The prompt was never brought with them, so for ten days this file's clinics --
# jv_v1, northgate, vital_edge, i.e. the demo line and two of the three live
# ones -- told the model to present slots in a format the engine's own tests
# forbid, while theorem_v3's prompt and SLOT_FORMATTER_SYSTEM_PROMPT both said
# numbered. That is `SLOT_PRESENTATION_ANALYSIS_2026-09-11.md` §2.4, and the
# turns where no producer claims the readout -- 74 of 104 since 3 Sep -- are
# where the model followed it.
#
# THIS TEST WAS ONE OF THE PINS. It asserted the superseded decision as
# correct, which is §3.6's third mechanism ("a correct test of an earlier
# decision becomes a defect pin"), the same way N1 was pinned by the replay
# gate and four tests. Rewritten to assert the decision that is actually in
# force, with the supersession recorded above rather than in a commit message
# nobody will find.

def test_the_numbering_is_structure_not_decoration(prompt):
    """The options are parsed for keypad entry: a flat list cannot be
    selected by number, which is B-80/P9/P11's whole family."""
    assert "number 1," in prompt
    assert "parsed for keypad" in prompt
    assert "flat sentence" in prompt


def test_the_prompt_no_longer_forbids_the_format_the_engine_speaks(prompt):
    assert "no numbered list" not in prompt
    assert "offer exactly two times" not in prompt
    assert "two — not three, not six" not in prompt


def test_only_the_engine_authors_a_slot_time(prompt):
    """Invariant 1 and the `one author` rule, as the model is told it."""
    assert "the engine speaks the times, not you" in prompt
    assert "slot_times_spoken" in prompt
    assert "never convert a 24-hour time yourself" in prompt


def test_a_confirmation_may_only_name_a_time_that_was_offered(prompt):
    """CA7ebc00839bf773bcf7cbaa52d7c60f7e: "10 to 12 works" was confirmed as
    "twenty to twelve", which is 11:40 and not on northgate's grid."""
    assert "must be one you read out of the data" in prompt
    assert "do not guess" in prompt


def test_a_named_day_is_answered_with_that_day(prompt):
    """N1 and N6. "What about Monday" withdrew both Monday times; "what else
    on Monday" was answered with Thursday."""
    assert "never answer with a different day" in prompt


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
