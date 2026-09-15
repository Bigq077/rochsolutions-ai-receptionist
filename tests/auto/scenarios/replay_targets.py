"""Where a scenario mined from a real call may safely be replayed.

A mined scenario carries the caller's turns from a real call at a real clinic.
Re-driving it needs two things that pull in opposite directions:

  fidelity — it must reach the SAME clinic config it was recorded against, or
             the greeting, services, booking system and slot wording all differ
             and the replay proves nothing;
  safety   — it must NOT reach the clinic that produced it, because that clinic
             has real patients and a real diary.

The resolution is a per-clinic *test line*: a number that loads the same config
lineage without serving patients. Where one exists, a mined scenario is pointed
at it. Where none exists, the scenario is NOT runnable and says so — refusing is
the whole point, because the alternative is what happened before this file
existed:

    regression_ff10f738, mined from Theorem, replayed 2026-09-15 against the
    default number. Susie answered "Northgate Physiotherapy's AI receptionist",
    the caller's turns did not fit that flow, the call stopped at flow_step 0
    after 5 turns — and it PASSED, because the mined `expected` is the
    placeholder {'no_technical_error': True} and she never said "technical
    issue". A green that means nothing is worse than a red.

Numbers come from CLINIC_BY_NUMBER in app/clinic_config.py. Keep them in step.
"""

from __future__ import annotations

# Clinic that produced the call  ->  number that is SAFE to replay it against.
#
# The value must load a config of the same lineage AND serve no patients.
REPLAY_TARGETS: dict[str, str] = {
    # theorem_v3 is the live Acuity line; theorem_v2 is its test line and
    # clinic_config.py describes v3 as "copy of theorem_v2", so the lineage
    # matches and the replay is faithful.
    "theorem_v3": "+447366530580",   # -> theorem_v2, Theorem test line
    "theorem_v2": "+447366530580",   # already the test line
    # northgate is the demo clinic — SUSIE_NUMBER's default, and 1 real call in
    # the 45 days to 2026-09-15. Safe to replay against itself.
    "northgate":  "+447366263180",
    "demo":       "+447366263180",
    # jv_v1 -> jv_v1_test, a replay-only copy of JV's config reached by an Ofcom
    # drama-reserved number. Gated by _jv_test_calendar_ready(): the copy ships
    # with a sentinel calendar id and is refused until a throwaway calendar
    # replaces it, because a clinic.json copied from another tenant that keeps
    # the original calendar_id writes silently into that tenant's diary
    # (clinic_config.py says exactly this at the line that sets it).
    "jv_v1":      "+447700900001",
    "jv_v1_test": "+447700900001",
}

_JV_CALENDAR_SENTINEL = "REPLACE_WITH_JV_TEST_CALENDAR_ID"


def _jv_test_calendar_ready() -> bool:
    """True once jv_v1_test points at a calendar that is not JV's own."""
    try:
        from app.clinic_config import get_clinic

        test_cal = ((get_clinic("jv_v1_test") or {}).get("calendar_id") or "").strip()
        live_cal = ((get_clinic("jv_v1") or {}).get("calendar_id") or "").strip()
    except Exception:
        return False
    if not test_cal or test_cal == _JV_CALENDAR_SENTINEL:
        return False
    if test_cal in ("primary",) or test_cal == live_cal:
        return False
    return True

# Clinics with NO safe replay target. Listed explicitly rather than omitted, so
# the reason is recorded and a future test line is an obvious one-line move.
NO_SAFE_TARGET: dict[str, str] = {
    "jv_v1": (
        "jv_v1_test exists but still carries the sentinel calendar id. Put a "
        "THROWAWAY Google Calendar id in "
        "app/clinics/jv_v1_test/clinic.json -> operational.calendar_id (share "
        "the calendar with the service account first), and these become "
        "runnable. It must not be jointventurephysiotherapy@gmail.com or "
        "'primary'."
    ),
    "vital_edge": (
        "Vital Edge is live on +447426779875 with a provisional Google Calendar "
        "model. No test line exists."
    ),
}


def replay_target(clinic_id: str | None) -> str | None:
    """The number to replay a scenario from `clinic_id` against, or None.

    None means "do not run this scenario" — never "use the default", which is
    the failure this module exists to prevent.
    """
    if not clinic_id:
        return None
    if clinic_id in ("jv_v1", "jv_v1_test") and not _jv_test_calendar_ready():
        return None
    return REPLAY_TARGETS.get(clinic_id)


def why_not_runnable(clinic_id: str | None) -> str:
    """A sentence explaining why a scenario cannot be replayed."""
    if not clinic_id:
        return (
            "the scenario records no source clinic — mine it again with a "
            "build of app/obs/to_scenario.py that writes source.clinic_id"
        )
    if clinic_id in NO_SAFE_TARGET:
        return NO_SAFE_TARGET[clinic_id]
    return (
        f"no replay target is configured for clinic {clinic_id!r} — add one to "
        "tests/auto/scenarios/replay_targets.py once a test line exists"
    )
