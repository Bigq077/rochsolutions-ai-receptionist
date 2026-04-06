# app/routes/health.py
"""
Health check endpoints for monitoring and deployment platforms like Render.
"""

from fastapi import APIRouter
from typing import Dict, Any
import os

router = APIRouter()


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """
    Basic health check endpoint.
    Returns 200 OK if the service is running.

    Used by:
    - Render deployment health checks
    - Load balancers
    - Monitoring systems
    - Test runner (prints git_commit to confirm which deploy is live)
    """
    # Priority 1: .git_commit file written by render.yaml buildCommand
    #   `git rev-parse --short HEAD > .git_commit`
    # Priority 2: RENDER_GIT_COMMIT env var (set by Render on some plans)
    # Priority 3: run git directly (works locally, not on Render runtime)
    git_commit = ""
    try:
        import pathlib
        _f = pathlib.Path(__file__).parent.parent.parent / ".git_commit"
        if _f.exists():
            git_commit = _f.read_text().strip()[:7]
    except Exception:
        pass
    if not git_commit:
        git_commit = os.getenv("RENDER_GIT_COMMIT", "")[:7] or ""
    if not git_commit:
        try:
            import subprocess
            git_commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except Exception:
            git_commit = "unknown"

    return {
        "status": "healthy",
        "service": "theorem-health-ai-receptionist",
        "git_commit": git_commit,
    }


@router.get("/")
async def root() -> Dict[str, str]:
    """
    Root endpoint - shows the service is running.
    """
    return {
        "message": "Theorem Health AI Receptionist API",
        "status": "running",
        "docs": "/docs",
        "health": "/health"
    }


@router.get("/ready")
async def readiness_check() -> Dict[str, Any]:
    """
    Readiness check - verifies the service is ready to handle requests.
    
    Checks:
    - Environment variables are loaded
    - Critical services are configured
    """
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

    # Check Redis (optional)
    try:
        from app.storage.redis_store import redis_client
        if redis_client:
            redis_client.ping()
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
