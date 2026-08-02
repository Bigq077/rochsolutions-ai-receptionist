"""B-20 — the screening catalogue must read as conditional, never as a checklist.

The prompt granted the model screening authority in one line
(clinic_template_prompt.py) under a header that called the checks PROACTIVE and
told it to run them BEFORE booking:

    CLINICAL SAFETY SCREENING — PROACTIVE RED-FLAG CHECKS (run BEFORE booking)
    SCREENS — match the caller's presentation to a row, then ask that screen's
    question, on its own, before moving to booking:

Read together that is a checklist, and the model worked through it. Across 133
jv_v1 calls in obs, 18 had the model screen where the deterministic Layer 1 did
not:

    8   the complaint matched no row at all — a knee sent to the back screen,
        a shoulder to the neck screen, an aching knee to the inflammatory screen
    8   right region, nothing indicating that screen — ankle -> dvt x4,
        neck -> vbi_neck x2
    2   genuine Layer 1 misses the model RESCUED (B-32)

On CA2ada6263 the caller objected mid-call: "i'm kind of confused ... i don't
know why you're asking me this question", and was then told a confirmed DVT risk
factor was "reassuring".

Owner decision 2026-08-03: option B. The authority is BOUNDED, not withdrawn —
because of those 2 rescues, where STT wrote "call's" for "calf" and "back pin"
for "back pain" and the model was the only layer that worked. Option C (remove
the catalogue) would have lost both.

What this file pins:

  * the bounding language is present in the rendered prompt;
  * the checklist language is gone, in BOTH places it lived — the engine header
    and jv_v1's clinic.json how_to_use. Those two used to contradict each other,
    which is how B-20 happened in the first place;
  * the safety half is NOT lost: a screen that DOES apply must still not be
    skipped to book faster, and every screen still renders its question and
    escalation;
  * the engine text stays clinic-agnostic. Which complaint maps to which screen
    is config. If a future edit hardcodes a body part into the renderer, that is
    the CLAUDE.md rule broken and test_engine_text_names_no_body_part catches it.

These are wording assertions, which are brittle by nature. That is deliberate:
B-20 WAS a wording defect, so the wording is the thing to pin. If you are here
because one failed, re-read the register section before relaxing it.
"""
from __future__ import annotations

import re

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import _render_clinical_screening


@pytest.fixture
def rendered():
    clinic = get_clinic("jv_v1")
    out = _render_clinical_screening(clinic, {})
    assert out, "jv_v1 must render a screening block"
    return out


@pytest.fixture
def how_to_use():
    # Via get_clinic, not a cwd-relative path — the value must be correct as the
    # engine actually resolves it, and the test must not depend on where pytest
    # was invoked from.
    return get_clinic("jv_v1")["clinical_screening"]["how_to_use"]


# ─────────────────────────────────────────────────────────────────────────
# The checklist reading is gone
# ─────────────────────────────────────────────────────────────────────────
def test_the_header_no_longer_calls_the_checks_proactive(rendered):
    """'PROACTIVE ... run BEFORE booking' is the checklist framing. It is the
    single line the register identifies as overriding how_to_use."""
    first = rendered.splitlines()[0]
    assert "PROACTIVE" not in first
    assert "CONDITIONAL" in first


def test_the_unbounded_grant_line_is_gone(rendered):
    """The exact instruction that told the model to do the matching itself with
    no test attached."""
    assert "match the caller's presentation to a row" not in rendered


def test_how_to_use_no_longer_reads_as_unconditional(how_to_use):
    """Config half. 'SAFETY SCREENING runs BEFORE booking.' as a bare sentence,
    plus 'never skip it to reach a booking faster', pulled against the engine
    text. Both were rendered, one line apart."""
    assert not how_to_use.startswith("SAFETY SCREENING runs BEFORE booking.")
    assert "never skip it to reach a booking faster" not in how_to_use


# ─────────────────────────────────────────────────────────────────────────
# The bound is present and says the right thing
# ─────────────────────────────────────────────────────────────────────────
def test_the_screens_list_is_marked_conditional(rendered):
    assert "CONDITIONAL, not a checklist" in rendered


def test_no_matching_row_means_ask_nothing(rendered):
    """The 8 worst orphans were complaints that matched no row. This is the
    instruction that addresses them, and it must be explicit rather than implied
    — 'only when it matches' alone leaves 'nearest row' available."""
    assert "matches" in rendered and "ask NOTHING from this list" in rendered
    assert "Never reach for the nearest row" in rendered


def test_an_undescribed_complaint_is_not_a_screen(rendered):
    """A caller who has said only 'I'd like to book an appointment' has given
    nothing to match. The instruction is to ask what it is for, not to screen."""
    assert "have not described a complaint yet" in rendered


def test_how_to_use_states_the_common_case(how_to_use):
    """The model needs to know that matching nothing is NORMAL, not a gap it
    should fill."""
    assert "Most callers match no screen at all" in how_to_use


# ─────────────────────────────────────────────────────────────────────────
# The safety half is intact — this is the half a careless edit would lose
# ─────────────────────────────────────────────────────────────────────────
def test_a_screen_that_applies_still_must_not_be_skipped(how_to_use):
    """Bounding when to ask must NOT weaken the instruction not to skip a
    warranted screen in order to book faster."""
    assert "never skip a screen that DOES apply" in how_to_use


def test_a_positive_answer_still_blocks_booking(how_to_use):
    assert "do NOT book" in how_to_use


def test_every_screen_still_renders_its_question_and_escalation(rendered):
    clinic = get_clinic("jv_v1")
    screens = clinic["clinical_screening"]["screens"]
    assert len(screens) == 6
    for s in screens:
        assert f'ASK: "{s["screen_question"]}"' in rendered, s["id"]
        assert s["escalation"] in rendered, s["id"]


def test_every_screen_still_renders_its_presentation(rendered):
    """The bound refers to 'the presentation named on that row', so every row
    must actually name one or the rule has nothing to point at."""
    clinic = get_clinic("jv_v1")
    for s in clinic["clinical_screening"]["screens"]:
        assert f'when the caller describes {s["presentation"]}' in rendered, s["id"]


def test_the_emergency_path_is_untouched(rendered):
    assert "chest pain" in rendered and "do not screen or book" in rendered


# ─────────────────────────────────────────────────────────────────────────
# Structural invariants
# ─────────────────────────────────────────────────────────────────────────
def test_a_clinic_without_screening_renders_nothing():
    """Clinics without the block must be unaffected — theorem and vital_edge
    both have screening disabled today."""
    assert _render_clinical_screening({}, {}) == ""
    assert _render_clinical_screening({"clinical_screening": {"enabled": False}}, {}) == ""
    assert _render_clinical_screening(
        {"clinical_screening": {"enabled": True, "screens": []}}, {}
    ) == ""


def test_engine_text_names_no_body_part():
    """CLAUDE.md: clinic-specific behaviour belongs in clinic.json, never in
    engine code. The temptation when writing this bound was to spell out 'a knee
    is not the inflammatory row' — true for jv_v1, wrong for a clinic that has a
    knee screen.

    Rendered, not source-parsed. The first version of this test scanned the
    function's string literals and failed on the word "calf" inside a COMMENT
    citing call CAcaae3aa7 — a false positive about a comment that should stay.
    Feeding the renderer a clinic whose own config contains no anatomy means
    every body part in the output can only have come from engine text.
    """
    neutral = {
        "clinical_screening": {
            "enabled": True,
            "how_to_use": "PLACEHOLDER_HOW_TO_USE",
            "screens": [{
                "id": "x",
                "label": "PLACEHOLDER_LABEL",
                "presentation": "PLACEHOLDER_PRESENTATION",
                "screen_question": "PLACEHOLDER_QUESTION?",
                "escalation": "PLACEHOLDER_ESCALATION",
            }],
        }
    }
    out = _render_clinical_screening(neutral, {}).lower()
    assert "placeholder_presentation" in out, "fixture did not reach the renderer"
    for part in ("knee", "shoulder", "ankle", "calf", "neck", "wrist", "shin",
                 "hip", "back", "leg", "spine"):
        assert not re.search(rf"(?<!\w){part}(?!\w)", out), (
            f"engine text names a body part ({part!r}) — that belongs in clinic.json"
        )
