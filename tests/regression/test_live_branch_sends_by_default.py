"""
jv_v2 is a LIVE clinic branch: outbound SMS defaults ON.

This branch was cut from `latency-eval`, which defaults `SMS_ENABLED` OFF
because it is an isolated timing-eval service that must never text a real
caller. That default must not come across, and nothing in the suite was
pinning it — which is exactly how it reached a live clinic last time.

    3b2f195, 2026-08-04
    "fix(sms): a live clinic line inherited an eval branch's silence"

    theorem-onboarding descends from latency-eval [...] The default came
    across with the lineage — past a comment, in this exact spot, reading
    "DO NOT port this default flip to main/theorem/jv live branches". It was
    ported anyway.

    The result: Mark's line sent nothing. No booking confirmation, no staff
    transfer notice, no reminder. Every acceptance call so far logged
    "[sms] SMS_ENABLED is off" and it read as correct, because that is
    exactly what a healthy eval branch prints.

Two things make that failure mode nasty enough to deserve a dedicated test:

  * it is SILENT. The log line is indistinguishable from correct behaviour on
    the branch it came from, so nothing alerts and nothing looks broken;
  * the prompt closes on "Confirmation text on its way" unconditionally, so
    the caller is actively promised something that will never arrive. A quiet
    failure becomes Susie saying an untrue thing to every patient.

A comment did not stop it. An assertion will.
"""

import ast
import inspect
import pathlib

import pytest

from app.notifications import sms as sms_mod


def test_sms_defaults_on_when_the_environment_is_silent(monkeypatch):
    """
    The default lives in code, not in Render's env panel. A live service that
    deploys with SMS_ENABLED unset must still send.
    """
    monkeypatch.delenv("SMS_ENABLED", raising=False)
    src = inspect.getsource(sms_mod)
    tree = ast.parse(src)

    defaults = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "SMS_ENABLED"
        ):
            # os.getenv("SMS_ENABLED", <default>)
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                defaults.append(node.args[1].value)

    assert defaults, (
        "no os.getenv(\"SMS_ENABLED\", ...) call found in app/notifications/sms.py "
        "— if the gate moved, re-aim this test rather than deleting it"
    )
    for d in defaults:
        assert str(d).strip().lower() in ("true", "1", "yes", "on"), (
            f"SMS_ENABLED defaults to {d!r} on a LIVE clinic branch. That is "
            f"latency-eval's default and it means this clinic sends NOTHING "
            f"unless someone remembers a Render variable — the exact failure in "
            f"3b2f195, where it read as healthy because silence is what a "
            f"correct eval branch prints."
        )


def test_the_eval_staff_redirect_is_off_unless_explicitly_configured(monkeypatch):
    """
    EVAL_STAFF_SMS_TO reroutes staff-directed SMS to a test handset. Harmless
    on an eval service, silent data loss on a live one — the practitioner
    simply stops being told about their own bookings.

    It is env-gated with no default, which is correct; this pins that, so a
    future "helpful" default cannot appear.
    """
    monkeypatch.delenv("EVAL_STAFF_SMS_TO", raising=False)
    number = "+447700900123"
    assert sms_mod.redirect_staff_sms(number) == number, (
        "staff SMS is being redirected with EVAL_STAFF_SMS_TO unset — on a live "
        "service that silently diverts every practitioner notification"
    )


def test_reminders_default_on_for_this_branch(monkeypatch):
    """
    The sibling of the SMS default, and the same reasoning. Kept here as well
    as in test_appointment_reminders_kill_switch so that a wholesale revert of
    that file cannot quietly restore eval semantics.
    """
    monkeypatch.delenv("APPOINTMENT_REMINDERS_ENABLED", raising=False)
    from app.notifications import scheduler

    assert scheduler._appointment_reminders_enabled() is True, (
        "appointment reminders default OFF on a live clinic branch — that is "
        "latency-eval's setting; the other two live clinics send unconditionally"
    )


def test_the_clinic_calendar_is_not_the_eval_demo_calendar():
    """
    latency-eval repoints jv_v1 at Quentin's 'Susie Demo' calendar on purpose,
    so eval calls never write into JV's diary. Cutting a live branch from it
    inherits that repoint, and every booking would land in the wrong calendar
    while looking completely successful.
    """
    import json

    cfg = json.loads(
        pathlib.Path("app/clinics/jv_v1/clinic.json").read_text(encoding="utf-8")
    )
    cal = ((cfg.get("operational") or {}).get("calendar_id") or "").strip()
    assert cal, "jv_v1 has no calendar_id — bookings would fall back to 'primary'"
    assert not cal.startswith("63bc844e"), (
        "jv_v1 still points at the latency-eval DEMO calendar (63bc844e...). "
        "Every booking would be written to Quentin's calendar instead of the "
        "clinic's, and nothing about the call would look wrong."
    )
