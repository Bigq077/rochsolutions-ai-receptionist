"""A missing transfer target must not dial a personal mobile.

`TRANSFER_FALLBACK_NUMBER` defaulted to `+447502211207` -- the handset that
places the test calls -- while the comment directly above it said the env var
existed "to avoid hardcoding a real UK number in source code".

Who that reached, before this:

  * any Render service that simply had not set the env var;
  * any clinic whose config forgot `transfer_phone`;
  * any Twilio number missing from TWILIO_TO_CLINIC, because an unmapped
    number falls through to the "demo" clinic, which carries no
    `transfer_phone` at all.

A patient asking for a human got a stranger's phone, and `resolve_transfer_target`
logs that case as a warning nobody was reading.

Empty is a CONTROLLED outcome, which is why this is safe: `_handle_transfer`
logs "transfer ABORTED" and declines to redirect, so the caller keeps talking
to Susie. Emitting `<Dial></Dial>` would drop them mid-call -- that is the
failure `test_transfer_promise_requires_target` already pins -- and dialling a
stranger is worse than either.

northgate names the number explicitly in its own clinic.json instead: it is the
demo line and a transfer is part of the demo. That is the difference this test
protects -- a demo destination someone chose, not a global default nobody knew
about.
"""
from __future__ import annotations

import pytest

# Every clinic that answers a real Twilio number. "demo" is deliberately
# excluded: it is the catch-all for an UNMAPPED number, and an unmapped number
# must not dial anyone.
LIVE_CLINICS = ["northgate", "jv_v1", "vital_edge", "theorem"]


def test_the_global_fallback_default_is_empty():
    """The default itself. Pinned separately from the per-clinic keys because
    it is the one that applies to a clinic nobody has thought about yet."""
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    assert cfg.TRANSFER_FALLBACK_NUMBER == "", (
        f"TRANSFER_FALLBACK_NUMBER defaults to {cfg.TRANSFER_FALLBACK_NUMBER!r}. "
        f"A service missing the env var now sends patients there."
    )


#: The one known exemption, and why it is not fixed here.
#:
#: `app/flows/triage_legacy.py` defaults THEOREM_NOTIFICATION_SMS to
#: +447870166861 -- Mark's own staff number, on the legacy /twilio path, for an
#: insurance booking alert. Same CLASS as the defect above, but not the same
#: severity: it texts that clinic's own staff rather than sending a patient to
#: a stranger. Emptying it would silently stop those alerts if the env var is
#: unset in Render, which cannot be checked from the repo -- so it is recorded
#: rather than changed. The two OTHER readers of that var
#: (`connection.py:17665`, `notifications/sms.py:107`) already default to
#: nothing, which is the shape this one should end up with.
_KNOWN_EXEMPT = {"app/flows/triage_legacy.py"}


def test_no_transfer_env_var_defaults_to_a_real_phone_number():
    """Grep, because the number lives in several files and only some of those
    occurrences are defaults. `sms_relay_to` and the docstrings that use it as
    an EXAMPLE are legitimate; a bare `os.getenv(..., "+44...")` is not.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app"
    offenders = []
    pattern = re.compile(r'os\.getenv\([^)]*,\s*["\']\+?44\d{9,}["\']\s*\)')
    for path in root.rglob("*.py"):
        rel = path.relative_to(root.parent).as_posix()
        if rel in _KNOWN_EXEMPT:
            continue
        for i, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if pattern.search(line):
                offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, (
        "an env var defaults to a real UK phone number:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("clinic_id", LIVE_CLINICS)
def test_every_live_clinic_names_its_own_transfer_target(clinic_id):
    """Read through `get_clinic`, never the JSON file.

    A key that exists in clinic.json but does not survive the loader's
    flattening is dead config that reads like a working one -- the recurring
    trap in this codebase.
    """
    from app.clinic_config import get_clinic

    target = ((get_clinic(clinic_id) or {}).get("transfer_phone") or "").strip()
    assert target, (
        f"{clinic_id} has no transfer_phone reaching get_clinic. With the "
        f"fallback now empty its transfers abort, and before this change they "
        f"dialled a private mobile."
    )
    assert target.startswith("+"), f"{clinic_id} transfer_phone {target!r} is not E.164"
