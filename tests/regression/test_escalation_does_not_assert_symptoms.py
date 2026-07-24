# tests/regression/test_escalation_does_not_assert_symptoms.py
"""P3 (2026-07-24) — the DVT escalation asserted a symptom the caller denied.

Incident
--------
On the 21:55 JV call the caller said the calf was not swollen. Susie escalated
anyway and opened with "A swollen, warm calf like that should be checked
urgently" — attributing to the caller a symptom they had just ruled out.

Two things produced that. The escalation only fired because the STT keyterm
boost was broken and the classifier mis-read the denial (fixed in e9217a9 and
79cbd78). But the wording is a defect in its own right: the escalation can
still legitimately fire on a volunteered risk factor with no symptom at all
("no swelling, but I had surgery last week"), and it would assert swelling.

Fix
---
`dvt` was the only one of six jv_v1 screens asserting a symptom as fact. The
other five already refer to the caller's answer without restating it — "Those
particular symptoms", "Because of those signs", "That pattern of stiffness".
The DVT escalation now matches its siblings. Clinical content is unchanged.
"""

import json

from pathlib import Path

import pytest

_CLINIC = Path("app/clinics/jv_v1/clinic.json")


@pytest.fixture(scope="module")
def screens():
    clinic = json.loads(_CLINIC.read_text(encoding="utf-8"))
    return {s["id"]: s for s in clinic["clinical_screening"]["screens"]}


def test_dvt_escalation_does_not_assert_the_symptom(screens):
    """The escalation must not tell the caller they have a symptom.

    It fires on a red flag, but that red flag may be a risk factor rather than
    a symptom, and the caller may have explicitly denied the symptom.
    """
    esc = screens["dvt"]["escalation"].lower()
    assert "a swollen, warm calf" not in esc, (
        "the DVT escalation asserts swelling and warmth as fact — the caller "
        "may have denied both and still be escalated on a risk factor"
    )


def test_dvt_escalation_keeps_every_clinical_element(screens):
    """Rewording must not quietly drop clinical content."""
    esc = screens["dvt"]["escalation"].lower()
    for required in ("urgent", "clot", "nhs 111", "a&e", "massage"):
        assert required in esc, f"DVT escalation lost {required!r}"


@pytest.mark.parametrize(
    "screen_id",
    ["cauda_equina", "dvt", "serious_spinal", "trauma_fracture", "vbi_neck", "inflammatory"],
)
def test_no_escalation_asserts_a_symptom_as_fact(screens, screen_id):
    """General form: no escalation opens by stating a symptom as established.

    Deixis ("those symptoms", "that pattern") refers to whatever the caller
    actually said. A bare symptom noun phrase asserts it independently, which
    is what went wrong on the DVT path.
    """
    esc = screens[screen_id]["escalation"]
    opening = esc.split("—")[0].strip().lower()
    asserted = [
        w for w in ("swollen", "warm calf", "numbness", "incontinence", "fever")
        if w in opening
    ]
    assert not asserted, (
        f"{screen_id}: escalation opens by asserting {asserted} — the caller "
        f"may have denied it. Refer to their answer instead. Opening: {opening!r}"
    )


# The shipped, routable clinics. "demo" is deliberately absent — see below.
_REAL_CLINICS = ("jv_v1", "theorem", "vital_edge")


@pytest.mark.parametrize("clinic_id", _REAL_CLINICS)
def test_shipped_clinic_configs_still_parse(clinic_id):
    """A hand-edited clinic.json that does not parse takes the clinic down."""
    path = Path("app/clinics") / clinic_id / "clinic.json"
    json.loads(path.read_text(encoding="utf-8"))


def test_demo_clinic_json_is_known_broken_but_unreachable():
    """`app/clinics/demo/clinic.json` contains Python source, not JSON.

    Found 2026-07-24 while adding the parse check above. It is inert: "demo"
    is the fallback for any unrecognised Twilio number
    (`clinic_id_from_twilio_to`), but `get_clinic("demo")` resolves from the
    legacy CLINICS dict in app/clinic_config.py and never reads this file.

    Pinned rather than skipped so the day someone changes loader precedence to
    prefer the directory file, this fails here instead of raising
    JSONDecodeError on a live call from an unmapped number.
    """
    from app.clinic_config import get_clinic

    path = Path("app/clinics/demo/clinic.json")
    if not path.exists():
        pytest.skip("demo stub removed — nothing left to guard")

    raw = path.read_text(encoding="utf-8")
    try:
        json.loads(raw)
    except json.JSONDecodeError:
        pass  # expected today
    else:
        pytest.skip("demo/clinic.json now parses — this guard can be deleted")

    assert get_clinic("demo"), (
        "demo/clinic.json is not valid JSON AND get_clinic('demo') no longer "
        "resolves — the unrecognised-number fallback is now broken"
    )
