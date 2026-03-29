# app/routes/admin.py
import asyncio
import json
import os
import httpx
from fastapi import APIRouter
from app.storage.redis_store import redis_delete_key
from app.media_streams.config import (
    ELEVENLABS_API_KEY,
    ASSEMBLYAI_API_KEY,
    ASSEMBLYAI_WS_URL,
    OPENAI_API_KEY,
)

router = APIRouter(prefix="/admin")

ADMIN_KEY = os.getenv("ADMIN_KEY", "")


@router.get("/test/session/{call_sid}")
async def get_test_session(call_sid: str):
    """
    Return the Media Streams session for a given call SID.
    Used by the automated test suite to retrieve what Susie said
    without needing to record and transcribe the call.

    Returns the conversation_history from the ms_session:{call_sid} Redis key.
    Also checks the legacy call:{call_sid} key as fallback.
    """
    try:
        from app.media_streams.session import _get_redis
        redis = _get_redis()
        if not redis:
            return {"ok": False, "error": "Redis not available"}

        # Try Media Streams session first
        ms_key = f"ms_session:{call_sid}"
        data = await redis.get(ms_key)
        if data:
            session = json.loads(data)
            return {
                "ok":                   True,
                "source":               "ms_session",
                "call_sid":             call_sid,
                "conversation_history": session.get("conversation_history", []),
                "turns":                session.get("turns", []),
                "state":                session.get("state"),
                # Flow-engine progress fields — used by evaluator for flow_completed
                "flow_step":            session.get("flow_step"),
                "flow_started":         session.get("flow_started"),
                "intent":               session.get("intent"),
                "reason":               session.get("reason"),
                "booking_confirmed":    session.get("booking_confirmed"),
                "reschedule_confirmed": session.get("reschedule_confirmed"),
                "cancel_confirmed":     session.get("cancel_confirmed"),
                "selected_slot":        session.get("selected_slot"),
                "full_name":            session.get("full_name"),
                "phone_confirmed":      session.get("phone_confirmed"),
                "selected_location":    session.get("selected_location"),
            }

        # Try legacy session
        legacy_key = f"call:{call_sid}"
        data = await redis.get(legacy_key)
        if data:
            session = json.loads(data)
            return {
                "ok":                   True,
                "source":               "legacy_session",
                "call_sid":             call_sid,
                "conversation_history": session.get("conversation_history", []),
                "turns":                session.get("turns", []),
                "state":                session.get("state"),
                "flow_step":            session.get("flow_step"),
                "flow_started":         session.get("flow_started"),
                "intent":               session.get("intent"),
                "reason":               session.get("reason"),
                "booking_confirmed":    session.get("booking_confirmed"),
                "reschedule_confirmed": session.get("reschedule_confirmed"),
                "cancel_confirmed":     session.get("cancel_confirmed"),
                "selected_slot":        session.get("selected_slot"),
                "full_name":            session.get("full_name"),
                "phone_confirmed":      session.get("phone_confirmed"),
                "selected_location":    session.get("selected_location"),
            }

        return {"ok": False, "error": f"No session found for {call_sid}"}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/diagnostics")
async def diagnostics():
    """
    Test ElevenLabs, AssemblyAI, and OpenAI connectivity from Render.

    Returns a JSON report showing which services are reachable and configured.
    Use this to diagnose why Susie's voice pipeline is failing.

    Usage: GET /admin/diagnostics
    """
    import websockets
    import websockets.exceptions

    results: dict = {}

    # ── ElevenLabs ──────────────────────────────────────────────────────────
    el_key = ELEVENLABS_API_KEY
    if not el_key:
        results["elevenlabs"] = {
            "ok": False,
            "error": "ELEVENLABS_API_KEY not set",
        }
    else:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://api.elevenlabs.io/v1/user",
                    headers={"xi-api-key": el_key},
                )
                if r.status_code == 200:
                    sub = r.json().get("subscription", {})
                    used  = sub.get("character_count", 0)
                    limit = sub.get("character_limit", 0)
                    results["elevenlabs"] = {
                        "ok":                True,
                        "key_prefix":        el_key[:8] + "...",
                        "chars_used":        used,
                        "chars_limit":       limit,
                        "chars_remaining":   limit - used,
                        "exhausted":         used >= limit > 0,
                    }
                else:
                    results["elevenlabs"] = {
                        "ok":        False,
                        "status":    r.status_code,
                        "error":     r.text[:300],
                        "key_prefix": el_key[:8] + "...",
                    }
        except Exception as exc:
            results["elevenlabs"] = {
                "ok":        False,
                "error":     str(exc),
                "key_prefix": el_key[:8] + "...",
            }

    # ── AssemblyAI ──────────────────────────────────────────────────────────
    aa_key = ASSEMBLYAI_API_KEY
    if not aa_key:
        results["assemblyai"] = {
            "ok":    False,
            "error": "ASSEMBLYAI_API_KEY not set",
        }
    else:
        try:
            async with websockets.connect(
                ASSEMBLYAI_WS_URL,
                additional_headers={"Authorization": aa_key},
                open_timeout=8.0,
                close_timeout=3.0,
            ) as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=8.0)
                data = json.loads(raw)
                if data.get("type") == "Begin":
                    results["assemblyai"] = {
                        "ok":         True,
                        "key_prefix": aa_key[:8] + "...",
                        "session_id": data.get("id"),
                        "expires_at": str(data.get("expires_at")),
                        "url":        ASSEMBLYAI_WS_URL[:80],
                    }
                else:
                    results["assemblyai"] = {
                        "ok":    False,
                        "error": f"Unexpected first message type={data.get('type')!r}",
                        "msg":   str(data)[:200],
                    }
        except asyncio.TimeoutError:
            results["assemblyai"] = {
                "ok":    False,
                "error": "Timeout — no Begin message within 8s",
                "key_prefix": aa_key[:8] + "...",
            }
        except Exception as exc:
            results["assemblyai"] = {
                "ok":    False,
                "error": str(exc),
                "key_prefix": aa_key[:8] + "...",
                "url":   ASSEMBLYAI_WS_URL[:80],
            }

    # ── OpenAI ──────────────────────────────────────────────────────────────
    oa_key = OPENAI_API_KEY
    if not oa_key:
        results["openai"] = {
            "ok":    False,
            "error": "OPENAI_API_KEY not set",
        }
    else:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {oa_key}"},
                )
                results["openai"] = {
                    "ok":        r.status_code == 200,
                    "status":    r.status_code,
                    "key_prefix": oa_key[:8] + "...",
                }
        except Exception as exc:
            results["openai"] = {
                "ok":        False,
                "error":     str(exc),
                "key_prefix": oa_key[:8] + "...",
            }

    overall = all(v.get("ok") for v in results.values())
    return {"ok": overall, **results}


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
