# app/routes/admin.py
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

        # ── 4. Test actual slot availability for each location ──────────
        # Find the best appointment type for a standard physio assessment
        physio_type = None
        physio_type_name = None
        for t in all_types:
            name_lower = t["name"].lower()
            if any(k in name_lower for k in ["physio", "physiotherapy", "assessment", "theorem"]):
                physio_type = t["id"]
                physio_type_name = t["name"]
                break

        if not physio_type and all_types:
            # Fallback: first non-blocked type
            skip_words = ["blocked", "training", "gong", "meditation", "breathe", "nada", "package"]
            for t in all_types:
                if not any(s in t["name"].lower() for s in skip_words):
                    physio_type = t["id"]
                    physio_type_name = t["name"]
                    break

        report["matched_appointment_type"] = {
            "id": physio_type,
            "name": physio_type_name,
            "note": "This is the type used when caller books a physio assessment",
        }

        slot_tests = {}
        test_cases = [
            ("alcester (no calendar filter)", None),
            ("alcester (with calendar ID)", env_cal_vars.get("ACUITY_CALENDAR_ID_ALCESTER") or None),
            ("redditch (with calendar ID)", env_cal_vars.get("ACUITY_CALENDAR_ID_REDDITCH") or None),
            ("mark calendar direct",        env_cal_vars.get("ACUITY_CALENDAR_ID_MARK") or None),
        ]

        for label, cal_id in test_cases:
            if physio_type is None:
                slot_tests[label] = {"error": "No usable appointment type found"}
                continue
            params = {
                "appointmentTypeID": physio_type,
                "date": today.isoformat(),
                "endDate": end_date.isoformat(),
                "timezone": "Europe/London",
            }
            if cal_id:
                params["calendarID"] = cal_id
            try:
                r = await client.get(f"{base}/availability/times", params=params)
                if r.status_code == 200:
                    slots = r.json()
                    first3 = [s.get("time") for s in slots[:3]] if isinstance(slots, list) else []
                    slot_tests[label] = {
                        "ok": True,
                        "total_slots_60_days": len(slots) if isinstance(slots, list) else "?",
                        "first_3_slots": first3,
                        "calendar_id_used": _mask(cal_id) if cal_id else "none (all calendars)",
                    }
                else:
                    slot_tests[label] = {
                        "ok": False,
                        "status": r.status_code,
                        "body": r.text[:400],
                        "calendar_id_used": _mask(cal_id) if cal_id else "none",
                    }
            except Exception as e:
                slot_tests[label] = {"ok": False, "error": str(e)}

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
