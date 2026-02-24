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
    """
    return {
        "status": "healthy",
        "service": "theorem-health-ai-receptionist",
        "version": "1.0.0"
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
    checks = {
        "twilio": bool(os.getenv("TWILIO_ACCOUNT_SID")),
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
    
    all_critical_ok = checks["twilio"]  # Only Twilio is critical
    
    return {
        "status": "ready" if all_critical_ok else "not_ready",
        "checks": checks,
    }
