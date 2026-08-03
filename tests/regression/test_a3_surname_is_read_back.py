# tests/regression/test_a3_surname_is_read_back.py
"""
A3 — the surname must be spoken back inside the booking read-back.

**Two live calls in a row wrote a wrong surname to a real calendar.**

    2 Aug  parser  "by the way"  -> surname `Way`   event ut7p0a17j71fabqpm9jlpspo24
    3 Aug  STT     "Roch"        -> surname `Rook`  event 94q9h39eo4n9qdm2o0eer81890
                                                    call  CA451f165085a33431137630a188ed871a

Different causes; identical outcome, because the surname was never said aloud.
On the 3 Aug call the proof is in the chunk lengths: the turn-8 read-back TTS
chunk is `len=83`, byte-identical to the turn-6 chunk generated *before any
surname existed*, while `book_appointment` carried `patient_name="Quentin Rook"`.
The name reached the calendar without ever reaching the caller's ear.

The causes of a wrong surname are unbounded — a word list fixed `Way`, and the
next one arrived from a different subsystem eight hours later. The read-back is
the only control that covers the whole class.

**What this test pins, and why each half matters:**

  1. Step 9 must require the FULL name, and its worked example must show one.
  2. The old blanket prohibition — "never read back, spell, or confirm the
     surname" — must be gone from the read-back's path.
  3. **The loop guard must survive.** The prohibition was not arbitrary: B-15
     recorded a caller sent round the surname loop twice on a live call, and
     over-rejecting is the failure that costs more than a wrong name. The
     surname is still never plausibility-checked, never spelled, and never a
     confirmation question of its own — it is said once, in the summary, and
     nowhere else.
  4. The read-back must still fit under the 200-char `last_bot_prompt` cap that
     `B-38` is about, with the surname added.
  5. Scope: the waitlist/callback path is a DIFFERENT write family and must be
     untouched (`REGISTER_B_U` §B-36 — never share a re-steer across families).

Assertions are semantic, not literal. Rewording is fine; dropping the surname
from the read-back, or reintroducing a surname confirmation question, is not.
"""
from __future__ import annotations

import re

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import build_clinic_prompt

# Every clinic whose prompt_engine is template_v1 — i.e. every clinic this
# prompt file actually serves. jv_v1 is the clinic from both incidents.
# theorem/demo carry no prompt_engine key and are served by the legacy
# susie_system_prompt.py, which this change deliberately does NOT touch.
TEMPLATE_CLINICS = ["jv_v1", "vital_edge"]


def _prompt(clinic_id: str) -> str:
    """The static (cacheable) half of the prompt — the behavioural spine, which
    is where the booking steps live. The dynamic half is per-turn CALL STATE."""
    static, _dynamic = build_clinic_prompt({}, get_clinic(clinic_id))
    return static


def _step9(prompt: str) -> str:
    """The WARM READBACK clause, from its heading to the next numbered step.

    Case-insensitive on the anchors so a caller may pass either the rendered
    prompt or a lowercased copy; the slice is returned in its original case.
    """
    hay = prompt.lower()
    start = hay.find("9. warm readback")
    assert start != -1, "Step 9 (WARM READBACK) is not in the prompt at all"
    end = hay.find("10. call book_appointment", start)
    assert end != -1, "Step 10 no longer follows Step 9 — the slice is wrong"
    return prompt[start:end]


# ─────────────────────────────────────────────────────────────────────────────
# 1. The read-back must carry the surname
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_step9_requires_the_full_name(clinic_id):
    """The read-back instruction must name the surname as required content.

    Pre-fix this read: "State caller first name, day, date, and time".
    """
    step9 = _step9(_prompt(clinic_id)).lower()

    assert "surname" in step9, (
        "Step 9 does not mention the surname at all. This is the pre-fix "
        "prompt: the read-back names only the first name, and a mis-heard "
        "surname reaches the calendar unheard (CA451f16, 3 Aug 2026)."
    )
    assert "full name" in step9, (
        "Step 9 must require the caller's FULL name explicitly — 'full name' "
        "is the phrase book_appointment's own contract uses."
    )


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_step9_worked_example_shows_a_surname(clinic_id):
    """The example is what the model actually imitates.

    An example reading "So that's James, Thursday the 7th of May…" teaches
    first-name-only regardless of what the surrounding instruction says.
    """
    step9 = _step9(_prompt(clinic_id))

    m = re.search(r"So that's ([A-Z][a-z]+(?: [A-Z][a-z]+)*)", step9)
    assert m, "Step 9 no longer carries a 'So that's <Name>' worked example"

    example_name = m.group(1)
    assert len(example_name.split()) >= 2, (
        f"Step 9's worked example reads \"So that's {example_name}, …\" — a "
        "single-token name. The model imitates the example over the prose, so "
        "a first-name-only example reproduces the defect."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. The blanket prohibition must be gone from the read-back's path
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_no_blanket_ban_on_reading_the_surname_back(clinic_id):
    """No surviving instruction may forbid reading the surname back outright.

    The prohibition was stated in three places that all reach the booking flow.
    Leaving any one of them contradicts Step 9 on the same turn.
    """
    prompt = _prompt(clinic_id)

    forbidden = [
        "never read back, spell, or confirm the surname",
        "it is NEVER confirmed, read back, spelled, or re-asked",
        "plausibility-checked, confirmed, read back, or spelled",
    ]
    for phrase in forbidden:
        assert phrase not in prompt, (
            f"The prompt still contains {phrase!r}. Step 9 now requires the "
            "surname in the read-back, so this instruction contradicts it on "
            "the same turn and the model will follow one of them at random."
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. The loop guard — the direction this fix could break
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_surname_is_still_never_a_confirmation_question(clinic_id):
    """Saying the surname ONCE in a summary is not the same as confirming it.

    B-15: a caller was sent round the surname loop twice on a live call.
    Over-rejecting costs more than a wrong name — it can lose the booking
    outright. The plausibility rules (PATH 2) must still exclude the surname.
    """
    prompt = _prompt(clinic_id).lower()

    assert "plausibility-checked" in prompt, (
        "The NAME CONFIRMATION RULES no longer exclude the surname from the "
        "plausibility check. PATH 2 confirms unusual names — and a surname is "
        "usually unusual — so this reopens the B-15 surname loop."
    )
    assert "spelled" in prompt, (
        "The prohibition on spelling the surname has been dropped. 'Was that "
        "R-O-C-H?' is the loop B-15 recorded."
    )

    step9 = _step9(prompt)
    assert "do not ask them to spell it" in step9, (
        "Step 9 must say explicitly that the read-back is NOT a spelling "
        "request. Without it, 'say the surname back' reads as 'verify the "
        "surname', which is the loop."
    )
    assert "is that right?" in step9, (
        "Step 9 must explicitly rule out a standalone 'is that right?' about "
        "the surname — the summary already ends in the booking CTA, and two "
        "questions in one turn is what produces the double confirmation."
    )


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_correction_is_accepted_without_a_further_question(clinic_id):
    """A read-back the caller cannot act on is decoration.

    The whole value of A3 is the caller hearing 'Rook' and saying no. Step 9
    must therefore name the correction path, and it must resolve in one
    re-statement rather than opening a spelling exchange.
    """
    step9 = _step9(_prompt(clinic_id)).lower()

    assert "correct" in step9, (
        "Step 9 no longer tells the model what to do when the caller corrects "
        "the read-back — the correction path is the point of the read-back."
    )
    assert "re-state" in step9 or "restate" in step9, (
        "Step 9 must re-state the whole summary after a correction, so the "
        "corrected surname is itself heard back once."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. B-38 — the surname must not push the CTA past the 200-char cap
# ─────────────────────────────────────────────────────────────────────────────

# `last_bot_prompt` is capped at 200 chars in run_turn(). B-38: a read-back long
# enough to push the booking CTA past the cut made every write gate blind to a
# question the caller had just been asked. The live read-back measured 148 chars
# with ~50 to spare — a surname spends part of that margin, so it is pinned here.
_LAST_BOT_PROMPT_CAP = 200

_READBACK_WITH_SURNAME = (
    "So that's Quentin Whitfield, Saturday the 15th of August at quarter past "
    "ten in the morning — shall I go ahead and book that in?"
)


def test_readback_with_a_surname_still_fits_under_the_prompt_cap():
    """The realistic read-back, surname included, must clear the B-38 cap."""
    assert len(_READBACK_WITH_SURNAME) < _LAST_BOT_PROMPT_CAP, (
        f"The read-back with a surname is {len(_READBACK_WITH_SURNAME)} chars, "
        f"at or over the {_LAST_BOT_PROMPT_CAP}-char last_bot_prompt cap. The "
        "booking CTA would be truncated away, which is B-38: the write is "
        "blocked, the caller's 'go ahead' is dropped, and Gate 5f re-steers."
    )


@pytest.mark.parametrize("clinic_id", TEMPLATE_CLINICS)
def test_step9_still_forbids_the_clauses_that_would_overrun_the_cap(clinic_id):
    """The margin exists because Step 9 bans the optional detail.

    Adding the surname is affordable only while the duration, the assessment
    description and the town stay out. If those bans are relaxed later, the
    surname is what tips it over.
    """
    step9 = _step9(_prompt(clinic_id)).lower()

    assert "not the duration" in step9, (
        "Step 9 no longer excludes the duration from the read-back. With the "
        "surname now included, that detail is what pushes the CTA past the cap."
    )
    assert "do not name the town" in step9, (
        "Step 9 no longer excludes the town from the read-back — same cap risk."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Scope — the callback/waitlist family must be untouched
# ─────────────────────────────────────────────────────────────────────────────

def test_waitlist_callback_path_is_unchanged():
    """A different write family. It has no booking read-back to carry a surname.

    REGISTER_B_U §B-36: a re-steer shared across write families was a defect in
    its own right. This change is scoped to the booking read-back; the callback
    path's first-name acknowledgement must survive verbatim.

    Rendered by **vital_edge**, not jv_v1 — the clause is gated on the clinic
    carrying a `booking_horizon_note`, and jv_v1 has none. Asserting against
    jv_v1 here skips silently and guards nothing.
    """
    prompt = _prompt("vital_edge")

    assert "This is a REAL callback for the practitioner" in prompt, (
        "vital_edge no longer renders the booking-horizon callback clause. If "
        "that is intentional, re-point this test at whichever template clinic "
        "does — do not let it lapse into a silent skip."
    )
    assert "read back the FIRST name only" in prompt, (
        "The callback/waitlist name step no longer reads back the first name "
        "only. That path takes details for a practitioner callback — it has no "
        "booking summary — so A3 must not have reached it."
    )
