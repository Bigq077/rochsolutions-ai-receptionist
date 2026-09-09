# tests/regression/test_d2_slot_presentation_caps_are_clinic_config.py
"""
D2 - how much of the diary is spoken belongs to the clinic, not to the module.

`_MAX_PRESENTED_DAYS`, `_MAX_PRESENTED_TIMES_MULTI_DAY` and
`_MAX_PRESENTED_TIMES_SINGLE_DAY` are the ONE owner of "how much is said" - the
sentence is built from `presented_days` by `build_slot_offer`, so what is not
selected here is not spoken. They were module constants shared by all four
clinics, and the numbers are an owner decision that differs by clinic: a
two-site physio line reading three days at two times is not the same call as a
single-room clinic with one practitioner.

WHAT THIS DOES NOT DO, and why. The handover ranked "slots drip-fed two at a
time" as the biggest failure cluster and said "do not just raise the number".
Measured against the corpus on 9 Sep 2026, the evidence says do not raise it at
all yet:

  * every judge complaint about drip-feeding predates BOTH 7624b1a7 (1 Sep,
    three days at two times) and fee6e67a (2 Sep, a continuation says "I also
    have"). Since the second landed there is exactly ONE readout complaint in
    the whole corpus;
  * one of the 2 Sep failures is a NINE-slot readout the caller hung up on, so
    more is demonstrably not better;
  * the surviving complaint (theorem_v3, 7 Sep 21:33) is not about batch size
    at all - see `docs/plan/OPEN_DEFECTS_2026-09-09.md`, D8.

So the defaults are exactly today's numbers and nothing moves for any clinic
until one opts in. That is the point: the lever exists, the owner decides, and
the decision is visible in `clinic.json` rather than in engine code.
"""
from __future__ import annotations

import pytest

from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import (
    _MAX_PRESENTED_DAYS,
    _MAX_PRESENTED_TIMES_MULTI_DAY,
    _MAX_PRESENTED_TIMES_SINGLE_DAY,
    _cap_presented_slots,
    _presentation_caps,
)


def _day(date: str, label: str, n: int) -> dict:
    starts = [f"{date}T{9 + i:02d}:00:00" for i in range(n)]
    return {
        "date": date,
        "day_label": label,
        "slot_times": [s[11:16] for s in starts],
        "slot_times_spoken": [f"time {i}" for i in range(n)],
        "slots": [{"start": s, "end": s} for s in starts],
    }


def _payload(days):
    return {"available_days": days, "total_days": len(days)}


# ── nothing moves without an opt-in ─────────────────────────────────────────

def test_the_defaults_are_todays_numbers():
    caps = _presentation_caps({"clinic_id": "theorem_v3"})
    assert caps["max_days"] == _MAX_PRESENTED_DAYS
    assert caps["times_per_day_multi"] == _MAX_PRESENTED_TIMES_MULTI_DAY
    assert caps["times_single_day"] == _MAX_PRESENTED_TIMES_SINGLE_DAY


@pytest.mark.parametrize(
    "clinic_id", ["theorem_v3", "jv_v1", "vital_edge", "northgate", "demo"]
)
def test_every_live_clinic_still_gets_the_module_defaults(clinic_id):
    """None of them has opted in, so all four must be byte-identical to the
    behaviour before this change."""
    caps = _presentation_caps({"clinic_id": clinic_id})
    assert caps == {
        "max_days": _MAX_PRESENTED_DAYS,
        "times_per_day_multi": _MAX_PRESENTED_TIMES_MULTI_DAY,
        "times_single_day": _MAX_PRESENTED_TIMES_SINGLE_DAY,
    }, clinic_id


def test_an_unknown_clinic_gets_the_defaults():
    for session in ({}, {"clinic_id": None}, {"clinic_id": "no-such-clinic"}):
        caps = _presentation_caps(session)
        assert caps["max_days"] == _MAX_PRESENTED_DAYS


def test_it_never_raises():
    """A readout preference must not be the thing that fails a lookup."""
    assert _presentation_caps(None)["max_days"] == _MAX_PRESENTED_DAYS


# ── and an opt-in is honoured ───────────────────────────────────────────────

def _with_config(monkeypatch, cfg):
    monkeypatch.setattr(
        rt, "_clinic_slot_presentation", lambda session: cfg, raising=True
    )


def test_a_clinic_can_widen_the_single_day_readout(monkeypatch):
    _with_config(monkeypatch, {"times_single_day": 5})
    out = _cap_presented_slots(
        _payload([_day("2026-09-14", "Monday 14th", 8)]), {"clinic_id": "x"}
    )
    assert out["presentation_mode"] == "single_day"
    assert len(out["first_day"]["slot_times"]) == 5
    assert out["first_day"]["more_times"] is True


def test_a_clinic_can_widen_the_multi_day_readout(monkeypatch):
    _with_config(monkeypatch, {"times_per_day_multi": 3})
    out = _cap_presented_slots(
        _payload([_day("2026-09-14", "Mon", 6), _day("2026-09-15", "Tue", 6)]),
        {"clinic_id": "x"},
    )
    assert out["presentation_mode"] == "multi_day"
    for day in out["presented_days"]:
        assert len(day["slot_times"]) == 3


def test_a_clinic_can_narrow_the_number_of_days(monkeypatch):
    _with_config(monkeypatch, {"max_days": 2})
    out = _cap_presented_slots(
        _payload([_day(f"2026-09-1{i}", f"D{i}", 4) for i in range(4, 8)]),
        {"clinic_id": "x"},
    )
    assert len(out["presented_days"]) == 2
    assert out["more_times"] is True
    # The BOOKABLE set is never trimmed — that invariant predates this.
    assert len(out["available_days"]) == 4
    assert out["total_days"] == 4


def test_a_nonsense_value_falls_back_rather_than_speaking_it(monkeypatch):
    """A typo in clinic.json must not read out forty slots or none."""
    for bad in ({"max_days": 0}, {"max_days": -1}, {"max_days": "three"},
                {"times_single_day": 999}, {"times_per_day_multi": None}):
        _with_config(monkeypatch, bad)
        caps = _presentation_caps({"clinic_id": "x"})
        assert caps["max_days"] == _MAX_PRESENTED_DAYS or caps["max_days"] >= 1
        assert 1 <= caps["times_single_day"] <= 9
        assert 1 <= caps["times_per_day_multi"] <= 9


def test_an_explicit_argument_still_wins(monkeypatch):
    """`max_days` is a parameter as well as a config key; a caller that passes
    one is asking for that number."""
    _with_config(monkeypatch, {"max_days": 2})
    out = _cap_presented_slots(
        _payload([_day(f"2026-09-1{i}", f"D{i}", 4) for i in range(4, 8)]),
        {"clinic_id": "x"},
        max_days=1,
    )
    assert out["presentation_mode"] == "single_day"


def test_theorem_cannot_opt_in_through_clinic_json_and_that_is_recorded():
    """Theorem's config does not come from the `operational` loader, so the
    new key is not readable for it — `get_clinic("theorem_v3")` has no
    `slot_presentation` at all rather than an empty one.

    That is not a bug introduced here: it is the same split that makes
    `theorem_v3` render its prompt from hardcoded Python rather than from
    `clinic.json`, and closing it is Phase 4 of the convergence work. Pinned so
    that "I set it and nothing happened" is answered by a test rather than by a
    call.
    """
    from app.clinic_config import get_clinic

    assert get_clinic("theorem_v3").get("slot_presentation") is None
    assert get_clinic("jv_v1").get("slot_presentation") == {}
    # Either way the caps resolve, which is the property that matters.
    assert _presentation_caps({"clinic_id": "theorem_v3"})["max_days"] == \
        _MAX_PRESENTED_DAYS
