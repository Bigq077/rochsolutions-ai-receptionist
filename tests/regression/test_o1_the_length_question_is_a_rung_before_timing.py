"""The session-length question has a POSITION, and it is before the timing one.

O-1, owner, 2026-08-30, from listening to the demo line — *"it seems a bit
awkward"*:

    Susie:  "Right — do you have a preference for when you'd like to come in?"
    caller: "whatever you've got next week"
    [ms_tools] check_availability blocked — 'sports_massage' offers [30, 60]
               minutes and the caller has not chosen. Asking first
    Susie:  "Would you like the thirty-minute session at thirty-eight pounds,
             or the sixty-minute at sixty-two?"

The timing question was asked, answered, and then abandoned mid-flow for a
second question the caller was not expecting.

WHAT THE DEFECT ACTUALLY WAS. Not a missing instruction — the prompt has
carried "DURATION QUESTION FOR <SERVICE>: ask whether they'd like a 30-minute
(£38) or 60-minute (£62) session" for months, with the right lengths and the
right prices. What it did not carry was a POSITION. The booking sequence is a
numbered ladder the model demonstrably follows (1b REASON -> 2 MODALITY THEN
TIMING -> 5 SLOT PRESENTATION -> ... -> 9 WARM READBACK) and the length
question was not on it. So the only thing that ever forced the question was
`duration_choice_gate`, which is a TOOL-time block and therefore cannot be
reached until the caller has already given a day. Late by construction.

WHAT THIS IS NOT. It is not a change to `duration_choice_gate`, and that gate
is deliberately untouched. It is what stopped a 90-minute booking being written
as 60 (`CA86c320ef`, 4 Aug) and it remains the backstop for the model ignoring
the rung. The split is the one the gate's own docstring already draws: the
prompt owns the ORDER, the gate owns whether the lookup may run yet.

WHAT THIS FILE CAN AND CANNOT PROVE. It proves the rung renders, that it is
positioned before the timing rung, that it defers to clinic.json for the
lengths and prices rather than hardcoding them, and that clinics with no
multi-length service render byte-identical. It CANNOT prove the model obeys it
— that is behavioural, and the counter-evidence is on the record: Vital Edge's
clinic.json already said "ask which length they'd like ... BEFORE offering any
appointment times" and the model walked past it, which is why the gate exists
at all ("It was loud. Loud is not a gate."). Ordering is verified through
`tests/harness/`, not here.
"""
from __future__ import annotations

import re

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import (
    _spine_has_duration_choice,
    build_clinic_prompt,
)


def _choice_clinics() -> list:
    """Every template_v1 clinic on THIS branch that sells a multi-length service.

    DISCOVERED, NOT LISTED. The first draft hardcoded
    ["northgate", "jv_v1", "vital_edge"] — canonical's roster. `northgate` is
    the demo line's clinic and no patient branch ships it, so the file went red
    on the very first port with six failures that looked like a broken rung and
    were a broken test. That is the third time this exact trap has been paid for
    in two days, so this file does not name a clinic at all.

    An empty result would make every parametrized test below vacuously green,
    which is worse than red — `test_this_branch_has_at_least_one_choice_clinic`
    exists to make that loud.
    """
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "app" / "clinics"
    found = []
    for d in sorted(root.iterdir()):
        try:
            cfg = json.loads((d / "clinic.json").read_text(encoding="utf-8"))
        except Exception:
            continue  # `demo/` is not valid JSON on every branch
        if cfg.get("prompt_engine") != "template_v1":
            continue
        try:
            if _spine_has_duration_choice(get_clinic(d.name)):
                found.append(d.name)
        except Exception:
            continue
    return found


CHOICE_CLINICS = _choice_clinics()


def test_this_branch_has_at_least_one_choice_clinic():
    """Guards the discovery above. With an empty list every parametrized test
    in this file would pass by running zero cases."""
    assert CHOICE_CLINICS, (
        "no template_v1 clinic on this branch sells a multi-length service, so "
        "nothing below is being exercised — if that is genuinely true for this "
        "branch, O-1 is N-A here and this file should not have been ported"
    )


#: Rung 2 is not spelled the same everywhere: a multi-modality clinic renders
#: "2. MODALITY THEN TIMING", a single-site one renders plain "2. TIMING".
#: Hardcoding either is the clinic-pin trap in miniature — the first draft of
#: this file pinned the northgate wording and reported vital_edge as badly
#: ordered when the rung was in exactly the right place.
_TIMING_RUNG = re.compile(r"^[ \t]*2\. (?:MODALITY THEN TIMING|TIMING)\b", re.M)


def _timing_rung_at(blob: str) -> int:
    m = _TIMING_RUNG.search(blob)
    assert m, "no timing rung rendered at all — the ladder has changed shape"
    return m.start()


def _rendered(clinic_id: str) -> str:
    clinic = get_clinic(clinic_id)
    static, dynamic = build_clinic_prompt(
        {"clinic_id": clinic_id, "turn_count": 3, "collected": {}}, clinic
    )
    return static + "\n" + dynamic


# ── The predicate ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("clinic_id", CHOICE_CLINICS)
def test_the_choice_clinics_are_detected(clinic_id):
    assert _spine_has_duration_choice(get_clinic(clinic_id))


def test_a_clinic_with_no_multi_length_service_is_not():
    """Gated on the CATALOGUE, never on a clinic id — 'if clinic == "..."' in
    engine code is the bug, not the fix."""
    assert not _spine_has_duration_choice({"services": [
        {"name": "Initial Assessment", "typical_duration_minutes": 45},
    ]})
    assert not _spine_has_duration_choice({"services": []})
    assert not _spine_has_duration_choice({})


def test_a_malformed_services_list_does_not_raise():
    """`get_clinic` on an unknown id returns a shape whose `services` is a list
    of strings. A prompt renderer is not the place to raise on that — it is how
    `northgate` hardcoded into a ported test read as a broken engine rather
    than a broken test."""
    assert _spine_has_duration_choice({"services": ["massage", "physio"]}) is False
    assert _spine_has_duration_choice(
        {"services": ["massage", {"typical_duration_minutes_options": [30, 60]}]}
    ) is True


# ── The rung ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("clinic_id", CHOICE_CLINICS)
def test_the_rung_is_rendered(clinic_id):
    assert "1c. SESSION LENGTH" in _rendered(clinic_id)


@pytest.mark.parametrize("clinic_id", CHOICE_CLINICS)
def test_the_rung_comes_before_the_timing_rung(clinic_id):
    """The whole of O-1 in one assertion.

    Position is the fix. If 1c ever renders after "2. MODALITY THEN TIMING" the
    model is being told to ask the length after the timing question, which is
    the reported defect restored.
    """
    blob = _rendered(clinic_id)
    at_1c = blob.index("1c. SESSION LENGTH")
    at_timing = _timing_rung_at(blob)
    assert at_1c < at_timing, (
        "the length rung renders AFTER the timing rung — the caller is asked "
        "when they want to come in, answers, and is then interrupted with a "
        "question they were not expecting"
    )


@pytest.mark.parametrize("clinic_id", CHOICE_CLINICS)
def test_the_rung_sits_between_reason_and_timing(clinic_id):
    """Not merely 'somewhere earlier' — the ladder reads 1b, 1c, 2 in order."""
    blob = _rendered(clinic_id)
    seq = [m.group(1) for m in re.finditer(
        r"^[ \t]*(1b|1c|2)\. (?:REASON|SESSION LENGTH|MODALITY THEN TIMING|TIMING)\b",
        blob, re.M
    )]
    assert seq[:3] == ["1b", "1c", "2"], seq[:6]


@pytest.mark.parametrize("clinic_id", CHOICE_CLINICS)
def test_the_rung_says_before_timing_and_not_after_slots(clinic_id):
    blob = _rendered(clinic_id)
    rung = blob[blob.index("1c. SESSION LENGTH"):]
    rung = rung[:rung.index("\n")]
    assert "BEFORE the timing question" in rung
    assert "after presenting slots" in rung


@pytest.mark.parametrize("clinic_id", CHOICE_CLINICS)
def test_an_already_given_length_is_not_re_asked(clinic_id):
    """The A4 re-ask family. The rung must not create the defect finding 3 just
    closed — a question asked again after the caller has answered it."""
    blob = _rendered(clinic_id)
    rung = blob[blob.index("1c. SESSION LENGTH"):]
    rung = rung[:rung.index("\n")]
    assert "do NOT ask again" in rung


# ── It must not hardcode a clinic fact ──────────────────────────────────────

@pytest.mark.parametrize("clinic_id", CHOICE_CLINICS)
def test_the_rung_quotes_no_length_or_price_of_its_own(clinic_id):
    """"A clinic fact does not belong in engine code — derive it from
    clinic.json or do not say it." The lengths and prices live in the DURATION
    QUESTION block, which is rendered from each service's own pricing; the rung
    points at that block rather than repeating it, so the two can never
    disagree. northgate is 30/60 and vital_edge is 60/90 — a rung that named
    either would be wrong for the other.
    """
    blob = _rendered(clinic_id)
    rung = blob[blob.index("1c. SESSION LENGTH"):]
    rung = rung[:rung.index("\n")]
    assert "£" not in rung, rung
    assert not re.search(r"\b(30|60|90)-minute\b", rung), rung
    # ...and it must point at where those facts actually are.
    assert "DURATION QUESTION" in rung


@pytest.mark.parametrize("clinic_id", CHOICE_CLINICS)
def test_the_duration_question_block_still_carries_the_facts(clinic_id):
    """The rung defers to this block, so the block must still exist and still
    name real lengths for this clinic. If it were ever removed the rung would
    point at nothing."""
    blob = _rendered(clinic_id)
    assert "DURATION QUESTION FOR" in blob
    opts = {
        int(o)
        for svc in get_clinic(clinic_id).get("services", [])
        if isinstance(svc, dict)
        for o in (svc.get("typical_duration_minutes_options") or [])
    }
    for m in opts:
        assert f"{m}-minute" in blob, f"{clinic_id} never names its {m}-minute option"


# ── Containment ─────────────────────────────────────────────────────────────

def test_a_clinic_without_the_choice_gets_no_rung():
    """Rendered from a synthetic catalogue rather than a real clinic id, so this
    keeps working when the clinic roster changes. The byte-identical claim for
    the real ones is `UNCHANGED_CLINIC_PROMPTS` in
    `test_b55_provisional_reschedule_closing`, which caught jv_v1 moving and
    confirmed demo / theorem / theorem_v3 did not.
    """
    clinic = dict(get_clinic("northgate"))
    clinic["services"] = [
        {k: v for k, v in svc.items() if k != "typical_duration_minutes_options"}
        for svc in clinic.get("services", []) if isinstance(svc, dict)
    ]
    assert not _spine_has_duration_choice(clinic)
    static, dynamic = build_clinic_prompt(
        {"clinic_id": "northgate", "turn_count": 3, "collected": {}}, clinic
    )
    assert "1c. SESSION LENGTH" not in (static + dynamic)


def test_the_tool_time_gate_is_untouched():
    """The rung is the ordering half only. `duration_choice_gate` is what
    stopped a 90-minute booking being written as 60 and it must still block a
    lookup for a service whose length nobody has chosen — if this ever goes
    green because the gate stopped gating, O-1 traded a cosmetic fix for the
    worst defect class in this system.
    """
    from app.tools.receptionist_tools import duration_choice_gate

    clinic_id = CHOICE_CLINICS[0]
    clinic = get_clinic(clinic_id)
    # The service and one of its own lengths, read off the catalogue — the
    # names differ per branch and so do the options (30/60 vs 60/90).
    svc = next(
        s for s in clinic["services"]
        if isinstance(s, dict) and s.get("typical_duration_minutes_options")
    )
    name = svc["name"]
    a_valid_length = int(svc["typical_duration_minutes_options"][0])

    session: dict = {}
    blocked = duration_choice_gate(clinic, name, session)
    assert blocked and blocked.get("error") == "duration_choice_required", blocked
    assert session.get("_duration_gate_fired") is True

    # ...and it still lets a captured choice straight through.
    assert duration_choice_gate(
        clinic, name, {"_service_duration_choice": a_valid_length}
    ) is None
