"""northgate stops screening and stops interrogating — and still says 999.

OWNER decision (Quentin), 2026-09-05, DEMO LINE ONLY. Two separate changes that
were discussed as one, and the distinction is the point of this file.

WHAT PROMPTED IT — CA3c3ca344, 2026-09-04 23:49:44

    caller: "yeah i'd like to book an appointment for my ankle
             it's nothing serious though"
    Susie:  "ankle pain can be tricky — is it more of a general ache, or does
             it catch you at certain times? Actually, let me get you sorted.
             Do you have a preference for when you'd like to come in?"

The owner read that as clinical screening. It was NOT: `match_screen_trigger`
returns None for that utterance and the log carries no `screen ... ARMED` line.
It came from the CONDITION FLUENCY mandate, which required "specific
understanding" for any NAMED BODY PART while exempting only descriptions with
no body part at all. "Ankle" is a named body part, nothing in the library
matches unspecified ankle pain, so the model asked a clinical question to
manufacture the specificity the rule demanded — then hit "do NOT ask two
questions" mid-turn and talked over itself. 8.7s to first content, the slowest
turn of the call.

THE TWO CHANGES

A. `condition_knowledge.mandatory: false` — the library stays, the COMPULSION
   goes, and interrogating to satisfy the step is now banned outright. Not a
   safety setting: fluency is warmth and perceived expertise.

B. `clinical_screening.enabled: false` — the six red-flag screens stop.
   The argument is ROLE COHERENCE, not base rates. A receptionist who asks a
   red-flag question and then says "that's reassuring" and books has
   ADJUDICATED, on a recorded line, without being a clinician and without the
   screens being validated. Declining to ask is staying in lane; asking and
   clearing is triage done badly. Red-flag screening still happens where it is
   trained and indemnified for — at the assessment.

WHAT SURVIVES, AND WHY THIS FILE EXISTS

The emergency intercept and the URGENT-CARE SAFETY NET both stay. A caller who
VOLUNTEERS a red flag is still told to seek urgent care; what stops is Susie
asking and grading. That split is only possible because `emergency_keywords()`
reads the keywords being CONFIGURED and never reads `enabled` — if someone
"tidies" that to go through `screening_config()`, B silently takes the 999
response with it and nothing fails. That is the regression this file exists to
catch.

ACCEPTED RESIDUAL RISK, recorded rather than glossed: the presentations these
screens target are the ones that do NOT feel serious to the caller — DVT reads
as a strained calf, cauda equina as ordinary back pain. Those callers are now
booked rather than asked. The risk is transferred to the assessment, not
removed.

SCOPE IS PART OF THE DECISION. jv_v1 carries the same 39-condition library AND
all six screens and is deliberately UNCHANGED — turning them off for a line
with real patients changes the risk posture of a practice carrying its own
indemnity, and is a separate, recorded decision. The jv_v1 assertions below are
not decoration; they are the scope pin.

Deterministic: no model, no network.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams.clinical_screening import (
    detect_emergency,
    emergency_intercept_enabled,
    emergency_keywords,
    match_screen_trigger,
    screening_enabled,
)
from app.prompts.clinic_template_prompt import (
    _screening_renders,
    build_clinic_prompt,
)


def _prompt(clinic_id):
    clinic = get_clinic(clinic_id)
    a, b = build_clinic_prompt({"clinic_id": clinic_id}, clinic)
    return (a or "") + "\n" + (b or "")


# ── B: the screens are off ─────────────────────────────────────────────────

def test_northgate_asks_no_screening_questions():
    clinic = get_clinic("northgate")
    assert screening_enabled(clinic) is False
    assert match_screen_trigger(
        "my lower back has been really bad and my leg's gone numb", clinic, {}
    ) is None


def test_the_screening_block_is_out_of_the_prompt():
    text = _prompt("northgate")
    assert "CLINICAL SAFETY SCREENING" not in text
    assert "cauda" not in text.lower()


def test_no_rule_defers_to_a_block_that_is_not_there():
    """The dangling-reference check.

    Three sentences elsewhere in the prompt said "the screen comes first". With
    the block gone they pointed at nothing — a conditional whose condition can
    never be met, which is inert at best and an invitation to invent the
    missing block at worst.
    """
    text = _prompt("northgate")
    assert "safety screen matches" not in text
    assert "screen comes first" not in text


# ── C: what must NOT have gone with it ─────────────────────────────────────

def test_a_volunteered_emergency_still_gets_999():
    """The load-bearing one.

    `emergency_keywords()` keys on the keywords being CONFIGURED, never on
    `enabled`. Route it through `screening_config()` and switching the screens
    off silently takes the emergency response too — with no test failing and
    nothing in the log.
    """
    clinic = get_clinic("northgate")
    assert emergency_intercept_enabled(clinic) is True
    assert len(emergency_keywords(clinic)) >= 20
    assert detect_emergency("i've got chest pain and i can't breathe", clinic)


def test_the_urgent_care_net_is_still_in_the_prompt():
    text = _prompt("northgate")
    assert "URGENT-CARE SAFETY NET" in text
    assert "999" in text
    assert "NHS 111" in text


# ── A: the compulsion is gone, the capability is not ───────────────────────

def test_the_mandate_no_longer_compels_specific_understanding():
    text = _prompt("northgate")
    assert "MANDATORY for specific complaints" not in text


def test_interrogating_to_satisfy_the_step_is_banned():
    """The actual defect, stated as a rule rather than hoped for."""
    text = _prompt("northgate")
    assert "NEVER ASK A CLINICAL QUESTION IN ORDER TO SATISFY THIS STEP" in text
    assert "brief warm acknowledgement is the RIGHT answer" in text


def test_the_condition_library_is_kept():
    """Trimmed, not deleted. A caller who describes a real presentation should
    still get a specific answer — that is the commercial value, and removing it
    buys no safety."""
    text = _prompt("northgate")
    assert "CONDITION FLUENCY" in text
    assert "Achilles tendinopathy" in text
    assert len((get_clinic("northgate").get("condition_knowledge") or {})
               .get("conditions") or []) > 30


def test_the_rest_of_the_clinical_prompt_survived():
    """`_special_case_clinical` is assigned in the same if/elif/else chain the
    override sits after. An earlier attempt made it a new BRANCH instead, which
    left that variable unbound and raised UnboundLocalError on every northgate
    call — caught before commit, pinned here."""
    text = _prompt("northgate")
    assert "SPECIAL-CASE CLINICAL" in text


# ── The scope pin ──────────────────────────────────────────────────────────

def test_jv_is_deliberately_untouched():
    """jv_v1 has the same library and the same six screens, and keeps both.

    If this fails, a change scoped to the demo line has reached a line with
    real patients.
    """
    clinic = get_clinic("jv_v1")
    assert screening_enabled(clinic) is True
    assert len((clinic.get("clinical_screening") or {}).get("screens") or []) == 6
    text = _prompt("jv_v1")
    assert "CLINICAL SAFETY SCREENING" in text
    assert "MANDATORY for specific complaints" in text


def test_vital_edge_and_theorem_keep_their_emergency_intercept():
    for clinic_id in ("vital_edge", "theorem"):
        assert emergency_intercept_enabled(get_clinic(clinic_id)) is True


# ── The two switches, as contracts ─────────────────────────────────────────

def test_enabled_without_screens_does_not_count_as_rendering():
    """`_render_clinical_screening` needs BOTH. vital_edge is the live case:
    `enabled` true with an empty screens list, which renders nothing — so a
    helper that checked only `enabled` would put the deferral sentences back
    for a clinic that has no screens."""
    assert _screening_renders({"clinical_screening":
                               {"enabled": True, "screens": []}}) is False
    assert _screening_renders({"clinical_screening":
                               {"enabled": False,
                                "screens": [{"id": "x"}]}}) is False
    assert _screening_renders({"clinical_screening":
                               {"enabled": True,
                                "screens": [{"id": "x"}]}}) is True
    assert _screening_renders({}) is False


def test_both_switches_default_to_todays_behaviour():
    """A clinic that sets neither key renders exactly as before. This is what
    kept jv_v1, vital_edge and theorem byte-identical."""
    from app.prompts.clinic_template_prompt import _render_clinical_screening
    assert _render_clinical_screening({}, {}) == ""
    assert (get_clinic("jv_v1").get("condition_knowledge") or {}).get(
        "mandatory", True
    ) is True
