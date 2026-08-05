"""
The confirmation SMS must use the clinic's own name and phone (2026-08-05).

A live Theorem confirmation read:

    Your appointment at the clinic is confirmed:
    ...
    To reschedule, reply to this message or call us on .

    See you soon!
    — the clinic

A patient was told to ring a number that was not there.

Nothing was missing from the data. `theorem_v3`'s config held
sms_name="Theorem Health and Wellness" and phone="07870 166861" the whole
time. The resolution that reads them sat inside a
`prompt_engine == "template_v1"` gate, so it ran for jv_v1 and vital_edge and
for nobody else. theorem_v3 has prompt_engine=None, so it fell through to the
CLINIC_NAME / CLINIC_PHONE environment variables — which are not set on its
Render service, and whose defaults are literally "the clinic" and "".

Identity is not a template-engine concern. These pin that it is resolved for
every clinic that knows its own name, and that the template-only behaviour
which SHOULD stay gated still is.
"""

import pytest

from app.clinic_config import get_clinic
from app.sms_templates import build_sms


def _sms(clinic_id: str, location: str = "", name: str = "Quentin") -> str:
    """`name` carries a space when the full name was captured on the call.

    That matters: `pending_full_name` is derived from whether the stored name
    contains a space, and it takes priority over the spelling-confirm line.
    """
    return build_sms({
        "clinic_id": clinic_id,
        "selected_location": location,
        "collected": {
            "full_name": "Quentin Roche",
            "name": name,
            "phone": "07502211207",
        },
        "new_or_returning": "new",
        "appointment_iso": "2026-08-12T15:00:00",
    })


# ── the defect itself ───────────────────────────────────────────────────────

def test_theorem_confirmation_names_the_clinic():
    body = _sms("theorem_v3", "alcester")
    assert "Theorem Health and Wellness" in body
    assert "at the clinic is confirmed" not in body, (
        "the SMS fell back to the CLINIC_NAME env default again"
    )
    assert "— the clinic" not in body


def test_theorem_confirmation_gives_a_number_to_ring():
    body = _sms("theorem_v3", "alcester")
    assert "call us on 07870 166861" in body
    assert "call us on ." not in body, (
        "the reschedule line has no phone number in it — the exact live defect"
    )


@pytest.mark.parametrize("clinic_id", ["theorem_v3", "theorem", "jv_v1", "demo"])
def test_no_clinic_ships_an_empty_reschedule_number(clinic_id):
    """The failure is silent and patient-facing, so assert it for all of them."""
    body = _sms(clinic_id)
    assert "call us on ." not in body, f"{clinic_id} has an empty phone in its SMS"
    assert "call us on \n" not in body


@pytest.mark.parametrize("clinic_id", ["theorem_v3", "theorem", "jv_v1", "demo"])
def test_identity_comes_from_config_not_the_env_default(clinic_id):
    """Each clinic's configured sms_name must appear in its own SMS."""
    expected = get_clinic(clinic_id).get("sms_name")
    assert expected, f"{clinic_id} has no sms_name configured"
    assert expected in _sms(clinic_id), (
        f"{clinic_id} did not use its configured name {expected!r}"
    )


# ── what must STAY gated ────────────────────────────────────────────────────

def test_the_spelling_confirm_note_is_still_template_only():
    """`_is_template_clinic` drives the spelling-confirm line, which is a
    genuinely template-only behaviour and was NOT part of the identity fix.
    Widening identity must not have widened this."""
    tpl = _sms("jv_v1", name="Quentin Roche")
    theorem = _sms("theorem_v3", "alcester", name="Quentin Roche")
    assert "spelled differently" in tpl or "correct spelling" in tpl, (
        "the template clinics lost their spelling-confirm note"
    )
    assert "spelled differently" not in theorem, (
        "the spelling-confirm note leaked to a non-template clinic"
    )


# ── the fees line carried over from `main` ──────────────────────────────────

def test_theorem_carries_its_fees_note():
    """`main` had this hardcoded as FEES_NOTE, "Wording confirmed by
    Mark/Quentin". This branch reads it from clinic config, and the wording was
    never moved across — so Mark's clinic silently stopped sending it."""
    body = _sms("theorem_v3", "alcester")
    assert "Fees: £85" in body
    assert "+£45 for Laser or Shockwave Therapy" in body
    assert "less than 24hrs notice" in body


def test_the_fees_note_matches_what_the_prompt_tells_callers():
    """A price in an SMS that contradicts the price on the call is worse than
    no price at all."""
    from app.prompts.susie_system_prompt import build_system_prompt_parts

    static, dynamic = build_system_prompt_parts({
        "clinic_id": "theorem_v3", "collected": {}, "soft_context": {},
    })
    prompt = static + "\n" + dynamic
    assert "£85" in prompt
    assert "£45 surcharge" in prompt
