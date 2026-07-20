# app/main.py
from dotenv import load_dotenv
load_dotenv()

import os
import logging
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

from app.routes.twilio import router as twilio_router
from app.routes.google_calendar import router as google_calendar_router
from app.routes.redis_debug import router as redis_debug_router
from app.routes.avatar import router as avatar_router
from app.routes.tts_eleven import router as tts_eleven_router
# Admin route (temporary, for clearing google_tokens)
from app.routes.admin import router as admin_router
# OpenAI Realtime API WebSocket bridge (active when REALTIME_ENABLED=true)
from app.routes.realtime import router as realtime_router

# Parallel Media Streams pipeline (active when MEDIA_STREAMS_ENABLED=true)
# Routes: POST /ms/incoming (TwiML), WS /ms/stream (WebSocket handler)
from app.media_streams.config import MEDIA_STREAMS_ENABLED
from app.media_streams.router import router as media_streams_router

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# SENTRY — optional error reporting
# Activate by setting SENTRY_DSN env var in Render.
# ============================================================================

from app.config import SENTRY_DSN

if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=0.05,   # 5 % of requests traced (low overhead)
        environment=os.getenv("ENV", "dev"),
        send_default_pii=False,    # HIPAA: never send PII to Sentry
    )
    logger.info("✅ Sentry initialised (env=%s)", os.getenv("ENV", "dev"))
else:
    logger.info("ℹ️  Sentry disabled (SENTRY_DSN not set)")

# ============================================================================
# CREATE APP
# ============================================================================

app = FastAPI(
    title="Theorem Health AI Receptionist",
    description="AI-powered phone receptionist for Theorem Health & Wellness",
    version="1.0.0",
)

# ============================================================================
# CORS MIDDLEWARE
# ============================================================================

# ✅ CORS — allow ALL Netlify domains + local dev
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https://.*\.netlify\.app$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# RATE LIMITING MIDDLEWARE
# Protects /twilio/* webhook endpoints against flooding/abuse.
# Uses Redis when available; degrades gracefully to a no-op without it.
# Limit: 200 POST requests per IP per minute — well above normal Twilio
# traffic but blocks bots hammering the endpoints directly.
# ============================================================================

_RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "200"))
_RATE_LIMIT_WINDOW_SEC = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60"))


@app.middleware("http")
async def twilio_rate_limit(request: Request, call_next):
    """Per-IP rate limit on /twilio/* POST endpoints using Redis."""
    if request.method == "POST" and (request.url.path.startswith("/twilio/") or request.url.path.startswith("/ms/")):
        client_ip = request.client.host if request.client else "unknown"
        try:
            from app.storage.redis_store import redis_client
            if redis_client:
                rl_key = f"ratelimit:{client_ip}"
                count = await redis_client.incr(rl_key)
                if count == 1:
                    await redis_client.expire(rl_key, _RATE_LIMIT_WINDOW_SEC)
                if count > _RATE_LIMIT_REQUESTS:
                    logger.warning(
                        "Rate limit exceeded: ip=%s count=%d path=%s",
                        client_ip, count, request.url.path,
                    )
                    return PlainTextResponse("Too Many Requests", status_code=429)
        except Exception as exc:
            # Never block a real Twilio call due to a Redis error
            logger.warning("Rate-limit Redis error (ignoring): %r", exc)
    return await call_next(request)


# ============================================================================
# HEALTH ENDPOINTS
# ============================================================================

@app.get("/health")
async def health():
    """
    Health check endpoint for Render and monitoring systems.
    Also exposes Phase 3 fallback counter for operational visibility.
    """
    phase3_fallbacks = None
    redis_ok = False

    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            await redis_client.ping()
            redis_ok = True
            raw = await redis_client.get("metrics:phase3:fallbacks")
            phase3_fallbacks = int(raw) if raw else 0
    except Exception:
        pass

    return {
        "ok": True,
        "status": "healthy",
        "service": "theorem-health-ai-receptionist",
        "version": "1.0.0",
        "redis": redis_ok,
        "phase3_fallbacks": phase3_fallbacks,
    }


@app.get("/")
def root():
    """Root endpoint - confirms service is running."""
    return {
        "status": "ok",
        "service": "Theorem Health AI Receptionist",
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/ready")
async def readiness():
    """Readiness check - verifies critical services are configured."""
    acuity_user_id = os.getenv("ACUITY_USER_ID", "").strip()
    acuity_api_key = os.getenv("ACUITY_API_KEY", "").strip()
    phase3 = os.getenv("PHASE3_ENABLED", "false").lower() == "true"

    checks = {
        "twilio": bool(os.getenv("TWILIO_ACCOUNT_SID")),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "phase3_enabled": phase3,
        "acuity_credentials": bool(acuity_user_id and acuity_api_key),
        "acuity_calendar_alcester": bool(os.getenv("ACUITY_CALENDAR_ID_ALCESTER")),
        "acuity_calendar_redditch": bool(os.getenv("ACUITY_CALENDAR_ID_REDDITCH")),
        "redis": False,
        "environment": os.getenv("RENDER") or "local",
    }

    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            await redis_client.ping()
            checks["redis"] = True
    except Exception:
        pass

    all_critical_ok = (
        checks["twilio"]
        and checks["anthropic"]
        and checks["phase3_enabled"]
        and checks["acuity_credentials"]
    )

    return {
        "status": "ready" if all_critical_ok else "not_ready",
        "checks": checks,
        "booking_ready": checks["acuity_credentials"] and checks["phase3_enabled"],
    }

# ============================================================================
# DIAGNOSTIC: Acuity forms dump
# GET /debug/acuity-forms — returns every form, field ID, type, required flag,
# and appointmentTypes list straight from Acuity.  Use this to find the correct
# field ID for each appointment type.
# ============================================================================

from fastapi.responses import JSONResponse

@app.get("/debug/acuity-forms")
async def debug_acuity_forms():
    """Dump all Acuity intake forms with field IDs — for diagnosing required-field errors."""
    try:
        from app.tools.receptionist_tools import _get_acuity_adapter
        adapter = _get_acuity_adapter()
        if not adapter:
            return JSONResponse({"error": "Acuity adapter not configured"}, status_code=503)
        response = await adapter._request_with_retry("GET", "/forms")
        forms_raw = response.json() if isinstance(response.json(), list) else []
        result = []
        for form in forms_raw:
            result.append({
                "form_id":          form.get("id"),
                "form_name":        form.get("name", ""),
                "appointmentTypes": form.get("appointmentTypes", []),
                "fields": [
                    {
                        "field_id":  f.get("id"),
                        "name":      str(f.get("name", ""))[:120],
                        "type":      f.get("type"),
                        "required":  f.get("required", False),
                    }
                    for f in form.get("fields", [])
                ],
            })
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ============================================================================
# ROUTERS
# ============================================================================

app.include_router(tts_eleven_router)
app.include_router(twilio_router)
app.include_router(google_calendar_router)
app.include_router(redis_debug_router)
app.include_router(avatar_router)
app.include_router(admin_router)   # temporary admin router
app.include_router(realtime_router)  # OpenAI Realtime WebSocket bridge

# Media Streams pipeline — always registered so Twilio routing works regardless of flag
app.include_router(media_streams_router)
if MEDIA_STREAMS_ENABLED:
    logger.info("✅ Media Streams system: ENABLED (/ms/incoming, /ms/stream)")
else:
    logger.info("ℹ️  Media Streams system: routes registered but MEDIA_STREAMS_ENABLED=false (kill switch active)")

logger.info("✅ All routes registered successfully")

# ============================================================================
# STARTUP EVENT
# ============================================================================

async def _prewarm_singletons() -> None:
    """
    Initialise latency-critical singletons at boot so callers never pay
    the cold-start cost mid-conversation.

    1. AsyncAnthropic client — creates the httpx connection pool to Anthropic.
       Without this the first API call opens a new TCP+TLS connection every
       turn, adding ~500 ms (and more after idle).
    2. Tool schemas / executors — importing the tool module on first use adds
       1-2 s of Python import time the first time the booking turn runs.
    3. AcuityAdapter singleton — primes the httpx connection pool to Acuity.
    """
    # 1. Anthropic client singleton
    try:
        from app.flows.conversation import _get_client
        _get_client()   # initialises _anthropic_client module global
        logger.info("✅ Anthropic client singleton pre-warmed")
    except Exception as e:
        logger.warning("⚠️  Anthropic pre-warm skipped: %r", e)

    # 2. Tool schemas (importing the module caches TOOL_SCHEMAS / TOOL_EXECUTORS)
    try:
        from app.tools.receptionist_tools import TOOL_SCHEMAS  # noqa: F401
        logger.info("✅ Tool schemas pre-loaded (%d tools)", len(TOOL_SCHEMAS))
    except Exception as e:
        logger.warning("⚠️  Tool schema pre-load skipped: %r", e)

    # 3. Acuity adapter singleton — instantiate AND make one real API call
    # so the TCP+TLS connection to Acuity is live before the first caller arrives.
    # We call GET /appointment-types (lightweight, read-only, always succeeds).
    try:
        from app.tools.receptionist_tools import _get_acuity_adapter
        adapter = _get_acuity_adapter()
        if adapter:
            try:
                resp = await adapter.client.get("/appointment-types", timeout=10.0)
                types_data = resp.json() if resp.status_code == 200 else []
                type_summary = {str(t.get("id")): t.get("name") for t in types_data}
                logger.info("✅ Acuity TCP connection pre-warmed (live)")
                logger.info("🗓️  Acuity appointment types available: %s", type_summary)
            except Exception:
                # Non-fatal: adapter is initialised; first real call may be 300ms slower
                logger.info("✅ Acuity adapter singleton pre-warmed (TCP not yet live)")
        else:
            logger.info("ℹ️  Acuity adapter not pre-warmed (credentials not set)")
    except Exception as e:
        logger.warning("⚠️  Acuity pre-warm skipped: %r", e)

    # 4. ElevenLabs TLS pool — the greeting is synthesised ~40ms after the call
    # connects, so a cold pool puts full DNS+TLS setup directly in front of the
    # caller (1,989ms on call CA717c7cc1 vs ~120ms once warm). Warm it here and
    # again per-call from the Twilio webhook (app/media_streams/router.py).
    try:
        from app.media_streams.tts_stream import prewarm as _tts_prewarm
        _elapsed = await _tts_prewarm()
        if _elapsed:
            logger.info("✅ ElevenLabs TLS pool pre-warmed (%.0fms)", _elapsed * 1000)
        else:
            logger.info("ℹ️  ElevenLabs not pre-warmed (no API key or request failed)")
    except Exception as e:
        logger.warning("⚠️  ElevenLabs pre-warm skipped: %r", e)


@app.on_event("startup")
async def startup():
    """
    Application startup tasks.
    - Logs startup info
    - Validates clinic credentials (#21)
    - Starts reminder worker (only if Redis available and not on Render)
    """
    logger.info("=" * 60)
    logger.info("🚀 Theorem Health AI Receptionist Starting...")
    logger.info("=" * 60)
    logger.info(f"Environment: {os.getenv('RENDER', 'local')}")
    logger.info(f"Twilio configured: {bool(os.getenv('TWILIO_ACCOUNT_SID'))}")

    # Check Redis availability
    redis_available = False
    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            await redis_client.ping()
            redis_available = True
            logger.info("✅ Redis connected")
    except Exception as e:
        logger.warning(f"⚠️  Redis not available: {e}")

    # ------------------------------------------------------------------ #
    # Pre-warm latency-critical singletons (#perf)
    #
    # Without this, the very first booking turn pays:
    #   - Python module import cost for anthropic, httpx, tool schemas (~1-2s)
    #   - TCP + TLS handshake to Anthropic API (~300-500ms cold)
    #   - TCP + TLS handshake to Acuity (~200-400ms cold)
    #
    # By initialising the singletons at startup, these costs are paid once
    # during Render's boot sequence and never on a live caller's turn.
    # ------------------------------------------------------------------ #
    await _prewarm_singletons()

    # ------------------------------------------------------------------ #
    # Clinic credential validation (#21)
    # Warn at startup if critical env vars are missing so problems surface
    # immediately in Render logs rather than at first real booking attempt.
    # ------------------------------------------------------------------ #
    _validate_clinic_config()

    # Start reminder worker whenever Redis is available (Render + local).
    if redis_available:
        try:
            import asyncio
            from app.notifications.scheduler import start_reminder_worker

            asyncio.create_task(start_reminder_worker(interval_seconds=300))
            logger.info("✅ SMS reminder worker started (checks every 5 minutes)")
        except Exception as e:
            logger.warning(f"⚠️  SMS reminder worker not started: {e}")
    else:
        logger.info("ℹ️  Reminder worker disabled (Redis not available)")

    # End-of-day booking digest worker. Self-gates on MEDIA_STREAMS_CLINIC_ID +
    # operational.digest.enabled, and is SMTP/Redis-optional (safe no-op until
    # configured), so it's always safe to start.
    try:
        import asyncio
        from app.notifications.digest import start_digest_worker

        asyncio.create_task(start_digest_worker(interval_seconds=300))
        logger.info("✅ Booking digest worker started (checks every 5 minutes)")
    except Exception as e:
        logger.warning(f"⚠️  Booking digest worker not started: {e}")

    # Daily call-quality digest worker (app/obs/worker.py). Self-gates on
    # OBS_DIGEST_ENABLED + OBS_DATABASE_URL and returns immediately when either is
    # unset, so it costs nothing until turned on. Read-only over the `calls` table.
    try:
        import asyncio
        from app.obs.worker import start_obs_digest_worker

        asyncio.create_task(start_obs_digest_worker(interval_seconds=300))
        logger.info("✅ Call digest worker started (checks every 5 minutes)")
    except Exception as e:
        logger.warning(f"⚠️  Call digest worker not started: {e}")

    logger.info("=" * 60)
    logger.info("✅ Startup complete - ready to accept requests")
    logger.info("=" * 60)


def _validate_clinic_config() -> None:
    """
    Validate all clinic credentials and log warnings for any that are missing.
    This runs at startup so problems surface in Render logs immediately.
    Does NOT raise — a missing credential is a configuration warning, not a
    crash condition (other clinics may still work fine).
    """
    try:
        from app.clinic_config import CLINICS, ACUITY_CONFIG

        for clinic_id, clinic in CLINICS.items():
            booking_system = clinic.get("booking_system")

            if booking_system == "acuity":
                acuity_cfg = ACUITY_CONFIG.get(clinic_id, {})
                if not acuity_cfg.get("user_id"):
                    logger.warning(
                        "⚠️  CLINIC CONFIG: ACUITY_USER_ID not set for clinic '%s'",
                        clinic_id,
                    )
                if not acuity_cfg.get("api_key"):
                    logger.warning(
                        "⚠️  CLINIC CONFIG: ACUITY_API_KEY not set for clinic '%s'",
                        clinic_id,
                    )
                # Check location-specific calendar IDs
                cal_ids = acuity_cfg.get("calendar_ids", {})
                for loc, cal_id in cal_ids.items():
                    if not cal_id:
                        logger.warning(
                            "⚠️  CLINIC CONFIG: Acuity calendar ID missing for "
                            "clinic='%s' location='%s'",
                            clinic_id, loc,
                        )

            elif booking_system == "google_calendar":
                if not os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"):
                    logger.warning(
                        "⚠️  CLINIC CONFIG: GOOGLE_SERVICE_ACCOUNT_JSON not set "
                        "(required for clinic '%s')",
                        clinic_id,
                    )

        # Check Twilio auth token for signature validation
        from app.config import TWILIO_AUTH_TOKEN
        if not TWILIO_AUTH_TOKEN:
            logger.warning(
                "⚠️  SECURITY: TWILIO_AUTH_TOKEN not set — "
                "webhook signature validation is DISABLED. "
                "Set this env var in Render to reject forged webhook calls."
            )

        # Check SESSION_SECRET for HMAC session keys
        from app.config import SESSION_SECRET
        if not SESSION_SECRET:
            logger.warning(
                "⚠️  SECURITY: SESSION_SECRET not set — "
                "Redis session keys are not HMAC-protected. "
                "Set this env var to harden session key confidentiality."
            )

        logger.info("✅ Clinic config validation complete")

    except Exception as exc:
        logger.warning("⚠️  Clinic config validation failed: %r", exc)


# ============================================================================
# SHUTDOWN EVENT
# ============================================================================

@app.on_event("shutdown")
async def shutdown():
    """Application shutdown tasks — close all async resources cleanly."""
    logger.info("=" * 60)
    logger.info("👋 Theorem Health AI Receptionist shutting down...")
    logger.info("=" * 60)

    # Close the Redis async connection pool so its sockets are released
    # before the process exits. Without this, the OS force-closes them
    # which can produce 'Bad file descriptor' errors in the uvicorn logs.
    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            await redis_client.aclose()
            logger.info("✅ Redis connection pool closed")
    except Exception as e:
        logger.warning(f"⚠️  Redis close warning: {e}")
