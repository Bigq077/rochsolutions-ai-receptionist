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
    assert "call us on 07380 841468" in body
    assert "call us on ." not in body, (
        "the contact line has no phone number in it — the exact live defect"
    )


def test_the_sms_number_is_the_line_the_sms_came_from():
    """2026-08-05, owner instruction: wherever a patient-facing message says
    "call us", it must be 07380 841468 — the clinic's own inbound line, which
    is also the number the SMS is sent from, so "reply to this message" and
    "call us" reach the same place. `phone` (07870 166861) is the team's direct
    number and is still what Susie quotes on a call, so this is a separate
    `sms_phone` key rather than a change to `phone`."""
    assert get_clinic("theorem_v3").get("sms_phone") == "07380 841468"
    assert "07870 166861" not in _sms("theorem_v3", "alcester"), (
        "the SMS is quoting the team's direct number again"
    )


def test_clinics_without_an_sms_phone_are_untouched():
    """`sms_phone` is Theorem-only; everyone else must still get `phone`."""
    for clinic_id in ("jv_v1", "vital_edge"):
        clinic = get_clinic(clinic_id)
        assert not clinic.get("sms_phone")
        assert f"call us on {clinic['phone']}" in _sms(clinic_id)


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


# ── the branded Maps short links carried over from `main` ───────────────────

def test_theorem_uses_the_branded_maps_short_link_per_location():
    """`main` sent a short branded redirect; this branch sent the raw ~120-char
    Google Maps URL, which is what made the message look untidy. Both redirects
    verified resolving 2026-08-05 before this was enabled."""
    alcester = _sms("theorem_v3", "alcester")
    redditch = _sms("theorem_v3", "redditch")
    assert "Maps: https://bit.ly/theorem-alcester" in alcester
    assert "Maps: https://bit.ly/theorem-redditch" in redditch
    assert "maps.google.com" not in alcester


def test_an_unknown_location_still_gets_a_working_maps_link(monkeypatch):
    """The short link is a nicety; the full URL stays as the fallback whenever
    the location is unknown.

    CLINIC_ADDRESS is read into a module global at import, and it IS set on the
    Theorem Render service (the live SMS printed the right address while the
    name and phone were blank) — so this patches it to the deployed shape
    rather than the bare test env.

    Note the remaining hole this does NOT cover: with no location AND no
    CLINIC_ADDRESS, the body still emits a bare "📍" and "Maps: " with nothing
    after them. Theorem has two sites, so defaulting one of them would be
    guessing — left as a deliberate open item.
    """
    monkeypatch.setattr(
        "app.sms_templates.CLINIC_ADDRESS",
        "The Greig Leisure Centre, Kinwarton Road, Alcester, B49 6AD",
    )
    body = _sms("theorem_v3", "")
    assert "Maps: https://maps.google.com/?q=" in body


def test_template_clinics_never_inherit_a_theorem_redirect():
    """jv_v1/vital_edge resolve their address from clinic.json but have no short
    link of their own — a location_id collision must not send their patients to
    a Theorem map."""
    for clinic_id in ("jv_v1", "vital_edge"):
        for loc in ("bolton", "kingston", "alcester", "redditch"):
            assert "bit.ly/theorem" not in _sms(clinic_id, loc), (
                f"{clinic_id}/{loc} inherited a Theorem Maps redirect"
            )


# ── the fees line carried over from `main` ──────────────────────────────────

def test_the_arrival_note_is_gone():
    """2026-08-05, owner instruction: the "arrive 5 mins early and bring any
    relevant medical records" paragraph comes out of the confirmation. Pinned
    so a future port from `main` (where it is hardcoded) doesn't put it back."""
    body = _sms("theorem_v3", "alcester")
    assert "arrive 5 mins early" not in body
    assert "medical records or scan results" not in body


def test_the_reschedule_wording_is_gone_but_a_contact_line_remains():
    """The reply-to-reschedule wording came out; the number did not. A
    confirmation with no way to reach the clinic would be a worse message than
    the one this replaced."""
    body = _sms("theorem_v3", "alcester")
    assert "To reschedule" not in body
    assert "Any questions, call us on 07380 841468." in body


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
