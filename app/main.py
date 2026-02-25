# app/main.py
from dotenv import load_dotenv
load_dotenv()

import os
import logging
from fastapi import FastAPI
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
# HEALTH ENDPOINTS
# ============================================================================

@app.get("/health")
def health():
    """Health check endpoint for Render and monitoring systems."""
    return {
        "ok": True,
        "status": "healthy",
        "service": "theorem-health-ai-receptionist",
        "version": "1.0.0"
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
def readiness():
    """Readiness check - verifies critical services are configured."""
    checks = {
        "twilio": bool(os.getenv("TWILIO_ACCOUNT_SID")),
        "environment": os.getenv("RENDER") or "local",
    }
    
    # Check Redis (optional)
    redis_available = False
    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            redis_client.ping()
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
            redis_client.ping()
            redis_available = True
            logger.info("✅ Redis connected")
    except Exception as e:
        logger.warning(f"⚠️  Redis not available: {e}")
    
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
