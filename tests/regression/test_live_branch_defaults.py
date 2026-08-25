"""vitaledge-onboarding is a LIVE clinic line: the quiet defaults must be ON.

This branch descends from `latency-eval`, which switches outbound comms OFF
because it is an isolated timing-eval service that must never text a real
caller. Those defaults are correct there and wrong here, and they travel with
every cherry-pick.

It has already happened once, on the sibling branch:

    3b2f195, 2026-08-04
    "fix(sms): a live clinic line inherited an eval branch's silence"

The default came across with the lineage, past a comment that said in as many
words "DO NOT port this default flip to live branches". The clinic then sent
nothing — no booking confirmation, no staff transfer notice, no reminder — and
every call logged "[sms] SMS_ENABLED is off", which is precisely what a healthy
eval branch prints. Nothing looked broken.

`jv_v2` grew a test for this. This branch never did, which is why it is being
added now, during the Wave 1 port that carries `sms_enabled()` over from
canonical — a port whose incoming default is "false".

These pin the DEFAULT IN CODE, not the Render env var. A live service that
deploys with the variable unset must still behave like a clinic.
"""

import pytest


def test_sms_defaults_on_when_the_environment_is_silent(monkeypatch):
    """The single source of truth for the SMS switch must be ON here.

    Deliberately asserts on `_SMS_ENABLED_DEFAULT` rather than AST-walking for
    an `os.getenv("SMS_ENABLED", ...)` call. Since 9b2691d2 the sender and the
    prompt share this one constant, so this single assertion now guards both
    what is sent AND what Susie says was sent — which the older AST-based form
    on jv_v2 could not do.
    """
    from app.notifications import sms as sms_mod

    monkeypatch.delenv("SMS_ENABLED", raising=False)
    assert sms_mod._SMS_ENABLED_DEFAULT.strip().lower() in sms_mod._TRUTHY, (
        f"SMS_ENABLED defaults to {sms_mod._SMS_ENABLED_DEFAULT!r} on a LIVE "
        f"clinic branch. That is latency-eval's default: this clinic would send "
        f"NOTHING unless someone remembered a Render variable, and it would read "
        f"as healthy in the logs — the exact shape of 3b2f195."
    )
    assert sms_mod.sms_enabled() is True


def test_the_prompt_follows_the_sender(monkeypatch):
    """The property 9b2691d2 exists to hold, pinned on the branch that ships it.

    With SMS on, the prompt must not be carrying the rule that forbids Susie
    from mentioning the text she has just caused to be sent.
    """
    monkeypatch.delenv("SMS_ENABLED", raising=False)
    from app.clinic_config import get_clinic
    from app.prompts.clinic_template_prompt import build_clinic_prompt

    static, dyn = build_clinic_prompt(
        {"clinic_id": "vital_edge"}, get_clinic("vital_edge")
    )
    text = static + dyn
    assert "NEVER tell the caller a confirmation text has been sent" not in text, (
        "SMS is ON for this clinic but the prompt still forbids mentioning the "
        "text. That is the send and the promise disagreeing again."
    )


def test_reminders_default_on_for_this_branch(monkeypatch):
    """The sibling of the SMS default, and the same reasoning."""
    monkeypatch.delenv("APPOINTMENT_REMINDERS_ENABLED", raising=False)
    from app.notifications import scheduler

    assert scheduler._appointment_reminders_enabled() is True, (
        "appointment reminders default OFF on a live clinic branch — that is "
        "latency-eval's setting, and the failure is silent: nothing is queued "
        "and no error is raised."
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


def test_the_clinic_calendar_is_not_an_eval_or_demo_calendar():
    """latency-eval repoints clinics at demo calendars so eval calls never write
    into a real diary. Inheriting that repoint means every booking looks
    successful and lands in the wrong place."""
    import json
    import pathlib

    cfg = json.loads(
        pathlib.Path("app/clinics/vital_edge/clinic.json").read_text(encoding="utf-8")
    )
    cal = ((cfg.get("operational") or {}).get("calendar_id") or "").strip()
    assert cal, "vital_edge has no calendar_id — bookings would fall back to 'primary'"
    assert "demo" not in cal.lower(), (
        f"vital_edge points at what looks like a demo calendar ({cal!r}); "
        f"bookings would succeed into the wrong diary."
    )
