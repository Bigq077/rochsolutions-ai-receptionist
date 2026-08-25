"""Whether a text is SENT and whether Susie SAYS one was sent are one switch.

They used to be two readers of `SMS_ENABLED`, each with its own default:

    app/notifications/sms.py               default "true"   on a live branch
    app/prompts/clinic_template_prompt.py  default "false"  on the same branch

With the env var unset in Render — which is the state a service is in until
somebody remembers — the text goes out while Susie tells the caller it will not.
That was live on theorem-onboarding, vitaledge-onboarding and jv_v2 on
2026-08-25, and it is invisible from either file alone: each one is
self-consistent and correct in isolation.

The remedy is not "keep them in step". It is one function that owns the read AND
the default, so a branch flipping its default cannot flip only half. These tests
pin that property rather than any particular default, so they stay true on the
eval branch (off) and the live branches (on) alike.
"""

import inspect
from unittest.mock import patch

import pytest

from app.notifications import sms as sms_module
from app.notifications.sms import sms_enabled


# ── the switch itself ──────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("true", True), ("TRUE", True), ("  true  ", True),
    ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("no", False), ("off", False),
    ("", False), ("banana", False),
])
def test_the_switch_reads_the_env_var(value, expected):
    with patch.dict("os.environ", {"SMS_ENABLED": value}):
        assert sms_enabled() is expected


def test_the_default_applies_when_the_var_is_unset():
    """Whatever this branch's default is, sms_enabled() must honour it — that is
    what makes the prompt follow the sender."""
    import os
    expected = sms_module._SMS_ENABLED_DEFAULT.strip().lower() in sms_module._TRUTHY
    env = {k: v for k, v in os.environ.items() if k != "SMS_ENABLED"}
    with patch.dict("os.environ", env, clear=True):
        assert sms_enabled() is expected


# ── nobody may read the var independently ──────────────────────────────────

def test_only_one_place_in_the_app_reads_sms_enabled_from_the_environment():
    """The guard that actually prevents the defect coming back.

    A second `os.getenv("SMS_ENABLED", ...)` anywhere in app/ reintroduces a
    second default, and the two can then be flipped apart on a branch — which is
    exactly how this shipped to three live clinics.
    """
    import pathlib
    root = pathlib.Path(sms_module.__file__).resolve().parents[1]
    readers = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if 'getenv("SMS_ENABLED"' in line and "#" not in line.split('getenv')[0]:
                readers.append(f"{path.relative_to(root).as_posix()}:{i}")

    # Assert on the FILE, never the line: sms.py is edited often and pinning a
    # line number here would fail the suite for reasons that have nothing to do
    # with the property being guarded.
    files = sorted({r.rsplit(":", 1)[0] for r in readers})
    assert files == ["notifications/sms.py"], (
        "SMS_ENABLED is read from the environment outside sms_enabled(): "
        f"{readers}. Call sms_enabled() instead — the default is part of the "
        "switch, and a second copy of it is the defect."
    )
    assert len(readers) == 1, (
        f"sms.py reads SMS_ENABLED {len(readers)} times ({readers}); there must "
        "be exactly one, inside sms_enabled()."
    )


def test_the_sender_gates_on_the_shared_switch():
    src = inspect.getsource(sms_module.SMSService.send_sms)
    assert "if not sms_enabled():" in src


def test_the_prompt_gates_on_the_shared_switch():
    from app.prompts import clinic_template_prompt
    src = inspect.getsource(clinic_template_prompt)
    assert src.count("sms_enabled") >= 2, (
        "a prompt site stopped using the shared switch"
    )


# ── the behaviour that was broken ──────────────────────────────────────────

@pytest.mark.parametrize("value", ["true", "false"])
def test_the_promise_and_the_send_always_agree(value):
    """The property, stated directly: for any environment, what the sender does
    and what the prompt promises come from the same answer."""
    from app.clinic_config import get_clinic
    from app.prompts.clinic_template_prompt import build_clinic_prompt

    with patch.dict("os.environ", {"SMS_ENABLED": value}):
        on = sms_enabled()
        _, dyn = build_clinic_prompt({"clinic_id": "jv_v1"}, get_clinic("jv_v1"))
        static, _ = build_clinic_prompt({"clinic_id": "jv_v1"}, get_clinic("jv_v1"))
        promised = "sent you a confirmation text" in (static + dyn)
        denied = "NEVER tell the caller a confirmation text has been sent" in (
            static + dyn
        )

    if on:
        assert not denied, "SMS is on, but the prompt forbids mentioning the text"
    else:
        assert not promised, (
            "SMS is off, but the prompt promises the caller a text — the exact "
            "shape heard on CA4969580082db5e757c3b1d04dd38e7ae"
        )
