"""Leanne is NOT a prescriber (Mark, 15 Sep 2026: "after assessment, she can ask
me to prescribe"). Mark is the only prescriber at Theorem.

canonical.py had Leanne as "Chartered Physiotherapist & Prescriber",
prescribes True, and that value had spread: the live theorem_v3 prompt said
"Practitioners (both qualified prescribers)", the FAQs said "Both Mark and
Leanne are qualified prescribers", and caller_concerns / knowledge / clinic.json
said "our physiotherapists are prescribers". obs, 15 Sep: 0 of 147 Theorem
calls had Susie mention prescribing, so no caller is known to have heard it.

This is a regulated fact about a named clinician. The test pins the fact AND
the pathway: a caller seeing Leanne who asks about pain relief is owed
"yes, via Mark", not a bare no.
"""
import json
import re
from pathlib import Path

import pytest

from app.clinics.theorem import canonical, caller_concerns
from app.prompts.susie_system_prompt import _build_theorem_v3

REPO = Path(__file__).resolve().parents[2]

# A claim that Leanne, "both", or the physios as a group prescribe.
FALSE_CLAIM = re.compile(
    r"both (?:mark and leanne )?(?:are )?(?:qualified )?prescribers"
    r"|\(both qualified prescribers\)"
    r"|(?:our |the )?(?:physiotherapists|physios|team) (?:are|is) (?:qualified )?(?:independent )?prescribers"
    r"|as qualified prescribers, our physiotherapists"
    r"|leanne (?:is|'s) (?:also )?(?:a )?(?:qualified )?prescriber"
    r"|chartered physiotherapist & prescriber",
    re.IGNORECASE,
)


def _prompt() -> str:
    built = _build_theorem_v3({"clinic_id": "theorem_v3", "collected": {},
                               "selected_location": "alcester", "v3_location_confirmed": True})
    return "\n".join(str(p) for p in built) if isinstance(built, tuple) else str(built)


def test_canonical_fact_and_pathway():
    leanne = canonical.PRACTITIONERS["leanne"]
    assert leanne["prescribes"] is False
    assert "Prescriber" not in leanne["role"]
    assert leanne["prescribing_via"] == "mark"
    assert canonical.PRACTITIONERS["mark"]["prescribes"] is True
    conflict = canonical.KNOWN_CONFLICTS["leanne_prescribes"]
    assert conflict["canonical"] is False and conflict["resolved"] is True


def test_the_live_theorem_prompt_says_mark_only_and_the_pathway():
    p = _prompt()
    assert not FALSE_CLAIM.search(p), FALSE_CLAIM.search(p).group(0)
    assert "Mark is the ONLY prescriber" in p
    assert "Leanne can ask Mark to prescribe" in p


def test_legacy_practitioner_record_agrees():
    from app.clinic_config import THEOREM_PRACTITIONERS
    assert THEOREM_PRACTITIONERS["leanne"]["prescribes"] is False
    assert "Prescriber" not in THEOREM_PRACTITIONERS["leanne"]["role"]


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


@pytest.mark.parametrize("clinic_id", ["theorem", "theorem_v2", "theorem_v3"])
def test_no_theorem_faq_or_service_text_repeats_the_claim(clinic_id):
    from app.clinic_config import CLINICS
    bad = [s for s in _strings(CLINICS[clinic_id]) if FALSE_CLAIM.search(s)]
    assert not bad, bad


def test_caller_concerns_knowledge_and_files_agree():
    texts = list(_strings(caller_concerns.__dict__.get("OBJECTION_PLAYBOOK", {})))
    texts += [str(v) for v in vars(caller_concerns).values() if isinstance(v, (dict, list, str))]
    from app.knowledge import knowledge
    texts += [str(v) for v in vars(knowledge).values() if isinstance(v, (dict, list, str))]
    from app.flows import triage_legacy
    texts += [str(v) for v in vars(triage_legacy).values() if isinstance(v, (dict, list, str))]
    texts.append((REPO / "app/clinics/theorem/knowledge.md").read_text(encoding="utf-8"))
    texts.append(json.dumps(json.loads((REPO / "app/clinics/theorem/clinic.json").read_text(encoding="utf-8-sig"))))
    bad = [m.group(0) for t in texts for m in [FALSE_CLAIM.search(t)] if m]
    assert not bad, bad


def test_the_medication_concern_gives_the_pathway_not_a_bare_no():
    c = caller_concerns.CONCERNS["medication_prescribing"] if hasattr(caller_concerns, "CONCERNS") else None
    blob = json.dumps(c) if c else json.dumps({k: v for k, v in vars(caller_concerns).items()
                                               if isinstance(v, dict)}, default=str)
    assert "Leanne is NOT a prescriber" in blob
    assert "ask Mark to" in blob
