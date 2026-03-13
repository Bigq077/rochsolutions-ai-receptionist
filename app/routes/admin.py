# app/routes/admin.py
import asyncio
import os
import httpx
from fastapi import APIRouter
from app.storage.redis_store import redis_delete_key

router = APIRouter(prefix="/admin")

ADMIN_KEY = os.getenv("ADMIN_KEY", "")


@router.get("/clear_google_tokens")
async def clear_google_tokens(key: str):
    if not ADMIN_KEY or key != ADMIN_KEY:
        return {"ok": False, "error": "unauthorized"}

    await redis_delete_key("google_tokens")
    return {"ok": True}


@router.get("/test-acuity")
async def test_acuity(key: str):
    """
    Read-only Acuity connectivity check. Hits three safe endpoints:
      /me                - confirms credentials are valid
      /appointment-types - lists configured appointment types
      /calendars         - lists practitioner calendars

    No appointments are created or modified.
    Usage: GET /admin/test-acuity?key=YOUR_ADMIN_KEY
    """
    if not ADMIN_KEY or key != ADMIN_KEY:
        return {"ok": False, "error": "unauthorized"}

    user_id = os.getenv("ACUITY_USER_ID", "").strip()
    api_key = os.getenv("ACUITY_API_KEY", "").strip()

    # Check env vars are present before hitting the API
    missing = [k for k, v in {"ACUITY_USER_ID": user_id, "ACUITY_API_KEY": api_key}.items() if not v]
    if missing:
        return {
            "ok": False,
            "error": "Missing env vars: " + ", ".join(missing),
            "hint": "Set these in the Render dashboard under Environment.",
        }

    base = "https://acuityscheduling.com/api/v1"
    auth = (user_id, api_key)
    results = {}

    async with httpx.AsyncClient(auth=auth, timeout=10.0) as client:

        # 1. Account info - proves credentials work
        try:
            r = await client.get(f"{base}/me")
            if r.status_code == 200:
                me = r.json()
                results["account"] = {
                    "ok": True,
                    "name": me.get("name"),
                    "email": me.get("email"),
                    "id": me.get("id"),
                }
            else:
                results["account"] = {"ok": False, "status": r.status_code, "body": r.text[:300]}
        except Exception as e:
            results["account"] = {"ok": False, "error": str(e)}

        # 2. Appointment types - confirms Acuity is configured correctly
        try:
            r = await client.get(f"{base}/appointment-types")
            if r.status_code == 200:
                types = r.json()
                results["appointment_types"] = {
                    "ok": True,
                    "count": len(types),
                    "types": [
                        {"id": t.get("id"), "name": t.get("name"), "duration": t.get("duration")}
                        for t in types
                    ],
                }
            else:
                results["appointment_types"] = {"ok": False, "status": r.status_code, "body": r.text[:300]}
        except Exception as e:
            results["appointment_types"] = {"ok": False, "error": str(e)}

        # 3. Calendars - shows which practitioner calendars exist
        try:
            r = await client.get(f"{base}/calendars")
            if r.status_code == 200:
                cals = r.json()
                results["calendars"] = {
                    "ok": True,
                    "count": len(cals),
                    "calendars": [
                        {"id": c.get("id"), "name": c.get("name"), "email": c.get("email")}
                        for c in cals
                    ],
                }
            else:
                results["calendars"] = {"ok": False, "status": r.status_code, "body": r.text[:300]}
        except Exception as e:
            results["calendars"] = {"ok": False, "error": str(e)}

    # Report which calendar ID env vars are set
    cal_vars = {
        "ACUITY_CALENDAR_ID_ALCESTER": os.getenv("ACUITY_CALENDAR_ID_ALCESTER", ""),
        "ACUITY_CALENDAR_ID_REDDITCH": os.getenv("ACUITY_CALENDAR_ID_REDDITCH", ""),
        "ACUITY_CALENDAR_ID_MARK":     os.getenv("ACUITY_CALENDAR_ID_MARK", ""),
        "ACUITY_CALENDAR_ID_LEANNE":   os.getenv("ACUITY_CALENDAR_ID_LEANNE", ""),
    }
    results["env_calendar_ids"] = {
        k: ("set" if v else "MISSING") for k, v in cal_vars.items()
    }

    overall_ok = all(v.get("ok") for v in results.values() if isinstance(v, dict) and "ok" in v)
    return {"ok": overall_ok, **results}


@router.get("/test-acuity-slots")
async def test_acuity_slots(key: str):
    """
    Deep Acuity slot diagnostic.  Answers two questions in one call:

    1. Are the right calendar IDs configured in Render env vars?
       Shows the actual stored value (last 6 chars visible) next to every
       calendar that Acuity actually knows about, so mismatches are obvious.

    2. Do slots actually come back from Acuity for each location?
       Calls /availability/times for every configured appointment type +
       calendar combination and returns the first 3 slots (or a clear error).

    Usage: GET /admin/test-acuity-slots?key=YOUR_ADMIN_KEY
    No appointments are created or modified.
    """
    from datetime import date, timedelta

    if not ADMIN_KEY or key != ADMIN_KEY:
        return {"ok": False, "error": "unauthorized"}

    user_id = os.getenv("ACUITY_USER_ID", "").strip()
    api_key = os.getenv("ACUITY_API_KEY", "").strip()

    if not user_id or not api_key:
        missing = [k for k, v in {"ACUITY_USER_ID": user_id, "ACUITY_API_KEY": api_key}.items() if not v]
        return {"ok": False, "error": "Missing env vars: " + ", ".join(missing)}

    base = "https://acuityscheduling.com/api/v1"
    auth = (user_id, api_key)

    def _mask(val: str) -> str:
        """Show last 6 chars so you can verify the ID without exposing it fully."""
        if not val:
            return "NOT SET"
        return f"...{val[-6:]}" if len(val) > 6 else val

    today = date.today()
    end_date = today + timedelta(days=60)

    report = {}

    async with httpx.AsyncClient(auth=auth, timeout=15.0) as client:

        # ── 1. Fetch all appointment types ──────────────────────────────
        try:
            r = await client.get(f"{base}/appointment-types")
            r.raise_for_status()
            all_types = r.json()
            report["appointment_types"] = [
                {"id": t["id"], "name": t["name"], "duration_min": t.get("duration")}
                for t in all_types
            ]
        except Exception as e:
            report["appointment_types"] = {"error": str(e)}
            return {"ok": False, **report}

        # ── 2. Fetch all calendars from Acuity ──────────────────────────
        try:
            r = await client.get(f"{base}/calendars")
            r.raise_for_status()
            all_cals = r.json()
            report["acuity_calendars"] = [
                {"id": c["id"], "name": c.get("name"), "email": c.get("email")}
                for c in all_cals
            ]
        except Exception as e:
            report["acuity_calendars"] = {"error": str(e)}
            return {"ok": False, **report}

        # ── 3. Compare env var calendar IDs against actual Acuity IDs ───
        acuity_cal_ids = {str(c["id"]) for c in all_cals}
        env_cal_vars = {
            "ACUITY_CALENDAR_ID_ALCESTER": os.getenv("ACUITY_CALENDAR_ID_ALCESTER", "").strip(),
            "ACUITY_CALENDAR_ID_REDDITCH": os.getenv("ACUITY_CALENDAR_ID_REDDITCH", "").strip(),
            "ACUITY_CALENDAR_ID_MARK":     os.getenv("ACUITY_CALENDAR_ID_MARK", "").strip(),
            "ACUITY_CALENDAR_ID_LEANNE":   os.getenv("ACUITY_CALENDAR_ID_LEANNE", "").strip(),
        }
        cal_id_check = {}
        for var, val in env_cal_vars.items():
            if not val:
                cal_id_check[var] = {"status": "NOT SET", "value": "NOT SET", "exists_in_acuity": False}
            elif val in acuity_cal_ids:
                cal_id_check[var] = {"status": "OK", "value": _mask(val), "exists_in_acuity": True}
            else:
                cal_id_check[var] = {
                    "status": "MISMATCH — ID not found in Acuity calendars",
                    "value": _mask(val),
                    "exists_in_acuity": False,
                    "acuity_calendar_ids_available": list(acuity_cal_ids),
                }
        report["calendar_id_check"] = cal_id_check

        # ── 4. Per-location correct type matching ───────────────────────
        # Alcester → "Theorem Clinics Alcester." (type with "alcester" in name, no practitioner)
        # Redditch → "Theorem Clinics Redditch" (type with "redditch" in name)
        skip_words = ["blocked", "training", "gong", "meditation", "breathe",
                      "nada", "package", "home visit", "outreach", "ins-",
                      "massage", "laser", "shockwave", "yoga", "rehab"]

        def _best_type_for_loc(loc_keyword: str):
            """Return (id, name) of the primary booking type for this location."""
            practitioner_words = ["leanne", "mark", " ins", "insurance"]
            candidates = [
                t for t in all_types
                if loc_keyword in t["name"].lower()
                and not any(s in t["name"].lower() for s in skip_words)
                and not any(p in t["name"].lower() for p in practitioner_words)
            ]
            if candidates:
                return candidates[0]["id"], candidates[0]["name"]
            # Fallback: any type with the location keyword
            fallback = [t for t in all_types if loc_keyword in t["name"].lower()]
            if fallback:
                return fallback[0]["id"], fallback[0]["name"]
            return None, None

        alcester_type_id, alcester_type_name = _best_type_for_loc("alcester")
        redditch_type_id, redditch_type_name = _best_type_for_loc("redditch")

        report["correct_type_mapping"] = {
            "alcester": {"type_id": alcester_type_id, "type_name": alcester_type_name},
            "redditch": {"type_id": redditch_type_id, "type_name": redditch_type_name},
        }

        # ── 5. Targeted slot tests — correct type + correct calendar ────
        alcester_cal = env_cal_vars.get("ACUITY_CALENDAR_ID_ALCESTER") or None
        redditch_cal = env_cal_vars.get("ACUITY_CALENDAR_ID_REDDITCH") or None

        async def _check_slots(label, type_id, cal_id):
            if not type_id:
                return label, {"error": "No appointment type found for this location"}
            params = {
                "appointmentTypeID": type_id,
                "date": today.isoformat(),
                "endDate": end_date.isoformat(),
                "timezone": "Europe/London",
            }
            if cal_id:
                params["calendarID"] = cal_id
            try:
                r = await client.get(f"{base}/availability/times", params=params)
                if r.status_code == 200:
                    slots = r.json() if isinstance(r.json(), list) else []
                    return label, {
                        "ok": True,
                        "type_id": type_id,
                        "calendar_id": cal_id or "none (all calendars)",
                        "total_slots_60_days": len(slots),
                        "first_3_slots": [s.get("time") for s in slots[:3]],
                    }
                else:
                    return label, {
                        "ok": False,
                        "type_id": type_id,
                        "calendar_id": cal_id or "none",
                        "status": r.status_code,
                        "error": r.text[:300],
                    }
            except Exception as e:
                return label, {"ok": False, "error": str(e)}

        slot_results = await asyncio.gather(
            _check_slots("alcester — correct type, correct calendar",
                         alcester_type_id, alcester_cal),
            _check_slots("alcester — correct type, NO calendar filter",
                         alcester_type_id, None),
            _check_slots("redditch — correct type, correct calendar",
                         redditch_type_id, redditch_cal),
            _check_slots("redditch — correct type, NO calendar filter",
                         redditch_type_id, None),
        )
        slot_tests = {label: result for label, result in slot_results}

        # ── 6. Full scan — every non-blocked type with no calendar filter ─
        # Shows which types actually have ANY availability in Acuity.
        all_scanned = []
        for t in all_types:
            tname = t["name"].lower()
            if any(s in tname for s in skip_words):
                continue
            try:
                r = await client.get(f"{base}/availability/times", params={
                    "appointmentTypeID": t["id"],
                    "date": today.isoformat(),
                    "endDate": end_date.isoformat(),
                    "timezone": "Europe/London",
                })
                if r.status_code == 200:
                    slots = r.json() if isinstance(r.json(), list) else []
                    all_scanned.append({
                        "id": t["id"],
                        "name": t["name"],
                        "slots_60_days": len(slots),
                        "next_slot": slots[0].get("time") if slots else None,
                    })
                else:
                    all_scanned.append({
                        "id": t["id"], "name": t["name"],
                        "slots_60_days": f"ERROR {r.status_code}",
                    })
            except Exception as e:
                all_scanned.append({"id": t["id"], "name": t["name"], "slots_60_days": f"ERROR {e}"})

        report["all_types_availability_scan"] = sorted(
            all_scanned, key=lambda x: x.get("slots_60_days") if isinstance(x.get("slots_60_days"), int) else -1,
            reverse=True,
        )
        report["slot_tests"] = slot_tests

    # Overall verdict
    cal_ok = all(v.get("status") == "OK" for v in cal_id_check.values() if isinstance(v, dict))
    slots_ok = any(
        isinstance(v, dict) and v.get("ok") and v.get("total_slots_60_days", 0)
        for v in slot_tests.values()
    )
    report["verdict"] = {
        "calendar_ids_configured_correctly": cal_ok,
        "slots_available": slots_ok,
        "summary": (
            "All good — slots are being returned." if (cal_ok and slots_ok)
            else "Issues found — check calendar_id_check and slot_tests above."
        ),
    }

    return {"ok": cal_ok and slots_ok, **report}
