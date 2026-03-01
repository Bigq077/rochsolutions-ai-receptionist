# app/routes/admin.py
import os
import httpx
from fastapi import APIRouter
from app.storage.redis_store import redis_delete_key  # we’ll add this helper

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
      /me               — confirms credentials are valid
      /appointment-types — lists configured appointment types
      /calendars        — lists practitioner calendars

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
            "error": f"Missing env vars: {‘, ‘.join(missing)}",
            "hint": "Set these in the Render dashboard under Environment.",
        }

    base = "https://acuityscheduling.com/api/v1"
    auth = (user_id, api_key)
    results = {}

    async with httpx.AsyncClient(auth=auth, timeout=10.0) as client:

        # 1. Account info — proves credentials work
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

        # 2. Appointment types — confirms Acuity is configured correctly
        try:
            r = await client.get(f"{base}/appointment-types")
            if r.status_code == 200:
                types = r.json()
                results["appointment_types"] = {
                    "ok": True,
                    "count": len(types),
                    "types": [{"id": t.get("id"), "name": t.get("name"), "duration": t.get("duration")} for t in types],
                }
            else:
                results["appointment_types"] = {"ok": False, "status": r.status_code, "body": r.text[:300]}
        except Exception as e:
            results["appointment_types"] = {"ok": False, "error": str(e)}

        # 3. Calendars — shows which practitioner calendars exist
        try:
            r = await client.get(f"{base}/calendars")
            if r.status_code == 200:
                cals = r.json()
                results["calendars"] = {
                    "ok": True,
                    "count": len(cals),
                    "calendars": [{"id": c.get("id"), "name": c.get("name"), "email": c.get("email")} for c in cals],
                }
            else:
                results["calendars"] = {"ok": False, "status": r.status_code, "body": r.text[:300]}
        except Exception as e:
            results["calendars"] = {"ok": False, "error": str(e)}

    # Also report which calendar ID env vars are set
    cal_vars = {
        "ACUITY_CALENDAR_ID_ALCESTER": os.getenv("ACUITY_CALENDAR_ID_ALCESTER", ""),
        "ACUITY_CALENDAR_ID_REDDITCH": os.getenv("ACUITY_CALENDAR_ID_REDDITCH", ""),
        "ACUITY_CALENDAR_ID_MARK":     os.getenv("ACUITY_CALENDAR_ID_MARK", ""),
        "ACUITY_CALENDAR_ID_LEANNE":   os.getenv("ACUITY_CALENDAR_ID_LEANNE", ""),
    }
    results["env_calendar_ids"] = {
        k: ("✅ set" if v else "❌ missing") for k, v in cal_vars.items()
    }

    overall_ok = all(v.get("ok") for v in results.values() if isinstance(v, dict) and "ok" in v)
    return {"ok": overall_ok, **results}
