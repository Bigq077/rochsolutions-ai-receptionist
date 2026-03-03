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
    if request.method == "POST" and request.url.path.startswith("/twilio/"):
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
    checks = {
        "twilio": bool(os.getenv("TWILIO_ACCOUNT_SID")),
        "environment": os.getenv("RENDER") or "local",
    }

    redis_available = False
    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            await redis_client.ping()
            redis_available = True
    except Exception:
        pass

    checks["redis"] = redis_available

    return {
        "status": "ready",
        "checks": checks,
    }

# ============================================================================
# ROUTERS
# ============================================================================

app.include_router(tts_eleven_router)
app.include_router(twilio_router)
app.include_router(google_calendar_router)
app.include_router(redis_debug_router)
app.include_router(avatar_router)
app.include_router(admin_router)  # temporary admin router

logger.info("✅ All routes registered successfully")

# ============================================================================
# STARTUP EVENT
# ============================================================================

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
    # Clinic credential validation (#21)
    # Warn at startup if critical env vars are missing so problems surface
    # immediately in Render logs rather than at first real booking attempt.
    # ------------------------------------------------------------------ #
    _validate_clinic_config()

    # Start reminder worker only if:
    # 1. Not on Render (Render doesn't have Redis on free tier)
    # 2. Redis is available
    if not os.getenv("RENDER") and redis_available:
        try:
            import asyncio
            from app.notifications.scheduler import start_reminder_worker

            asyncio.create_task(start_reminder_worker(interval_seconds=300))
            logger.info("✅ SMS reminder worker started (checks every 5 minutes)")
        except Exception as e:
            logger.warning(f"⚠️  SMS reminder worker not started: {e}")
    else:
        if os.getenv("RENDER"):
            logger.info("ℹ️  Running on Render - reminder worker disabled (no Redis)")
        else:
            logger.info("ℹ️  Reminder worker disabled (Redis not available)")

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
