"""theorem-onboarding is a LIVE clinic line: the quiet defaults must be ON.

This is the branch it happened to.

    3b2f195, 2026-08-04
    "fix(sms): a live clinic line inherited an eval branch's silence"

theorem-onboarding descends from latency-eval, which switches outbound comms
OFF because it is an isolated timing-eval service that must never text a real
caller. That default came across with the lineage, straight past a comment
saying in as many words "DO NOT port this default flip to live branches".

Mark's line then sent nothing — no booking confirmation, no staff transfer
notice, no reminder. Every acceptance call logged "[sms] SMS_ENABLED is off"
and read as correct, because that is exactly what a healthy eval branch prints.
Nothing alerted, because nothing threw.

A comment did not stop it. jv_v2 grew an assertion afterwards; this branch —
the one that actually broke — never did. It is added now, during the Wave 1
port that carries sms_enabled() over from canonical, whose incoming default
is "false".

These pin the DEFAULT IN CODE, not the Render env var. A live service that
deploys with the variable unset must still behave like a clinic.
"""

import pytest


def test_sms_defaults_on_when_the_environment_is_silent(monkeypatch):
    """The single source of truth for the SMS switch must be ON here."""
    from app.notifications import sms as sms_mod

    monkeypatch.delenv("SMS_ENABLED", raising=False)

    assert hasattr(sms_mod, "_SMS_ENABLED_DEFAULT"), (
        "the SMS switch moved — find its single source of truth and re-aim this "
        "test at it. Do not delete it: this is a live clinic line."
    )
    default = sms_mod._SMS_ENABLED_DEFAULT
    assert str(default).strip().lower() in sms_mod._TRUTHY, (
        f"SMS_ENABLED defaults to {default!r} on a LIVE clinic branch. That is "
        f"latency-eval's default, and it is how 3b2f195 silenced this exact "
        f"line — invisibly, because silence is what a correct eval branch prints."
    )
    assert sms_mod.sms_enabled() is True


def test_the_theorem_prompt_promises_a_text_it_cannot_gate(monkeypatch):
    """Why the default above is load-bearing HERE specifically.

    Theorem does not render clinic_template_prompt, so it never got the
    SMS_ENABLED-gated closing that the template clinics have. Its own prompt
    promises the confirmation text unconditionally.

    That is fine while SMS is ON and a lie the moment it is OFF — which is
    precisely what made 3b2f195 worse than silence. This test does not demand
    the promise be removed; it pins the COUPLING, so that anyone switching SMS
    off on this branch is told that doing so makes Susie lie to every caller.
    """
    from app.notifications import sms as sms_mod
    from app.prompts import susie_system_prompt as sp
    import inspect

    src = inspect.getsource(sp)
    promises_unconditionally = "I've just sent you a confirmation text" in src

    if promises_unconditionally:
        assert sms_mod.sms_enabled() is True, (
            "Theorem's prompt promises the caller a confirmation text with no "
            "SMS_ENABLED gate of its own, but outbound SMS is OFF. Every caller "
            "would be told about a text that is never sent. Either turn SMS "
            "back on, or gate the promise in _build_theorem_v3 — do not leave "
            "this pair as it is."
        )


def test_reminders_default_on_for_this_branch(monkeypatch):
    """The sibling of the SMS default, and the same silent failure."""
    monkeypatch.delenv("APPOINTMENT_REMINDERS_ENABLED", raising=False)
    from app.notifications import scheduler

    assert scheduler._appointment_reminders_enabled() is True, (
        "appointment reminders default OFF on a live clinic branch — that is "
        "latency-eval's setting; nothing is queued and no error is raised."
    )


def test_the_eval_staff_redirect_is_off_unless_explicitly_configured(monkeypatch):
    """EVAL_STAFF_SMS_TO reroutes staff-directed SMS to a test handset.

    Harmless on an eval service, silent data loss on a live one — the
    practitioner simply stops being told about their own bookings.
    """
    from app.notifications import sms as sms_mod

    monkeypatch.delenv("EVAL_STAFF_SMS_TO", raising=False)
    number = "+447700900123"
    assert sms_mod.redirect_staff_sms(number) == number, (
        "staff SMS is being redirected with EVAL_STAFF_SMS_TO unset — on a live "
        "service that silently diverts every practitioner notification"
    )
