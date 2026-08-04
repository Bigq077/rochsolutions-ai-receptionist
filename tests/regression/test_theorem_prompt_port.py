"""Item 3 of THEOREM_PORT_PLAN — the _build_theorem_v3 prompt delta.

This is a SELECTIVE MERGE, not a copy. main holds better clinic content;
latency-eval holds a better engine and several deliberate guards main never
got. Porting main's function wholesale would silently revert those, so the
carve-outs below are as load-bearing as the additions and each has its own test.

PORTED FROM MAIN
  - PERSONA CHARACTER + LANGUAGE — SIGNAL EXPERTISE AND CARE
  - REDDITCH REDIRECT, driven by THEOREM_LOCATIONS['redditch']['bookable']
  - PACING and the never-improvise-a-phone-question bullet (slot formatter)
  - the (a)/(b) caller-ID branch — ours assumed a calling number always exists
  - the "Lovely" rule (see below)

NOT PORTED — deliberate
  - main's PHONE HAND-OFF duplicate in SLOT_FORMATTER_SYSTEM_PROMPT (plan 3c).
    latency-eval already carries that contract; a second copy of a
    literal-matched contract is exactly how the two drift apart.
  - main's in-prompt PACING at :3022, which mandates "Number 1 — [time]".
    Our slot format is "Number 1, [day_label] —" and the comma form is parsed
    for keypad selection. Porting it would contradict our own format rule.

KEPT FROM LATENCY-EVAL — main has neither (plan 3d)
  - REQUESTED DAY FULL
  - the presented_days cap

Plan 3b ("Lovely") is RESOLVED as a non-issue, and the plan's framing was wrong.
It asked whether to port main's relaxation over latency-eval's outright ban.
Both of those rules live in get_system_prompt(), the LEGACY pipeline reached only
from flows/conversation.py and routes/realtime.py — Theorem never executes it.

_build_theorem_v3 has always carried its own, narrower rule: never OPEN a slot
readback with an affirmation ("Perfect"/"Great"/"Brilliant"/"Lovely"), while
modelling "Lovely — " as correct in three transition examples. That is already
main's position. No port needed, and the legacy blocks were left untouched.
"""
import pytest

import app.clinic_config as cc
from app.prompts.susie_system_prompt import (
    _build_theorem_v3,
    SLOT_FORMATTER_SYSTEM_PROMPT,
)

SESSION = {
    "clinic_id": "theorem_v3",
    "collected": {},
    "selected_location": "alcester",
    "v3_location_confirmed": True,
}


@pytest.fixture
def static_block():
    return _build_theorem_v3(dict(SESSION))[0]


# ── ported content ────────────────────────────────────────────────────────


@pytest.mark.parametrize("marker", [
    "PERSONA CHARACTER",
    "LANGUAGE — SIGNAL EXPERTISE AND CARE",
])
def test_persona_and_language_blocks_present(static_block, marker):
    assert marker in static_block


def test_caller_id_missing_has_a_deterministic_fallback(static_block):
    """Ours previously said only "The calling number is available in CALL
    STATE" — true usually, but a withheld-caller-ID call had no defined path
    and the model was free to improvise a phone question, which breaks
    downstream number collection."""
    assert "CALL STATE SHOWS NO calling number" in static_block
    assert "STRAIGHT to the keypad line below" in static_block


# ── Redditch redirect, both toggle directions ─────────────────────────────


def test_redditch_redirect_present_when_not_bookable(static_block):
    assert "REDDITCH — NOT BOOKABLE THROUGH SUSIE" in static_block


def test_redditch_redirect_disappears_when_bookable():
    """The flag is the single toggle for re-enabling Redditch. If flipping it
    left the redirect in place, Susie would refuse to book a bookable site."""
    original = cc.THEOREM_LOCATIONS["redditch"]["bookable"]
    try:
        cc.THEOREM_LOCATIONS["redditch"]["bookable"] = True
        s = _build_theorem_v3(dict(SESSION))[0]
        assert "REDDITCH — NOT BOOKABLE" not in s
        assert "\n\n\n" not in s, "empty block left a blank paragraph in the cached prefix"
    finally:
        cc.THEOREM_LOCATIONS["redditch"]["bookable"] = original


def test_redirect_is_booking_only_not_a_general_refusal():
    """Redditch FAQs must still be answered — only booking is redirected."""
    s = _build_theorem_v3(dict(SESSION))[0]
    assert "This redirect is for BOOKING only" in s


# ── slot formatter: the two bullets, and the duplicate we refused ─────────


def test_pacing_bullet_ported():
    assert "• PACING" in SLOT_FORMATTER_SYSTEM_PROMPT


def test_never_improvise_a_phone_question_ported():
    assert "NEVER improvise a phone question" in SLOT_FORMATTER_SYSTEM_PROMPT


def test_phone_handoff_contract_was_NOT_duplicated_into_slot_formatter():
    """Plan 3c. If this fails someone ported main's PHONE HAND-OFF section into
    the shared formatter. There would then be two copies of a contract that is
    matched by literal string downstream, and they will drift."""
    assert "── PHONE HAND-OFF" not in SLOT_FORMATTER_SYSTEM_PROMPT


def test_slot_format_stays_comma_form_not_main_em_dash_form():
    """Plan carve-out. main:3022 mandates "Number 1 — [time]". Ours is
    "Number 1, [day_label] —" and the comma form is parsed for keypad
    selection, so the two cannot both be true."""
    assert "Number 1, [day_label] —" in SLOT_FORMATTER_SYSTEM_PROMPT
    assert "Number 1 — half past nine" not in SLOT_FORMATTER_SYSTEM_PROMPT


# ── what latency-eval keeps that main never had (plan 3d) ─────────────────


def test_requested_day_full_survives_the_port():
    assert "REQUESTED DAY FULL" in SLOT_FORMATTER_SYSTEM_PROMPT


def test_presented_days_cap_survives_the_port():
    import app.prompts.susie_system_prompt as sp
    src = open(sp.__file__, encoding="utf-8").read()
    assert "presented_days" in src


def test_reschedule_closing_fix_survives_the_port(static_block):
    """f94c7e7 aligned Theorem's reschedule closing to the wording Gate 5f can
    actually see. main does not have that fix, so any wholesale copy of main's
    function silently reverts it."""
    assert "That's you rescheduled" in static_block


def test_canonical_prices_survive_the_port(static_block):
    assert "£85" in static_block
    assert "£75" not in static_block


# ── "Lovely": relaxed, but the name echo stays banned ─────────────────────


def test_lovely_is_banned_as_an_affirmation_opener(static_block):
    """Theorem's scoped ban: never OPEN a slot readback with an affirmation.
    This is the part that must not be relaxed."""
    assert "NEVER open with 'Perfect', 'Great', 'Brilliant', 'Lovely'" in static_block


def test_lovely_still_permitted_as_a_warm_transition(static_block):
    """And the part that must not be tightened. Theorem's prompt models
    "Lovely — " as correct output, so an outright ban would contradict its own
    examples."""
    assert "'Lovely — let me just confirm the details.'" in static_block


# ── screening stays out ───────────────────────────────────────────────────


@pytest.mark.parametrize("term", [
    "clinical_screening",
    "cauda equina",
    "night sweats",
    "red flag",
])
def test_no_screening_language_entered_the_prompt(static_block, term):
    """Mark wants no clinical triage. The prompt must not acquire screening
    questions by way of a ported block."""
    assert term.lower() not in static_block.lower()
