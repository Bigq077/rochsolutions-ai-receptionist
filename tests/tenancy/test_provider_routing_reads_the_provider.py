"""Provider routing asked the clinic's NAME when the answer was in its config.

Eight places in `receptionist_tools.py` chose a booking backend like this:

    if _resolve_clinic_id(session) in ("theorem", "theorem_v2", "theorem_v3"):
        return await _book_appointment_acuity(args, session)

`get_clinic("theorem_v3")` has reported `booking_system='acuity'` the whole
time. The engine held the answer and asked the name — which is the one thing
CLAUDE.md tells it not to do:

    If you find yourself writing `if clinic == "..."` in `app/`, stop — that is
    the bug, not the fix.

The cost is concrete: a second Acuity clinic could not be routed at all, so
"can you onboard us" had a different answer depending on the caller's booking
software. Phase A of docs/plan/MULTITENANCY_SCOPE_2026-09-07.md.

BEHAVIOUR-PRESERVING THE DAY IT LANDS, and asserted rather than asserted-in-
prose: the clinics reporting `acuity` are exactly the three ids the tuples
named, and no others.

WHAT WAS DELIBERATELY NOT CONVERTED. The service-catalogue gate
(`_is_theorem_gate`) refuses any service but 'physiotherapy assessment' and
names Mark in its message. That is Theorem's business rule, not a provider
choice — a second Acuity clinic may sell a dozen services. Guards, screens and
write rules stay on the clinic id for the same reason, and because safety nets
gated on `booking_system ==` have silently excluded clinics before.

AND ROUTING MUST NOT OUTRUN CREDENTIALS. `_make_acuity_adapter` builds one
singleton from Theorem's un-prefixed env vars, so a clinic could now be ROUTED
to Acuity and land in Theorem's diary. The last test here is what stops that
reaching a patient: it fails the suite instead.
"""

import inspect
import re
from pathlib import Path

import pytest

from app import clinic_config as cc
from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import _ACUITY_CREDENTIALLED_CLINICS, uses_acuity

_TUPLE = 'in ("theorem", "theorem_v2", "theorem_v3")'
_ALL_CLINICS = sorted(
    {p.parent.name for p in Path("app/clinics").glob("*/clinic.json")}
    | set(cc.CLINICS)
    | set(cc.TWILIO_TO_CLINIC.values())
)


def _booking_system(cid):
    cfg = cc.get_clinic(cid) or {}
    return ((cfg.get("operational") or {}).get("booking_system")
            or cfg.get("booking_system"))


# ---------------------------------------------------------------------------
# The claim that makes this safe: nothing changes today
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cid", _ALL_CLINICS)
def test_the_new_test_agrees_with_the_old_one_for_every_clinic(cid):
    """`booking_system == 'acuity'` and the hardcoded tuple must select the
    same clinics. If a future clinic breaks this, the conversion changed
    behaviour and the failure says which clinic did it."""
    by_name = cid in ("theorem", "theorem_v2", "theorem_v3")
    by_provider = uses_acuity({"clinic_id": cid})
    assert by_provider == by_name, (
        "%s: booking_system=%r says acuity=%s, the old tuple said %s"
        % (cid, _booking_system(cid), by_provider, by_name))


@pytest.mark.parametrize("junk", [None, "", {}, 0, [], "a string",
                                  {"clinic_id": None},
                                  {"clinic_id": "no-such-clinic"}])
def test_it_never_raises_and_never_guesses_acuity(junk):
    """An unreadable config routes to the Google path, whose own guards refuse
    on a missing calendar id. A refusal, not a booking into someone else's
    diary."""
    assert uses_acuity(junk) is False


# ---------------------------------------------------------------------------
# The conversion actually happened, and stopped where it should
# ---------------------------------------------------------------------------

def test_no_provider_route_still_switches_on_the_clinic_name():
    """One tuple may remain — the service catalogue — and it is named here so
    that a second one cannot appear unnoticed."""
    src = inspect.getsource(rt)
    lines = [l.strip() for l in src.split("\n") if _TUPLE in l]
    assert len(lines) == 1, (
        "expected exactly the service-catalogue gate to survive, found %d:\n  %s"
        % (len(lines), "\n  ".join(lines)))
    assert lines[0].startswith("_is_theorem_gate ="), lines[0]


@pytest.mark.parametrize("executor", [
    "_book_appointment_acuity",
    "_lookup_appointment_acuity",
    "_cancel_appointment_acuity",
    "_reschedule_appointment_acuity",
    "_check_availability_acuity",
])
def test_every_acuity_executor_is_reached_through_the_provider_test(executor):
    """The call that dispatches to each Acuity executor must be guarded by
    `uses_acuity`, not by an identity."""
    src = inspect.getsource(rt).split("\n")
    calls = [i for i, l in enumerate(src)
             if executor + "(args, session)" in l and "async def" not in l]
    assert calls, "%s is never dispatched — re-aim this test" % executor
    for i in calls:
        window = "\n".join(src[max(0, i - 4):i + 1])
        assert "uses_acuity(session)" in window, (
            "%s is dispatched without the provider test:\n%s" % (executor, window))


def test_the_service_catalogue_gate_was_left_alone():
    """It refuses every service but one and names Mark. That is a clinic's
    business rule, and converting it would impose Theorem's catalogue on any
    future Acuity clinic."""
    src = inspect.getsource(rt)
    i = src.index("_is_theorem_gate =")
    assert _TUPLE in src[i:i + 200]
    assert "uses_acuity" not in src[i:i + 200]


# ---------------------------------------------------------------------------
# Routing must not outrun credentials
# ---------------------------------------------------------------------------

def test_every_acuity_clinic_has_credentials_the_engine_can_reach():
    """`_make_acuity_adapter` builds ONE singleton from Theorem's un-prefixed
    env vars, so a clinic that declares Acuity without being credentialled
    would be routed to Theorem's diary.

    This is the test that makes Phase A safe to ship ahead of Phase B: adding
    `"booking_system": "acuity"` to a clinic.json fails here, loudly, before it
    can take a call.
    """
    offenders = [cid for cid in _ALL_CLINICS
                 if str(_booking_system(cid) or "").lower() == "acuity"
                 and cid not in _ACUITY_CREDENTIALLED_CLINICS]
    assert not offenders, (
        "these clinics declare Acuity but the engine holds no credentials for "
        "them, so they would book into Theorem's diary: %s\n"
        "Phase B (per-clinic ACUITY_CONFIG) is what lifts this." % offenders)


def test_the_credentialled_list_is_not_wider_than_the_config():
    """The other direction: an id listed as credentialled that no longer books
    through Acuity is dead weight and hides the next mistake."""
    stale = [cid for cid in _ACUITY_CREDENTIALLED_CLINICS
             if str(_booking_system(cid) or "").lower() != "acuity"]
    assert not stale, stale
