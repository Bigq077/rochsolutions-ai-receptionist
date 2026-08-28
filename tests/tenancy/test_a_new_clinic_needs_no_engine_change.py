"""A new clinic is a config file, not a branch. This is the gate that proves it.

WHY THIS FILE EXISTS

One clinic = one branch = one Render service. Measured over 24-28 Aug 2026:
70 commits of engineering on canonical, 199 re-applying them to the clinic
branches. 74% of the work was re-applying finished work. The tax is 2.84x at
three live clinics; the end-of-September webinar implies eighteen, where it is
17x and the model simply stops working.

So the deliverable is not "port faster". It is that clinic #4 through #18 are
onboarded with a clinic.json, a number in TWILIO_TO_CLINIC, and calendar
credentials -- no engine change, no branch, no deploy of anything but config.

These tests stand up a clinic that does not exist, from config alone, and
assert the engine never learns its name. `test_no_previous_tenant_leaks` is the
one that matters: the real onboarding motion is copying a working tenant's
clinic.json and editing it, so the question is not "does a new clinic work" but
"does any of the OLD one survive the copy".

WHAT IT ALREADY CAUGHT

* A top-level `calendar_id` -- the obvious key, and the one every legacy clinic
  in CLINICS uses -- was silently discarded by the json mapper. A clinic.json
  copied from another tenant therefore kept writing into that tenant's diary,
  and nothing in the call sounded wrong.
* `app/clinics/demo/clinic.json` is a fragment of PYTHON source saved with a
  .json extension. Inert today (demo resolves from the legacy dict first), but
  a perfect demonstration of the silent-failure mode: an unparseable clinic.json
  is swallowed and the tenant silently BECOMES the demo clinic -- demo persona
  to the caller, demo calendar for the booking.

Deterministic: no model, no network, no calendar. The clinic is written to a
tmp_path and `_CLINICS_DIR` is redirected at it, so nothing touches the repo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import app.clinic_config as cc

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "app"

# The clinic we invent. Nothing in app/ may ever mention it.
NEW_ID = "northgate"
NEW_NUMBER = "+447700900999"          # Ofcom reserved FICTITIOUS range
NEW_CALENDAR = "northgate-clinic@group.calendar.google.com"

# The tenant we copy FROM, and every trace of them that must not survive.
DONOR = "jv_v1"
DONOR_TRACES = ("Joint Venture", "JVP", "Bolton", "bolton", "jv_v1")

_REWRITES = (
    ("Joint Venture Physiotherapy", "Northgate Physiotherapy"),
    ("Joint Venture", "Northgate"),
    ("JVP", "NGP"),
    ("Bolton", "Didsbury"),
    ("bolton", "didsbury"),
    ("jv_v1", NEW_ID),
)


@pytest.fixture
def new_clinic(tmp_path, monkeypatch):
    """Onboard a clinic the way a human would: copy a tenant, edit, map a number.

    The rewrite is deliberately a blunt find-and-replace over the raw text,
    because that is what onboarding actually is. What it CANNOT reach -- the
    opaque calendar id -- is exactly the trap this file exists to catch, so the
    calendar is repointed explicitly, as an onboarding step in its own right.
    """
    raw = (APP / "clinics" / DONOR / "clinic.json").read_text(encoding="utf-8-sig")
    for old, new in _REWRITES:
        raw = raw.replace(old, new)
    loaded = json.loads(raw)
    loaded["operational"]["calendar_id"] = NEW_CALENDAR

    root = tmp_path / "clinics"
    (root / NEW_ID).mkdir(parents=True)
    (root / NEW_ID / "clinic.json").write_text(json.dumps(loaded), encoding="utf-8")

    # Point the loader at the scratch tree, keeping the real clinics visible so
    # the shared-calendar check still has something to collide with.
    for child in (APP / "clinics").iterdir():
        if child.is_dir() and (child / "clinic.json").is_file() and child.name != NEW_ID:
            dst = root / child.name
            dst.mkdir(exist_ok=True)
            (dst / "clinic.json").write_bytes((child / "clinic.json").read_bytes())

    monkeypatch.setattr(cc, "_CLINICS_DIR", root)
    monkeypatch.setattr(cc, "_CLINIC_JSON_CACHE", {})
    monkeypatch.setitem(cc.TWILIO_TO_CLINIC, NEW_NUMBER, NEW_ID)
    return loaded


# ---------------------------------------------------------------------------
# It resolves, and it runs the loop the live clinics run
# ---------------------------------------------------------------------------

def test_the_number_reaches_the_new_clinic(new_clinic):
    assert cc.clinic_id_from_twilio_to(NEW_NUMBER) == NEW_ID
    assert cc.twilio_number_for_clinic(NEW_ID) == NEW_NUMBER


def test_it_is_not_quietly_the_demo_clinic(new_clinic):
    """get_clinic falls back to demo for anything it cannot load, silently."""
    clinic = cc.get_clinic(NEW_ID)
    assert clinic["clinic_id"] == NEW_ID
    assert "Northgate" in (clinic.get("clinic_name") or clinic.get("display_name") or "")
    assert clinic.get("display_name") != cc.CLINICS["demo"]["display_name"]


def test_it_runs_the_freeform_loop_like_every_live_clinic(new_clinic):
    assert cc.is_freeform_clinic(NEW_ID) is True
    assert cc.single_location_template(NEW_ID) == "didsbury"


def test_it_passes_the_onboarding_checklist(new_clinic):
    assert cc.validate_clinic_config(NEW_ID) == []


# ---------------------------------------------------------------------------
# The engine never learns its name
# ---------------------------------------------------------------------------

def test_no_engine_file_mentions_the_new_clinic(new_clinic):
    """The whole point. A clinic is data; app/ must not know this one exists."""
    offenders = []
    for path in APP.rglob("*.py"):
        if NEW_ID in path.read_text(encoding="utf-8", errors="replace"):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, (
        f"{NEW_ID!r} appears in engine code: {offenders}. A clinic must be "
        "config. If a behaviour needs to differ, gate it on a clinic.json key "
        "(booking_system / availability_mode / prompt_engine), never on an id."
    )


def test_no_previous_tenant_leaks_into_the_new_clinics_prompt(new_clinic):
    """Copy a tenant, rename them, and NOTHING of the original may survive.

    If a donor string reaches the model after a full rewrite of the config, it
    came from engine code -- a hardcoded clinic name in a prompt module -- and
    that is a tenancy leak, not a wording bug. It would tell a Didsbury caller
    they were through to Bolton.
    """
    from app.prompts.susie_system_prompt import build_system_prompt_parts

    parts = build_system_prompt_parts({"clinic_id": NEW_ID})
    prompt = "\n".join(p if isinstance(p, str) else str(p) for p in parts)

    assert "Northgate" in prompt and "Didsbury" in prompt, (
        "the new clinic's own identity did not reach the model at all")

    leaks = {t: prompt.count(t) for t in DONOR_TRACES if t in prompt}
    assert not leaks, (
        f"the donor tenant leaked into a different clinic's prompt: {leaks}. "
        "Every one of these came from engine code, because the config no "
        "longer contains them."
    )


# ---------------------------------------------------------------------------
# The trap that writes bookings into someone else's diary
# ---------------------------------------------------------------------------

def test_the_new_clinic_books_into_its_own_calendar(new_clinic):
    from app.tools import receptionist_tools as rt

    clinic = cc.get_clinic(NEW_ID)
    resolved = rt._resolve_calendar_id(clinic, cc.single_location_template(NEW_ID))
    assert resolved == NEW_CALENDAR
    assert resolved != cc.get_clinic(DONOR).get("calendar_id")


def test_a_top_level_calendar_id_is_honoured(tmp_path, monkeypatch, new_clinic):
    """It used to be silently discarded, which is how a copy keeps the donor's.

    `operational.calendar_id` is the documented key, but the top-level one is
    what the legacy CLINICS dict uses, so it is the obvious place to reach for.
    Reaching for it must not fail silently.
    """
    root = cc._CLINICS_DIR
    loaded = json.loads((root / NEW_ID / "clinic.json").read_text(encoding="utf-8"))
    loaded["operational"].pop("calendar_id", None)
    loaded["calendar_id"] = "top-level@group.calendar.google.com"
    (root / NEW_ID / "clinic.json").write_text(json.dumps(loaded), encoding="utf-8")
    monkeypatch.setattr(cc, "_CLINIC_JSON_CACHE", {})

    assert cc.get_clinic(NEW_ID).get("calendar_id") == \
        "top-level@group.calendar.google.com"


def test_sharing_a_calendar_with_another_clinic_is_caught(new_clinic, monkeypatch):
    """The exact result of copying a clinic.json and not repointing it."""
    root = cc._CLINICS_DIR
    loaded = json.loads((root / NEW_ID / "clinic.json").read_text(encoding="utf-8"))
    loaded["operational"]["calendar_id"] = cc.get_clinic(DONOR)["calendar_id"]
    (root / NEW_ID / "clinic.json").write_text(json.dumps(loaded), encoding="utf-8")
    monkeypatch.setattr(cc, "_CLINIC_JSON_CACHE", {})

    problems = cc.validate_clinic_config(NEW_ID)
    assert any("shared with" in p for p in problems), problems


def test_an_unreadable_clinic_json_is_caught_before_it_becomes_demo(
        new_clinic, monkeypatch):
    """app/clinics/demo/clinic.json is Python source with a .json extension.

    Inert there, because demo resolves from CLINICS first. For a real tenant
    the same mistake is invisible: the loader swallows it, get_clinic serves
    DEMO, and the caller hears the wrong clinic while the booking goes to the
    demo calendar. The checklist has to catch it, because the call will not.
    """
    root = cc._CLINICS_DIR
    (root / NEW_ID / "clinic.json").write_text(
        "# app/clinic_config.py\n\nCLINICS = {\n", encoding="utf-8")
    monkeypatch.setattr(cc, "_CLINIC_JSON_CACHE", {})

    assert cc.get_clinic(NEW_ID).get("display_name") == \
        cc.CLINICS["demo"]["display_name"], "precondition: it becomes demo"
    problems = cc.validate_clinic_config(NEW_ID)
    assert any("does not parse" in p for p in problems), problems


def test_a_clinic_json_saved_on_windows_still_loads(new_clinic, monkeypatch):
    """A BOM must not turn a tenant into the demo clinic.

    No clinic.json carries one today, so this is a guard rather than a repair.
    It is worth having because onboarding hands a JSON file to people who are
    not engineers, and every common Windows editor will write a BOM: the loader
    would raise, the loader would swallow it, and the tenant would silently
    serve the demo persona from the demo calendar. An onboarding format cannot
    have a failure mode that a text editor can trigger by default.
    """
    root = cc._CLINICS_DIR
    path = root / NEW_ID / "clinic.json"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    monkeypatch.setattr(cc, "_CLINIC_JSON_CACHE", {})

    assert "Northgate" in (cc.get_clinic(NEW_ID).get("clinic_name") or "")
    assert cc.validate_clinic_config(NEW_ID) == []


# ---------------------------------------------------------------------------
# The clinics that are actually live
# ---------------------------------------------------------------------------

# Live config problems the checklist finds that are NOT ours to fix silently.
# Each needs an owner decision about a real clinic, so it is recorded here
# rather than either changing a live clinic on a guess or weakening the check.
KNOWN_OPEN = {
    "vital_edge": [
        # opening_hours.kingston.sunday says "Closed" while
        # operational.working_hours.sun is 09:00-18:00. Both are live:
        # _check_availability_diary reads working_hours and hands it to the
        # slot generator, and opening_hours is what Susie reads out. Because
        # the diary reader treats an empty day as FREE, an unworked Sunday
        # looks wide open. Needs the practitioner to say whether he works
        # Sundays -- guessing either way changes a live clinic's bookable week.
        "sunday",
    ],
}


def test_every_mapped_clinic_passes_the_checklist():
    """No tmp fixture: this is the real TWILIO_TO_CLINIC, as deployed."""
    failing = {}
    for cid, problems in cc.validate_all_clinics().items():
        unexpected = [p for p in problems
                      if not any(k in p for k in KNOWN_OPEN.get(cid, []))]
        if unexpected:
            failing[cid] = unexpected
    assert not failing, "\n".join(
        f"{cid}: " + "; ".join(probs) for cid, probs in failing.items())


def test_the_known_open_problems_are_still_real():
    """A stale exemption is worse than none — it hides the next one.

    If a KNOWN_OPEN entry stops firing, someone fixed it; delete the entry
    rather than leaving a permanent hole in the check above.
    """
    for cid, markers in KNOWN_OPEN.items():
        problems = " ".join(cc.validate_clinic_config(cid))
        for marker in markers:
            assert marker in problems, (
                f"{cid} no longer reports {marker!r} — remove it from "
                "KNOWN_OPEN so the checklist covers it again")


def test_no_two_live_clinics_share_a_calendar():
    """A double-booking has already been caused by a mis-pointed calendar id."""
    by_calendar: dict[str, list[str]] = {}
    for cid in sorted(set(cc.TWILIO_TO_CLINIC.values())):
        clinic = cc.get_clinic(cid)
        if not (clinic.get("booking_system") or "").startswith("google_calendar"):
            continue                     # Acuity clinics carry no calendar_id
        cal = (clinic.get("calendar_id") or "").strip()
        if cal:
            by_calendar.setdefault(cal, []).append(cid)
    shared = {c: ids for c, ids in by_calendar.items() if len(ids) > 1}
    assert not shared, f"clinics sharing a calendar: {shared}"
